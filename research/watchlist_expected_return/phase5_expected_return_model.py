"""5단계: 기대수익률 shadow 모델의 시간순 검증."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from research.watchlist_expected_return.phase1_data_audit import DEFAULT_KRX_DB, DEFAULT_WATCHLIST_DB
from research.watchlist_expected_return.phase2_score_relationship import _pearson, _ranks
from research.watchlist_expected_return.phase4_holding_strategy import (
    load_price_paths,
    simulate_tp_sl,
)


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "results"
FEATURES = ("entry_volume", "entry_close", "market_cap", "trading_value", "ratio", "intraday_rank")
LOG_FEATURES = {"entry_volume", "entry_close", "market_cap", "trading_value"}
TARGET_POLICY = {"tp": 0.07, "sl": 0.02, "max_hold_days": 2, "touch_policy": "sl_first", "cost_rate": 0.01}
RIDGE_ALPHA = 10.0
MIN_TRAIN_ROWS = 50


def attach_target(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        outcome = simulate_tp_sl(
            row,
            TARGET_POLICY["tp"],
            TARGET_POLICY["sl"],
            TARGET_POLICY["max_hold_days"],
            TARGET_POLICY["touch_policy"],
        )
        if outcome is not None:
            output.append({
                **row,
                "target_net_return": outcome["gross_return"] - TARGET_POLICY["cost_rate"],
                "target_exit_reason": outcome["exit_reason"],
            })
    return output


def _raw_feature(row: dict[str, Any], feature: str) -> float | None:
    value = row.get(feature)
    if value is None:
        return None
    value = float(value)
    return math.log1p(max(0.0, value)) if feature in LOG_FEATURES else value


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    augmented = [matrix[index][:] + [vector[index]] for index in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        if abs(augmented[column][column]) < 1e-12:
            augmented[column][column] = 1e-12
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                augmented[row][index] - factor * augmented[column][index]
                for index in range(size + 1)
            ]
    return [augmented[index][-1] for index in range(size)]


def fit_ridge(rows: list[dict[str, Any]], alpha: float = RIDGE_ALPHA) -> dict[str, Any]:
    medians = {}
    means = {}
    scales = {}
    for feature in FEATURES:
        values = [_raw_feature(row, feature) for row in rows]
        usable = [value for value in values if value is not None]
        medians[feature] = statistics.median(usable) if usable else 0.0
        filled = [value if value is not None else medians[feature] for value in values]
        means[feature] = statistics.fmean(filled)
        scales[feature] = statistics.pstdev(filled) or 1.0

    design = []
    targets = []
    for row in rows:
        vector = [1.0]
        for feature in FEATURES:
            value = _raw_feature(row, feature)
            filled = medians[feature] if value is None else value
            vector.append((filled - means[feature]) / scales[feature])
        design.append(vector)
        targets.append(float(row["target_net_return"]))

    width = len(FEATURES) + 1
    xtx = [[sum(row[i] * row[j] for row in design) for j in range(width)] for i in range(width)]
    xty = [sum(row[i] * target for row, target in zip(design, targets)) for i in range(width)]
    for index in range(1, width):
        xtx[index][index] += alpha
    return {
        "features": list(FEATURES),
        "medians": medians,
        "means": means,
        "scales": scales,
        "coefficients": _solve(xtx, xty),
        "alpha": alpha,
        "training_count": len(rows),
    }


def predict(model: dict[str, Any], row: dict[str, Any]) -> float:
    vector = [1.0]
    for feature in model["features"]:
        value = _raw_feature(row, feature)
        filled = model["medians"][feature] if value is None else value
        vector.append((filled - model["means"][feature]) / model["scales"][feature])
    return sum(coefficient * value for coefficient, value in zip(model["coefficients"], vector))


def walk_forward_predictions(rows: list[dict[str, Any]], min_train_rows: int = MIN_TRAIN_ROWS) -> list[dict[str, Any]]:
    dates = sorted({row["date"] for row in rows})
    predictions = []
    for date in dates:
        train = [row for row in rows if row["date"] < date]
        if len(train) < min_train_rows:
            continue
        test = [row for row in rows if row["date"] == date]
        model = fit_ridge(train)
        baseline = statistics.fmean(row["target_net_return"] for row in train)
        volume_threshold = statistics.median(row["entry_volume"] for row in train if row.get("entry_volume") is not None)
        for row in test:
            predictions.append({
                "date": row["date"],
                "ticker": row["ticker"],
                "actual": row["target_net_return"],
                "predicted": predict(model, row),
                "baseline_predicted": baseline,
                "high_entry_volume": row.get("entry_volume") is not None and row["entry_volume"] >= volume_threshold,
                "training_count": len(train),
            })
    return predictions


def _mae(actual: list[float], predicted: list[float]) -> float:
    return statistics.fmean(abs(left - right) for left, right in zip(actual, predicted))


def _rmse(actual: list[float], predicted: list[float]) -> float:
    return math.sqrt(statistics.fmean((left - right) ** 2 for left, right in zip(actual, predicted)))


def _percentile_scores(values: list[float]) -> list[int]:
    if len(values) <= 1:
        return [50] * len(values)
    ranks = _ranks(values)
    return [round((rank - 1) / (len(values) - 1) * 100) for rank in ranks]


def evaluate_predictions(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    actual = [row["actual"] for row in predictions]
    model_values = [row["predicted"] for row in predictions]
    baseline_values = [row["baseline_predicted"] for row in predictions]
    scores = _percentile_scores(model_values)
    evaluated = [{**row, "score": score} for row, score in zip(predictions, scores)]
    bins = {}
    for low, high in ((0, 19), (20, 39), (40, 59), (60, 79), (80, 100)):
        values = [row["actual"] for row in evaluated if low <= row["score"] <= high]
        bins[f"{low}-{high}"] = {
            "count": len(values),
            "mean_actual": round(statistics.fmean(values), 6) if values else None,
            "positive_rate": round(sum(value > 0 for value in values) / len(values), 4) if values else None,
        }
    high_volume = [row for row in evaluated if row["high_entry_volume"]]
    top = [row for row in evaluated if row["score"] >= 80]
    metrics = {
        "count": len(evaluated),
        "model_mae": round(_mae(actual, model_values), 6),
        "baseline_mae": round(_mae(actual, baseline_values), 6),
        "model_rmse": round(_rmse(actual, model_values), 6),
        "baseline_rmse": round(_rmse(actual, baseline_values), 6),
        "pearson": _pearson(model_values, actual),
        "spearman": _pearson(_ranks(model_values), _ranks(actual)),
        "top_score_count": len(top),
        "top_score_mean_actual": round(statistics.fmean(row["actual"] for row in top), 6) if top else None,
        "top_score_positive_rate": round(sum(row["actual"] > 0 for row in top) / len(top), 4) if top else None,
        "high_volume_count": len(high_volume),
        "high_volume_mean_actual": round(statistics.fmean(row["actual"] for row in high_volume), 6) if high_volume else None,
        "score_bins": bins,
    }
    metrics["accepted"] = bool(
        metrics["count"] >= 40
        and metrics["model_mae"] < metrics["baseline_mae"]
        and metrics["pearson"] is not None and metrics["pearson"] > 0.1
        and metrics["top_score_count"] >= 8
        and metrics["top_score_mean_actual"] > 0
    )
    return {"metrics": metrics, "predictions": evaluated}


def analyze_model(rows: list[dict[str, Any]]) -> dict[str, Any]:
    targeted = attach_target(rows)
    predictions = walk_forward_predictions(targeted)
    evaluation = evaluate_predictions(predictions)
    final_model = fit_ridge(targeted)
    reference_predictions = sorted(predict(final_model, row) for row in targeted)
    return {
        "analysis_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target_policy": TARGET_POLICY,
        "features": list(FEATURES),
        "sample_count": len(targeted),
        "walk_forward_count": len(predictions),
        "acceptance_rule": "OOS 40건 이상, MAE가 과거 평균보다 작음, Pearson>0.1, 상위점수 8건 이상·실현평균>0",
        "evaluation": evaluation["metrics"],
        "decision": "shadow_candidate" if evaluation["metrics"]["accepted"] else "model_rejected",
        "final_research_model": {
            **final_model,
            "prediction_reference": reference_predictions,
            "score_definition": "예측 순수익률의 학습 표본 내 백분위 0~100",
        },
        "intraday_data_status": {
            "available_for_history": False,
            "impact": "TP/SL 동시 도달 순서를 일봉으로 확정할 수 없어 target label 오차가 큼",
            "required_future_fields": ["date", "ticker", "timestamp", "open", "high", "low", "close", "volume"],
        },
        "langgraph_feature_contract": {
            "status": "deferred",
            "reason": "기존 근거에 생성시각·출처시각이 없어 point-in-time 학습 안전성을 보장할 수 없음",
            "required_fields": ["as_of", "source_published_at", "source_type", "event_type", "evidence_strength", "source_url"],
        },
        "predictions": evaluation["predictions"],
    }


def render_markdown(result: dict[str, Any]) -> str:
    metrics = result["evaluation"]
    lines = [
        "# Watchlist 기대수익 연구 — 5단계 Shadow 모델 검증",
        "",
        f"- 전체 표본: {result['sample_count']}건",
        f"- Walk-forward 검증 표본: {result['walk_forward_count']}건",
        f"- 모델 MAE / 기준 MAE: {metrics['model_mae']} / {metrics['baseline_mae']}",
        f"- 모델 RMSE / 기준 RMSE: {metrics['model_rmse']} / {metrics['baseline_rmse']}",
        f"- 예측-실현 Pearson / Spearman: {metrics['pearson']} / {metrics['spearman']}",
        f"- 상위 점수군 실현 평균: {metrics['top_score_mean_actual']}",
        f"- 고거래량군 실현 평균: {metrics['high_volume_mean_actual']}",
        f"- 판정: {result['decision']}",
        "",
        "## 점수 구간별 실현 순수익",
        "",
        "| 점수 | 표본 | 평균 | 양수 비율 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for band, values in metrics["score_bins"].items():
        lines.append(f"| {band} | {values['count']} | {values['mean_actual']} | {values['positive_rate']} |")
    lines.extend([
        "", "## 제한", "",
        f"- 분봉 과거 데이터 사용 가능: {result['intraday_data_status']['available_for_history']}",
        f"- {result['intraday_data_status']['impact']}",
        f"- LangGraph 텍스트 특징: {result['langgraph_feature_contract']['status']} — {result['langgraph_feature_contract']['reason']}",
    ])
    return "\n".join(lines) + "\n"


def write_results(result: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "phase5_expected_return_model.json"
    md_path = output_dir / "phase5_expected_return_model.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="기대수익률 shadow 모델 시간순 검증")
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    result = analyze_model(load_price_paths(args.watchlist_db, args.krx_db))
    for path in write_results(result, args.output_dir):
        print(f"[phase5] {path}")


if __name__ == "__main__":
    main()

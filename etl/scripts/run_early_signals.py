"""급등 전 변화 포착 실행기 — 일자별(cutoff) 재현 가능 (PLAN §12, §20).

Usage (etl/ 에서):
    uv run python scripts/run_early_signals.py --cutoff 2026-07-01 --stage capture
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401

import argparse
import json
import time
import uuid
from datetime import datetime, timezone

from early_signals import sources, storage
from early_signals.identity import IDENTITY_VERSION

POLICY_VERSION = "policy_v2"      # §12.2 처리 한도 상향 (§21.1 조정 가능 항목)
CODE_VERSION = "stage1"


def parse_cutoff(value: str) -> str:
    """--cutoff 는 KST 날짜 또는 시각. 저장·비교는 UTC ISO 로 통일한다(§4.1)."""
    text = value.strip()
    if len(text) == 10:
        moment = datetime.strptime(text, "%Y-%m-%d").replace(
            hour=15, minute=40, second=0, tzinfo=sources.KST)
    else:
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=sources.KST)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def latest_capture_run(db, cutoff_at: str, need_events: bool = False) -> str | None:
    """manifest 가 동결된 run 중 최신 것. 이미 assess 한 run 도 이어서 쓸 수 있다.

    run_status 로만 고르면 assess 뒤 재실행이 "capture run 이 없다" 로 막힌다.
    """
    having = " HAVING count(e.event_id) > 0" if need_events else ""
    with storage.connect_ro(db) as con:
        row = con.execute(
            "SELECT r.run_id FROM runs r JOIN manifest_entries m ON m.manifest_id = r.run_id"
            " LEFT JOIN events e ON e.source_version_id = m.source_version_id"
            f" WHERE r.cutoff_at=? AND r.run_status <> 'failed'"
            f" GROUP BY r.run_id, r.started_at{having}"
            " ORDER BY r.started_at DESC LIMIT 1", (cutoff_at,)).fetchone()
    return row[0] if row else None


def run_extract(args) -> int:
    """§12.2 예산 내에서 1차 추출. cutoff 직전 원문부터 본다.

    운영 FIFO(오래된 순)와 달리 여기서는 기준일 시점의 새 변화를 보기 위해 최신순으로
    표본을 뽑는다. 첫 실행은 90일치가 전부 미처리라 FIFO 로는 기준일과 무관한 구간만
    처리된다. 이 선택은 결과에 sample_order 로 남긴다.
    """
    import duckdb

    from early_signals import analysis, policy

    with duckdb.connect(str(policy.KRX_DB), read_only=True) as krx:
        entity_master = {str(c): n for c, n in
                         krx.execute("SELECT code, name FROM stock_names").fetchall()}
    cutoff_at = parse_cutoff(args.cutoff)
    run_id = args.run_id or latest_capture_run(args.db, cutoff_at)
    if not run_id:
        print("[early_signals] capture run 이 없다 — --stage capture 를 먼저 실행")
        return 1

    with storage.connect_ro(args.db) as con:
        ids = storage.load_manifest(con, run_id)
        records = storage.load_sources(con, ids)
    records.sort(key=lambda r: (r["published_at"] or "", r["source_version_id"]), reverse=True)
    batch = records[: args.limit]

    started = time.monotonic()
    totals = {"processed": 0, "with_events": 0, "events": 0, "rejected": 0,
              "llm_calls": 0, "errors": 0, "cached": 0, "unresolved_entities": 0}
    for index, record in enumerate(batch, start=1):
        input_hash = record["source_version_id"]
        with storage.connect_ro(args.db) as con:
            cached = storage.load_processing(
                con, stage="extract_changes", input_hash=input_hash,
                policy_version=storage.EXTRACT_CACHE_POLICY,
                prompt_version=analysis.PROMPT_VERSION,
                model_identity=args.model or "codex_default", code_version=CODE_VERSION)
        if cached is not None:
            totals["cached"] += 1
            events = cached.get("events", [])
        else:
            units = sources.snapshot_document(record)
            result = analysis.extract_changes(record, units, model=args.model,
                                              entity_master=entity_master)
            events = result["events"]
            totals["llm_calls"] += result["llm_calls"]
            totals["rejected"] += result["rejected"]
            totals["unresolved_entities"] += result.get("unresolved_entities", 0)
            totals["errors"] += len(result["errors"])
            with storage.connect_rw(args.db) as con:
                storage.record_processing(
                    con, stage="extract_changes", input_hash=input_hash,
                    policy_version=storage.EXTRACT_CACHE_POLICY,
                    prompt_version=analysis.PROMPT_VERSION,
                    model_identity=args.model or "codex_default", code_version=CODE_VERSION,
                    status="done", output={"events": events},
                    elapsed_sec=result["elapsed_sec"], char_count=result["chars"])
        if events:
            for event in events:
                event.setdefault("origin_group_id", record["origin_group_id"])
                event.setdefault("independence", record["independence"])
            with storage.connect_rw(args.db) as con:
                storage.persist_events(con, events)
            totals["with_events"] += 1
            totals["events"] += len(events)
        totals["processed"] += 1
        if index % 25 == 0:
            print(f"[extract] {index}/{len(batch)} events={totals['events']} "
                  f"{time.monotonic() - started:.0f}s", flush=True)

    elapsed = time.monotonic() - started
    per_source = elapsed / max(totals["processed"], 1)
    backlog = len(records) - len(batch)
    throughput = {
        **totals, "elapsed_sec": round(elapsed, 1), "sec_per_source": round(per_source, 2),
        "manifest_sources": len(records), "sample_order": "published_at DESC",
        "backlog": backlog,
        "runs_to_clear_backlog": (backlog + args.limit - 1) // args.limit if args.limit else None,
        "hours_to_clear_backlog": round(backlog * per_source / 3600, 1),
    }
    with storage.connect_rw(args.db) as con:
        storage.finish_run(con, run_id, status="extracted", throughput=throughput)
    print(json.dumps({"run_id": run_id, "throughput": throughput}, ensure_ascii=False, indent=2))
    return 0


def run_assess(args) -> int:
    """저장된 이벤트로 종목 평가·선정·보고서를 만든다. LLM 을 다시 부르지 않는다(§14)."""
    from early_signals import policy, report

    cutoff_at = parse_cutoff(args.cutoff)
    run_id = args.run_id or latest_capture_run(args.db, cutoff_at, need_events=True)
    if not run_id:
        print("[early_signals] extract 된 run 이 없다 — --stage extract 를 먼저 실행")
        return 1
    cutoff_date = args.cutoff

    with storage.connect_ro(args.db) as con:
        by_entity = report.load_events_by_entity(con, run_id)
        row = con.execute("SELECT throughput_json, mode FROM runs WHERE run_id=?",
                          (run_id,)).fetchone()
    throughput = json.loads(row[0] or "{}")
    assessments, feasibility = report.assess_entities(by_entity, cutoff_date)
    selected = policy.select_candidates(assessments)

    payload = {
        "run_id": run_id, "mode": row[1], "cutoff_date": cutoff_date,
        "price_as_of": next((a["price"]["price_as_of"] for a in assessments
                             if a["price"].get("price_as_of")), None),
        "manifest_sources": throughput.get("manifest_sources", 0),
        "processed": throughput.get("processed", 0),
        "backlog": throughput.get("backlog", 0),
        "events": throughput.get("events", 0),
        "with_events": throughput.get("with_events", 0),
        "rejected": throughput.get("rejected", 0),
        "result_status": "partial" if throughput.get("backlog") else (
            "no_candidates" if not selected else "candidates"),
        "assessments": assessments, "feasibility": feasibility, "selected": selected,
    }
    path = report.render_artifact(payload)
    with storage.connect_rw(args.db) as con:
        for item in assessments:
            episode = storage.find_open_episode(con, item["subject_id"], cutoff_at)
            item["episode_id"] = episode[0] if episode else f"{item['subject_id']}-{args.cutoff}"
            storage.persist_assessment(con, run_id, item)
        storage.finish_run(con, run_id, status="assessed", artifact_path=str(path),
                           result={"entities": feasibility["entities"],
                                   "selected": [s["subject_id"] for s in selected],
                                   "actions": feasibility["actions"]})
    print(json.dumps({"run_id": run_id, "artifact": str(path),
                      "feasibility": feasibility,
                      "selected": [s["subject_id"] for s in selected]},
                     ensure_ascii=False, indent=2))
    return 0


def run_evaluate(args) -> int:
    """저장된 판단의 28/56/84일 성과를 잰다(§17.2). LLM 을 부르지 않는다.

    live 기록과 historical_exploration 통계를 섞지 않는다 — run 의 모드를 결과에 남긴다.
    """
    import duckdb

    from early_signals import evaluation, policy

    cutoff_at = parse_cutoff(args.cutoff)
    run_id = args.run_id or latest_capture_run(args.db, cutoff_at, need_events=True)
    if not run_id:
        print("[early_signals] 평가할 run 이 없다")
        return 1
    with storage.connect_ro(args.db) as con:
        mode = con.execute("SELECT mode FROM runs WHERE run_id=?", (run_id,)).fetchone()[0]
        stored = storage.load_assessments(con, run_id)
    if not stored:
        print("[early_signals] 저장된 판단이 없다 — --stage assess 를 먼저 실행")
        return 1

    duck = duckdb.connect(str(policy.KRX_DB), read_only=True)
    try:
        sessions = policy.market_calendar(
            duck.execute("SELECT max(date) FROM ohlcv").fetchone()[0], duck)["sessions"]
        entry = evaluation.entry_date(args.cutoff.replace("-", ""), sessions)
        records = []
        for item in stored:
            daily = policy.load_daily(item["subject_id"], sessions, duck)
            outcome = evaluation.evaluate_outcomes(entry, sessions, daily) if entry else {
                "status": "no_entry_session"}
            bucket = evaluation.matched_benchmark_bucket(
                (item.get("price") or {}).get("market_cap"))
            for horizon, result in (outcome.get("horizons") or {}).items():
                if result.get("status") != "done":
                    continue
                peer = evaluation.build_matched_benchmark(
                    entry, result["exit_date"], bucket, sessions, duck,
                    exclude={i["subject_id"] for i in stored})
                result["peer"] = peer
                result["excess"] = (round(result["return"] - peer["return"], 6)
                                    if peer.get("return") is not None else None)
            records.append({**item, "entry_date": entry, "bucket": bucket, "outcome": outcome})
    finally:
        duck.close()

    unique = evaluation.dedupe_episodes(records)
    summary = {
        "run_id": run_id, "mode": mode, "cutoff": args.cutoff, "entry_date": entry,
        "assessments": len(stored), "independent_episodes": len(unique),
        "live_statistics": mode == "live",
        "by_action": {},
    }
    for action in sorted({r["action"] for r in unique}):
        subset = [r for r in unique if r["action"] == action]
        summary["by_action"][action] = {
            str(h): evaluation.summarize_outcomes(subset, h, action)
            for h in evaluation.HORIZONS}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def run_capacity(args) -> int:
    """§12.3 처리량 게이트. LLM 을 부르지 않고 실측치로만 판정한다."""
    from early_signals import capacity

    cutoff_at = parse_cutoff(args.cutoff)
    run_id = args.run_id or latest_capture_run(args.db, cutoff_at)
    with storage.connect_ro(args.db) as con:
        backlog = capacity.load_backlog(con, run_id) if run_id else 0
        result = capacity.assess_feasibility(con, args.cutoff, backlog,
                                             runs_per_day=args.runs_per_day)
    print(json.dumps({"run_id": run_id, **result}, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="급등 전 변화 포착")
    parser.add_argument("--cutoff", required=True, help="KST 기준일. 예 2026-07-01")
    parser.add_argument("--mode", default="historical_exploration",
                        choices=("live", "replay_observed", "historical_exploration"))
    parser.add_argument("--stage", default="capture",
                        choices=("capture", "extract", "assess", "evaluate", "capacity"))
    parser.add_argument("--run-id", default=None, help="extract 단계에서 이어 붙일 capture run")
    parser.add_argument("--limit", type=int, default=300, help="1차 추출 예산(§12.2)")
    parser.add_argument("--model", default=None, help="생략하면 codex 기본 모델")
    parser.add_argument("--runs-per-day", type=int, default=1, help="capacity 단계 가정")
    parser.add_argument("--since-days", type=int, default=90)
    parser.add_argument("--exclude-channel", nargs="*", default=[])
    parser.add_argument("--db", type=pathlib.Path, default=storage.DEFAULT_DB)
    args = parser.parse_args(argv)

    if args.stage == "extract":
        return run_extract(args)
    if args.stage == "assess":
        return run_assess(args)
    if args.stage == "evaluate":
        return run_evaluate(args)
    if args.stage == "capacity":
        return run_capacity(args)

    cutoff_at = parse_cutoff(args.cutoff)
    observed_at = storage.utc_now()
    run_id = f"{args.mode}-{args.cutoff}-{uuid.uuid4().hex[:8]}"
    storage.ensure_schema(args.db)

    with storage.connect_rw(args.db) as con:
        storage.start_run(con, run_id=run_id, mode=args.mode, cutoff_at=cutoff_at,
                          policy_version=POLICY_VERSION, identity_version=IDENTITY_VERSION,
                          code_version=CODE_VERSION)
        if not storage.acquire_run_lease(con, run_id):
            print("[early_signals] 다른 실행이 lease 를 쥐고 있다 — 중단")
            return 1

    telegram = sources.capture_telegram(
        cutoff_at, since_days=args.since_days, observed_at=observed_at,
        channels_excluded=args.exclude_channel)
    reports, report_stats = sources.capture_reports(
        cutoff_at, since_days=args.since_days, observed_at=observed_at)
    records = telegram + reports

    with storage.connect_rw(args.db) as con:
        ids = [storage.persist_source_version(con, record) for record in records]
        manifest_hash = storage.freeze_manifest(con, run_id, ids)
        coverage = sources.check_coverage(records, cutoff_at, report_stats, args.mode)
        inventory = sources.inventory_sources(records)
        storage.finish_run(con, run_id, status="captured", coverage=coverage,
                           throughput={"inventory": inventory},
                           manifest_id=run_id, manifest_hash=manifest_hash,
                           result={"captured": len(ids)})

    print(json.dumps({"run_id": run_id, "cutoff_at": cutoff_at,
                      "coverage": coverage, "inventory": inventory},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""전략 예산 조회·리셋 도구 테스트 (unittest, 네트워크 없음)."""
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts.strategy_budget import (
    blocked_now,
    parse_assignments,
    pick_since,
    status_lines,
    write_config,
)
from scripts import strategy_budget as sb

KST = ZoneInfo("Asia/Seoul")


def _at(hour: int, minute: int, day: str = "20261014") -> datetime:
    base = datetime.strptime(day, "%Y%m%d").replace(tzinfo=KST)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ParseAssignmentsTest(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(
            parse_assignments(["rebound=6000000", "envelope=1000000"]),
            {"rebound": 6000000, "envelope": 1000000},
        )

    def test_unknown_strategy(self):
        with self.assertRaises(ValueError):
            parse_assignments(["foo=1"])

    def test_negative(self):
        with self.assertRaises(ValueError):
            parse_assignments(["rebound=-1"])

    def test_non_integer(self):
        with self.assertRaises(ValueError):
            parse_assignments(["rebound=abc"])


class BlockedNowTest(unittest.TestCase):
    def test_trading_day_morning_session(self):
        self.assertTrue(blocked_now(_at(10, 0), True))

    def test_trading_day_before_open(self):
        self.assertFalse(blocked_now(_at(8, 19), True))

    def test_trading_day_at_close_boundary(self):
        self.assertFalse(blocked_now(_at(15, 35), True))

    def test_holiday(self):
        self.assertFalse(blocked_now(_at(10, 0), False))


class PickSinceTest(unittest.TestCase):
    def test_after_hours_skips_holiday(self):
        # 거래일 20:00, 다음날 휴일·그다음 거래일 → 그다음 날
        trading = {"20261016"}
        since = pick_since(_at(20, 0, "20261014"), trading.__contains__)
        self.assertEqual(since, "20261016")

    def test_before_open_on_trading_day(self):
        since = pick_since(_at(7, 0, "20261014"), lambda day: day == "20261014")
        self.assertEqual(since, "20261014")


class WriteConfigTest(unittest.TestCase):
    def test_preserves_other_keys_and_returns_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            _write_json(cfg, {
                "capital": 5000000, "capital_since": "20260101",
                "broker": {"url": "http://127.0.0.1:8001"}, "extra": {"a": {"amount": 3}},
            })
            old = write_config(cfg, 6000000, "20261015")
            self.assertEqual(old, 5000000)
            data = json.loads(cfg.read_text(encoding="utf-8"))
            self.assertEqual(data["capital"], 6000000)
            self.assertEqual(data["capital_since"], "20261015")
            self.assertEqual(data["broker"], {"url": "http://127.0.0.1:8001"})
            self.assertEqual(data["extra"], {"a": {"amount": 3}})
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["config.json"])


class ResetFlowTest(unittest.TestCase):
    def _strategies(self, tmp: str):
        paths = {}
        for name in ("rebound", "envelope", "rights_dip", "jevq"):
            cfg = Path(tmp) / name / "config.json"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            _write_json(cfg, {"capital": 1000000, "capital_since": "20260101", "keep": name})
            paths[name] = ("http://127.0.0.1:8001", str(cfg))
        return paths

    def test_reset_updates_configs_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            strategies = self._strategies(tmp)
            before_untouched = {
                name: Path(strategies[name][1]).read_bytes()
                for name in ("rights_dip", "jevq")
            }
            db = str(Path(tmp) / "watchlist.sqlite3")
            with (
                patch.object(sb, "STRATEGIES", strategies),
                patch.object(sb, "DB", Path(db)),
                patch.object(sb, "now_seoul", return_value=_at(20, 0, "20261014")),
                patch.object(sb, "is_krx_trading_day", return_value=True),
                patch.object(sb, "fetch_total_assets", return_value=10 ** 12),
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = sb.main(["reset", "rebound=6000000", "envelope=1000000",
                                    "--note", "테스트"])
                self.assertEqual(code, 0)
                self.assertIn("예약은 각 전략 다음 배치에서 갱신", buf.getvalue())
            for name, want in (("rebound", 6000000), ("envelope", 1000000)):
                data = json.loads(Path(strategies[name][1]).read_text(encoding="utf-8"))
                self.assertEqual(data["capital"], want)
                self.assertEqual(data["capital_since"], "20261015")  # 다음 거래일
                self.assertEqual(data["keep"], name)
            for name, raw in before_untouched.items():
                self.assertEqual(Path(strategies[name][1]).read_bytes(), raw)
            con = sqlite3.connect(db)
            try:
                rows = con.execute(
                    "SELECT strategy, old_capital, new_capital, since, note "
                    "FROM strategy_budget_resets",
                ).fetchall()
            finally:
                con.close()
            self.assertEqual(len(rows), 2)
            by_strategy = {row[0]: row for row in rows}
            self.assertEqual(by_strategy["rebound"][1:], (1000000, 6000000, "20261015", "테스트"))
            self.assertEqual(by_strategy["envelope"][1:], (1000000, 1000000, "20261015", "테스트"))

    def test_reset_blocked_intraday_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            strategies = self._strategies(tmp)
            before = {n: Path(p[1]).read_bytes() for n, p in strategies.items()}
            db = str(Path(tmp) / "watchlist.sqlite3")
            with (
                patch.object(sb, "STRATEGIES", strategies),
                patch.object(sb, "DB", Path(db)),
                patch.object(sb, "now_seoul", return_value=_at(10, 0, "20261014")),
                patch.object(sb, "is_krx_trading_day", return_value=True),
                patch.object(sb, "fetch_total_assets", return_value=10 ** 12),
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = sb.main(["reset", "rebound=6000000"])
                self.assertEqual(code, 2)
                self.assertIn("장중 리셋 금지", buf.getvalue())
            for name, raw in before.items():
                self.assertEqual(Path(strategies[name][1]).read_bytes(), raw)
            self.assertFalse(Path(db).exists())


class StatusWarningTest(unittest.TestCase):
    def test_seed_sum_over_assets_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            strategies = {}
            for name, capital in (("rebound", 6000000), ("envelope", 1000000)):
                cfg = Path(tmp) / f"{name}.json"
                _write_json(cfg, {"capital": capital, "capital_since": "20261015"})
                strategies[name] = ("http://127.0.0.1:8001", str(cfg))
            lines = status_lines(
                strategies=strategies,
                balance_fn=lambda url: 1000,
                db_path=str(Path(tmp) / "watchlist.sqlite3"),
            )
            self.assertTrue(any("rebound" in line for line in lines))
            self.assertTrue(any("예약 없음" in line for line in lines))
            self.assertIn("⚠️ 전략 시드 합이 총자산 초과", lines)


if __name__ == "__main__":
    unittest.main()

"""market_session 단위 테스트.

실제 market_sessions.json은 CLI 성공 케이스에서만 읽는다. 나머지는
sessions dict·임시 json 파일을 주입한다.
"""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from scripts.market_session import (
    at, csat_missing, load, main, session, shift, wait_until,
)

KST = ZoneInfo("Asia/Seoul")
SAT = {"20261119": ("10:00", "16:30")}  # 수능일 시간표


def _write(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


class NormalDayTest(unittest.TestCase):
    def test_flat(self):
        self.assertEqual(session("20261110", {}), ("09:00", "15:30"))
        self.assertEqual(shift("20261110", "15:19:20", sessions={}), 0)
        self.assertEqual(at("20261110", "15:19:20", sessions={}),
                         datetime(2026, 11, 10, 15, 19, 20, tzinfo=KST))


class SatTest(unittest.TestCase):
    def test_morning_plus_1h(self):
        self.assertEqual(at("20261119", "09:00:20", sessions=SAT),
                         datetime(2026, 11, 19, 10, 0, 20, tzinfo=KST))

    def test_afternoon_plus_1h(self):
        self.assertEqual(at("20261119", "15:19:20", sessions=SAT),
                         datetime(2026, 11, 19, 16, 19, 20, tzinfo=KST))


class NewYearAutoTest(unittest.TestCase):
    """2027-01-01 금(공휴일) → 01-04 월이 첫 거래일. sessions={}로 자동 판정."""

    def test_morning_plus_1h(self):
        self.assertEqual(session("20270104", {}), ("10:00", "15:30"))
        self.assertEqual(at("20270104", "09:00:20", sessions={}),
                         datetime(2027, 1, 4, 10, 0, 20, tzinfo=KST))

    def test_afternoon_flat(self):
        self.assertEqual(at("20270104", "15:19:20", sessions={}),
                         datetime(2027, 1, 4, 15, 19, 20, tzinfo=KST))

    def test_next_day_flat(self):
        self.assertEqual(shift("20270105", "09:00:20", sessions={}), 0)
        self.assertEqual(shift("20270105", "15:19:20", sessions={}), 0)


class FileOverridesNewYearTest(unittest.TestCase):
    def test_explicit_normal_hours(self):
        sessions = {"20270104": ("09:00", "15:30")}
        self.assertEqual(shift("20270104", "09:00:20", sessions=sessions), 0)
        self.assertEqual(shift("20270104", "15:19:20", sessions=sessions), 0)


class AnchorTest(unittest.TestCase):
    def test_open_anchor_past_noon(self):
        self.assertEqual(at("20270104", "13:00", anchor="open", sessions={}),
                         datetime(2027, 1, 4, 14, 0, 0, tzinfo=KST))

    def test_close_anchor_before_noon(self):
        self.assertEqual(shift("20270104", "09:30", anchor="close", sessions={}), 0)


class BadAnchorTest(unittest.TestCase):
    def test_shift_rejects(self):
        with self.assertRaises(ValueError):
            shift("20261110", "09:00", anchor="x", sessions={})

    def test_at_rejects(self):
        with self.assertRaises(ValueError):
            at("20261110", "09:00", anchor="x", sessions={})


class LoadFileTest(unittest.TestCase):
    def test_valid(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(Path(d) / "ok.json",
                       {"20261119": {"open": "10:00", "close": "16:30",
                                     "why": "2027학년도 수능"}})
            self.assertEqual(load(p), {"20261119": ("10:00", "16:30")})

    def test_rejects(self):
        cases = {
            "open_eq_close": {"20261119": {"open": "10:00", "close": "10:00"}},
            "open_gt_close": {"20261119": {"open": "16:30", "close": "10:00"}},
            "hour_25": {"20261119": {"open": "25:00", "close": "16:30"}},
            "key_7digit": {"2026119": {"open": "10:00", "close": "16:30"}},
        }
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            for name, payload in cases.items():
                with self.subTest(name):
                    _write(p, payload)
                    with self.assertRaises(ValueError):
                        load(p)

    def test_missing_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load(Path(d) / "nope.json"), {})


class WaitUntilTest(unittest.TestCase):
    def test_sleeps_remaining_once(self):
        target = at("20261119", "15:19:20", sessions=SAT)
        sleep = mock.Mock()
        wait_until("20261119", "15:19:20", sessions=SAT,
                   now_fn=lambda: target - timedelta(seconds=10),
                   sleep_fn=sleep)
        sleep.assert_called_once_with(10)

    def test_no_sleep_when_past(self):
        target = at("20261119", "15:19:20", sessions=SAT)
        sleep = mock.Mock()
        wait_until("20261119", "15:19:20", sessions=SAT,
                   now_fn=lambda: target + timedelta(seconds=1),
                   sleep_fn=sleep)
        sleep.assert_not_called()


class CliTest(unittest.TestCase):
    def test_sat_shift(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main(["--shift", "15:10", "--date", "20261119"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().strip(), "3600")

    def test_bad_file_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            bad = _write(Path(d) / "bad.json",
                         {"20261119": {"open": "16:30", "close": "10:00"}})
            with mock.patch("scripts.market_session.SESSIONS_FILE", bad):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    rc = main(["--shift", "15:10", "--date", "20261119"])
        self.assertEqual(rc, 2)
        self.assertIn("시간표", err.getvalue())


class CsatMissingTest(unittest.TestCase):
    def test_october_empty_is_missing(self):
        self.assertTrue(csat_missing("20261001", {}))

    def test_september_empty_is_not_missing(self):
        self.assertFalse(csat_missing("20260930", {}))

    def test_csat_line_present(self):
        self.assertFalse(csat_missing("20261001", SAT))

    def test_last_year_line_does_not_count(self):
        self.assertTrue(csat_missing("20271001", SAT))

    def test_new_year_line_is_not_csat(self):
        self.assertTrue(csat_missing("20271001", {"20270104": ("10:00", "15:30")}))


if __name__ == "__main__":
    unittest.main()

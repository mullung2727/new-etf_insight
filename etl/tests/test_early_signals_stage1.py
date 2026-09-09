"""1단계 수용 테스트 — 원문 버전·시점·manifest·처리 상태 (PLAN §20-1).

T03 cutoff 이후 발행/관측 거부 · T04 과거 게시일을 관측 시각으로 복사하지 않음
T05 같은 PDF 다채널 → 독립 근거 1개 · T06 같은 발표 인용 → 독립 2건 아님
T19 읽기 실패를 coverage_incomplete 로 · T23 lease 단일 실행
T33 동결 이후 확보분은 현재 manifest 밖 · T39 query_only·FK·rollback
"""
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _bootstrap  # noqa: F401,E402

from early_signals import sources, storage  # noqa: E402
from early_signals.identity import (  # noqa: E402
    change_key, content_hash, locator_hash, normalize_event_identity,
    normalize_origin_group, split_units,
)

KST = timezone(timedelta(hours=9))
CUTOFF = "2026-07-01T06:40:00+00:00"      # 2026-07-01 15:40 KST


def make_telegram_db(path: Path, rows: list[tuple]) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE telegram_posts (channel TEXT, post_id INTEGER,"
                " date_kst TEXT, text TEXT, raw_json TEXT)")
    con.executemany("INSERT INTO telegram_posts (channel, post_id, date_kst, text)"
                    " VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()
    return path


class CutoffTest(unittest.TestCase):
    def test_t03_rejects_sources_published_after_cutoff(self):
        with TemporaryDirectory() as tmp:
            db = make_telegram_db(Path(tmp) / "t.sqlite3", [
                ("getfeed", 1, "2026-06-30 10:00:00", "이전 글"),
                ("getfeed", 2, "2026-07-01 20:00:00", "cutoff 이후 글"),
            ])
            records = sources.capture_telegram(
                CUTOFF, observed_at="2026-07-01T06:40:00+00:00", db_path=db)
        self.assertEqual([r["raw"]["post_id"] for r in records], [1])

    def test_t04_past_post_keeps_its_own_published_at(self):
        """과거 게시일을 first_observed_at 으로 복사하지 않는다."""
        with TemporaryDirectory() as tmp:
            db = make_telegram_db(Path(tmp) / "t.sqlite3",
                                  [("getfeed", 1, "2026-05-02 09:00:00", "과거 글")])
            observed = "2026-07-01T06:40:00+00:00"
            record = sources.capture_telegram(CUTOFF, observed_at=observed, db_path=db)[0]
        self.assertEqual(record["published_at"], "2026-05-02T00:00:00+00:00")
        self.assertEqual(record["first_observed_at"], observed)
        self.assertEqual(record["available_at"], observed)   # 늦은 쪽 = 관측 시각

    def test_date_only_report_uses_end_of_day_kst(self):
        published = sources._date_to_utc_end_of_day("2026-06-30")
        self.assertEqual(published, "2026-06-30T14:59:59+00:00")


class IndependenceTest(unittest.TestCase):
    def test_t05_same_pdf_is_one_origin_group(self):
        """같은 PDF 를 여러 채널이 전달해도 독립 근거는 1개다."""
        groups = {
            normalize_origin_group(pdf_bytes_hash="abc", source_version_id=f"v{i}")[0]
            for i in range(5)
        }
        self.assertEqual(len(groups), 1)

    def test_t05_mobile_and_desktop_ids_merge_by_pdf_hash(self):
        mobile = normalize_origin_group(pdf_bytes_hash="same", url="https://m.example/1")
        desktop = normalize_origin_group(pdf_bytes_hash="same", url="https://example/nid=9")
        self.assertEqual(mobile[0], desktop[0])

    def test_t06_same_announcement_two_brokers_are_not_auto_merged(self):
        """다른 증권사 두 리포트는 자동 병합하지 않고 각자 그룹을 갖는다."""
        a = normalize_origin_group(publisher_id="B001", published_date="2026-06-30",
                                   entity_ids=["005930"], title="2분기 가이던스 상향")
        b = normalize_origin_group(publisher_id="B002", published_date="2026-06-30",
                                   entity_ids=["005930"], title="2분기 가이던스 상향")
        self.assertNotEqual(a[0], b[0])
        self.assertEqual((a[1], b[1]), ("known", "known"))

    def test_unresolvable_origin_is_unknown(self):
        group, independence = normalize_origin_group(source_version_id="v1")
        self.assertEqual(independence, "unknown")
        self.assertTrue(group)

    def test_url_normalization_drops_tracking_params(self):
        a = normalize_origin_group(url="https://Example.com/a/?b=2&utm_source=x")[0]
        b = normalize_origin_group(url="https://example.com/a?b=2")[0]
        self.assertEqual(a, b)


class IdentityTest(unittest.TestCase):
    def test_units_split_by_sentence_with_coordinates(self):
        units = split_units("첫 문장이다. 둘째 문장이다.", page=1)
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0]["start_char"], 0)
        self.assertTrue(units[1]["start_char"] > units[0]["end_char"] - 1)

    def test_same_unit_two_changes_get_different_keys(self):
        """같은 문장의 계약 확대와 마진 하락은 change_key 로 갈린다."""
        contract = change_key([{"name": "계약금액", "period": "2026", "basis": "연결"}],
                              "upgraded", "positive")
        margin = change_key([{"name": "영업이익률", "period": "2026", "basis": "연결"}],
                            "downgraded", "negative")
        self.assertNotEqual(contract, margin)

    def test_metric_dictionary_normalizes_synonyms(self):
        a = change_key([{"name": "영업마진", "period": "2026", "basis": "연결"}], "x", "y")
        b = change_key([{"name": "영업이익률", "period": "2026", "basis": "연결"}], "x", "y")
        self.assertEqual(a, b)

    def test_fingerprint_ignores_claim_wording(self):
        unit = {"unit_id": "1:0", "page": 1, "start_char": 0, "end_char": 10}
        anchor = locator_hash("sv1", unit)
        key = change_key([{"name": "계약금액"}], "new", "positive")
        first = normalize_event_identity("sv1", ["005930"], [anchor], key, "ex1")
        second = normalize_event_identity("sv1", ["005930"], [anchor], key, "ex1")
        self.assertEqual(first["event_fingerprint"], second["event_fingerprint"])

    def test_event_without_anchor_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_event_identity("sv1", ["005930"], [], "k", "ex1")

    def test_content_hash_is_extracted_text_not_bytes(self):
        self.assertEqual(content_hash("본문"), content_hash("본문"))
        self.assertNotEqual(content_hash("본문"), content_hash("본문 "))


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = Path(self.tmp.name) / "es.sqlite3"
        storage.ensure_schema(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _record(self, key="a", text="본문"):
        digest = content_hash(text)
        return {
            "source_version_id": f"sv-{key}-{digest[:8]}", "source_type": "telegram",
            "source_key": key, "content_hash": digest, "origin_group_id": "g1",
            "independence": "known", "published_at": "2026-06-30T00:00:00+00:00",
            "published_precision": "date", "first_observed_at": CUTOFF,
            "available_at": CUTOFF, "extracted_text": text, "entity_ids": [],
        }

    def test_t39_readonly_connection_rejects_writes(self):
        with storage.connect_ro(self.db) as con:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("INSERT INTO runs (run_id, mode, cutoff_at, policy_version,"
                            " identity_version, run_status, started_at)"
                            " VALUES ('r','live','c','p','i','running','t')")

    def test_t39_foreign_keys_are_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with storage.connect_rw(self.db) as con:
                con.execute("INSERT INTO manifest_entries (manifest_id, source_version_id)"
                            " VALUES ('m','없는소스')")

    def test_t39_exception_rolls_back(self):
        with self.assertRaises(RuntimeError):
            with storage.connect_rw(self.db) as con:
                storage.persist_source_version(con, self._record())
                raise RuntimeError("중단")
        with storage.connect_ro(self.db) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM source_versions").fetchone()[0], 0)

    def test_same_content_is_stored_once_new_version_kept(self):
        with storage.connect_rw(self.db) as con:
            first = storage.persist_source_version(con, self._record(text="본문"))
            again = storage.persist_source_version(con, self._record(text="본문"))
            changed = storage.persist_source_version(con, self._record(text="본문 수정"))
        self.assertEqual(first, again)
        self.assertNotEqual(first, changed)
        with storage.connect_ro(self.db) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM source_versions").fetchone()[0], 2)

    def test_t33_manifest_freezes_inputs(self):
        """동결 이후 확보한 원문은 현재 manifest 에 들어가지 않는다."""
        with storage.connect_rw(self.db) as con:
            first = storage.persist_source_version(con, self._record(key="a"))
            storage.freeze_manifest(con, "run1", [first])
            later = storage.persist_source_version(con, self._record(key="b"))
        with storage.connect_ro(self.db) as con:
            frozen = storage.load_manifest(con, "run1")
        self.assertEqual(frozen, [first])
        self.assertNotIn(later, frozen)

    def test_t23_lease_allows_single_holder(self):
        with storage.connect_rw(self.db) as con:
            storage.start_run(con, run_id="r1", mode="live", cutoff_at=CUTOFF,
                              policy_version="policy_v1", identity_version="identity_v1",
                              code_version="c")
            self.assertTrue(storage.acquire_run_lease(con, "r1", owner="1@A"))
            self.assertFalse(storage.acquire_run_lease(con, "r1", owner="2@A"))

    def test_t23_stale_lease_is_taken_over(self):
        with storage.connect_rw(self.db) as con:
            storage.start_run(con, run_id="r1", mode="live", cutoff_at=CUTOFF,
                              policy_version="policy_v1", identity_version="identity_v1",
                              code_version="c")
            storage.acquire_run_lease(con, "r1", owner="999999@OTHERPC",
                                      now="2026-07-01T00:00:00+00:00")
            taken = storage.acquire_run_lease(con, "r1", owner="2@A",
                                              now="2026-07-01T01:00:00+00:00")
        self.assertFalse(taken)      # 다른 PC 는 생존 확인 불가 → 인수 거부(fail-closed)


class CoverageTest(unittest.TestCase):
    def test_t19_unreadable_pdf_marks_coverage_incomplete(self):
        stats = {"scanned": 3, "in_window": 3, "parsed": 2, "unreadable": 1, "after_cutoff": 0}
        coverage = sources.check_coverage([], CUTOFF, stats)
        self.assertTrue(coverage["coverage_incomplete"])

    def test_clean_scan_is_complete(self):
        stats = {"scanned": 2, "in_window": 2, "parsed": 2, "unreadable": 0, "after_cutoff": 0}
        self.assertFalse(sources.check_coverage([], CUTOFF, stats)["coverage_incomplete"])

    def test_report_capture_skips_after_cutoff_and_unreadable(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "삼성전자_005930"
            folder.mkdir(parents=True)
            # cutoff 당일(07-01) 리포트는 날짜만 있어 23:59:59 KST 로 보므로 cutoff(15:40) 이후다
            for date in ("2026-06-30", "2026-07-01"):
                (folder / f"{date}_미래에셋_{date.replace('-','')}_company_1.pdf").write_bytes(b"x")
            records, stats = sources.capture_reports(
                CUTOFF, observed_at=CUTOFF, report_dir=Path(tmp),
                extract_text=lambda p: ["본문이다."])
        self.assertEqual(stats["in_window"], 2)
        self.assertEqual(stats["after_cutoff"], 1)
        self.assertEqual([r["raw"]["date_kst"] for r in records], ["2026-06-30"])


if __name__ == "__main__":
    unittest.main()


class FinishRunTest(unittest.TestCase):
    """뒤 단계가 앞 단계의 실측치를 지우면 §12.3 처리량 게이트가 무의미해진다."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = Path(self.tmp.name) / "es.sqlite3"
        storage.ensure_schema(self.db)
        with storage.connect_rw(self.db) as con:
            storage.start_run(con, run_id="r1", mode="live", cutoff_at=CUTOFF,
                              policy_version="p", identity_version="i", code_version="c")

    def tearDown(self):
        self.tmp.cleanup()

    def test_later_stage_does_not_null_earlier_throughput(self):
        with storage.connect_rw(self.db) as con:
            storage.finish_run(con, "r1", status="extracted", throughput={"processed": 300})
            storage.finish_run(con, "r1", status="assessed", artifact_path="a.md")
        with storage.connect_ro(self.db) as con:
            row = con.execute(
                "SELECT throughput_json, artifact_path, run_status FROM runs WHERE run_id='r1'"
            ).fetchone()
        self.assertIn("300", row[0])
        self.assertEqual(row[1], "a.md")
        self.assertEqual(row[2], "assessed")


class RunRevisionTest(unittest.TestCase):
    """같은 cutoff 재실행이 unique 제약에 막혀 조용히 사라지면 안 된다(§15.2)."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = Path(self.tmp.name) / "es.sqlite3"
        storage.ensure_schema(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _start(self, con, run_id):
        return storage.start_run(con, run_id=run_id, mode="historical_exploration",
                                 cutoff_at=CUTOFF, policy_version="policy_v1",
                                 identity_version="identity_v1", code_version="c")

    def test_rerun_same_cutoff_bumps_revision(self):
        with storage.connect_rw(self.db) as con:
            self.assertEqual(self._start(con, "r1"), 1)
            self.assertEqual(self._start(con, "r2"), 2)
        with storage.connect_ro(self.db) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM runs").fetchone()[0], 2)

    def test_lease_works_on_the_new_revision(self):
        with storage.connect_rw(self.db) as con:
            self._start(con, "r1")
            storage.acquire_run_lease(con, "r1", owner="1@A")
            self._start(con, "r2")
            self.assertTrue(storage.acquire_run_lease(con, "r2", owner="1@A"))

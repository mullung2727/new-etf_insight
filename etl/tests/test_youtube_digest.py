"""youtube_digest.py (하루 2회 통합 시장 브리핑) 단위 테스트.

저장소 계약(미전송 선별·전송 후 ledger 확정) → 검증·점수 재계산 → 메시지 포맷 →
세션 러너 멱등성 → 스케줄 러너(ps1) 순.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import youtube_digest as yd

PROJECT_ROOT = Path(__file__).resolve().parents[2]

CH_A = "UCeN2YeJcBCRJoXgzF_OU3qw"  # unrealtech
CH_B = "UCxvdCnvGODDyuvnELnLkQWw"  # 이효석아카데미


def _make_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE youtube_videos (
            channel_id TEXT NOT NULL, video_id TEXT NOT NULL, title TEXT NOT NULL,
            published_at_utc TEXT NOT NULL, date_kst TEXT NOT NULL, url TEXT NOT NULL,
            transcript TEXT, transcript_lang TEXT, transcript_source TEXT, raw_json TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(channel_id, video_id)
        );
        CREATE TABLE youtube_video_summaries (
            channel_id TEXT NOT NULL, video_id TEXT NOT NULL, date_kst TEXT NOT NULL,
            model TEXT, summary_json TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(channel_id, video_id)
        );
        """
    )
    yd.ensure_schema(con)
    return con


def _add_video(
    con: sqlite3.Connection,
    channel_id: str,
    video_id: str,
    published_utc: str,
    *,
    with_summary: bool = True,
    headline: str = "헤드라인",
) -> None:
    date_kst = dt.datetime.fromisoformat(published_utc).astimezone(yd.KST).strftime("%Y-%m-%d")
    con.execute(
        "INSERT INTO youtube_videos(channel_id, video_id, title, published_at_utc, date_kst,"
        " url, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (channel_id, video_id, f"제목 {video_id}", published_utc, date_kst,
         f"https://youtu.be/{video_id}", "now", "now"),
    )
    if with_summary:
        con.execute(
            "INSERT INTO youtube_video_summaries(channel_id, video_id, date_kst, model,"
            " summary_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (channel_id, video_id, date_kst, "test",
             json.dumps({
                 "headline": headline,
                 "issues": [{"title": "이슈", "summary": "내용", "time_hint": None}],
                 "bullets": ["불릿"],
                 "risk_or_caveat": None,
                 "stocks": [{"name": "SK하이닉스", "ticker": "000660"}],
             }, ensure_ascii=False),
             "now", "now"),
        )


class SessionWindowTest(unittest.TestCase):
    def test_morning_cutoff_is_10kst_and_start_48h_before(self):
        start, cutoff = yd.session_window("2026-08-07", "morning")
        self.assertEqual(cutoff, "2026-08-07T01:00:00+00:00")  # 10:00 KST
        self.assertEqual(start, "2026-08-05T01:00:00+00:00")

    def test_evening_cutoff_is_18kst(self):
        _, cutoff = yd.session_window("2026-08-07", "evening")
        self.assertEqual(cutoff, "2026-08-07T09:00:00+00:00")  # 18:00 KST


class FetchUnsentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = _make_db(Path(self.tmp.name) / "t.sqlite3")
        self.window = yd.session_window("2026-08-07", "morning")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def _fetch(self):
        return yd.fetch_unsent_summaries(
            self.con, window_start_utc=self.window[0], cutoff_utc=self.window[1]
        )

    def test_in_window_unsent_is_fetched(self):
        _add_video(self.con, CH_A, "v1", "2026-08-06T13:00:00+00:00")
        rows = self._fetch()
        self.assertEqual([r["video_id"] for r in rows], ["v1"])
        self.assertEqual(rows[0]["source_ref"], f"{CH_A}/v1")
        self.assertEqual(rows[0]["channel_label"], "unrealtech")

    def test_late_summarized_older_video_within_48h_is_recovered(self):
        _add_video(self.con, CH_A, "v_late", "2026-08-05T12:00:00+00:00")
        self.assertEqual([r["video_id"] for r in self._fetch()], ["v_late"])

    def test_older_than_48h_is_excluded(self):
        _add_video(self.con, CH_A, "v_old", "2026-08-04T12:00:00+00:00")
        self.assertEqual(self._fetch(), [])

    def test_after_cutoff_is_excluded(self):
        _add_video(self.con, CH_A, "v_future", "2026-08-07T05:00:00+00:00")
        self.assertEqual(self._fetch(), [])

    def test_video_without_summary_is_excluded(self):
        _add_video(self.con, CH_A, "v_nosum", "2026-08-06T13:00:00+00:00", with_summary=False)
        self.assertEqual(self._fetch(), [])

    def test_empty_summary_is_excluded_so_it_can_be_retried_later(self):
        _add_video(self.con, CH_A, "v_empty", "2026-08-06T13:00:00+00:00")
        self.con.execute(
            "UPDATE youtube_video_summaries SET summary_json=? WHERE video_id='v_empty'",
            (json.dumps({"headline": "", "issues": [], "bullets": []}),),
        )
        self.assertEqual(self._fetch(), [])

    def test_broken_summary_json_is_excluded(self):
        _add_video(self.con, CH_A, "v_broken", "2026-08-06T13:00:00+00:00")
        self.con.execute(
            "UPDATE youtube_video_summaries SET summary_json='{not json' "
            "WHERE video_id='v_broken'"
        )
        self.assertEqual(self._fetch(), [])

    def test_kst_offset_timestamp_is_compared_by_instant_not_string(self):
        # 수집 폴백이 쓰는 '+09:00' 표기. 문자열 비교면 cutoff(=+00:00) 밖으로 밀린다.
        _add_video(self.con, CH_A, "v_kst_in", "2026-08-07T09:30:00+09:00")  # 00:30Z, 안쪽
        _add_video(self.con, CH_A, "v_kst_out", "2026-08-07T10:30:00+09:00")  # 01:30Z, 바깥
        self.assertEqual([r["video_id"] for r in self._fetch()], ["v_kst_in"])

    def test_delivered_video_is_not_fetched_again(self):
        _add_video(self.con, CH_A, "v1", "2026-08-06T13:00:00+00:00")
        yd.mark_run_sent(
            self.con, date_kst="2026-08-06", session="evening", source_refs=[f"{CH_A}/v1"]
        )
        self.assertEqual(self._fetch(), [])


class MarkRunSentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = _make_db(Path(self.tmp.name) / "t.sqlite3")
        _add_video(self.con, CH_A, "v1", "2026-08-06T13:00:00+00:00")
        yd.save_generated_run(
            self.con,
            date_kst="2026-08-07",
            session="morning",
            window=yd.session_window("2026-08-07", "morning"),
            source_refs=[f"{CH_A}/v1"],
            digest={"highlights": []},
            message_text="메시지",
            warnings=[],
        )
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_status_and_deliveries_land_together(self):
        yd.mark_run_sent(
            self.con, date_kst="2026-08-07", session="morning", source_refs=[f"{CH_A}/v1"]
        )
        self.con.commit()
        self.assertEqual(
            yd.get_digest_run(self.con, "2026-08-07", "morning")["status"], "sent"
        )
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM youtube_digest_deliveries").fetchone()[0], 1
        )

    def test_rollback_cancels_status_and_deliveries_together(self):
        yd.mark_run_sent(
            self.con, date_kst="2026-08-07", session="morning", source_refs=[f"{CH_A}/v1"]
        )
        self.con.rollback()
        self.assertEqual(
            yd.get_digest_run(self.con, "2026-08-07", "morning")["status"], "generated"
        )
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM youtube_digest_deliveries").fetchone()[0], 0
        )

    def test_same_delivery_twice_makes_no_duplicate_row(self):
        for _ in range(2):
            yd.mark_run_sent(
                self.con, date_kst="2026-08-07", session="morning", source_refs=[f"{CH_A}/v1"]
            )
        self.con.commit()
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM youtube_digest_deliveries").fetchone()[0], 1
        )


def _rows(*refs: tuple[str, str, str]) -> list[dict]:
    out = []
    for channel_id, video_id, published in refs:
        out.append({
            "channel_id": channel_id,
            "channel_label": {CH_A: "unrealtech", CH_B: "이효석아카데미"}[channel_id],
            "video_id": video_id,
            "source_ref": f"{channel_id}/{video_id}",
            "title": f"제목 {video_id}",
            "url": "",
            "published_at_utc": published,
            "published_at_kst": "08-06 22:00",
            "summary": {
                "headline": "HBF는 HBM과 SSD 사이 계층",
                "issues": [{"title": "AI 메모리", "summary": "512GB 목표", "time_hint": None}],
                "bullets": [],
                "risk_or_caveat": None,
                "stocks": [{"name": "SK하이닉스", "ticker": "000660"}],
            },
        })
    return out


def _highlight(**over) -> dict:
    base = {
        "title": "AI 메모리 계층 변화",
        "summary": "HBF는 HBM 대체가 아니라 새 계층으로 제시됐다.",
        "evidence": "512GB·0.4~3TB/s 목표",
        "view_difference": None,
        "uncertainty": None,
        "investment_links": ["SK하이닉스"],
        "source_video_ids": [f"{CH_A}/v1"],
        "score_breakdown": {
            "market_impact": 20, "evidence_quality": 20, "novelty": 15,
            "investment_relevance": 15, "cross_channel": 0,
        },
    }
    base.update(over)
    return base


class ValidateDigestTest(unittest.TestCase):
    def setUp(self):
        self.rows = _rows(
            (CH_A, "v1", "2026-08-06T13:00:00+00:00"),
            (CH_B, "v2", "2026-08-06T14:00:00+00:00"),
        )

    def test_score_total_is_recomputed_by_code(self):
        raw = {"highlights": [_highlight()]}
        highlights, _ = yd.validate_digest(raw, self.rows)
        self.assertEqual(highlights[0]["score_total"], 70)

    def test_out_of_range_score_is_rejected_with_warning(self):
        item = _highlight()
        item["score_breakdown"]["market_impact"] = 99
        highlights, warnings = yd.validate_digest({"highlights": [item]}, self.rows)
        self.assertEqual(highlights, [])
        self.assertTrue(any(w.startswith("score_out_of_range") for w in warnings))

    def test_unknown_source_ref_is_rejected(self):
        item = _highlight(source_video_ids=["UCzzz/none"])
        highlights, warnings = yd.validate_digest({"highlights": [item]}, self.rows)
        self.assertEqual(highlights, [])
        self.assertTrue(any(w.startswith("highlight_without_valid_source") for w in warnings))

    def test_single_channel_cannot_claim_cross_channel_score(self):
        item = _highlight()
        item["score_breakdown"]["cross_channel"] = 10
        highlights, warnings = yd.validate_digest({"highlights": [item]}, self.rows)
        self.assertEqual(highlights[0]["score_breakdown"]["cross_channel"], 0)
        self.assertEqual(highlights[0]["score_total"], 70)
        self.assertTrue(any(w.startswith("cross_channel_single_source") for w in warnings))

    def test_two_channels_keep_cross_channel_score(self):
        item = _highlight(source_video_ids=[f"{CH_A}/v1", f"{CH_B}/v2"])
        item["score_breakdown"]["cross_channel"] = 10
        highlights, _ = yd.validate_digest({"highlights": [item]}, self.rows)
        self.assertEqual(highlights[0]["score_total"], 80)
        self.assertEqual(highlights[0]["source_channels"], ["unrealtech", "이효석아카데미"])

    def test_same_source_set_is_deduped_keeping_higher_score(self):
        low = _highlight(title="낮은 점수")
        high = _highlight(title="높은 점수")
        high["score_breakdown"]["novelty"] = 20
        highlights, warnings = yd.validate_digest({"highlights": [low, high]}, self.rows)
        self.assertEqual([h["title"] for h in highlights], ["높은 점수"])
        self.assertTrue(any(w.startswith("duplicate_source_set") for w in warnings))

    def test_investment_link_not_in_input_is_dropped(self):
        item = _highlight(investment_links=["SK하이닉스", "없는종목"])
        highlights, warnings = yd.validate_digest({"highlights": [item]}, self.rows)
        self.assertEqual(highlights[0]["investment_links"], ["SK하이닉스"])
        self.assertTrue(any(w.startswith("investment_link_not_in_input") for w in warnings))

    def test_sorted_by_score_then_first_published(self):
        a = _highlight(title="A", source_video_ids=[f"{CH_B}/v2"])
        b = _highlight(title="B", source_video_ids=[f"{CH_A}/v1"])
        highlights, _ = yd.validate_digest({"highlights": [a, b]}, self.rows)
        self.assertEqual([h["title"] for h in highlights], ["B", "A"])


class FormatMessageTest(unittest.TestCase):
    def setUp(self):
        self.rows = _rows(
            (CH_A, "v1", "2026-08-06T13:00:00+00:00"),
            (CH_B, "v2", "2026-08-06T14:00:00+00:00"),
        )

    def _validated(self, items: list[dict]) -> list[dict]:
        return yd.validate_digest({"highlights": items}, self.rows)[0]

    def test_no_new_videos_returns_none(self):
        self.assertIsNone(yd.format_digest_message("2026-08-07", "morning", [], []))

    def test_no_highlight_above_threshold_gives_status_message(self):
        low = _highlight()
        low["score_breakdown"] = {
            "market_impact": 5, "evidence_quality": 5, "novelty": 5,
            "investment_relevance": 5, "cross_channel": 0,
        }
        msg = yd.format_digest_message(
            "2026-08-07", "morning", self.rows, self._validated([low])
        )
        self.assertIn("주요 이슈 없음", msg)
        self.assertIn("신규 영상 2개", msg)

    def test_multiline_summary_becomes_one_bullet_per_line(self):
        item = _highlight(summary="HBF는 HBM 대체 아닌 새 계층\n512GB 용량으로 저장 계층 겨냥")
        msg = yd.format_digest_message(
            "2026-08-07", "morning", self.rows, self._validated([item])
        )
        self.assertIn("  - HBF는 HBM 대체 아닌 새 계층", msg)
        self.assertIn("  - 512GB 용량으로 저장 계층 겨냥", msg)

    def test_single_paragraph_summary_stays_one_line(self):
        """개조식 이전에 쌓인 행은 기계적으로 쪼개지 않는다."""
        msg = yd.format_digest_message(
            "2026-08-07", "morning", self.rows, self._validated([_highlight()])
        )
        summary_lines = [ln for ln in msg.splitlines() if ln.startswith("  - ")]
        self.assertEqual(len(summary_lines), 1)

    def test_channel_label_not_channel_id_is_shown(self):
        msg = yd.format_digest_message(
            "2026-08-07", "morning", self.rows, self._validated([_highlight()])
        )
        self.assertIn("출처: unrealtech", msg)
        self.assertNotIn(CH_A, msg)

    def test_optional_lines_appear_only_when_present(self):
        plain = yd.format_digest_message(
            "2026-08-07", "morning", self.rows, self._validated([_highlight()])
        )
        self.assertNotIn("차이:", plain)
        self.assertNotIn("불확실:", plain)
        rich = yd.format_digest_message(
            "2026-08-07", "morning", self.rows,
            self._validated([_highlight(view_difference="엇갈림", uncertainty="검증 필요")]),
        )
        self.assertIn("차이: 엇갈림", rich)
        self.assertIn("불확실: 검증 필요", rich)

    def test_sorted_by_score_and_capped_at_max_highlights(self):
        items = []
        for idx, novelty in enumerate([20, 18, 16, 14]):
            item = _highlight(title=f"H{idx}", source_video_ids=[f"{CH_A}/v1"])
            item["score_breakdown"] = dict(item["score_breakdown"], novelty=novelty)
            item["source_video_ids"] = [f"{CH_A}/v1"] if idx % 2 else [f"{CH_B}/v2"]
            items.append(item)
        highlights = self._validated(items)
        msg = yd.format_digest_message("2026-08-07", "morning", self.rows, highlights)
        self.assertLessEqual(msg.count("• ["), yd.MAX_HIGHLIGHTS)
        scores = [int(part.split("점]")[0]) for part in msg.split("• [")[1:]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_long_items_drop_from_lowest_rank_instead_of_slicing(self):
        long_rows = _rows((CH_A, "v1", "2026-08-06T13:00:00+00:00"))
        items = []
        for idx, novelty in enumerate([20, 18, 16]):
            item = _highlight(
                title=f"H{idx}" + "가" * 40,
                summary="요" * 140,
                evidence="근" * 110,
                view_difference="차" * 90,
                uncertainty="불" * 90,
            )
            item["score_breakdown"] = dict(item["score_breakdown"], novelty=novelty)
            items.append(item)
        # 같은 source set은 dedupe되므로 검증을 우회해 포맷터만 직접 시험한다.
        highlights = []
        for idx, item in enumerate(items):
            highlights.append({
                **item,
                "score_total": 90 - idx,
                "source_channels": ["unrealtech"],
                "first_published_at_utc": "2026-08-06T13:00:00+00:00",
            })
        msg = yd.format_digest_message("2026-08-07", "morning", long_rows, highlights)
        self.assertLessEqual(len(msg), yd.MAX_MESSAGE_CHARS)
        self.assertGreaterEqual(msg.count("• ["), 1)
        self.assertTrue(msg.endswith("전망이 아님"))


class CollectFailureNoteTest(unittest.TestCase):
    def test_channel_ids_are_shown_as_labels(self):
        note = yd.collect_failure_note([CH_A, CH_B])
        self.assertIn("unrealtech", note)
        self.assertIn("이효석아카데미", note)
        self.assertNotIn(CH_A, note)

    def test_no_failure_gives_no_note(self):
        self.assertIsNone(yd.collect_failure_note([]))

    def test_note_appears_under_header(self):
        rows = _rows((CH_A, "v1", "2026-08-06T13:00:00+00:00"))
        msg = yd.format_digest_message(
            "2026-08-07", "morning", rows, [], ["⚠️ 수집 실패: 이효석아카데미"]
        )
        self.assertIn("⚠️ 수집 실패: 이효석아카데미", msg)
        self.assertLess(msg.index("수집 실패"), msg.index("주요 이슈 없음"))

    def test_note_only_message_when_no_new_videos(self):
        msg = yd.format_digest_message("2026-08-07", "morning", [], [], ["⚠️ 수집 실패: X"])
        self.assertIn("신규 영상 0개", msg)
        self.assertIn("⚠️ 수집 실패: X", msg)

    def test_no_rows_and_no_note_still_none(self):
        self.assertIsNone(yd.format_digest_message("2026-08-07", "morning", [], [], []))


class RunDigestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.sqlite3"
        con = _make_db(self.db)
        _add_video(con, CH_A, "v1", "2026-08-06T13:00:00+00:00")
        _add_video(con, CH_B, "v2", "2026-08-06T14:00:00+00:00")
        con.commit()
        con.close()
        self.sent: list[str] = []
        self.llm_calls = 0

    def tearDown(self):
        self.tmp.cleanup()

    def _generate(self, prompt, *, output_schema_path, search=False):
        self.llm_calls += 1
        return json.dumps({"highlights": [
            _highlight(source_video_ids=[f"{CH_A}/v1", f"{CH_B}/v2"])
        ]}, ensure_ascii=False)

    def _run(self, *, send_ok=True, dry_run=False, session="morning", **kwargs):
        def send(message, channel=None):
            self.sent.append(message)
            return send_ok
        return yd.run_digest(
            date_kst="2026-08-07",
            session=session,
            db_path=self.db,
            dry_run=dry_run,
            generate_fn=self._generate,
            send_fn=send,
            **kwargs,
        )

    def _one(self, sql: str):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql).fetchone()[0]
        finally:
            con.close()

    def _delivery_count(self) -> int:
        return self._one("SELECT COUNT(*) FROM youtube_digest_deliveries")

    def test_dry_run_touches_neither_ledger_nor_sender(self):
        self.assertEqual(self._run(dry_run=True), 0)
        self.assertEqual(self.sent, [])
        self.assertEqual(self._delivery_count(), 0)
        self.assertEqual(self._one("SELECT COUNT(*) FROM youtube_digest_runs"), 0)

    def test_ledger_written_only_after_successful_send(self):
        self.assertEqual(self._run(), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self._delivery_count(), 2)
        self.assertEqual(self._one("SELECT status FROM youtube_digest_runs"), "sent")

    def test_send_failure_exits_1_and_keeps_ledger_empty(self):
        self.assertEqual(self._run(send_ok=False), 1)
        self.assertEqual(self._delivery_count(), 0)
        self.assertEqual(self._one("SELECT status FROM youtube_digest_runs"), "generated")

    def test_rerun_after_send_failure_reuses_generated_message_without_llm(self):
        self._run(send_ok=False)
        calls_before = self.llm_calls
        self.assertEqual(self._run(), 0)
        self.assertEqual(self.llm_calls, calls_before)
        self.assertEqual(self.sent[0], self.sent[1])

    def test_rerun_skips_resend_when_another_session_already_delivered(self):
        self._run(send_ok=False)  # morning 생성, 전송 실패 → ledger 비어 있음
        self.assertEqual(self._run(session="evening"), 0)  # evening이 v1,v2를 배달
        sent_before = len(self.sent)
        self.assertEqual(self._run(), 0)  # morning 재실행
        self.assertEqual(len(self.sent), sent_before)
        self.assertEqual(
            self._one("SELECT status FROM youtube_digest_runs WHERE session='morning'"), "sent"
        )

    def test_rerun_still_sends_when_some_source_is_undelivered(self):
        self._run(send_ok=False)
        con = sqlite3.connect(self.db)
        try:
            yd.mark_run_sent(
                con, date_kst="2026-08-07", session="evening", source_refs=[f"{CH_A}/v1"]
            )
            con.commit()
        finally:
            con.close()
        sent_before = len(self.sent)
        self.assertEqual(self._run(), 0)
        self.assertEqual(len(self.sent), sent_before + 1)

    def test_already_sent_session_does_not_send_again(self):
        self._run()
        self.assertEqual(self._run(), 0)
        self.assertEqual(len(self.sent), 1)

    def test_llm_failure_leaves_videos_unsent_for_next_run(self):
        def boom(prompt, *, output_schema_path, search=False):
            raise RuntimeError("llm down")
        with self.assertRaises(RuntimeError):
            yd.run_digest(
                date_kst="2026-08-07", session="morning", db_path=self.db,
                generate_fn=boom, send_fn=lambda m, c=None: True,
            )
        self.assertEqual(self._delivery_count(), 0)
        self.assertEqual(self._run(), 0)
        self.assertEqual(self._delivery_count(), 2)

    def test_collect_failure_warning_rides_along_with_digest(self):
        self.assertEqual(self._run(collect_failed=[CH_B], summarize_failed=True), 0)
        self.assertIn("이효석아카데미", self.sent[0])
        self.assertIn("영상 요약 단계 실패", self.sent[0])
        self.assertIn("🔥 주요 내용", self.sent[0])

    def test_warning_only_send_does_not_consume_the_session(self):
        self._run()  # v1, v2 소진
        self.sent.clear()
        self.assertEqual(self._run(session="evening", collect_failed=[CH_B]), 0)
        self.assertIn("신규 영상 0개", self.sent[0])
        # run 행을 만들지 않아 나중에 진짜 브리핑을 보낼 수 있어야 한다.
        self.assertEqual(
            self._one("SELECT COUNT(*) FROM youtube_digest_runs WHERE session='evening'"), 0
        )

    def test_no_new_summaries_skips_llm_and_send(self):
        self._run()  # v1, v2 소진
        self.sent.clear()
        calls_before = self.llm_calls
        self.assertEqual(self._run(session="evening"), 0)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.llm_calls, calls_before)

    def test_next_session_recovers_video_that_missed_previous_send(self):
        self._run()
        con = sqlite3.connect(self.db)
        try:
            _add_video(con, CH_A, "v3", "2026-08-07T02:00:00+00:00")
            con.commit()
        finally:
            con.close()
        self.sent.clear()
        self.assertEqual(self._run(session="evening"), 0)
        refs = json.loads(
            self._one(
                "SELECT source_video_ids_json FROM youtube_digest_runs WHERE session='evening'"
            )
        )
        self.assertEqual(refs, [f"{CH_A}/v3"])


class ScheduleRunnerTest(unittest.TestCase):
    """ops 러너: 수집→영상요약→통합 digest 연결과 검증된 실행 패턴."""

    def setUp(self):
        self.ps1 = (
            PROJECT_ROOT / "ops" / "scheduled-tasks" / "run-youtube-digest-session.ps1"
        ).read_text(encoding="utf-8")

    def test_uses_venv_python_not_uv(self):
        self.assertIn(r".\.venv\Scripts\python.exe", self.ps1)
        self.assertNotIn("uv run", self.ps1)

    def test_morning_collects_previous_and_current_day(self):
        self.assertIn('if ($Session -eq "morning")', self.ps1)
        self.assertIn("AddDays(-1)", self.ps1)

    def test_collects_all_channels_via_run_youtube_channels(self):
        self.assertIn(r"scripts\run_youtube_channels.py", self.ps1)

    def test_reuses_existing_auto_only_analysis(self):
        self.assertIn(r"scripts\run_youtube_analysis.py", self.ps1)
        self.assertIn("--auto-only", self.ps1)
        self.assertIn("--lookback-days", self.ps1)

    def test_digest_step_sends_to_telegram_report_channel(self):
        self.assertIn(r"scripts\youtube_digest.py", self.ps1)
        self.assertIn("telegram_report", self.ps1)

    def test_collect_and_summarize_failures_do_not_abort_the_run(self):
        # 수집·요약은 soft(계속), digest만 throw. 라벨 문구가 아니라 호출 수로 본다.
        self.assertIn("function Invoke-StepSoft", self.ps1)
        self.assertEqual(self.ps1.count("Invoke-StepSoft \""), 2)
        self.assertEqual(self.ps1.count("Invoke-Step \""), 1)

    def test_failed_channels_are_passed_to_digest_message(self):
        self.assertIn("ERROR channel=(UC[\\w-]{22})", self.ps1)
        self.assertIn("--collect-failed", self.ps1)
        self.assertIn("--summarize-failed", self.ps1)

    def test_native_stderr_does_not_fail_the_step(self):
        self.assertIn("$ErrorActionPreference = 'Continue'", self.ps1)
        self.assertIn("$LASTEXITCODE", self.ps1)

    def test_failure_notice_reuses_send_report_messages(self):
        self.assertIn(r"scripts\send_report_messages.py", self.ps1)

    def test_task_xml_times_match_session_hours(self):
        ops = PROJECT_ROOT / "ops" / "scheduled-tasks"
        morning = (ops / "youtube-digest-morning.xml").read_text(encoding="utf-8")
        evening = (ops / "youtube-digest-evening.xml").read_text(encoding="utf-8")
        self.assertIn("T10:00:00", morning)
        self.assertIn("T18:00:00", evening)
        for xml in (morning, evening):
            self.assertIn("<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>", xml)
            self.assertIn("<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>", xml)


if __name__ == "__main__":
    unittest.main()

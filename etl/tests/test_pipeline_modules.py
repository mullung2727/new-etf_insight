import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.collect_etf_candidates import download_candidate_pdfs, should_download_pdf
from scripts.pdf_langgraph.pdf_analysis_langgraph import (
    CORRECTION_REVIEW_SCHEMA_PATH,
    CORRECTION_UPDATE_SCHEMA_PATH,
    EXTERNAL_RESEARCH_SCHEMA_PATH,
    SUMMARY_SCHEMA_PATH,
    call_codex,
    call_llm,
    enrich_missing_holding_identifiers,
    extract_classification_hints,
    normalize_summary,
    review_correction_filing,
    update_record_from_correction,
)
from new_etf_insight.llm import generate_json, get_provider
from new_etf_insight.dart_pdf import (
    build_pdf_download_main_url,
    extract_pdf_download_dcm_no,
    extract_prospectus_file_url,
)
from new_etf_insight.dart_viewer import (
    ViewerSection,
    build_etf_key,
    extract_fund_code,
    fetch_dart_viewer_text,
)
from new_etf_insight.daily_pipeline import run_daily_pipeline, run_period_as_daily_runs
from new_etf_insight.etf_classifier import classify_pre_listing_equity_etf
from new_etf_insight.filing_filter import is_candidate_filing, matches_candidate_query, to_candidate
from new_etf_insight.holding_backfill import backfill_weighted_missing_identifiers
from new_etf_insight.holding_identifier import (
    HoldingIdentifierResolver,
    SecurityMasterItem,
    compact_holding_key,
    load_holding_aliases,
    normalize_holding_name,
    parse_nasdaq_listed,
    parse_other_listed,
)
from new_etf_insight.llm.chatgpt_oauth_provider import ChatGptOAuthProvider
from new_etf_insight.llm.codex_provider import CodexProvider
from new_etf_insight.llm.openclaw_provider import OpenClawProvider




TARGET_TEXT = """
1. 집합투자기구 명칭: KB RISE 현대차고정피지컬AI 증권 상장지수 투자신탁(주식)(ET942)
2. 집합투자업자 명칭: KB자산운용주식회사
5. 증권신고서 효력발생일: 2026년 04월 29일
상장일: 2026년 00월 00일(예정)
이 투자신탁은 국내주식을 법에서 정하는 주된 투자대상으로 하며,
한국경제신문에서 산출 및 관리하는 "KEDI 현대차고정피지컬AI 지수(시장가격)"를 기초지수로 한다.
증권(주식형), 개방형, 추가형, 상장지수투자신탁(ETF)
기초지수의 구성종목과 구성비율을 완전 복제하는 방식으로 포트폴리오를 구성할 계획입니다.
투자신탁보수 합계 0.400
"""


class FilingFilterTest(unittest.TestCase):
    def test_keeps_investment_prospectus(self) -> None:
        self.assertTrue(
            is_candidate_filing(
                {
                    "corp_name": "KB자산운용",
                    "report_nm": "투자설명서(집합투자증권)(KBRISE현대차고정피지컬AI증권상장지수투자신탁(주식))",
                }
            )
        )

    def test_excludes_etn(self) -> None:
        self.assertFalse(
            is_candidate_filing(
                {
                    "corp_name": "한국투자증권",
                    "report_nm": "일괄신고추가서류(파생결합증권-상장지수증권)",
                }
            )
        )

    def test_excludes_non_equity_etf(self) -> None:
        self.assertFalse(
            is_candidate_filing(
                {
                    "corp_name": "삼성자산운용",
                    "report_nm": "투자설명서(집합투자증권-KODEX 국채10년 상장지수투자신탁(채권))",
                }
            )
        )

    def test_excludes_securities_issuance_result_report(self) -> None:
        self.assertFalse(
            is_candidate_filing(
                {
                    "corp_name": "미래에셋자산운용",
                    "report_nm": "증권발행실적보고서(집합투자증권)(미래에셋TIGER미국나스닥100ETF선물증권상장지수투자신탁(주식-파생형))",
                }
            )
        )

    def test_query_matches_without_spaces(self) -> None:
        self.assertTrue(
            matches_candidate_query(
                {
                    "corp_name": "KB자산운용",
                    "report_nm": "투자설명서(집합투자증권)(KBRISE현대차고정피지컬AI증권상장지수투자신탁(주식))",
                },
                "KB RISE 현대차고정피지컬AI",
            )
        )

    def test_candidate_keeps_corp_code(self) -> None:
        candidate = to_candidate(
            {
                "rcept_no": "20260429000010",
                "rcept_dt": "20260429",
                "corp_code": "00104500",
                "corp_name": "KB자산운용",
                "report_nm": "투자설명서(집합투자증권)",
            }
        )

        self.assertEqual(candidate.corp_code, "00104500")


class EtfClassifierTest(unittest.TestCase):
    def test_detects_pre_listing_equity_etf(self) -> None:
        result = classify_pre_listing_equity_etf(TARGET_TEXT)

        self.assertTrue(result.is_pre_listing_equity_etf)
        self.assertIn("상장지수", result.reasons)
        self.assertIn("주식형", result.reasons)
        self.assertIn("상장예정", result.reasons)


class DartPdfTest(unittest.TestCase):
    def test_extracts_download_button_dcm_no(self) -> None:
        html = """
        <button onclick="openPdfDownload('20260429000010', '11351971');">다운로드</button>
        """

        self.assertEqual(extract_pdf_download_dcm_no(html, "20260429000010"), "11351971")

    def test_builds_pdf_download_main_url(self) -> None:
        self.assertEqual(
            build_pdf_download_main_url("20260429000010", "11351971"),
            "https://dart.fss.or.kr/pdf/download/main.do?rcp_no=20260429000010&dcm_no=11351971",
        )

    def test_extracts_prospectus_file_url(self) -> None:
        html = """
        <td><a class="btnFile" href="/pdf/download/pdf.do?rcp_no=20260429000010&amp;dcm_no=11351971"></a></td>
        <td>
            <a class="btnFile" href="/pdf/download/file.do?rcp_no=20260429000010&amp;dcm_id=10611&amp;dcm_seq=815&amp;fl_nm=kb+rise+%ED%88%AC%EC%9E%90%EC%84%A4%EB%AA%85%EC%84%9C.pdf">
        </td>
        """

        self.assertEqual(
            extract_prospectus_file_url(html),
            "https://dart.fss.or.kr/pdf/download/file.do?rcp_no=20260429000010&dcm_id=10611&dcm_seq=815&fl_nm=kb+rise+%ED%88%AC%EC%9E%90%EC%84%A4%EB%AA%85%EC%84%9C.pdf",
        )


class DartViewerTest(unittest.TestCase):
    def test_extracts_fund_code(self) -> None:
        text = "1. 집합투자기구 명칭 : KB RISE 현대차고정피지컬AI (펀드코드 : ET942 )"

        self.assertEqual(extract_fund_code(text), "ET942")

    def test_extracts_fund_code_with_spaced_label(self) -> None:
        text = "집합투자기구 명칭 : 테스트 ETF (펀드 코드：ej669)"

        self.assertEqual(extract_fund_code(text), "EJ669")

    def test_extracts_fund_code_from_table_text(self) -> None:
        text = "집합투자기구 명칭(종류형 명칭) 펀드코드 한화 PLUS 은채권혼합증권상장지수투자신탁 EU027"

        self.assertEqual(extract_fund_code(text), "EU027")

    def test_builds_etf_key(self) -> None:
        self.assertEqual(build_etf_key("00104500", "et942"), "00104500_ET942")

    def test_fetch_dart_viewer_text_joins_section_texts(self) -> None:
        sections = [
            ViewerSection("20260429000103", "1", "1", "0", "10", "dart4.xsd"),
            ViewerSection("20260429000103", "1", "2", "10", "10", "dart4.xsd"),
            ViewerSection("20260429000103", "1", "3", "20", "10", "dart4.xsd"),
        ]

        with (
            patch("new_etf_insight.dart_viewer.fetch_dart_main_html", return_value="main html"),
            patch("new_etf_insight.dart_viewer.extract_viewer_sections", return_value=sections),
            patch(
                "new_etf_insight.dart_viewer.fetch_viewer_section_text",
                side_effect=["정 정 신 고", "", "정정사항"],
            ),
        ):
            self.assertEqual(fetch_dart_viewer_text("20260429000103"), "정 정 신 고\n정정사항")


class HoldingIdentifierTest(unittest.TestCase):
    def test_normalizes_common_stock_suffixes(self) -> None:
        self.assertEqual(normalize_holding_name("NVIDIA Corporation Common Stock"), "NVIDIA")

    def test_compact_key_ignores_spacing_punctuation_and_company_suffixes(self) -> None:
        self.assertEqual(compact_holding_key("Kakao Games Corp."), "KAKAOGAMES")
        self.assertEqual(compact_holding_key("KakaoGames, Inc."), "KAKAOGAMES")
        self.assertEqual(compact_holding_key("Alphabet Inc-CL A"), "ALPHABETA")
        self.assertEqual(compact_holding_key("Sandisk Corp/DE"), "SANDISK")

    def test_compact_key_match_enriches_english_name_variants(self) -> None:
        resolver = HoldingIdentifierResolver()
        resolver._kr_master = [SecurityMasterItem(name="KakaoGames", ticker="293490", exchange="KOSDAQ")]

        result = resolver.enrich_items(
            [{"name": "Kakao Games Corp.", "ticker": None, "exchange": None, "weight": "2"}],
            primary_country="KR",
        )

        self.assertEqual(result[0]["ticker"], "293490")
        self.assertEqual(result[0]["exchange"], "KOSDAQ")

    def test_loads_holding_aliases_by_compact_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            alias_path = Path(tmpdir) / "aliases.json"
            alias_path.write_text(
                json.dumps(
                    {
                        "aliases": [
                            {
                                "name": "크래프톤",
                                "ticker": "259960",
                                "exchange": "KOSPI",
                                "source": "manual_alias",
                                "aliases": ["KRAFTON"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            aliases = load_holding_aliases(alias_path)

        self.assertEqual(aliases["KRAFTON"].ticker, "259960")
        self.assertEqual(aliases["KRAFTON"].source, "manual_alias")

    def test_alias_match_enriches_english_to_korean_security(self) -> None:
        resolver = HoldingIdentifierResolver()
        resolver._kr_master = []
        resolver._us_master = []
        resolver._aliases = {
            "KRAFTON": SecurityMasterItem(
                name="크래프톤",
                ticker="259960",
                exchange="KOSPI",
                source="manual_alias",
            )
        }

        result = resolver.enrich_items(
            [{"name": "KRAFTON, Inc.", "ticker": None, "exchange": None, "weight": "26.86%"}],
            primary_country="KR",
        )

        self.assertEqual(result[0]["ticker"], "259960")
        self.assertEqual(result[0]["exchange"], "KOSPI")

    def test_parses_nasdaq_listed_rows(self) -> None:
        rows = parse_nasdaq_listed(
            "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
            "NVDA|NVIDIA Corporation - Common Stock|Q|N|N|100|N|N\n"
            "TEST|Test Issue Inc. - Common Stock|Q|Y|N|100|N|N\n"
            "File Creation Time: test\n"
        )

        self.assertEqual(rows, [SecurityMasterItem(name="NVIDIA Corporation - Common Stock", ticker="NVDA", exchange="NASDAQ")])

    def test_parses_other_listed_rows(self) -> None:
        rows = parse_other_listed(
            "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
            "A|Agilent Technologies, Inc. Common Stock|N|A|N|100|N|A\n"
        )

        self.assertEqual(rows, [SecurityMasterItem(name="Agilent Technologies, Inc. Common Stock", ticker="A", exchange="NYSE")])

    def test_enriches_kr_holding_from_cached_master(self) -> None:
        resolver = HoldingIdentifierResolver(bas_dd="20260430")
        resolver._kr_master = [SecurityMasterItem(name="삼성전자", ticker="005930", exchange="KOSPI")]

        result = resolver.enrich_items(
            [{"name": "삼성전자", "ticker": None, "exchange": None, "weight": "10"}],
            primary_country="KR",
        )

        self.assertEqual(result[0]["ticker"], "005930")
        self.assertEqual(result[0]["exchange"], "KOSPI")

    def test_enriches_us_holding_from_cached_master(self) -> None:
        resolver = HoldingIdentifierResolver()
        resolver._us_master = [SecurityMasterItem(name="NVIDIA Corporation Common Stock", ticker="NVDA", exchange="NASDAQ")]

        result = resolver.enrich_items(
            [{"name": "NVIDIA", "ticker": None, "exchange": None, "weight": "10"}],
            primary_country="US",
        )

        self.assertEqual(result[0]["ticker"], "NVDA")
        self.assertEqual(result[0]["exchange"], "NASDAQ")

    def test_keeps_existing_identifier_values(self) -> None:
        resolver = HoldingIdentifierResolver()

        result = resolver.enrich_items(
            [{"name": "NVIDIA", "ticker": "NVDA", "exchange": "NASDAQ", "weight": "10"}],
            primary_country="US",
        )

        self.assertEqual(result[0]["ticker"], "NVDA")
        self.assertEqual(result[0]["exchange"], "NASDAQ")

    def test_fetches_master_once_per_resolver(self) -> None:
        resolver = HoldingIdentifierResolver(bas_dd="20260430")
        with patch(
            "new_etf_insight.holding_identifier.fetch_krx_master",
            return_value=[SecurityMasterItem(name="삼성전자", ticker="005930", exchange="KOSPI")],
        ) as fetch_master:
            resolver.enrich_items([{"name": "삼성전자", "ticker": None, "exchange": None}], primary_country="KR")
            resolver.enrich_items([{"name": "삼성전자", "ticker": None, "exchange": None}], primary_country="KR")

        fetch_master.assert_called_once_with("20260430")

    def test_langgraph_enrichment_node_updates_missing_identifiers(self) -> None:
        resolver = HoldingIdentifierResolver()
        resolver._kr_master = [SecurityMasterItem(name="삼성전자", ticker="005930", exchange="KOSPI")]
        state = {
            "summary": {
                "market_exposure": {"primary_country": "KR"},
                "holdings": {
                    "items": [{"name": "삼성전자", "ticker": None, "exchange": None, "weight": "10"}],
                },
            },
            "holding_identifier_resolver": resolver,
        }

        result = enrich_missing_holding_identifiers(state)

        item = result["summary"]["holdings"]["items"][0]
        self.assertEqual(item["ticker"], "005930")
        self.assertEqual(item["exchange"], "KOSPI")


class HoldingBackfillTest(unittest.TestCase):
    def test_backfills_weighted_missing_identifiers_in_existing_record_json(self) -> None:
        resolver = HoldingIdentifierResolver()
        resolver._kr_master = [SecurityMasterItem(name="삼성전자", ticker="005930", exchange="KOSPI")]

        with tempfile.TemporaryDirectory() as tmpdir:
            records_dir = Path(tmpdir) / "runs" / "20260430" / "records"
            records_dir.mkdir(parents=True)
            record_path = records_dir / "001_ET001.json"
            record_path.write_text(
                json.dumps(
                    {
                        "source": {"etf_key": "001_ET001"},
                        "summary": {
                            "fund_name": "테스트 ETF",
                            "market_exposure": {"primary_country": "KR"},
                            "holdings": {
                                "items": [
                                    {"name": "삼성전자", "ticker": None, "exchange": None, "weight": "10%"},
                                    {"name": "현금", "ticker": None, "exchange": None, "weight": None},
                                ]
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report_path = Path(tmpdir) / "report.json"
            report = backfill_weighted_missing_identifiers(
                Path(tmpdir) / "runs",
                report_path=report_path,
                resolver=resolver,
            )
            updated = json.loads(record_path.read_text(encoding="utf-8"))
            report_exists = report_path.exists()

        item = updated["summary"]["holdings"]["items"][0]
        self.assertEqual(item["ticker"], "005930")
        self.assertEqual(item["exchange"], "KOSPI")
        self.assertEqual(item["identifier_source"], "security_master_exact")
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["updated_count"], 1)
        self.assertEqual(report["unresolved_count"], 0)
        self.assertTrue(report_exists)

    def test_reports_unresolved_weighted_identifier_without_overwriting_dry_run(self) -> None:
        resolver = HoldingIdentifierResolver()
        resolver._us_master = []

        with tempfile.TemporaryDirectory() as tmpdir:
            records_dir = Path(tmpdir) / "records"
            records_dir.mkdir()
            record_path = records_dir / "001_ET001.json"
            original = {
                "source": {"etf_key": "001_ET001"},
                "summary": {
                    "market_exposure": {"primary_country": "US"},
                    "holdings": {"items": [{"name": "Unknown Corp", "ticker": None, "exchange": None, "weight": "5%"}]},
                },
            }
            record_path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

            report = backfill_weighted_missing_identifiers(records_dir, dry_run=True, resolver=resolver)
            unchanged = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual(unchanged, original)
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["updated_count"], 0)
        self.assertEqual(report["unresolved_count"], 1)
        self.assertEqual(report["unresolved"][0]["name"], "Unknown Corp")


class CorrectionReviewTest(unittest.TestCase):
    def test_extracts_classification_hints_without_final_classification(self) -> None:
        hints = extract_classification_hints(
            "집합투자기구 명칭: 테스트 AI반도체 액티브 ETF\n"
            "기초지수: FnGuide AI 반도체 지수\n"
            "커버드콜 전략은 사용하지 않습니다."
        )

        self.assertIn("액티브", hints["detected_keywords"]["active_terms"])
        self.assertIn("FnGuide", hints["detected_keywords"]["index_provider_terms"])
        self.assertIn("AI", hints["detected_keywords"]["theme_terms"])
        self.assertIn("반도체", hints["detected_keywords"]["theme_terms"])
        self.assertIn("힌트", hints["note"])

    def test_summary_schemas_require_theme_classification(self) -> None:
        expected_statuses = ["theme", "mixed", "non_theme", "unknown"]
        expected_buckets = [
            "technology",
            "energy_materials",
            "healthcare",
            "industrial_defense",
            "consumer_demographic",
            "finance_income",
            "country_macro",
            "digital_asset",
            "none",
            "unknown",
        ]
        for schema_path in (SUMMARY_SCHEMA_PATH, CORRECTION_UPDATE_SCHEMA_PATH):
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            summary_schema = schema["properties"]["summary"] if schema_path == CORRECTION_UPDATE_SCHEMA_PATH else schema
            theme_schema = summary_schema["properties"]["theme_classification"]

            self.assertIn("theme_classification", summary_schema["required"])
            self.assertEqual(
                theme_schema["required"],
                ["theme_status", "theme_bucket", "structure_tags", "confidence", "evidence"],
            )
            self.assertEqual(theme_schema["properties"]["theme_status"]["enum"], expected_statuses)
            self.assertEqual(theme_schema["properties"]["theme_bucket"]["enum"], expected_buckets)

    def test_summary_schemas_require_market_exposure(self) -> None:
        for schema_path in (SUMMARY_SCHEMA_PATH, CORRECTION_UPDATE_SCHEMA_PATH):
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            summary_schema = schema["properties"]["summary"] if schema_path == CORRECTION_UPDATE_SCHEMA_PATH else schema
            market_exposure = summary_schema["properties"]["market_exposure"]

            self.assertIn("market_exposure", summary_schema["required"])
            self.assertEqual(market_exposure["required"], ["primary_country", "evidence"])
            self.assertEqual(
                market_exposure["properties"]["primary_country"]["enum"],
                ["KR", "US", "CN", "HK", "JP", "IN", "VN", "GLOBAL", "MIXED", "UNKNOWN"],
            )

    def test_holding_item_schemas_include_ticker_and_exchange(self) -> None:
        for schema_path in (SUMMARY_SCHEMA_PATH, CORRECTION_UPDATE_SCHEMA_PATH):
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            summary_schema = schema["properties"]["summary"] if schema_path == CORRECTION_UPDATE_SCHEMA_PATH else schema
            item_schema = summary_schema["properties"]["holdings"]["properties"]["items"]["items"]

            self.assertEqual(item_schema["required"], ["name", "ticker", "exchange", "weight"])
            self.assertEqual(item_schema["properties"]["ticker"]["type"], ["string", "null"])
            self.assertEqual(item_schema["properties"]["exchange"]["type"], ["string", "null"])

        external_schema = json.loads(EXTERNAL_RESEARCH_SCHEMA_PATH.read_text(encoding="utf-8"))
        item_schema = external_schema["properties"]["items"]["items"]
        self.assertEqual(item_schema["required"], ["name", "ticker", "exchange", "weight"])
        self.assertEqual(item_schema["properties"]["ticker"]["type"], ["string", "null"])
        self.assertEqual(item_schema["properties"]["exchange"]["type"], ["string", "null"])

    def test_normalizes_lightweight_summary_issues(self) -> None:
        summary, warnings = normalize_summary(
            {
                "keywords": None,
                "missing_info": None,
                "theme_classification": {
                    "theme_status": "non_theme",
                    "theme_bucket": "technology",
                    "structure_tags": None,
                    "confidence": 1.2,
                    "evidence": "test",
                },
                "holdings": {
                    "available_in_pdf": True,
                    "items": [{"name": "NVIDIA", "ticker": "", "exchange": " ", "weight": "10%"}],
                    "where_to_find_more": None,
                },
            }
        )

        self.assertEqual(summary["keywords"], [])
        self.assertEqual(summary["missing_info"], [])
        self.assertEqual(summary["theme_classification"]["theme_bucket"], "none")
        self.assertEqual(summary["theme_classification"]["structure_tags"], [])
        self.assertEqual(summary["theme_classification"]["confidence"], 1)
        self.assertIsNone(summary["holdings"]["items"][0]["ticker"])
        self.assertIsNone(summary["holdings"]["items"][0]["exchange"])
        self.assertIn("non_theme_bucket_normalized_to_none", warnings)
        self.assertIn("holding_ticker_blank_normalized_to_null", warnings)

    def test_normalizes_theme_bucket_none_for_theme(self) -> None:
        summary, warnings = normalize_summary(
            {
                "theme_classification": {
                    "theme_status": "theme",
                    "theme_bucket": "none",
                    "structure_tags": [],
                    "confidence": -0.1,
                    "evidence": "test",
                },
            }
        )

        self.assertEqual(summary["theme_classification"]["theme_bucket"], "unknown")
        self.assertEqual(summary["theme_classification"]["confidence"], 0)
        self.assertIn("theme_bucket_none_normalized_to_unknown", warnings)

    def test_reviews_correction_filing_with_schema_limited_codex_call(self) -> None:
        filing = {
            "rcept_no": "20260429000103",
            "rcept_dt": "20260429",
            "corp_name": "타임폴리오자산운용",
            "report_nm": "[기재정정]투자설명서(집합투자증권)",
        }

        with (
            patch(
                "scripts.pdf_langgraph.pdf_analysis_langgraph.fetch_dart_viewer_text",
                return_value="정정사항\n정정사유\n정 정 전\n정 정 후",
            ) as fetch_text,
            patch(
                "scripts.pdf_langgraph.pdf_analysis_langgraph.call_llm",
                return_value='{"needs_update": true, "reason": "투자전략 정정"}',
            ) as call_llm_mock,
        ):
            result = review_correction_filing(filing)

        fetch_text.assert_called_once_with("20260429000103")
        call_llm_mock.assert_called_once()
        self.assertEqual(call_llm_mock.call_args.kwargs["output_schema_path"], CORRECTION_REVIEW_SCHEMA_PATH)
        self.assertIn("타임폴리오자산운용", call_llm_mock.call_args.args[0])
        self.assertIn("정정사항", call_llm_mock.call_args.args[0])
        self.assertEqual(result, {"needs_update": True, "reason": "투자전략 정정"})

    def test_updates_record_from_correction_with_schema_limited_codex_call(self) -> None:
        existing_record = {
            "route": "pdf_holdings_available",
            "summary": {"fund_name": "기존 ETF", "keywords": ["기존"]},
            "research_prompt": "",
            "source": {"rcept_no": "20260429000001"},
            "first_rcept_dt": "20260401",
            "revision_count": 0,
        }
        filing = {
            "rcept_no": "20260429000103",
            "rcept_dt": "20260429",
            "corp_name": "타임폴리오자산운용",
            "report_nm": "[기재정정]투자설명서(집합투자증권)",
        }
        review = {"needs_update": True, "reason": "투자전략 변경"}

        with (
            patch(
                "scripts.pdf_langgraph.pdf_analysis_langgraph.fetch_dart_viewer_text",
                return_value="정정사항\n정정 전\n정정 후",
            ) as fetch_text,
            patch(
                "scripts.pdf_langgraph.pdf_analysis_langgraph.call_llm",
                return_value=(
                    '{"route":"correction_updated",'
                    '"summary":{"fund_name":"수정 ETF","keywords":null,'
                    '"theme_classification":{"theme_status":"theme","theme_bucket":"none",'
                    '"structure_tags":[],"confidence":0.5,"evidence":"test"}},'
                    '"research_prompt":""}'
                ),
            ) as call_llm_mock,
        ):
            result = update_record_from_correction(existing_record, filing, review)

        fetch_text.assert_called_once_with("20260429000103")
        call_llm_mock.assert_called_once()
        self.assertEqual(call_llm_mock.call_args.kwargs["output_schema_path"], CORRECTION_UPDATE_SCHEMA_PATH)
        self.assertIn("기존 ETF", call_llm_mock.call_args.args[0])
        self.assertIn("투자전략 변경", call_llm_mock.call_args.args[0])
        self.assertEqual(result["summary"]["fund_name"], "수정 ETF")
        self.assertEqual(result["summary"]["keywords"], [])
        self.assertEqual(result["summary"]["theme_classification"]["theme_bucket"], "unknown")
        self.assertIn("theme_bucket_none_normalized_to_unknown", result["validation_warnings"])


class LlmProviderTest(unittest.TestCase):
    def test_defaults_to_codex_provider(self) -> None:
        self.assertIsInstance(get_provider(), CodexProvider)

    def test_selects_openclaw_provider(self) -> None:
        with patch.dict("os.environ", {"ETF_LLM_PROVIDER": "openclaw"}):
            self.assertIsInstance(get_provider(), OpenClawProvider)

    def test_selects_chatgpt_oauth_provider(self) -> None:
        with patch.dict("os.environ", {"ETF_LLM_PROVIDER": "chatgpt_oauth"}):
            self.assertIsInstance(get_provider(), ChatGptOAuthProvider)

    def test_generate_json_uses_selected_provider(self) -> None:
        with patch("new_etf_insight.llm.get_provider") as get_provider_mock:
            provider = get_provider_mock.return_value
            provider.generate_json.return_value = '{"ok": true}'

            result = generate_json("prompt", output_schema_path=CORRECTION_REVIEW_SCHEMA_PATH, provider_name="openclaw")

        get_provider_mock.assert_called_once_with("openclaw")
        provider.generate_json.assert_called_once_with(
            "prompt",
            output_schema_path=CORRECTION_REVIEW_SCHEMA_PATH,
            search=False,
        )
        self.assertEqual(result, '{"ok": true}')

    def test_call_codex_alias_uses_generic_llm_call(self) -> None:
        with patch("scripts.pdf_langgraph.pdf_analysis_langgraph.call_llm", return_value='{"ok": true}') as call_llm_mock:
            result = call_codex("prompt", output_schema_path=CORRECTION_REVIEW_SCHEMA_PATH)

        call_llm_mock.assert_called_once_with(
            "prompt",
            search=False,
            output_schema_path=CORRECTION_REVIEW_SCHEMA_PATH,
        )
        self.assertEqual(result, '{"ok": true}')

    def test_call_llm_uses_provider_adapter(self) -> None:
        with patch("scripts.pdf_langgraph.pdf_analysis_langgraph.generate_json", return_value='{"ok": true}') as generate_mock:
            result = call_llm("prompt", output_schema_path=CORRECTION_REVIEW_SCHEMA_PATH, search=True)

        generate_mock.assert_called_once_with(
            "prompt",
            search=True,
            output_schema_path=CORRECTION_REVIEW_SCHEMA_PATH,
        )
        self.assertEqual(result, '{"ok": true}')

    def test_codex_provider_builds_existing_command(self) -> None:
        with (
            patch("new_etf_insight.llm.codex_provider.subprocess.run") as run_mock,
            patch("new_etf_insight.llm.codex_provider.tempfile.TemporaryDirectory") as tempdir_mock,
        ):
            tempdir_mock.return_value.__enter__.return_value = "/tmp/llm"
            Path("/tmp/llm").mkdir(parents=True, exist_ok=True)
            Path("/tmp/llm/codex_last_message.txt").write_text('{"ok": true}', encoding="utf-8")

            result = CodexProvider().generate_json(
                "prompt",
                output_schema_path=CORRECTION_REVIEW_SCHEMA_PATH,
                search=True,
        )

        command = run_mock.call_args.args[0]
        expected_codex = "codex.cmd" if os.name == "nt" else "codex"
        self.assertEqual(command[:2], [expected_codex, "--search"])
        self.assertIn("--output-schema", command)
        self.assertIn(str(CORRECTION_REVIEW_SCHEMA_PATH), command)
        self.assertEqual(command[-1], "-")
        self.assertEqual(run_mock.call_args.kwargs["input"], "prompt")
        self.assertEqual(result, '{"ok": true}')

    def test_openclaw_provider_requires_command(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENCLAW_LLM_COMMAND"):
                OpenClawProvider().generate_json("prompt", output_schema_path=CORRECTION_REVIEW_SCHEMA_PATH)

    def test_openclaw_provider_runs_configured_command_with_prompt_stdin(self) -> None:
        command_template = "openclaw llm --schema {output_schema_path} --out {output_path} --search {search}"
        with (
            patch.dict("os.environ", {"OPENCLAW_LLM_COMMAND": command_template}, clear=True),
            patch("new_etf_insight.llm.openclaw_provider.subprocess.run") as run_mock,
            patch("new_etf_insight.llm.openclaw_provider.tempfile.TemporaryDirectory") as tempdir_mock,
        ):
            tempdir_mock.return_value.__enter__.return_value = "/tmp/openclaw-llm"
            Path("/tmp/openclaw-llm").mkdir(parents=True, exist_ok=True)
            Path("/tmp/openclaw-llm/openclaw_last_message.txt").write_text('{"ok": true}', encoding="utf-8")

            result = OpenClawProvider().generate_json(
                "prompt",
                output_schema_path=CORRECTION_REVIEW_SCHEMA_PATH,
                search=True,
            )

        run_mock.assert_called_once_with(
            [
                "openclaw",
                "llm",
                "--schema",
                str(CORRECTION_REVIEW_SCHEMA_PATH),
                "--out",
                "/tmp/openclaw-llm/openclaw_last_message.txt",
                "--search",
                "true",
            ],
            check=True,
            input="prompt",
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result, '{"ok": true}')

    def test_chatgpt_oauth_provider_refreshes_and_calls_responses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            auth_path = Path(td) / "auth-profiles.json"
            auth_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "profiles": {
                            "openai:default": {
                                "type": "oauth",
                                "provider": "openai",
                                "refresh": "old-refresh",
                                "access": "old-access",
                                "expires": 0,
                                "accountId": "acct-test",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            class FakeResponse:
                def __init__(self, *, ok=True, status_code=200, payload=None, lines=None):
                    self.ok = ok
                    self.status_code = status_code
                    self._payload = payload or {}
                    self._lines = lines or []
                    self.text = json.dumps(self._payload)

                def json(self):
                    return self._payload

                def iter_lines(self, decode_unicode=True):
                    return iter(self._lines)

            calls = []

            def fake_post(url, **kwargs):
                calls.append((url, kwargs))
                if "oauth/token" in url:
                    return FakeResponse(
                        payload={
                            "access_token": "new-access",
                            "refresh_token": "new-refresh",
                            "expires_in": 3600,
                        }
                    )
                return FakeResponse(lines=[
                    'data: {"type":"response.output_text.delta","delta":"{\\"ok\\":true}"}',
                    "data: [DONE]",
                ])

            with (
                patch.dict("os.environ", {"ETF_AUTH_PROFILES_PATH": str(auth_path), "ETF_OPENAI_MODEL": "gpt-test"}, clear=True),
                patch("new_etf_insight.llm.chatgpt_oauth_provider.requests.post", side_effect=fake_post),
            ):
                result = ChatGptOAuthProvider().generate_json(
                    "prompt",
                    output_schema_path=CORRECTION_REVIEW_SCHEMA_PATH,
                    search=True,
                )

            self.assertEqual(result, '{"ok":true}')
            self.assertEqual(calls[0][1]["data"]["refresh_token"], "old-refresh")
            request_body = calls[1][1]["json"]
            self.assertEqual(request_body["model"], "gpt-test")
            self.assertEqual(request_body["text"]["format"]["type"], "json_schema")
            self.assertNotIn("tools", request_body)
            saved = json.loads(auth_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["profiles"]["openai:default"]["refresh"], "new-refresh")


class CollectEtfCandidatesScriptTest(unittest.TestCase):
    def test_should_download_pdf_only_for_stock_reports(self) -> None:
        self.assertTrue(should_download_pdf({"report_nm": "투자설명서(집합투자증권)(ETF(주식))"}))
        self.assertFalse(should_download_pdf({"report_nm": "일괄신고서(집합투자증권-신탁형)(ETF(채권혼합))"}))

    def test_download_candidate_pdfs_skips_non_stock_reports(self) -> None:
        candidates = [
            {"rcept_no": "20260429000012", "corp_name": "키움", "report_nm": "투자설명서(주식)"},
            {"rcept_no": "20260429000031", "corp_name": "한화", "report_nm": "일괄신고서(채권혼합)"},
        ]

        with patch("scripts.collect_etf_candidates.download_representative_prospectus_pdf") as download:
            download.return_value = Path("downloads/pdfs/20260429000012_1.pdf")
            result = download_candidate_pdfs(candidates, Path("downloads/pdfs"))

        download.assert_called_once_with("20260429000012", Path("downloads/pdfs"))
        self.assertEqual(
            result,
            [
                {
                    "rcept_no": "20260429000012",
                    "corp_name": "키움",
                    "report_nm": "투자설명서(주식)",
                    "pdf_path": "downloads/pdfs/20260429000012_1.pdf",
                }
            ],
        )


class DailyPipelineTest(unittest.TestCase):
    def test_period_run_uses_daily_output_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / "runs"

            with patch("new_etf_insight.daily_pipeline.run_daily_pipeline") as run_daily:
                run_daily.side_effect = [
                    {"begin": "20260429", "end": "20260429", "results": []},
                    {"begin": "20260430", "end": "20260430", "results": []},
                ]

                result = run_period_as_daily_runs("20260429", "20260430", runs_dir)

        self.assertEqual(run_daily.call_count, 2)
        self.assertEqual(
            run_daily.call_args_list[0].args,
            (
                "20260429",
                "20260429",
                runs_dir / "20260429" / "records",
                runs_dir / "20260429" / "pdfs",
            ),
        )
        self.assertEqual(
            run_daily.call_args_list[1].args,
            (
                "20260430",
                "20260430",
                runs_dir / "20260430" / "records",
                runs_dir / "20260430" / "pdfs",
            ),
        )
        self.assertEqual(result["begin"], "20260429")
        self.assertEqual(result["end"], "20260430")
        self.assertEqual(len(result["daily_results"]), 2)

    def test_creates_record_with_etf_key_path(self) -> None:
        candidates = [
            {
                "rcept_no": "20260429000012",
                "rcept_dt": "20260429",
                "corp_code": "00104500",
                "corp_name": "KB자산운용",
                "report_nm": "투자설명서(집합투자증권)(ETF(주식))",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_dir = base_dir / "records"
            pdf_dir = base_dir / "pdfs"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=candidates),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value="ET942"),
                patch(
                    "new_etf_insight.daily_pipeline.download_representative_prospectus_pdf",
                    return_value=pdf_dir / "20260429000012_1.pdf",
                ),
                patch(
                    "new_etf_insight.daily_pipeline.analyze_pdf",
                    return_value={"route": "pdf_holdings_available", "summary": {}, "research_prompt": ""},
                ) as analyze_pdf_mock,
            ):
                result = run_daily_pipeline("20260429", "20260429", records_dir, pdf_dir)
                record = json.loads((records_dir / "00104500_ET942.json").read_text(encoding="utf-8"))

        self.assertIsInstance(analyze_pdf_mock.call_args.kwargs["holding_identifier_resolver"], HoldingIdentifierResolver)
        self.assertEqual(result["results"][0]["action"], "created")
        self.assertEqual(result["results"][0]["etf_key"], "00104500_ET942")
        self.assertEqual(record["source"]["rcept_no"], "20260429000012")
        self.assertEqual(record["first_rcept_dt"], "20260429")
        self.assertEqual(record["revision_count"], 0)

    def test_skips_correction_when_review_says_no_update(self) -> None:
        candidates = [
            {
                "rcept_no": "20260429000103",
                "rcept_dt": "20260429",
                "corp_code": "00104500",
                "corp_name": "KB자산운용",
                "report_nm": "[기재정정]투자설명서(집합투자증권)(ETF(주식))",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_dir = base_dir / "records"
            records_dir.mkdir()
            record_path = records_dir / "00104500_ET942.json"
            record_path.write_text('{"source": {"rcept_no": "20260429000012"}}', encoding="utf-8")

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=candidates),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value="ET942"),
                patch(
                    "new_etf_insight.daily_pipeline.review_correction_filing",
                    return_value={"needs_update": False, "reason": "핵심 필드 변경 없음"},
                ),
                patch("new_etf_insight.daily_pipeline.analyze_pdf") as analyze_pdf_mock,
            ):
                result = run_daily_pipeline("20260429", "20260429", records_dir, base_dir / "pdfs")

        analyze_pdf_mock.assert_not_called()
        self.assertEqual(result["results"][0]["action"], "skipped")

    def test_skips_existing_non_correction_record_without_overwrite(self) -> None:
        candidates = [
            {
                "rcept_no": "20260429000012",
                "rcept_dt": "20260429",
                "corp_code": "00104500",
                "corp_name": "KB자산운용",
                "report_nm": "투자설명서(집합투자증권)(ETF(주식))",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_dir = base_dir / "records"
            records_dir.mkdir()
            record_path = records_dir / "00104500_ET942.json"
            original = {"source": {"rcept_no": "20260429000012"}, "summary": {"fund_name": "기존 ETF"}}
            record_path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=candidates),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value="ET942"),
                patch("new_etf_insight.daily_pipeline.download_representative_prospectus_pdf") as download_mock,
                patch("new_etf_insight.daily_pipeline.analyze_pdf") as analyze_pdf_mock,
            ):
                result = run_daily_pipeline("20260429", "20260429", records_dir, base_dir / "pdfs")
                record = json.loads(record_path.read_text(encoding="utf-8"))

        download_mock.assert_not_called()
        analyze_pdf_mock.assert_not_called()
        self.assertEqual(result["results"][0]["action"], "skipped")
        self.assertEqual(result["results"][0]["reason"], "existing_record")
        self.assertEqual(record, original)

    def test_skips_existing_same_correction_record_without_review(self) -> None:
        candidates = [
            {
                "rcept_no": "20260429000103",
                "rcept_dt": "20260429",
                "corp_code": "00104500",
                "corp_name": "KB자산운용",
                "report_nm": "[기재정정]투자설명서(집합투자증권)(ETF(주식))",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_dir = base_dir / "records"
            records_dir.mkdir()
            record_path = records_dir / "00104500_ET942.json"
            original = {"source": {"rcept_no": "20260429000103"}, "summary": {"fund_name": "기존 정정 ETF"}}
            record_path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=candidates),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value="ET942"),
                patch("new_etf_insight.daily_pipeline.review_correction_filing") as review_mock,
                patch("new_etf_insight.daily_pipeline.analyze_pdf") as analyze_pdf_mock,
            ):
                result = run_daily_pipeline("20260429", "20260429", records_dir, base_dir / "pdfs")
                record = json.loads(record_path.read_text(encoding="utf-8"))

        review_mock.assert_not_called()
        analyze_pdf_mock.assert_not_called()
        self.assertEqual(result["results"][0]["action"], "skipped")
        self.assertEqual(result["results"][0]["reason"], "existing_record")
        self.assertEqual(record, original)

    def test_skips_correction_without_existing_record(self) -> None:
        candidates = [
            {
                "rcept_no": "20260429000103",
                "rcept_dt": "20260429",
                "corp_code": "00104500",
                "corp_name": "KB자산운용",
                "report_nm": "[기재정정]투자설명서(집합투자증권)(ETF(주식))",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_dir = base_dir / "records"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=candidates),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value="ET942"),
                patch("new_etf_insight.daily_pipeline.review_correction_filing") as review_mock,
                patch("new_etf_insight.daily_pipeline.download_representative_prospectus_pdf") as download_mock,
                patch("new_etf_insight.daily_pipeline.analyze_pdf") as analyze_pdf_mock,
            ):
                result = run_daily_pipeline("20260429", "20260429", records_dir, base_dir / "pdfs")

        review_mock.assert_not_called()
        download_mock.assert_not_called()
        analyze_pdf_mock.assert_not_called()
        self.assertEqual(result["results"][0]["action"], "skipped")
        self.assertEqual(result["results"][0]["reason"], "correction_without_existing_record")

    def test_continues_after_pdf_analysis_failure(self) -> None:
        candidates = [
            {
                "rcept_no": "20260429000012",
                "rcept_dt": "20260429",
                "corp_code": "00104500",
                "corp_name": "KB자산운용",
                "report_nm": "투자설명서(집합투자증권)(ETF(주식))",
            },
            {
                "rcept_no": "20260429000103",
                "rcept_dt": "20260429",
                "corp_code": "00104500",
                "corp_name": "KB자산운용",
                "report_nm": "[기재정정]투자설명서(집합투자증권)(ETF(주식))",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_dir = base_dir / "records"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=candidates),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value="ET942"),
                patch("new_etf_insight.daily_pipeline.download_representative_prospectus_pdf", side_effect=ValueError("PDF link missing")),
                patch("new_etf_insight.daily_pipeline.analyze_pdf") as analyze_pdf_mock,
            ):
                result = run_daily_pipeline("20260429", "20260429", records_dir, base_dir / "pdfs")

        analyze_pdf_mock.assert_not_called()
        self.assertEqual(result["results"][0]["action"], "failed")
        self.assertEqual(result["results"][0]["reason"], "pdf_analysis_failed")
        self.assertEqual(result["results"][0]["error_type"], "ValueError")
        self.assertEqual(result["results"][1]["action"], "skipped")
        self.assertEqual(result["results"][1]["reason"], "correction_without_existing_record")

    def test_skips_correction_at_least_60_days_after_first_rcept(self) -> None:
        candidates = [
            {
                "rcept_no": "20260531000103",
                "rcept_dt": "20260531",
                "corp_code": "00104500",
                "corp_name": "KB자산운용",
                "report_nm": "[기재정정]투자설명서(집합투자증권)(ETF(주식))",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_dir = base_dir / "records"
            records_dir.mkdir()
            record_path = records_dir / "00104500_ET942.json"
            original = {
                "source": {"rcept_no": "20260401000012"},
                "first_rcept_dt": "20260401",
                "summary": {"fund_name": "기존 ETF"},
            }
            record_path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=candidates),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value="ET942"),
                patch("new_etf_insight.daily_pipeline.review_correction_filing") as review_mock,
                patch("new_etf_insight.daily_pipeline.update_record_from_correction") as update_record_mock,
                patch("new_etf_insight.daily_pipeline.analyze_pdf") as analyze_pdf_mock,
            ):
                result = run_daily_pipeline("20260531", "20260531", records_dir, base_dir / "pdfs")
                record = json.loads(record_path.read_text(encoding="utf-8"))

        review_mock.assert_not_called()
        update_record_mock.assert_not_called()
        analyze_pdf_mock.assert_not_called()
        self.assertEqual(result["results"][0]["action"], "skipped")
        self.assertEqual(result["results"][0]["reason"], "correction_after_60_days")
        self.assertEqual(record, original)

    def test_updates_correction_without_pdf_reanalysis(self) -> None:
        candidates = [
            {
                "rcept_no": "20260429000103",
                "rcept_dt": "20260429",
                "corp_code": "00104500",
                "corp_name": "KB자산운용",
                "report_nm": "[기재정정]투자설명서(집합투자증권)(ETF(주식))",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_dir = base_dir / "records"
            records_dir.mkdir()
            record_path = records_dir / "00104500_ET942.json"
            record_path.write_text(
                json.dumps(
                    {
                        "route": "pdf_holdings_available",
                        "summary": {"fund_name": "기존 ETF"},
                        "research_prompt": "",
                        "source": {"rcept_no": "20260429000012"},
                        "first_rcept_dt": "20260412",
                        "revision_count": 0,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=candidates),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value="ET942"),
                patch(
                    "new_etf_insight.daily_pipeline.review_correction_filing",
                    return_value={"needs_update": True, "reason": "투자전략 변경"},
                ),
                patch(
                    "new_etf_insight.daily_pipeline.update_record_from_correction",
                    return_value={"route": "correction_updated", "summary": {"fund_name": "수정 ETF"}, "research_prompt": ""},
                ) as update_record_mock,
                patch("new_etf_insight.daily_pipeline.download_representative_prospectus_pdf") as download_mock,
                patch("new_etf_insight.daily_pipeline.analyze_pdf") as analyze_pdf_mock,
            ):
                result = run_daily_pipeline("20260429", "20260429", records_dir, base_dir / "pdfs")
                record = json.loads(record_path.read_text(encoding="utf-8"))

        update_record_mock.assert_called_once()
        download_mock.assert_not_called()
        analyze_pdf_mock.assert_not_called()
        self.assertEqual(result["results"][0]["action"], "updated")
        self.assertEqual(record["summary"]["fund_name"], "수정 ETF")
        self.assertEqual(record["first_rcept_dt"], "20260412")
        self.assertEqual(record["revision_count"], 1)
        self.assertEqual(record["source"]["rcept_no"], "20260429000103")


if __name__ == "__main__":
    unittest.main()

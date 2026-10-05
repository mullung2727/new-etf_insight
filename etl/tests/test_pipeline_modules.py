import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.collect_etf_candidates import download_candidate_pdfs, should_download_pdf
from scripts.pdf_langgraph.pdf_analysis_langgraph import (
    CORRECTION_REVIEW_SCHEMA_PATH,
    CORRECTION_UPDATE_SCHEMA_PATH,
    EXTERNAL_RESEARCH_SCHEMA_PATH,
    SUMMARY_SCHEMA_PATH,
    build_external_research_source_context,
    discover_fnindex_holdings,
    discover_official_holding_sources,
    call_codex,
    call_llm,
    enrich_missing_holding_identifiers,
    extract_classification_hints,
    normalize_summary,
    prepare_external_research,
    research_external_holdings,
    review_correction_filing,
    route_holdings,
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
from new_etf_insight.llm.codex_provider import CodexProvider




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




    def test_routes_weightless_pdf_holdings_to_external_research(self) -> None:
        route = route_holdings(
            {
                "summary": {
                    "holdings": {
                        "available_in_pdf": True,
                        "items": [
                            {"name": "Applied Optoelectronics Inc", "ticker": "AAOI", "exchange": "NASDAQ", "weight": None}
                        ],
                    }
                }
            }
        )

        self.assertEqual(route, "prepare_external_research")

    def test_routes_weighted_pdf_holdings_to_finalize(self) -> None:
        route = route_holdings(
            {
                "summary": {
                    "holdings": {
                        "available_in_pdf": True,
                        "items": [
                            {"name": "삼성전자", "ticker": "005930", "exchange": "KOSPI", "weight": "10"}
                        ],
                    }
                }
            }
        )

        self.assertEqual(route, "finalize_pdf_result")

    def test_skips_fnindex_discovery_for_non_fnguide_provider(self) -> None:
        class FailingSession:
            def get(self, url, **kwargs):
                raise AssertionError("FnIndex should not be queried for non-FnGuide indices")

        result = discover_fnindex_holdings(
            {
                "fund_name": "HANARO 미국 AI 광통신 TOP10",
                "asset_manager": "NH-Amundi 자산운용",
                "index": {"name": "INDXX US AI Optical Communication Top10 Index", "provider": "INDXX"},
            },
            session=FailingSession(),
        )

        self.assertIsNone(result)
    def test_discovers_fnindex_holdings_from_scored_index_candidate(self) -> None:
        class FakeResponse:
            def __init__(self, payload=None, text=""):
                self._payload = payload
                self.text = text

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class FakeSession:
            def get(self, url, **kwargs):
                return FakeResponse(
                    text='{"value":"FI00.WLT.KB5","label":"FnGuide 네트워크 인프라 지수"}'
                )

            def post(self, url, **kwargs):
                self.post_kwargs = kwargs
                return FakeResponse(
                    payload=[
                        {"CMP_CD": "A000660", "CMP_NM": "SK하이닉스", "WE": 23.46},
                        {"CMP_CD": "A005930", "CMP_NM": "삼성전자", "WE": 22.17},
                    ]
                )

        result = discover_fnindex_holdings(
            {
                "fund_name": "KoAct 광통신&위성네트워크",
                "asset_manager": "삼성액티브자산운용",
                "index": {"name": "FnGuide 네트워크 인프라 지수", "provider": "FnGuide"},
            },
            session=FakeSession(),
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["index_code"], "FI00.WLT.KB5")
        self.assertEqual(result["source_url"], "https://www.fnindex.co.kr/overview/detail/I/FI00.WLT.KB5")
        self.assertEqual(result["items"][0], {"name": "SK하이닉스", "ticker": "000660", "exchange": None, "weight": 23.46})

    def test_official_source_node_fills_holdings_without_llm_research(self) -> None:
        state = {
            "summary": {
                "fund_name": "KoAct 광통신&위성네트워크",
                "asset_manager": "삼성액티브자산운용",
                "index": {"name": "FnGuide 네트워크 인프라 지수", "provider": "FnGuide"},
                "market_exposure": {"primary_country": "KR"},
                "holdings": {"available_in_pdf": False, "items": [], "where_to_find_more": []},
                "missing_info": [],
            },
            "validation_warnings": [],
            "official_source_candidates": [],
            "route": "external_research_required",
        }

        with patch(
            "scripts.pdf_langgraph.pdf_analysis_langgraph.discover_fnindex_holdings",
            return_value={
                "holdings_found": True,
                "weights_found": True,
                "source_url": "https://www.fnindex.co.kr/overview/detail/I/FI00.WLT.KB5",
                "source_name": "FnGuide 네트워크 인프라 지수",
                "source_type": "index_page",
                "as_of_date": None,
                "items": [{"name": "SK하이닉스", "ticker": "000660", "exchange": None, "weight": 23.46}],
                "missing_info": [],
                "provider": "FnIndex",
                "index_code": "FI00.WLT.KB5",
            },
        ):
            result = discover_official_holding_sources(state)

        self.assertEqual(result["route"], "external_research_completed")
        self.assertEqual(result["summary"]["holdings"]["items"][0]["name"], "SK하이닉스")
        self.assertEqual(result["official_source_candidates"][0]["index_code"], "FI00.WLT.KB5")
    def test_external_research_prompt_runs_without_where_to_find_more(self) -> None:
        state = {
            "summary": {
                "fund_name": "KoAct 테스트 ETF",
                "asset_manager": "테스트운용",
                "index": {"name": "테스트 지수", "provider": "테스트 산출기관"},
                "holdings": {"available_in_pdf": False, "items": [], "where_to_find_more": []},
                "missing_info": [],
            },
            "source": {
                "rcept_no": "20260706000006",
                "rcept_dt": "20260706",
                "corp_name": "테스트운용",
                "report_nm": "투자설명서",
                "fund_code": "EV488",
            },
        }

        result = prepare_external_research(state)

        self.assertEqual(result["route"], "external_research_required")
        self.assertIn("KoAct 테스트 ETF", result["research_prompt"])
        self.assertIn("DART 공시: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260706000006", result["research_prompt"])
        self.assertIn("PDF에서 구체 출처를 제시하지 못함", result["research_prompt"])
        self.assertNotIn("외부 검색 단서 부족", result["summary"].get("missing_info", []))

    def test_builds_external_research_source_context(self) -> None:
        context = build_external_research_source_context(
            {
                "rcept_no": "20260706000006",
                "rcept_dt": "20260706",
                "corp_name": "삼성액티브자산운용",
                "report_nm": "투자설명서",
                "fund_code": "EV488",
            }
        )

        self.assertIn("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260706000006", context)
        self.assertIn("펀드코드: EV488", context)

    def test_empty_external_research_keeps_pdf_holdings(self) -> None:
        pdf_items = [
            {"name": "Applied Optoelectronics Inc", "ticker": "AAOI", "exchange": "NASDAQ", "weight": None}
        ]
        state = {
            "summary": {"holdings": {"available_in_pdf": True, "items": pdf_items}, "missing_info": []},
            "validation_warnings": [],
            "research_prompt": "find holdings",
            "route": "external_research_required",
        }

        with patch(
            "scripts.pdf_langgraph.pdf_analysis_langgraph.call_llm",
            return_value='{"source_name": null, "source_url": null, "as_of_date": null, "items": []}',
        ):
            result = research_external_holdings(state)

        self.assertEqual(result["route"], "external_research_completed")
        self.assertEqual(result["summary"]["holdings"]["items"], pdf_items)

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

    def test_generate_json_uses_selected_provider(self) -> None:
        with patch("new_etf_insight.llm.get_provider") as get_provider_mock:
            provider = get_provider_mock.return_value
            provider.generate_json.return_value = '{"ok": true}'

            result = generate_json("prompt", output_schema_path=CORRECTION_REVIEW_SCHEMA_PATH, provider_name="codex")

        get_provider_mock.assert_called_once_with("codex")
        provider.generate_json.assert_called_once_with(
            "prompt",
            output_schema_path=CORRECTION_REVIEW_SCHEMA_PATH,
            search=False,
            model=None,
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
            run_mock.return_value.returncode = 0
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
        # codex stdout 을 잡지 않으면 부모 stdout 으로 새어 보고문을 오염시킨다.
        self.assertTrue(run_mock.call_args.kwargs["capture_output"])

    def test_codex_provider_failure_raises_without_writing_to_stdout(self) -> None:
        buffer = io.StringIO()
        with (
            patch("new_etf_insight.llm.codex_provider.subprocess.run") as run_mock,
            redirect_stdout(buffer),
        ):
            run_mock.return_value.returncode = 1
            run_mock.return_value.stdout = "codex noise"
            run_mock.return_value.stderr = "boom"

            with self.assertRaises(subprocess.CalledProcessError):
                CodexProvider().generate_json(
                    "prompt", output_schema_path=CORRECTION_REVIEW_SCHEMA_PATH
                )

        self.assertEqual(buffer.getvalue(), "")


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
            records_dir = base_dir / "runs" / "20260429" / "records"
            pdf_dir = base_dir / "runs" / "20260429" / "pdfs"

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
            records_dir = base_dir / "runs" / "20260429" / "records"
            records_dir.mkdir(parents=True)
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
            records_dir = base_dir / "runs" / "20260429" / "records"
            records_dir.mkdir(parents=True)
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
            records_dir = base_dir / "runs" / "20260429" / "records"
            records_dir.mkdir(parents=True)
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
            records_dir = base_dir / "runs" / "20260429" / "records"

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
            records_dir = base_dir / "runs" / "20260429" / "records"

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
            records_dir = base_dir / "runs" / "20260531" / "records"
            records_dir.mkdir(parents=True)
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
            records_dir = base_dir / "runs" / "20260429" / "records"
            records_dir.mkdir(parents=True)
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


class FilingHistoryPreservationTest(unittest.TestCase):
    def _history_rows(self, db_path):
        import sqlite3

        con = sqlite3.connect(str(db_path))
        try:
            return con.execute(
                "SELECT etf_key, rcept_no, action, reason, first_collected_at, record_json IS NOT NULL "
                "FROM etf_filing_history ORDER BY rcept_no"
            ).fetchall()
        finally:
            con.close()

    def _history_full_rows(self, db_path):
        import sqlite3

        con = sqlite3.connect(str(db_path))
        try:
            return con.execute(
                "SELECT etf_key, rcept_no, rcept_dt, first_collected_at, action, reason, filing_json, record_json "
                "FROM etf_filing_history ORDER BY rcept_no"
            ).fetchall()
        finally:
            con.close()

    def _record_fund_name(self, db_path, etf_key):
        import sqlite3

        con = sqlite3.connect(str(db_path))
        try:
            row = con.execute(
                "SELECT fund_name FROM etf_records WHERE etf_key = ?", [etf_key]
            ).fetchone()
        finally:
            con.close()
        return row[0] if row else None

    def _write_day_record(self, base_dir, date_text, etf_key, record):
        records_dir = base_dir / "runs" / date_text / "records"
        records_dir.mkdir(parents=True, exist_ok=True)
        (records_dir / f"{etf_key}.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )

    def test_cross_day_correction_preserves_first_and_two_snapshots(self) -> None:
        corp, fund, etf_key = "00999001", "ZZ901", "00999001_ZZ901"
        original_no, correction_no = "20260429000001", "20260430000002"
        first_time = "2026-04-29T00:00:00+00:00"
        candidates = [
            {
                "rcept_no": correction_no,
                "rcept_dt": "20260430",
                "corp_code": corp,
                "corp_name": "테스트운용",
                "report_nm": "[기재정정]투자설명서(집합투자증권)(ETF(주식))",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            self._write_day_record(
                base_dir,
                "20260429",
                etf_key,
                {
                    "route": "pdf_holdings_available",
                    "summary": {"fund_name": "원본 ETF"},
                    "research_prompt": "",
                    "source": {
                        "rcept_no": original_no, "rcept_dt": "20260429", "corp_code": corp,
                        "corp_name": "테스트운용", "report_nm": "투자설명서",
                        "fund_code": fund, "etf_key": etf_key, "pdf_path": "x.pdf",
                    },
                    "first_rcept_no": original_no,
                    "first_rcept_dt": "20260429",
                    "first_collected_at": first_time,
                    "collected_at": first_time,
                    "revision_count": 0,
                },
            )
            records_dir = base_dir / "runs" / "20260430" / "records"
            pdf_dir = base_dir / "runs" / "20260430" / "pdfs"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=candidates),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.review_correction_filing",
                    return_value={"needs_update": True, "reason": "보수율 정정"},
                ),
                patch(
                    "new_etf_insight.daily_pipeline.update_record_from_correction",
                    return_value={
                        "route": "correction_updated",
                        "summary": {"fund_name": "정정 ETF"},
                        "research_prompt": "",
                    },
                ),
            ):
                result = run_daily_pipeline("20260430", "20260430", records_dir, pdf_dir)
                updated = json.loads((records_dir / f"{etf_key}.json").read_text(encoding="utf-8"))
                rows = self._history_rows(base_dir / "db" / "etf_insight.sqlite3")

        self.assertEqual(result["results"][0]["action"], "updated")
        self.assertEqual(updated["summary"]["fund_name"], "정정 ETF")
        self.assertEqual(updated["first_rcept_no"], original_no)
        self.assertEqual(updated["first_rcept_dt"], "20260429")
        self.assertEqual(updated["first_collected_at"], first_time)
        self.assertEqual(updated["revision_count"], 1)
        self.assertEqual(updated["source"]["rcept_no"], correction_no)
        self.assertEqual(len(rows), 2)
        self.assertEqual([row[1] for row in rows], [original_no, correction_no])
        self.assertTrue(all(row[5] for row in rows))
        self.assertEqual(rows[0][2], "created")
        self.assertEqual(rows[0][4], first_time)
        self.assertEqual(rows[1][2], "updated")
        self.assertEqual(rows[1][3], "보수율 정정")
        self.assertEqual(rows[1][4], updated["collected_at"])

    def test_same_day_reversed_candidates_process_in_filing_order(self) -> None:
        corp, fund, etf_key = "00999002", "ZZ902", "00999002_ZZ902"
        nos = ["20260429000011", "20260429000012", "20260429000013"]
        candidates = [
            {
                "rcept_no": nos[2], "rcept_dt": "20260429", "corp_code": corp,
                "corp_name": "T", "report_nm": "[기재정정]투자설명서(ETF(주식))",
            },
            {
                "rcept_no": nos[1], "rcept_dt": "20260429", "corp_code": corp,
                "corp_name": "T", "report_nm": "[기재정정]투자설명서(ETF(주식))",
            },
            {
                "rcept_no": nos[0], "rcept_dt": "20260429", "corp_code": corp,
                "corp_name": "T", "report_nm": "투자설명서(ETF(주식))",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_dir = base_dir / "runs" / "20260429" / "records"
            pdf_dir = base_dir / "runs" / "20260429" / "pdfs"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=candidates),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.download_representative_prospectus_pdf",
                    return_value=pdf_dir / "x.pdf",
                ),
                patch(
                    "new_etf_insight.daily_pipeline.analyze_pdf",
                    return_value={
                        "route": "pdf_holdings_available",
                        "summary": {"fund_name": "v0"},
                        "research_prompt": "",
                    },
                ),
                patch(
                    "new_etf_insight.daily_pipeline.review_correction_filing",
                    return_value={"needs_update": True, "reason": "정정"},
                ),
                patch(
                    "new_etf_insight.daily_pipeline.update_record_from_correction",
                    side_effect=[
                        {"route": "correction_updated", "summary": {"fund_name": "v1"}, "research_prompt": ""},
                        {"route": "correction_updated", "summary": {"fund_name": "v2"}, "research_prompt": ""},
                    ],
                ),
            ):
                result = run_daily_pipeline("20260429", "20260429", records_dir, pdf_dir)
                final = json.loads((records_dir / f"{etf_key}.json").read_text(encoding="utf-8"))
                rows = self._history_rows(base_dir / "db" / "etf_insight.sqlite3")

        self.assertEqual([item["action"] for item in result["results"]], ["created", "updated", "updated"])
        self.assertEqual([item["rcept_no"] for item in result["results"]], nos)
        self.assertEqual(final["summary"]["fund_name"], "v2")
        self.assertEqual(final["revision_count"], 2)
        self.assertEqual(final["first_rcept_no"], nos[0])
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row[5] for row in rows))
        self.assertEqual(final["first_collected_at"], rows[0][4])

    def test_same_rcept_no_rerun_skips_without_llm_and_preserves_history(self) -> None:
        corp, fund, etf_key = "00999003", "ZZ903", "00999003_ZZ903"
        candidate = {
            "rcept_no": "20260429000021", "rcept_dt": "20260429", "corp_code": corp,
            "corp_name": "T", "report_nm": "투자설명서(ETF(주식))",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_dir = base_dir / "runs" / "20260429" / "records"
            pdf_dir = base_dir / "runs" / "20260429" / "pdfs"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.download_representative_prospectus_pdf",
                    return_value=pdf_dir / "x.pdf",
                ),
                patch(
                    "new_etf_insight.daily_pipeline.analyze_pdf",
                    return_value={
                        "route": "pdf_holdings_available",
                        "summary": {"fund_name": "v0"},
                        "research_prompt": "",
                    },
                ),
            ):
                first = run_daily_pipeline("20260429", "20260429", records_dir, pdf_dir)
            before_json = (records_dir / f"{etf_key}.json").read_text(encoding="utf-8")
            before_rows = self._history_full_rows(base_dir / "db" / "etf_insight.sqlite3")

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch("new_etf_insight.daily_pipeline.download_representative_prospectus_pdf") as download_mock,
                patch("new_etf_insight.daily_pipeline.analyze_pdf") as analyze_mock,
                patch("new_etf_insight.daily_pipeline.review_correction_filing") as review_mock,
                patch("new_etf_insight.daily_pipeline.update_record_from_correction") as update_mock,
            ):
                second = run_daily_pipeline("20260429", "20260429", records_dir, pdf_dir)
            after_json = (records_dir / f"{etf_key}.json").read_text(encoding="utf-8")
            after_rows = self._history_full_rows(base_dir / "db" / "etf_insight.sqlite3")

        self.assertEqual(first["results"][0]["action"], "created")
        self.assertEqual(second["results"][0]["action"], "skipped")
        self.assertEqual(second["results"][0]["reason"], "existing_record")
        download_mock.assert_not_called()
        analyze_mock.assert_not_called()
        review_mock.assert_not_called()
        update_mock.assert_not_called()
        self.assertEqual(after_json, before_json)
        self.assertEqual(after_rows, before_rows)

    def test_skipped_no_update_correction_leaves_observation_history(self) -> None:
        corp, fund, etf_key = "00999004", "ZZ904", "00999004_ZZ904"
        original_no, correction_no = "20260429000031", "20260429000032"
        candidates = [
            {
                "rcept_no": correction_no, "rcept_dt": "20260429", "corp_code": corp,
                "corp_name": "T", "report_nm": "[기재정정]투자설명서(ETF(주식))",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            self._write_day_record(
                base_dir,
                "20260429",
                etf_key,
                {
                    "route": "pdf_holdings_available",
                    "summary": {"fund_name": "원본 ETF"},
                    "research_prompt": "",
                    "source": {
                        "rcept_no": original_no, "rcept_dt": "20260429", "corp_code": corp,
                        "corp_name": "T", "report_nm": "투자설명서",
                        "fund_code": fund, "etf_key": etf_key, "pdf_path": "x.pdf",
                    },
                    "first_rcept_dt": "20260429",
                    "revision_count": 0,
                },
            )
            records_dir = base_dir / "runs" / "20260429" / "records"
            before_json = (records_dir / f"{etf_key}.json").read_text(encoding="utf-8")

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=candidates),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.review_correction_filing",
                    return_value={"needs_update": False, "reason": "오타 정정"},
                ),
                patch("new_etf_insight.daily_pipeline.update_record_from_correction") as update_mock,
            ):
                result = run_daily_pipeline(
                    "20260429", "20260429", records_dir, base_dir / "runs" / "20260429" / "pdfs"
                )
            after_json = (records_dir / f"{etf_key}.json").read_text(encoding="utf-8")
            rows = self._history_rows(base_dir / "db" / "etf_insight.sqlite3")
            fund_name = self._record_fund_name(base_dir / "db" / "etf_insight.sqlite3", etf_key)

        update_mock.assert_not_called()
        self.assertEqual(result["results"][0]["action"], "skipped")
        self.assertEqual(result["results"][0]["reason"], "오타 정정")
        self.assertEqual(after_json, before_json)
        self.assertEqual(fund_name, "원본 ETF")
        self.assertEqual(len(rows), 2)
        skipped = [row for row in rows if row[1] == correction_no][0]
        self.assertEqual(skipped[2], "skipped")
        self.assertEqual(skipped[3], "오타 정정")
        self.assertFalse(skipped[5])

    def test_skipped_60_day_correction_leaves_observation_history(self) -> None:
        corp, fund, etf_key = "00999005", "ZZ905", "00999005_ZZ905"
        original_no, correction_no = "20260401000041", "20260531000042"
        candidates = [
            {
                "rcept_no": correction_no, "rcept_dt": "20260531", "corp_code": corp,
                "corp_name": "T", "report_nm": "[기재정정]투자설명서(ETF(주식))",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            self._write_day_record(
                base_dir,
                "20260401",
                etf_key,
                {
                    "route": "pdf_holdings_available",
                    "summary": {"fund_name": "원본 ETF"},
                    "research_prompt": "",
                    "source": {
                        "rcept_no": original_no, "rcept_dt": "20260401", "corp_code": corp,
                        "corp_name": "T", "report_nm": "투자설명서",
                        "fund_code": fund, "etf_key": etf_key, "pdf_path": "x.pdf",
                    },
                    "first_rcept_dt": "20260401",
                    "revision_count": 0,
                },
            )
            records_dir = base_dir / "runs" / "20260531" / "records"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=candidates),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch("new_etf_insight.daily_pipeline.review_correction_filing") as review_mock,
            ):
                result = run_daily_pipeline(
                    "20260531", "20260531", records_dir, base_dir / "runs" / "20260531" / "pdfs"
                )
            rows = self._history_rows(base_dir / "db" / "etf_insight.sqlite3")

        review_mock.assert_not_called()
        self.assertEqual(result["results"][0]["action"], "skipped")
        self.assertEqual(result["results"][0]["reason"], "correction_after_60_days")
        self.assertFalse((records_dir / f"{etf_key}.json").exists())
        self.assertEqual(len(rows), 2)
        skipped = [row for row in rows if row[1] == correction_no][0]
        self.assertEqual(skipped[2], "skipped")
        self.assertFalse(skipped[5])

    def test_skipped_correction_without_record_leaves_observation_history(self) -> None:
        corp, fund, etf_key = "00999006", "ZZ906", "00999006_ZZ906"
        correction_no = "20260429000051"
        candidates = [
            {
                "rcept_no": correction_no, "rcept_dt": "20260429", "corp_code": corp,
                "corp_name": "T", "report_nm": "[기재정정]투자설명서(ETF(주식))",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_dir = base_dir / "runs" / "20260429" / "records"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=candidates),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch("new_etf_insight.daily_pipeline.review_correction_filing") as review_mock,
            ):
                result = run_daily_pipeline(
                    "20260429", "20260429", records_dir, base_dir / "runs" / "20260429" / "pdfs"
                )
            rows = self._history_rows(base_dir / "db" / "etf_insight.sqlite3")

        review_mock.assert_not_called()
        self.assertEqual(result["results"][0]["action"], "skipped")
        self.assertEqual(result["results"][0]["reason"], "correction_without_existing_record")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], correction_no)
        self.assertEqual(rows[0][2], "skipped")
        self.assertFalse(rows[0][5])

    def test_stale_filing_does_not_regress_latest(self) -> None:
        corp, fund, etf_key = "00999007", "ZZ907", "00999007_ZZ907"
        old_no, latest_no, stale_no = "20260429000061", "20260430000062", "20260429000069"
        candidates = [
            {
                "rcept_no": stale_no, "rcept_dt": "20260429", "corp_code": corp,
                "corp_name": "T", "report_nm": "[기재정정]투자설명서(ETF(주식))",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            self._write_day_record(
                base_dir,
                "20260429",
                etf_key,
                {
                    "route": "pdf_holdings_available",
                    "summary": {"fund_name": "v0"},
                    "research_prompt": "",
                    "source": {
                        "rcept_no": old_no, "rcept_dt": "20260429", "corp_code": corp,
                        "corp_name": "T", "report_nm": "투자설명서",
                        "fund_code": fund, "etf_key": etf_key, "pdf_path": "x.pdf",
                    },
                    "first_rcept_dt": "20260429",
                    "revision_count": 0,
                },
            )
            self._write_day_record(
                base_dir,
                "20260430",
                etf_key,
                {
                    "route": "correction_updated",
                    "summary": {"fund_name": "v1"},
                    "research_prompt": "",
                    "source": {
                        "rcept_no": latest_no, "rcept_dt": "20260430", "corp_code": corp,
                        "corp_name": "T", "report_nm": "[기재정정]투자설명서",
                        "fund_code": fund, "etf_key": etf_key, "pdf_path": "x.pdf",
                    },
                    "first_rcept_dt": "20260429",
                    "revision_count": 1,
                },
            )
            records_dir = base_dir / "runs" / "20260430" / "records"
            before_json = (records_dir / f"{etf_key}.json").read_text(encoding="utf-8")

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=candidates),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch("new_etf_insight.daily_pipeline.review_correction_filing") as review_mock,
                patch("new_etf_insight.daily_pipeline.update_record_from_correction") as update_mock,
            ):
                result = run_daily_pipeline("20260430", "20260430", records_dir, base_dir / "runs" / "20260430" / "pdfs")
            after_json = (records_dir / f"{etf_key}.json").read_text(encoding="utf-8")
            rows = self._history_rows(base_dir / "db" / "etf_insight.sqlite3")
            fund_name = self._record_fund_name(base_dir / "db" / "etf_insight.sqlite3", etf_key)

        review_mock.assert_not_called()
        update_mock.assert_not_called()
        self.assertEqual(result["results"][0]["action"], "skipped")
        self.assertEqual(result["results"][0]["reason"], "stale_filing")
        self.assertEqual(after_json, before_json)
        self.assertEqual(fund_name, "v1")
        stale = [row for row in rows if row[1] == stale_no][0]
        self.assertEqual(stale[2], "skipped")
        self.assertFalse(stale[5])

    def test_failed_then_success_reuses_same_history_row(self) -> None:
        corp, fund, etf_key = "00999008", "ZZ908", "00999008_ZZ908"
        candidate = {
            "rcept_no": "20260429000071", "rcept_dt": "20260429", "corp_code": corp,
            "corp_name": "T", "report_nm": "투자설명서(ETF(주식))",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_dir = base_dir / "runs" / "20260429" / "records"
            pdf_dir = base_dir / "runs" / "20260429" / "pdfs"
            db_path = base_dir / "db" / "etf_insight.sqlite3"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.download_representative_prospectus_pdf",
                    side_effect=ValueError("PDF link missing"),
                ),
                patch("new_etf_insight.daily_pipeline.analyze_pdf"),
            ):
                first = run_daily_pipeline("20260429", "20260429", records_dir, pdf_dir)
            failed_rows = self._history_full_rows(db_path)
            self.assertFalse((records_dir / f"{etf_key}.json").exists())

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.download_representative_prospectus_pdf",
                    return_value=pdf_dir / "x.pdf",
                ),
                patch(
                    "new_etf_insight.daily_pipeline.analyze_pdf",
                    return_value={
                        "route": "pdf_holdings_available",
                        "summary": {"fund_name": "v0"},
                        "research_prompt": "",
                    },
                ),
            ):
                second = run_daily_pipeline("20260429", "20260429", records_dir, pdf_dir)
            success_rows = self._history_full_rows(db_path)
            record = json.loads((records_dir / f"{etf_key}.json").read_text(encoding="utf-8"))

        self.assertEqual(first["results"][0]["action"], "failed")
        self.assertEqual(len(failed_rows), 1)
        self.assertEqual(failed_rows[0][4], "failed")
        first_time = failed_rows[0][3]
        self.assertIsNotNone(first_time)
        self.assertEqual(second["results"][0]["action"], "created")
        self.assertEqual(len(success_rows), 1)
        self.assertEqual(success_rows[0][4], "created")
        self.assertEqual(success_rows[0][3], first_time)
        self.assertIsNotNone(success_rows[0][7])
        self.assertEqual(record["first_collected_at"], first_time)
        self.assertEqual(record["collected_at"], first_time)
        self.assertEqual(record["revision_count"], 0)

    def test_absent_parent_correction_retry_succeeds_and_fills_snapshot(self) -> None:
        corp, fund, etf_key = "00999101", "ZZ101", "00999101_ZZ101"
        original_no, correction_no = "20260429000101", "20260430000102"
        correction_candidate = {
            "rcept_no": correction_no, "rcept_dt": "20260430", "corp_code": corp,
            "corp_name": "T", "report_nm": "[기재정정]투자설명서(ETF(주식))",
        }
        original_candidate = {
            "rcept_no": original_no, "rcept_dt": "20260429", "corp_code": corp,
            "corp_name": "T", "report_nm": "투자설명서(ETF(주식))",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_30 = base_dir / "runs" / "20260430" / "records"
            pdfs_30 = base_dir / "runs" / "20260430" / "pdfs"
            records_29 = base_dir / "runs" / "20260429" / "records"
            pdfs_29 = base_dir / "runs" / "20260429" / "pdfs"
            db_path = base_dir / "db" / "etf_insight.sqlite3"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(correction_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch("new_etf_insight.daily_pipeline.review_correction_filing") as review_mock,
            ):
                first = run_daily_pipeline("20260430", "20260430", records_30, pdfs_30)
            review_mock.assert_not_called()
            self.assertEqual(first["results"][0]["action"], "skipped")
            self.assertEqual(first["results"][0]["reason"], "correction_without_existing_record")
            first_rows = self._history_full_rows(db_path)
            self.assertEqual(len(first_rows), 1)
            first_time = first_rows[0][3]
            self.assertIsNotNone(first_time)
            first_filing = first_rows[0][6]

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(original_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.download_representative_prospectus_pdf",
                    return_value=pdfs_29 / "x.pdf",
                ),
                patch(
                    "new_etf_insight.daily_pipeline.analyze_pdf",
                    return_value={
                        "route": "pdf_holdings_available",
                        "summary": {"fund_name": "원본 ETF"},
                        "research_prompt": "",
                    },
                ),
            ):
                second = run_daily_pipeline("20260429", "20260429", records_29, pdfs_29)
            self.assertEqual(second["results"][0]["action"], "created")

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(correction_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.review_correction_filing",
                    return_value={"needs_update": True, "reason": "뒤늦은 정정"},
                ),
                patch(
                    "new_etf_insight.daily_pipeline.update_record_from_correction",
                    return_value={
                        "route": "correction_updated",
                        "summary": {"fund_name": "정정 ETF"},
                        "research_prompt": "",
                    },
                ),
            ):
                third = run_daily_pipeline("20260430", "20260430", records_30, pdfs_30)
            final_rows = self._history_full_rows(db_path)
            updated = json.loads((records_30 / f"{etf_key}.json").read_text(encoding="utf-8"))

        self.assertEqual(third["results"][0]["action"], "updated")
        self.assertEqual(updated["summary"]["fund_name"], "정정 ETF")
        self.assertEqual(updated["revision_count"], 1)
        self.assertEqual(len(final_rows), 2)
        correction_row = [row for row in final_rows if row[1] == correction_no][0]
        self.assertEqual(correction_row[4], "updated")
        self.assertEqual(correction_row[5], "뒤늦은 정정")
        self.assertIsNotNone(correction_row[7])
        self.assertEqual(correction_row[3], first_time)
        self.assertEqual(correction_row[6], first_filing)

    def test_failed_then_no_update_skipped_avoids_llm_on_rerun(self) -> None:
        corp, fund, etf_key = "00999102", "ZZ102", "00999102_ZZ102"
        original_no, correction_no = "20260429000111", "20260429000112"
        correction_candidate = {
            "rcept_no": correction_no, "rcept_dt": "20260429", "corp_code": corp,
            "corp_name": "T", "report_nm": "[기재정정]투자설명서(ETF(주식))",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            self._write_day_record(
                base_dir, "20260429", etf_key,
                {
                    "route": "pdf_holdings_available",
                    "summary": {"fund_name": "원본 ETF"},
                    "research_prompt": "",
                    "source": {
                        "rcept_no": original_no, "rcept_dt": "20260429", "corp_code": corp,
                        "corp_name": "T", "report_nm": "투자설명서",
                        "fund_code": fund, "etf_key": etf_key, "pdf_path": "x.pdf",
                    },
                    "first_rcept_no": original_no,
                    "first_rcept_dt": "20260429",
                    "revision_count": 0,
                },
            )
            records_dir = base_dir / "runs" / "20260429" / "records"
            pdf_dir = base_dir / "runs" / "20260429" / "pdfs"
            db_path = base_dir / "db" / "etf_insight.sqlite3"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(correction_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.review_correction_filing",
                    side_effect=ValueError("LLM boom"),
                ),
            ):
                first = run_daily_pipeline("20260429", "20260429", records_dir, pdf_dir)
            self.assertEqual(first["results"][0]["action"], "failed")
            failed_rows = self._history_full_rows(db_path)
            failed_time = [row for row in failed_rows if row[1] == correction_no][0][3]

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(correction_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.review_correction_filing",
                    return_value={"needs_update": False, "reason": "경미한 오타"},
                ),
                patch("new_etf_insight.daily_pipeline.update_record_from_correction") as update_mock,
            ):
                second = run_daily_pipeline("20260429", "20260429", records_dir, pdf_dir)
            update_mock.assert_not_called()
            self.assertEqual(second["results"][0]["action"], "skipped")
            self.assertEqual(second["results"][0]["reason"], "경미한 오타")
            skipped_rows = self._history_full_rows(db_path)
            skipped_row = [row for row in skipped_rows if row[1] == correction_no][0]
            self.assertEqual(skipped_row[4], "skipped")
            self.assertEqual(skipped_row[5], "경미한 오타")
            self.assertEqual(skipped_row[3], failed_time)
            self.assertIsNone(skipped_row[7])

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(correction_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch("new_etf_insight.daily_pipeline.review_correction_filing") as review_mock,
                patch("new_etf_insight.daily_pipeline.update_record_from_correction") as update_mock2,
                patch("new_etf_insight.daily_pipeline.download_representative_prospectus_pdf") as download_mock,
                patch("new_etf_insight.daily_pipeline.analyze_pdf") as analyze_mock,
            ):
                third = run_daily_pipeline("20260429", "20260429", records_dir, pdf_dir)
            rerun_rows = self._history_full_rows(db_path)

        review_mock.assert_not_called()
        update_mock2.assert_not_called()
        download_mock.assert_not_called()
        analyze_mock.assert_not_called()
        self.assertEqual(third["results"][0]["action"], "skipped")
        self.assertEqual(third["results"][0]["reason"], "경미한 오타")
        self.assertEqual(rerun_rows, skipped_rows)

    def test_lost_success_json_rerun_restores_snapshot_without_llm(self) -> None:
        corp, fund, etf_key = "00999103", "ZZ103", "00999103_ZZ103"
        candidate = {
            "rcept_no": "20260429000121", "rcept_dt": "20260429", "corp_code": corp,
            "corp_name": "T", "report_nm": "투자설명서(ETF(주식))",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_dir = base_dir / "runs" / "20260429" / "records"
            pdf_dir = base_dir / "runs" / "20260429" / "pdfs"
            db_path = base_dir / "db" / "etf_insight.sqlite3"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.download_representative_prospectus_pdf",
                    return_value=pdf_dir / "x.pdf",
                ),
                patch(
                    "new_etf_insight.daily_pipeline.analyze_pdf",
                    return_value={
                        "route": "pdf_holdings_available",
                        "summary": {"fund_name": "원본 ETF"},
                        "research_prompt": "",
                    },
                ),
            ):
                first = run_daily_pipeline("20260429", "20260429", records_dir, pdf_dir)
            self.assertEqual(first["results"][0]["action"], "created")
            before_json = (records_dir / f"{etf_key}.json").read_text(encoding="utf-8")
            before_rows = self._history_full_rows(db_path)

            (records_dir / f"{etf_key}.json").unlink()
            self.assertFalse((records_dir / f"{etf_key}.json").exists())

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch("new_etf_insight.daily_pipeline.download_representative_prospectus_pdf") as download_mock,
                patch("new_etf_insight.daily_pipeline.analyze_pdf") as analyze_mock,
                patch("new_etf_insight.daily_pipeline.review_correction_filing") as review_mock,
                patch("new_etf_insight.daily_pipeline.update_record_from_correction") as update_mock,
            ):
                second = run_daily_pipeline("20260429", "20260429", records_dir, pdf_dir)
            after_json = (records_dir / f"{etf_key}.json").read_text(encoding="utf-8")
            after_rows = self._history_full_rows(db_path)

        download_mock.assert_not_called()
        analyze_mock.assert_not_called()
        review_mock.assert_not_called()
        update_mock.assert_not_called()
        self.assertEqual(second["results"][0]["action"], "skipped")
        self.assertEqual(after_json, before_json)
        self.assertEqual(after_rows, before_rows)

    def test_db_only_previous_enables_correction_update(self) -> None:
        corp, fund, etf_key = "00999104", "ZZ104", "00999104_ZZ104"
        original_no, correction_no = "20260429000131", "20260430000132"
        original_candidate = {
            "rcept_no": original_no, "rcept_dt": "20260429", "corp_code": corp,
            "corp_name": "T", "report_nm": "투자설명서(ETF(주식))",
        }
        correction_candidate = {
            "rcept_no": correction_no, "rcept_dt": "20260430", "corp_code": corp,
            "corp_name": "T", "report_nm": "[기재정정]투자설명서(ETF(주식))",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_29 = base_dir / "runs" / "20260429" / "records"
            pdfs_29 = base_dir / "runs" / "20260429" / "pdfs"
            records_30 = base_dir / "runs" / "20260430" / "records"
            pdfs_30 = base_dir / "runs" / "20260430" / "pdfs"
            db_path = base_dir / "db" / "etf_insight.sqlite3"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(original_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.download_representative_prospectus_pdf",
                    return_value=pdfs_29 / "x.pdf",
                ),
                patch(
                    "new_etf_insight.daily_pipeline.analyze_pdf",
                    return_value={
                        "route": "pdf_holdings_available",
                        "summary": {"fund_name": "원본 ETF"},
                        "research_prompt": "",
                    },
                ),
            ):
                run_daily_pipeline("20260429", "20260429", records_29, pdfs_29)
            original_json = json.loads((records_29 / f"{etf_key}.json").read_text(encoding="utf-8"))
            first_no, first_dt, first_time = (
                original_json["first_rcept_no"],
                original_json["first_rcept_dt"],
                original_json["first_collected_at"],
            )
            (records_29 / f"{etf_key}.json").unlink()

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(correction_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.review_correction_filing",
                    return_value={"needs_update": True, "reason": "DB 기반 정정"},
                ),
                patch(
                    "new_etf_insight.daily_pipeline.update_record_from_correction",
                    return_value={
                        "route": "correction_updated",
                        "summary": {"fund_name": "정정 ETF"},
                        "research_prompt": "",
                    },
                ) as update_mock,
            ):
                result = run_daily_pipeline("20260430", "20260430", records_30, pdfs_30)
            updated = json.loads((records_30 / f"{etf_key}.json").read_text(encoding="utf-8"))
            rows = self._history_rows(db_path)

        update_mock.assert_called_once()
        previous_arg = update_mock.call_args.args[0]
        self.assertEqual(previous_arg["summary"]["fund_name"], "원본 ETF")
        self.assertEqual(result["results"][0]["action"], "updated")
        self.assertEqual(updated["summary"]["fund_name"], "정정 ETF")
        self.assertEqual(updated["first_rcept_no"], first_no)
        self.assertEqual(updated["first_rcept_dt"], first_dt)
        self.assertEqual(updated["first_collected_at"], first_time)
        self.assertEqual(updated["revision_count"], 1)
        self.assertEqual(len(rows), 2)

    def test_old_json_newer_db_correction_uses_db_snapshot(self) -> None:
        corp, fund, etf_key = "00999105", "ZZ105", "00999105_ZZ105"
        v0_no, v1_no, v2_no = "20260429000141", "20260430000142", "20260501000143"
        v0_candidate = {
            "rcept_no": v0_no, "rcept_dt": "20260429", "corp_code": corp,
            "corp_name": "T", "report_nm": "투자설명서(ETF(주식))",
        }
        v1_candidate = {
            "rcept_no": v1_no, "rcept_dt": "20260430", "corp_code": corp,
            "corp_name": "T", "report_nm": "[기재정정]투자설명서(ETF(주식))",
        }
        v2_candidate = {
            "rcept_no": v2_no, "rcept_dt": "20260501", "corp_code": corp,
            "corp_name": "T", "report_nm": "[기재정정]투자설명서(ETF(주식))",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_29 = base_dir / "runs" / "20260429" / "records"
            pdfs_29 = base_dir / "runs" / "20260429" / "pdfs"
            records_30 = base_dir / "runs" / "20260430" / "records"
            pdfs_30 = base_dir / "runs" / "20260430" / "pdfs"
            records_01 = base_dir / "runs" / "20260501" / "records"
            pdfs_01 = base_dir / "runs" / "20260501" / "pdfs"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(v0_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.download_representative_prospectus_pdf",
                    return_value=pdfs_29 / "x.pdf",
                ),
                patch(
                    "new_etf_insight.daily_pipeline.analyze_pdf",
                    return_value={
                        "route": "pdf_holdings_available",
                        "summary": {"fund_name": "v0"},
                        "research_prompt": "",
                    },
                ),
            ):
                run_daily_pipeline("20260429", "20260429", records_29, pdfs_29)
            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(v1_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.review_correction_filing",
                    return_value={"needs_update": True, "reason": "v1 정정"},
                ),
                patch(
                    "new_etf_insight.daily_pipeline.update_record_from_correction",
                    return_value={
                        "route": "correction_updated",
                        "summary": {"fund_name": "v1"},
                        "research_prompt": "",
                    },
                ),
            ):
                run_daily_pipeline("20260430", "20260430", records_30, pdfs_30)
            (records_30 / f"{etf_key}.json").unlink()

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(v2_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.review_correction_filing",
                    return_value={"needs_update": True, "reason": "v2 정정"},
                ),
                patch(
                    "new_etf_insight.daily_pipeline.update_record_from_correction",
                    return_value={
                        "route": "correction_updated",
                        "summary": {"fund_name": "v2"},
                        "research_prompt": "",
                    },
                ) as update_mock,
            ):
                result = run_daily_pipeline("20260501", "20260501", records_01, pdfs_01)
            final = json.loads((records_01 / f"{etf_key}.json").read_text(encoding="utf-8"))

        update_mock.assert_called_once()
        previous_arg = update_mock.call_args.args[0]
        self.assertEqual(previous_arg["summary"]["fund_name"], "v1")
        self.assertEqual(result["results"][0]["action"], "updated")
        self.assertEqual(final["summary"]["fund_name"], "v2")
        self.assertEqual(final["revision_count"], 2)
        self.assertEqual(final["first_rcept_no"], v0_no)

    def test_db_only_same_rcept_correction_rerun_restores_without_llm(self) -> None:
        corp, fund, etf_key = "00999106", "ZZ106", "00999106_ZZ106"
        original_no, correction_no = "20260429000151", "20260430000152"
        original_candidate = {
            "rcept_no": original_no, "rcept_dt": "20260429", "corp_code": corp,
            "corp_name": "T", "report_nm": "투자설명서(ETF(주식))",
        }
        correction_candidate = {
            "rcept_no": correction_no, "rcept_dt": "20260430", "corp_code": corp,
            "corp_name": "T", "report_nm": "[기재정정]투자설명서(ETF(주식))",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_29 = base_dir / "runs" / "20260429" / "records"
            pdfs_29 = base_dir / "runs" / "20260429" / "pdfs"
            records_30 = base_dir / "runs" / "20260430" / "records"
            pdfs_30 = base_dir / "runs" / "20260430" / "pdfs"
            db_path = base_dir / "db" / "etf_insight.sqlite3"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(original_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.download_representative_prospectus_pdf",
                    return_value=pdfs_29 / "x.pdf",
                ),
                patch(
                    "new_etf_insight.daily_pipeline.analyze_pdf",
                    return_value={
                        "route": "pdf_holdings_available",
                        "summary": {"fund_name": "원본 ETF"},
                        "research_prompt": "",
                    },
                ),
            ):
                run_daily_pipeline("20260429", "20260429", records_29, pdfs_29)
            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(correction_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.review_correction_filing",
                    return_value={"needs_update": True, "reason": "정정"},
                ),
                patch(
                    "new_etf_insight.daily_pipeline.update_record_from_correction",
                    return_value={
                        "route": "correction_updated",
                        "summary": {"fund_name": "정정 ETF"},
                        "research_prompt": "",
                    },
                ),
            ):
                run_daily_pipeline("20260430", "20260430", records_30, pdfs_30)
            before_json = (records_30 / f"{etf_key}.json").read_text(encoding="utf-8")
            before_rows = self._history_full_rows(db_path)
            (records_29 / f"{etf_key}.json").unlink()
            (records_30 / f"{etf_key}.json").unlink()

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(correction_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch("new_etf_insight.daily_pipeline.download_representative_prospectus_pdf") as download_mock,
                patch("new_etf_insight.daily_pipeline.analyze_pdf") as analyze_mock,
                patch("new_etf_insight.daily_pipeline.review_correction_filing") as review_mock,
                patch("new_etf_insight.daily_pipeline.update_record_from_correction") as update_mock,
            ):
                result = run_daily_pipeline("20260430", "20260430", records_30, pdfs_30)
            after_json = (records_30 / f"{etf_key}.json").read_text(encoding="utf-8")
            after_rows = self._history_full_rows(db_path)

        download_mock.assert_not_called()
        analyze_mock.assert_not_called()
        review_mock.assert_not_called()
        update_mock.assert_not_called()
        self.assertEqual(result["results"][0]["action"], "skipped")
        self.assertEqual(after_json, before_json)
        self.assertEqual(after_rows, before_rows)

    def test_new_general_filing_leaves_observation_history_without_summary_change(self) -> None:
        corp, fund, etf_key = "00999107", "ZZ107", "00999107_ZZ107"
        original_no, general_no = "20260429000161", "20260430000162"
        original_candidate = {
            "rcept_no": original_no, "rcept_dt": "20260429", "corp_code": corp,
            "corp_name": "T", "report_nm": "투자설명서(ETF(주식))",
        }
        general_candidate = {
            "rcept_no": general_no, "rcept_dt": "20260430", "corp_code": corp,
            "corp_name": "T", "report_nm": "일괄신고서(ETF(주식))",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_29 = base_dir / "runs" / "20260429" / "records"
            pdfs_29 = base_dir / "runs" / "20260429" / "pdfs"
            records_30 = base_dir / "runs" / "20260430" / "records"
            pdfs_30 = base_dir / "runs" / "20260430" / "pdfs"
            db_path = base_dir / "db" / "etf_insight.sqlite3"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(original_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.download_representative_prospectus_pdf",
                    return_value=pdfs_29 / "x.pdf",
                ),
                patch(
                    "new_etf_insight.daily_pipeline.analyze_pdf",
                    return_value={
                        "route": "pdf_holdings_available",
                        "summary": {"fund_name": "원본 ETF"},
                        "research_prompt": "",
                    },
                ),
            ):
                run_daily_pipeline("20260429", "20260429", records_29, pdfs_29)
            before_json = (records_29 / f"{etf_key}.json").read_text(encoding="utf-8")

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(general_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch("new_etf_insight.daily_pipeline.download_representative_prospectus_pdf") as download_mock,
                patch("new_etf_insight.daily_pipeline.analyze_pdf") as analyze_mock,
            ):
                second = run_daily_pipeline("20260430", "20260430", records_30, pdfs_30)
            download_mock.assert_not_called()
            analyze_mock.assert_not_called()
            self.assertEqual(second["results"][0]["action"], "skipped")
            self.assertEqual(second["results"][0]["reason"], "existing_record")
            self.assertFalse((records_30 / f"{etf_key}.json").exists())
            after_first_json = (records_29 / f"{etf_key}.json").read_text(encoding="utf-8")
            self.assertEqual(after_first_json, before_json)
            mid_rows = self._history_rows(db_path)
            self.assertEqual(len(mid_rows), 2)
            general_row = [row for row in mid_rows if row[1] == general_no][0]
            self.assertEqual(general_row[2], "skipped")
            self.assertEqual(general_row[3], "existing_record")
            self.assertFalse(general_row[5])
            self.assertIsNotNone(general_row[4])

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(general_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch("new_etf_insight.daily_pipeline.download_representative_prospectus_pdf") as download_mock2,
                patch("new_etf_insight.daily_pipeline.analyze_pdf") as analyze_mock2,
            ):
                third = run_daily_pipeline("20260430", "20260430", records_30, pdfs_30)
            final_rows = self._history_rows(db_path)

        download_mock2.assert_not_called()
        analyze_mock2.assert_not_called()
        self.assertEqual(third["results"][0]["action"], "skipped")
        self.assertEqual(final_rows, mid_rows)

    def test_legacy_correction_only_next_correction_keeps_first_nulls(self) -> None:
        corp, fund, etf_key = "00999201", "ZZ201", "00999201_ZZ201"
        corr1_no, corr2_no = "20260610000101", "20260611000102"
        candidates = [
            {
                "rcept_no": corr2_no, "rcept_dt": "20260611", "corp_code": corp,
                "corp_name": "T", "report_nm": "[기재정정]투자설명서(ETF(주식))",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            self._write_day_record(
                base_dir, "20260610", etf_key,
                {
                    "route": "correction_updated",
                    "summary": {"fund_name": "레거시 정정"},
                    "research_prompt": "",
                    "source": {
                        "rcept_no": corr1_no, "rcept_dt": "20260610", "corp_code": corp,
                        "corp_name": "T", "report_nm": "[기재정정]투자설명서",
                        "fund_code": fund, "etf_key": etf_key, "pdf_path": "x.pdf",
                    },
                    "first_rcept_dt": "20260601",
                    "collected_at": "2026-06-10T00:00:00+00:00",
                    "revision_count": 1,
                },
            )
            records_dir = base_dir / "runs" / "20260611" / "records"
            pdf_dir = base_dir / "runs" / "20260611" / "pdfs"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=candidates),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.review_correction_filing",
                    return_value={"needs_update": True, "reason": "추가 정정"},
                ),
                patch(
                    "new_etf_insight.daily_pipeline.update_record_from_correction",
                    return_value={
                        "route": "correction_updated",
                        "summary": {"fund_name": "신규 정정"},
                        "research_prompt": "",
                    },
                ),
            ):
                result = run_daily_pipeline("20260611", "20260611", records_dir, pdf_dir)
                record = json.loads((records_dir / f"{etf_key}.json").read_text(encoding="utf-8"))

        self.assertEqual(result["results"][0]["action"], "updated")
        self.assertEqual(record["summary"]["fund_name"], "신규 정정")
        self.assertEqual(record["first_rcept_dt"], "20260601")
        self.assertIsNone(record.get("first_rcept_no"))
        self.assertIsNone(record.get("first_collected_at"))
        self.assertEqual(record["revision_count"], 2)
        self.assertIsNotNone(record.get("collected_at"))

    def test_skipped_history_time_reused_for_retry_success(self) -> None:
        corp, fund, etf_key = "00999202", "ZZ202", "00999202_ZZ202"
        original_no, correction_no = "20260429000201", "20260430000202"
        correction_candidate = {
            "rcept_no": correction_no, "rcept_dt": "20260430", "corp_code": corp,
            "corp_name": "T", "report_nm": "[기재정정]투자설명서(ETF(주식))",
        }
        original_candidate = {
            "rcept_no": original_no, "rcept_dt": "20260429", "corp_code": corp,
            "corp_name": "T", "report_nm": "투자설명서(ETF(주식))",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_30 = base_dir / "runs" / "20260430" / "records"
            pdfs_30 = base_dir / "runs" / "20260430" / "pdfs"
            records_29 = base_dir / "runs" / "20260429" / "records"
            pdfs_29 = base_dir / "runs" / "20260429" / "pdfs"
            db_path = base_dir / "db" / "etf_insight.sqlite3"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(correction_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
            ):
                run_daily_pipeline("20260430", "20260430", records_30, pdfs_30)
            t1 = self._history_full_rows(db_path)[0][3]
            self.assertIsNotNone(t1)

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(original_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.download_representative_prospectus_pdf",
                    return_value=pdfs_29 / "x.pdf",
                ),
                patch(
                    "new_etf_insight.daily_pipeline.analyze_pdf",
                    return_value={
                        "route": "pdf_holdings_available",
                        "summary": {"fund_name": "원본 ETF"},
                        "research_prompt": "",
                    },
                ),
            ):
                run_daily_pipeline("20260429", "20260429", records_29, pdfs_29)

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(correction_candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.review_correction_filing",
                    return_value={"needs_update": True, "reason": "뒤늦은 정정"},
                ),
                patch(
                    "new_etf_insight.daily_pipeline.update_record_from_correction",
                    return_value={
                        "route": "correction_updated",
                        "summary": {"fund_name": "정정 ETF"},
                        "research_prompt": "",
                    },
                ),
            ):
                third = run_daily_pipeline("20260430", "20260430", records_30, pdfs_30)
            updated = json.loads((records_30 / f"{etf_key}.json").read_text(encoding="utf-8"))
            rows = self._history_full_rows(db_path)
            correction_row = [row for row in rows if row[1] == correction_no][0]

        self.assertEqual(third["results"][0]["action"], "updated")
        self.assertEqual(correction_row[3], t1)
        self.assertEqual(updated["collected_at"], t1)

    def test_failed_null_time_stays_null_on_success(self) -> None:
        import sqlite3

        corp, fund, etf_key = "00999203", "ZZ203", "00999203_ZZ203"
        candidate = {
            "rcept_no": "20260429000211", "rcept_dt": "20260429", "corp_code": corp,
            "corp_name": "T", "report_nm": "투자설명서(ETF(주식))",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_dir = base_dir / "runs" / "20260429" / "records"
            pdf_dir = base_dir / "runs" / "20260429" / "pdfs"
            db_path = base_dir / "db" / "etf_insight.sqlite3"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.download_representative_prospectus_pdf",
                    side_effect=ValueError("PDF link missing"),
                ),
                patch("new_etf_insight.daily_pipeline.analyze_pdf"),
            ):
                first = run_daily_pipeline("20260429", "20260429", records_dir, pdf_dir)
            self.assertEqual(first["results"][0]["action"], "failed")
            con = sqlite3.connect(str(db_path))
            try:
                con.execute(
                    "UPDATE etf_filing_history SET first_collected_at = NULL WHERE etf_key = ? AND rcept_no = ?",
                    [etf_key, candidate["rcept_no"]],
                )
                con.commit()
            finally:
                con.close()

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.download_representative_prospectus_pdf",
                    return_value=pdf_dir / "x.pdf",
                ),
                patch(
                    "new_etf_insight.daily_pipeline.analyze_pdf",
                    return_value={
                        "route": "pdf_holdings_available",
                        "summary": {"fund_name": "v0"},
                        "research_prompt": "",
                    },
                ),
            ):
                second = run_daily_pipeline("20260429", "20260429", records_dir, pdf_dir)
            record = json.loads((records_dir / f"{etf_key}.json").read_text(encoding="utf-8"))
            rows = self._history_full_rows(db_path)

        self.assertEqual(second["results"][0]["action"], "created")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0][3])
        self.assertIsNone(record.get("collected_at"))
        self.assertIsNone(record.get("first_collected_at"))

    def test_new_filing_gets_utc_collected_time(self) -> None:
        corp, fund, etf_key = "00999204", "ZZ204", "00999204_ZZ204"
        candidate = {
            "rcept_no": "20260429000221", "rcept_dt": "20260429", "corp_code": corp,
            "corp_name": "T", "report_nm": "투자설명서(ETF(주식))",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_dir = base_dir / "runs" / "20260429" / "records"
            pdf_dir = base_dir / "runs" / "20260429" / "pdfs"
            db_path = base_dir / "db" / "etf_insight.sqlite3"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.download_representative_prospectus_pdf",
                    return_value=pdf_dir / "x.pdf",
                ),
                patch(
                    "new_etf_insight.daily_pipeline.analyze_pdf",
                    return_value={
                        "route": "pdf_holdings_available",
                        "summary": {"fund_name": "v0"},
                        "research_prompt": "",
                    },
                ),
            ):
                result = run_daily_pipeline("20260429", "20260429", records_dir, pdf_dir)
            record = json.loads((records_dir / f"{etf_key}.json").read_text(encoding="utf-8"))
            rows = self._history_full_rows(db_path)

        self.assertEqual(result["results"][0]["action"], "created")
        collected = record.get("collected_at")
        self.assertIsNotNone(collected)
        assert collected is not None
        self.assertIn("T", collected)
        self.assertTrue(collected.endswith("+00:00"))
        self.assertEqual(rows[0][3], collected)
        self.assertEqual(record.get("first_collected_at"), collected)

    def test_snapshot_restore_failure_reports_failed_without_touching_history(self) -> None:
        corp, fund, etf_key = "00999205", "ZZ205", "00999205_ZZ205"
        candidate = {
            "rcept_no": "20260429000231", "rcept_dt": "20260429", "corp_code": corp,
            "corp_name": "T", "report_nm": "투자설명서(ETF(주식))",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            records_dir = base_dir / "runs" / "20260429" / "records"
            pdf_dir = base_dir / "runs" / "20260429" / "pdfs"
            db_path = base_dir / "db" / "etf_insight.sqlite3"

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch(
                    "new_etf_insight.daily_pipeline.download_representative_prospectus_pdf",
                    return_value=pdf_dir / "x.pdf",
                ),
                patch(
                    "new_etf_insight.daily_pipeline.analyze_pdf",
                    return_value={
                        "route": "pdf_holdings_available",
                        "summary": {"fund_name": "원본 ETF"},
                        "research_prompt": "",
                    },
                ),
            ):
                first = run_daily_pipeline("20260429", "20260429", records_dir, pdf_dir)
            self.assertEqual(first["results"][0]["action"], "created")
            before_rows = self._history_full_rows(db_path)
            (records_dir / f"{etf_key}.json").unlink()

            with (
                patch("new_etf_insight.daily_pipeline.collect_candidates", return_value=[dict(candidate)]),
                patch("new_etf_insight.daily_pipeline.fetch_fund_code_from_dart_viewer", return_value=fund),
                patch("new_etf_insight.daily_pipeline.download_representative_prospectus_pdf") as download_mock,
                patch("new_etf_insight.daily_pipeline.analyze_pdf") as analyze_mock,
                patch("new_etf_insight.daily_pipeline.review_correction_filing") as review_mock,
                patch("new_etf_insight.daily_pipeline.update_record_from_correction") as update_mock,
                patch("pathlib.Path.write_text", side_effect=OSError("disk full")),
            ):
                second = run_daily_pipeline("20260429", "20260429", records_dir, pdf_dir)
            after_rows = self._history_full_rows(db_path)
            file_exists = (records_dir / f"{etf_key}.json").exists()

        download_mock.assert_not_called()
        analyze_mock.assert_not_called()
        review_mock.assert_not_called()
        update_mock.assert_not_called()
        self.assertEqual(second["results"][0]["action"], "failed")
        self.assertEqual(second["results"][0]["reason"], "snapshot_restore_failed")
        self.assertEqual(second["results"][0]["error_type"], "OSError")
        self.assertFalse(file_exists)
        self.assertEqual(after_rows, before_rows)


if __name__ == "__main__":
    unittest.main()

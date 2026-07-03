"""stock_names 매핑 테스트 — KRX ISU_NM 파싱 + duckdb upsert/조회."""
import unittest
from unittest.mock import MagicMock, patch

import duckdb

from scripts.stock_names import (
    ensure_table,
    fetch_names,
    load_code_to_name,
    load_name_to_code,
    upsert_names,
)


class TestFetchNames(unittest.TestCase):
    def _resp(self, payload):
        r = MagicMock()
        r.raise_for_status.return_value = None
        r.json.return_value = payload
        return r

    def test_parses_isu_cd_and_isu_nm_both_markets(self):
        session = MagicMock()
        session.get.return_value = self._resp(
            {"OutBlock_1": [{"ISU_CD": "005930", "ISU_NM": "삼성전자"}]}
        )
        with patch("scripts.stock_names.time.sleep"):
            pairs = fetch_names("20260701", "k", session=session)
        # KOSPI + KOSDAQ 콜 각 1행
        self.assertEqual(pairs, [("005930", "삼성전자"), ("005930", "삼성전자")])

    def test_skips_rows_missing_name_or_code(self):
        session = MagicMock()
        session.get.return_value = self._resp(
            {"OutBlock_1": [
                {"ISU_CD": "005930", "ISU_NM": "삼성전자"},
                {"ISU_CD": "", "ISU_NM": "이름만"},
                {"ISU_CD": "000660", "ISU_NM": ""},
            ]}
        )
        with patch("scripts.stock_names.time.sleep"):
            pairs = fetch_names("20260701", "k", session=session)
        self.assertEqual(pairs, [("005930", "삼성전자"), ("005930", "삼성전자")])

    def test_missing_outblock_raises(self):
        session = MagicMock()
        session.get.return_value = self._resp({"respMsg": "oops"})
        with self.assertRaises(RuntimeError):
            fetch_names("20260701", "k", session=session)


class TestUpsertLoad(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        ensure_table(self.con)

    def tearDown(self):
        self.con.close()

    def test_roundtrip_name_to_code(self):
        upsert_names(self.con, [("005930", "삼성전자"), ("000660", "SK하이닉스")])
        self.assertEqual(load_name_to_code(self.con)["삼성전자"], "005930")
        self.assertEqual(load_code_to_name(self.con)["000660"], "SK하이닉스")

    def test_upsert_replaces_name(self):
        upsert_names(self.con, [("005930", "구이름")])
        upsert_names(self.con, [("005930", "삼성전자")])
        self.assertEqual(load_code_to_name(self.con)["005930"], "삼성전자")
        self.assertEqual(len(load_code_to_name(self.con)), 1)

    def test_dup_name_deterministic(self):
        # 동일 종목명 두 코드 → ORDER BY code 로 항상 가장 큰 코드 선택(재현성 보장)
        upsert_names(self.con, [("222222", "중복명"), ("111111", "중복명")])
        self.assertEqual(load_name_to_code(self.con)["중복명"], "222222")


if __name__ == "__main__":
    unittest.main()

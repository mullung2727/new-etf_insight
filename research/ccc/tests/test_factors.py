"""합성 데이터로 factors 검증."""

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

import pandas as pd

from research.ccc.factors import FILES, load_factors, parse_monthly

TEXT_5 = (
    "Fama/French Test File\n"
    "Description line\n"
    "\n"
    ",Mkt-RF,SMB,HML,RMW,CMA,RF\n"
    "196307,   -0.39,   -0.48,   -0.84,    0.64,   -1.15,    0.27\n"
    "196308,    5.07,   -0.66,    1.06,    0.37,    0.79,    0.25\n"
    "\n"
    " Annual Factors: January-December\n"
    ",Mkt-RF,SMB,HML,RMW,CMA,RF\n"
    "  1963,   10.00,    5.00,    3.00,    1.00,    2.00,    0.30\n"
)

TEXT_MOM = (
    "Momentum Test\n"
    "\n"
    ",Mom\n"
    "196308,    0.45\n"
    "196309,    1.23\n"
    "\n"
    " Annual Factors: January-December\n"
    ",Mom\n"
    "  1963,    5.00\n"
)

TEXT_MISS = (
    "desc\n"
    ",Mkt-RF,SMB\n"
    "196307, -99.99, 1.00\n"
    "196308, 2.00, -999\n"
    "\n"
)


def _zip_bytes(csv_name: str, csv_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(csv_name, csv_text.encode("latin-1"))
    return buf.getvalue()


class TestFactors(unittest.TestCase):
    def test_f1_monthly_block(self) -> None:
        df = parse_monthly(TEXT_5)
        self.assertEqual(len(df), 2)
        self.assertEqual(df.index.name, "ym")
        self.assertTrue(pd.api.types.is_integer_dtype(df.index))
        self.assertEqual(list(df.index), [196307, 196308])
        self.assertNotIn(1963, list(df.index))
        self.assertAlmostEqual(df.loc[196307, "Mkt-RF"], -0.0039)
        self.assertAlmostEqual(df.loc[196308, "RF"], 0.0025)

    def test_f2_missing(self) -> None:
        df = parse_monthly(TEXT_MISS)
        self.assertTrue(pd.isna(df.loc[196307, "Mkt-RF"]))
        self.assertTrue(pd.isna(df.loc[196308, "SMB"]))
        self.assertAlmostEqual(df.loc[196307, "SMB"], 0.01)

    def test_f3_crlf(self) -> None:
        lf = parse_monthly(TEXT_5)
        crlf = parse_monthly(TEXT_5.replace("\n", "\r\n"))
        pd.testing.assert_frame_equal(lf, crlf)

    def test_f4_load_factors_fake(self) -> None:
        five_zip = _zip_bytes(f"{FILES[0]}.csv", TEXT_5)
        mom_zip = _zip_bytes(f"{FILES[1]}.csv", TEXT_MOM)
        calls: list[str] = []

        def fake(url: str) -> bytes:
            calls.append(url)
            return mom_zip if FILES[1] in url else five_zip

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            df = load_factors(raw_root=root, get_bytes=fake)
            self.assertEqual(
                list(df.columns), ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF", "Mom"]
            )
            self.assertEqual(len(df), 1)  # 196308만 겹침
            self.assertEqual(list(df.index), [196308])
            self.assertTrue((root / f"{FILES[0]}.zip").exists())
            self.assertTrue((root / f"{FILES[1]}.zip").exists())
            self.assertEqual(len(calls), 2)
            calls.clear()
            df2 = load_factors(raw_root=root, get_bytes=fake)
            self.assertEqual(calls, [])
            pd.testing.assert_frame_equal(df, df2)


if __name__ == "__main__":
    unittest.main()

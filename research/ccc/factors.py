"""Ken French 월별 팩터 읽기·결합."""

from pathlib import Path
from typing import Callable, Optional
import zipfile

import pandas as pd
from new_etf_insight.data_paths import ken_french_dir

RAW_ROOT = ken_french_dir()
BASE_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
FILES = ("F-F_Research_Data_5_Factors_2x3_CSV", "F-F_Momentum_Factor_CSV")


def parse_monthly(text: str) -> pd.DataFrame:
    """월 블록만 파싱 → ym 인덱스, 값 /100."""
    lines = text.splitlines()
    cols: list[str] = []
    beg = 0
    for i, ln in enumerate(lines):
        if ln.startswith(","):  # 첫 헤더
            cols = [c.strip() for c in ln.split(",")[1:]]
            beg = i + 1
            break
    if not cols:
        raise ValueError("header not found")
    yms: list[int] = []
    rows: list[list[float]] = []
    for ln in lines[beg:]:
        s = ln.strip()
        if len(s) < 6 or not s[:6].isdigit():  # 빈 줄·연간 블록에서 중단
            break
        cells = s.split(",")
        yms.append(int(cells[0].strip()))
        vals: list[float] = []
        for tok in cells[1:]:
            t = tok.strip()
            if t in ("-99.99", "-999", ""):
                vals.append(float("nan"))
            else:
                v = float(t)
                vals.append(float("nan") if v in (-99.99, -999) else v / 100.0)
        vals += [float("nan")] * (len(cols) - len(vals))
        rows.append(vals[: len(cols)])
    return pd.DataFrame(rows, index=pd.Index(yms, name="ym"), columns=cols)


def _default_get_bytes(url: str) -> bytes:
    import requests  # 지연 임포트

    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    r.raise_for_status()
    return r.content


def load_factors(
    raw_root: Path = RAW_ROOT,
    refresh: bool = False,
    get_bytes: Optional[Callable[[str], bytes]] = None,
) -> pd.DataFrame:
    """두 zip 로드 후 ym 기준 inner join."""
    if get_bytes is None:
        get_bytes = _default_get_bytes
    raw_root = Path(raw_root)
    raw_root.mkdir(parents=True, exist_ok=True)
    dfs: list[pd.DataFrame] = []
    for name in FILES:
        dest = raw_root / f"{name}.zip"
        if refresh or not dest.exists():
            data = get_bytes(f"{BASE_URL}{name}.zip")
            tmp = dest.with_name(dest.name + ".tmp")  # 원자적 교체
            tmp.write_bytes(data)
            tmp.replace(dest)
        with zipfile.ZipFile(dest) as z:
            csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            text = z.read(csv_name).decode("latin-1")
        dfs.append(parse_monthly(text))
    df = dfs[0].join(dfs[1], how="inner")
    return df[["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF", "Mom"]]


if __name__ == "__main__":
    _df = load_factors()
    print(f"{len(_df)} {_df.index[0]} {_df.index[-1]}")

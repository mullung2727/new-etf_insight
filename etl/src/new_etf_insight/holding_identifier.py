from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


KRX_BASE_URL = "http://data-dbg.krx.co.kr/svc/apis/sto"
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"


@dataclass(frozen=True)
class SecurityMasterItem:
    name: str
    ticker: str
    exchange: str
    source: str = "security_master_exact"


def normalize_holding_name(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.upper()
    normalized = re.sub(r"\([^)]*\)", " ", normalized)
    normalized = normalized.replace("&", " AND ")
    normalized = re.sub(r"[^0-9A-Z가-힣]+", " ", normalized)
    normalized = re.sub(r"\bCL\s+([A-Z])\b", r"\1", normalized)
    normalized = re.sub(
        r"\b(CORPORATION|CORP|INCORPORATED|INC|LTD|LIMITED|PLC|CO|COMPANY|LLC|HOLDINGS?)\b",
        " ",
        normalized,
    )
    normalized = re.sub(
        r"\b(COMMON|STOCK|CAPITAL|ORDINARY|SHARES|SHARE|CLASS|ADR|ADS|EACH|REPRESENTING)\b",
        " ",
        normalized,
    )
    normalized = re.sub(r"\b(DE|MD|OH|MI|CA)\b", " ", normalized)
    normalized = re.sub(r"\b(THE)\b", " ", normalized)
    normalized = normalized.replace("보통주", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def compact_holding_key(value: str | None) -> str:
    return re.sub(r"\s+", "", normalize_holding_name(value))


class HoldingIdentifierResolver:
    def __init__(self, bas_dd: str | None = None) -> None:
        self.bas_dd = bas_dd
        self._kr_master: list[SecurityMasterItem] | None = None
        self._us_master: list[SecurityMasterItem] | None = None
        self._aliases: dict[str, SecurityMasterItem] | None = None

    def enrich_items(self, items: list[dict[str, Any]], primary_country: str | None = None) -> list[dict[str, Any]]:
        enriched = []
        for item in items:
            if item.get("ticker") and item.get("exchange"):
                enriched.append(item)
                continue

            match = self.resolve(str(item.get("name", "")), primary_country=primary_country)
            if match is None:
                enriched.append(item)
                continue

            enriched.append(
                {
                    **item,
                    "ticker": item.get("ticker") or match.ticker,
                    "exchange": item.get("exchange") or match.exchange,
                }
            )
        return enriched

    def resolve(self, name: str, primary_country: str | None = None) -> SecurityMasterItem | None:
        normalized = normalize_holding_name(name)
        if not normalized:
            return None

        countries = self._candidate_countries(primary_country)
        for country in countries:
            master = self.kr_master if country == "KR" else self.us_master
            match = self._match_exact(normalized, master)
            if match is not None:
                return match

        alias_match = self.aliases.get(compact_holding_key(normalized))
        if alias_match is not None:
            return alias_match
        return None

    @property
    def kr_master(self) -> list[SecurityMasterItem]:
        if self._kr_master is None:
            self._kr_master = fetch_krx_master(self.bas_dd)
        return self._kr_master

    @property
    def us_master(self) -> list[SecurityMasterItem]:
        if self._us_master is None:
            self._us_master = fetch_us_master()
        return self._us_master

    @property
    def aliases(self) -> dict[str, SecurityMasterItem]:
        if self._aliases is None:
            self._aliases = load_holding_aliases()
        return self._aliases

    @staticmethod
    def _candidate_countries(primary_country: str | None) -> list[str]:
        if primary_country == "KR":
            return ["KR"]
        if primary_country == "US":
            return ["US"]
        return ["KR", "US"]

    @staticmethod
    def _match_exact(normalized_name: str, master: list[SecurityMasterItem]) -> SecurityMasterItem | None:
        compact_name = compact_holding_key(normalized_name)
        matches = [
            item
            for item in master
            if normalize_holding_name(item.name) == normalized_name
            or compact_holding_key(item.name) == compact_name
        ]
        if len(matches) == 1:
            return matches[0]
        return None


def fetch_krx_master(bas_dd: str | None = None) -> list[SecurityMasterItem]:
    load_dotenv(_project_env_path())
    api_key = os.environ.get("KRX_API_KEY")
    if not api_key:
        return []

    start_date = _parse_bas_dd(bas_dd)
    for day_offset in range(10):
        target_date = start_date - timedelta(days=day_offset)
        if target_date.weekday() >= 5:
            continue
        target_bas_dd = target_date.strftime("%Y%m%d")
        rows = [
            *_fetch_krx_endpoint("stk_bydd_trd", target_bas_dd, api_key),
            *_fetch_krx_endpoint("ksq_bydd_trd", target_bas_dd, api_key),
        ]
        items = [
            SecurityMasterItem(
                name=str(row.get("ISU_NM", "")),
                ticker=str(row.get("ISU_CD", "")),
                exchange=str(row.get("MKT_NM", "")),
            )
            for row in rows
            if row.get("ISU_NM") and row.get("ISU_CD") and row.get("MKT_NM")
        ]
        if len(items) > 500:
            return items
    return []


def load_holding_aliases(path: Path | None = None) -> dict[str, SecurityMasterItem]:
    alias_path = path or _default_alias_path()
    if not alias_path.exists():
        return {}

    data = json.loads(alias_path.read_text(encoding="utf-8"))
    aliases: dict[str, SecurityMasterItem] = {}
    for row in data.get("aliases", []):
        ticker = str(row.get("ticker", "")).strip()
        exchange = str(row.get("exchange", "")).strip()
        name = str(row.get("name", "")).strip()
        if not ticker or not exchange or not name:
            continue

        item = SecurityMasterItem(
            name=name,
            ticker=ticker,
            exchange=exchange,
            source=str(row.get("source") or "manual_alias"),
        )
        for alias in row.get("aliases", []):
            key = compact_holding_key(str(alias))
            if key:
                aliases[key] = item
    return aliases


def _fetch_krx_endpoint(endpoint: str, bas_dd: str, api_key: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"{KRX_BASE_URL}/{endpoint}",
        headers={"AUTH_KEY": api_key},
        params={"basDd": bas_dd},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("OutBlock_1", [])


def fetch_us_master() -> list[SecurityMasterItem]:
    return [
        *parse_nasdaq_listed(_fetch_text(NASDAQ_LISTED_URL)),
        *parse_other_listed(_fetch_text(OTHER_LISTED_URL)),
    ]


def parse_nasdaq_listed(text: str) -> list[SecurityMasterItem]:
    rows = _parse_pipe_rows(text)
    return [
        SecurityMasterItem(
            name=row["Security Name"],
            ticker=row["Symbol"],
            exchange="NASDAQ",
        )
        for row in rows
        if row.get("Symbol") and row.get("Security Name") and row.get("Test Issue") != "Y"
    ]


def parse_other_listed(text: str) -> list[SecurityMasterItem]:
    exchange_map = {
        "A": "NYSE American",
        "N": "NYSE",
        "P": "NYSE Arca",
        "Z": "Cboe BZX",
        "V": "IEX",
    }
    return [
        SecurityMasterItem(
            name=row["Security Name"],
            ticker=row["ACT Symbol"],
            exchange=exchange_map.get(row.get("Exchange", ""), row.get("Exchange", "")),
        )
        for row in _parse_pipe_rows(text)
        if row.get("ACT Symbol") and row.get("Security Name") and row.get("Test Issue") != "Y"
    ]


def _parse_pipe_rows(text: str) -> list[dict[str, str]]:
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith("File Creation Time")
    ]
    if not lines:
        return []
    headers = lines[0].split("|")
    rows = []
    for line in lines[1:]:
        values = line.split("|")
        if len(values) != len(headers):
            continue
        rows.append(dict(zip(headers, values, strict=True)))
    return rows


def _fetch_text(url: str) -> str:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def _parse_bas_dd(bas_dd: str | None) -> datetime:
    if bas_dd:
        return datetime.strptime(bas_dd, "%Y%m%d")
    return datetime.today()


def _project_env_path() -> Path:
    return Path(__file__).resolve().parents[3] / ".env"


def _default_alias_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "holding_aliases.json"

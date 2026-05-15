from __future__ import annotations

import json
from pathlib import Path

from new_etf_insight.dart_client import get_api_key, fetch_filing_page

api_key = get_api_key()
payload = fetch_filing_page(api_key, "20260429", "20260429", page_no=1, page_count=100)

target = next(
    (f for f in (payload.get("list") or []) if f.get("rcept_no") == "20260429000010"),
    None,
)

out = Path("../artifacts/etf_candidates/temp_raw.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(target, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(target, ensure_ascii=False, indent=2))

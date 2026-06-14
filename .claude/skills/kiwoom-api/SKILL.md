---
name: kiwoom-api
description: Look up Kiwoom REST/WS API specs from kiwoom_api.xlsx (TR codes, request/response fields, examples). Use whenever you need a Kiwoom TR spec — e.g. "미체결 조회 TR", "kt10002 정정주문 필드", "what does ka10075 return". Avoids the recurring xlsx encoding/parse trial-and-error.
---

# Kiwoom API spec lookup

`kiwoom_api.xlsx` (repo root) holds one sheet per TR. Sheet titles are `한글명(코드)`,
e.g. `미체결요청(ka10075)`. Each sheet has Method/domain/URL, Request fields,
Response fields, and JSON examples.

Run the helper — never hand-roll openpyxl again, and never guess a TR spec from memory:

```bash
# list every TR (code + Korean name)
python .claude/skills/kiwoom-api/parse.py

# find TRs by Korean keyword
python .claude/skills/kiwoom-api/parse.py --grep 미체결

# dump full field table(s) for given code(s)
python .claude/skills/kiwoom-api/parse.py ka10075 kt10002
```

On Windows run with `PYTHONIOENCODING=utf-8` if the console still mangles Korean,
though the script already forces UTF-8 stdout.

Code map lives in `broker/kiwoom/tr.py` — add new TR constants there (single source
of truth; every call routes through it).

# STEP 2 — 채널 파싱 (channels.py)

**파일**: `broker/kiwoom/ws/channels.py` (신규)

## 이게 뭐고 왜 필요한가

키움 WS는 여러 종류의 메시지를 보낸다. 다 섞여서 옴:
- `LOGIN` 응답 (로그인 됐다)
- `PING` (살아있냐 확인)
- `REG` 응답 (구독 등록됐다)
- `REAL` (진짜 실시간 데이터 — 체결, 시세 등)

이 중 **우리가 EventBus로 흘려보낼 건 `REAL` 뿐**이다. 나머지(LOGIN/PING/REG)는
WS 매니저(STEP 3)가 자체적으로 처리하지 버스로 안 보냄.

또 `REAL` 메시지는 한 번에 **여러 건**이 묶여 올 수 있다 (`data` 가 리스트).
그리고 각 건은 키움 포맷(중첩된 dict)이라 그대로 쓰기 불편하다.

그래서 channels.py 가 하는 일 딱 하나:
> **키움 REAL 메시지 → `[(채널, 값딕셔너리), ...]` 평평한 리스트로 정리.**

WS 매니저는 이 결과를 받아 `for ch, vals in parse → bus.publish(ch, vals)` 만 하면 됨.

## 왜 매니저(STEP 3)랑 분리하나

"메시지 파싱" 과 "WS 연결 관리" 는 다른 일이다. 섞으면 나중에:
- 새 채널(시세 0B) 추가할 때 → channels.py 만 고치면 됨
- WS 재연결 로직 고칠 때 → manager.py 만 고치면 됨

각자 한 가지 일만 하니 테스트도 쉽다. channels.py 는 **네트워크 없이** 순수
함수로 테스트 가능 (dict 넣고 결과 확인). STEP 1 처럼 안전한 단위.

---

## 키움 REAL 메시지 생김새 (복습)

```json
{
  "trnm": "REAL",
  "data": [
    {
      "type": "00",
      "name": "주문체결",
      "item": "005930",
      "values": {
        "9001": "005930",
        "913": "체결",
        "905": "+매수",
        "910": "60700",
        "911": "1",
        "908": "094022"
      }
    }
  ]
}
```

- `trnm`: 메시지 종류. `"REAL"` 만 우리 관심.
- `data`: 실시간 건들의 **리스트** (여러 개 가능).
- `data[i].type`: 채널 ID (`"00"` = 체결). → 우리 `channel`.
- `data[i].values`: 실제 값 딕셔너리. → 우리 `payload`.
- `item`/`name`: 종목코드/채널이름. `values["9001"]` 에 종목코드 또 있으니 안 써도 됨.

---

## 정답 코드

```python
"""Parse Kiwoom realtime (REAL) WS messages into (channel, values) pairs.

Only ``trnm == "REAL"`` carries realtime data; LOGIN/PING/REG replies are
handled by the WS manager and yield nothing here. One REAL message may bundle
several items, so this returns a list.
"""

from __future__ import annotations


def parse_message(raw: dict) -> list[tuple[str, dict]]:
    """Return ``[(channel, values), ...]`` from a Kiwoom WS message.

    Non-REAL messages (LOGIN, PING, REG replies) return an empty list. Each
    REAL ``data`` entry maps its ``type`` to the channel and ``values`` to the
    payload that gets published on the bus.
    """
    if raw.get("trnm") != "REAL":
        return []

    out: list[tuple[str, dict]] = []
    for entry in raw.get("data", []):
        channel = entry.get("type")
        values = entry.get("values")
        if channel and isinstance(values, dict):
            out.append((channel, values))
    return out
```

## 코드 줄별 설명

- **`if raw.get("trnm") != "REAL": return []`**
  REAL 아니면 즉시 빈 리스트. LOGIN/PING/REG 가 여기 들어와도 그냥 `[]` 나옴.
  WS 매니저는 빈 리스트면 publish 안 하니 자연스럽게 무시됨.
  `raw["trnm"]` 아니라 `raw.get("trnm")` 쓴 이유: 키가 없어도 에러 안 나게 (방어).

- **`for entry in raw.get("data", [])`**
  `data` 가 리스트라 순회. `data` 키 자체가 없으면 `[]` 기본값으로 → 루프 0번.

- **`channel = entry.get("type")` / `values = entry.get("values")`**
  각 건에서 채널 ID와 값 딕셔너리 꺼냄.

- **`if channel and isinstance(values, dict)`**
  방어 코드. `type` 이 비었거나 `values` 가 dict 아니면 건너뜀.
  깨진 데이터가 와도 터지지 않고 그 건만 조용히 스킵.

- **`out.append((channel, values))`**
  `(채널, 값)` 튜플로 쌓음. 이게 WS 매니저가 그대로 `bus.publish(채널, 값)` 에 쓸 형태.

- **반환 `list[tuple[str, dict]]`**
  STEP 3에서:
  ```python
  for channel, values in channels.parse_message(msg):
      bus.publish(channel, values)
  ```
  이 한 줄에 딱 맞물림.

## 설계 노트

- **`item`/`name` 버림**: 종목코드는 `values["9001"]` 에 있고, 채널이름은 프론트에서
  안 씀. 필요하면 나중에 추가. 지금은 최소만.
- **채널별 분기 없음**: `"00"` 이든 `"0B"`(시세) 든 동일하게 `(type, values)` 로 처리.
  채널이 늘어도 이 함수는 안 바뀜 — 그게 "확장 가능" 의 의미. 채널별 *의미 해석*
  (어떤 필드가 체결가냐)은 프론트(STEP 8)가 함.

---

## 검증 (네트워크 없이)

`broker/` 에서 venv 파이썬으로:

```python
from kiwoom.ws.channels import parse_message

# 1) REAL 체결 메시지 → [("00", {...})]
real = {
    "trnm": "REAL",
    "data": [{
        "type": "00",
        "name": "주문체결",
        "item": "005930",
        "values": {"9001": "005930", "913": "체결", "910": "60700"},
    }],
}
print("REAL  :", parse_message(real))

# 2) PING → [] (실시간 데이터 아님)
print("PING  :", parse_message({"trnm": "PING"}))

# 3) LOGIN 응답 → []
print("LOGIN :", parse_message({"trnm": "LOGIN", "return_code": 0}))

# 4) REAL 인데 여러 건 묶임 → 2개 나옴
multi = {
    "trnm": "REAL",
    "data": [
        {"type": "00", "values": {"913": "체결"}},
        {"type": "0B", "values": {"10": "60700"}},
    ],
}
print("MULTI :", parse_message(multi))
```

기대 출력:
```
REAL  : [('00', {'9001': '005930', '913': '체결', '910': '60700'})]
PING  : []
LOGIN : []
MULTI : [('00', {'913': '체결'}), ('0B', {'10': '60700'})]
```

이게 나오면 STEP 2 통과:
- REAL 만 뽑고, PING/LOGIN 은 무시 (`[]`)
- 여러 건 묶여 와도 각각 분리

## 다음
STEP 3 — `manager.py` (진짜 키움 WS 연결: LOGIN → PING echo → REG → 수신 →
`parse_message` → `bus.publish`). 여기서 처음 실제 데이터가 들어옴.

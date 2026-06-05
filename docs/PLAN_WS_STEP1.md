# STEP 1 — EventBus (인메모리 pub/sub)

**파일**: `broker/kiwoom/ws/event_bus.py` (신규)

## 이게 뭐고 왜 필요한가

키움 WS 매니저(STEP 3)는 체결 데이터를 받는다. SSE 엔드포인트(STEP 4)는 그걸
브라우저로 내보낸다. 둘은 서로를 직접 알면 안 된다 — WS 매니저가 "지금 누가
듣고 있지?" 를 신경 쓰면 코드가 엉킨다.

그래서 가운데에 **EventBus** 를 둔다. 우체국 같은 것:
- WS 매니저는 "00 채널로 이거 발행해" 만 한다 (`publish`) — 누가 받는지 모름
- SSE 엔드포인트는 "나 전체 채널 들을래" 하고 큐를 등록한다 (`subscribe`)
- 발행되면 버스가 등록된 큐에 자동으로 넣어준다

이렇게 하면 채널이 100개 늘어도 발행자/구독자는 서로 안 바뀐다. 이게 "확장 가능한
파이프라인" 의 핵심.

## 왜 asyncio.Queue 인가

발행자(WS 매니저)와 구독자(각 SSE 연결)는 **다른 asyncio 태스크**에서 돈다.
태스크 사이로 데이터를 안전하게 넘기는 표준 도구가 `asyncio.Queue`.

- 발행자: `queue.put_nowait(item)` — 큐에 넣고 즉시 리턴 (안 기다림)
- 구독자: `await queue.get()` — 들어올 때까지 대기

버스는 "구독자별 큐 1개" 를 들고 있다가, publish 시 모든 해당 큐에 넣는다.

## `"*"` 와일드카드가 왜 필요한가

SSE 엔드포인트(STEP 4) 하나가 **모든 채널**(체결 00, 시세 0B, system ...)을
브라우저로 흘려보낸다. 채널마다 따로 구독하면 번거로우니, `"*"` 로 구독하면
어떤 채널이 발행되든 다 받게 한다.

규칙:
- `subscribe("00", q)` → `publish("00", ...)` 만 받음
- `subscribe("*", q)` → 모든 `publish(...)` 받음
- 그래서 `publish(ch, data)` 는 **`ch` 구독자 + `"*"` 구독자** 양쪽에 넣어야 함

---

## 정답 코드

```python
"""In-memory pub/sub bus bridging the Kiwoom WS feed to SSE clients.

The WS manager publishes channel events without knowing who listens; each SSE
connection subscribes a queue and drains it. A ``"*"`` subscription receives
every channel, which is how the SSE endpoint streams the whole feed.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class EventBus:
    """Channel-keyed fan-out to asyncio queues. One queue per subscriber."""

    def __init__(self) -> None:
        # channel -> set of subscriber queues. "*" is the all-channels bucket.
        self._subs: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, channel: str, queue: asyncio.Queue) -> None:
        """Register ``queue`` to receive events for ``channel`` (or "*" for all)."""
        self._subs.setdefault(channel, set()).add(queue)

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        """Remove ``queue`` from ``channel``. Safe to call if not present."""
        subs = self._subs.get(channel)
        if subs:
            subs.discard(queue)
            if not subs:
                del self._subs[channel]

    def publish(self, channel: str, payload: dict) -> None:
        """Fan ``payload`` out to ``channel`` subscribers and all "*" subscribers.

        Wraps as ``{"channel": ..., "payload": ...}`` so SSE clients can tell
        channels apart. Uses ``put_nowait``; a full queue drops the event with a
        warning rather than blocking the publisher.
        """
        event = {"channel": channel, "payload": payload}
        targets = self._subs.get(channel, set()) | self._subs.get("*", set())
        for queue in targets:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("event bus queue full, dropping %s event", channel)


# Process-wide singleton. Publishers and SSE endpoints import this same instance.
bus = EventBus()
```

## 코드 줄별 설명

- **`self._subs: dict[str, set[asyncio.Queue]]`**
  채널 이름 → 그 채널을 듣는 큐들의 집합. `set` 이라 같은 큐 중복 등록 방지 +
  제거 빠름.

- **`subscribe` — `setdefault(channel, set()).add(queue)`**
  채널이 처음이면 빈 set 만들고, 거기에 큐 추가. 한 줄로 "없으면 생성 후 추가".

- **`unsubscribe`**
  큐 빼고, 그 채널에 아무도 안 남으면 빈 set 도 삭제 (메모리 누수 방지).
  SSE 연결 끊길 때 호출됨 (STEP 4의 `finally`).

- **`publish` — `targets = 채널 구독자 | "*" 구독자`**
  집합 합집합(`|`). `00` 발행이면 `00` 직접 구독자 + 전체 구독자(`"*"`)에게 간다.
  SSE는 `"*"` 로 구독하므로 여기 포함됨.

- **`{"channel": channel, "payload": payload}` 로 감싸기**
  브라우저가 "이게 체결(00)이냐 시스템(system)이냐" 를 구분해야 한다. 그래서
  채널명을 같이 넣는다. 이 형식이 그대로 SSE → 프론트(STEP 6 `BrokerEvent`)까지 간다.

- **`put_nowait` + `QueueFull` 처리**
  발행자(WS 매니저)는 **절대 멈추면 안 된다** — 멈추면 PING echo 가 늦어 키움
  연결이 끊긴다. 그래서 기다리는 `await put` 대신 `put_nowait`. 혹시 구독자가
  느려 큐가 꽉 차면, 블로킹 대신 그 이벤트만 버리고 경고 로그.
  (기본 `asyncio.Queue()` 는 무한 크기라 실제론 거의 안 참. 방어 코드.)

- **`bus = EventBus()` 모듈 하단 싱글턴**
  `from kiwoom.ws.event_bus import bus` 하면 WS 매니저든 SSE든 **같은 인스턴스**를
  쓴다. 이게 둘을 잇는 연결점.

---

## 검증 (직접 실행)

`broker/` 에서 venv 파이썬으로:

```python
import asyncio
from kiwoom.ws.event_bus import bus

async def main():
    q_all = asyncio.Queue()    # "*" 전체 구독자 (SSE 역할)
    q_00 = asyncio.Queue()     # "00" 만 듣는 구독자

    bus.subscribe("*", q_all)
    bus.subscribe("00", q_00)

    bus.publish("00", {"913": "체결", "9001": "005930"})
    bus.publish("system", {"type": "connected"})

    # q_all 은 둘 다 받아야 함 (2개)
    print("q_all 1:", q_all.get_nowait())   # 00 이벤트
    print("q_all 2:", q_all.get_nowait())   # system 이벤트
    # q_00 은 00 만 (1개)
    print("q_00  1:", q_00.get_nowait())    # 00 이벤트
    print("q_00 비었나:", q_00.empty())      # True 여야 함

asyncio.run(main())
```

기대 출력:
```
q_all 1: {'channel': '00', 'payload': {'913': '체결', '9001': '005930'}}
q_all 2: {'channel': 'system', 'payload': {'type': 'connected'}}
q_00  1: {'channel': '00', 'payload': {'913': '체결', '9001': '005930'}}
q_00 비었나: True
```

이게 나오면 STEP 1 통과. `"*"` 가 전체를 받고, `"00"` 은 자기 것만 받는 게 확인됨.

> `kiwoom/ws/__init__.py` 빈 파일도 같이 만들어야 `kiwoom.ws` 패키지로 import 됨.

## 다음
STEP 2 — `channels.py` (키움 REAL 메시지 → `(채널, values)` 파싱)

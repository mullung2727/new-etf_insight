"""In-memory pub/sub bus bridging the Kiwoom WS feed to SSE clients.

The WS manager publishes channel events without knowing who listens; each SSE
connection subscribes a queue and drains it. A ``"*"`` subscription receives
every channel, which is how the SSE endpoint streams the whole feed.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


class EventBus:
    """Channel-keyed fan-out to asyncio ques. One queue per subscriber."""

    def __init__(self) -> None:
        # channel -> set of subscriber queues. "*" is the all-channels bucket.
        self._subs: dict[str, set[asyncio.Queue]] = {}
        # 최신 "system" 연결상태(sticky). connected/disconnected는 1회성 broadcast라
        # 나중에 붙은 SSE 구독자가 놓치면 영영 "끊김"으로 오표시 → 마지막 상태를 기억해
        # 구독 즉시 재생한다. (system만 상태성; 체결/틱 등은 transient라 재생 안 함.)
        self._last_system: dict | None = None

    def subscribe(self, channel: str, queue: asyncio.Queue) -> None:
        """Register ``queue`` to receive events for ``channel`` (or "*" for all).

        system 채널/전체 구독자에게는 마지막 연결상태를 즉시 1건 흘려 동기화한다.
        """
        self._subs.setdefault(channel, set()).add(queue)
        if channel in ("system", "*") and self._last_system is not None:
            try:
                queue.put_nowait(self._last_system)
            except asyncio.QueueFull:
                pass

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
        event = {"channel":channel, "payload": payload}
        if channel == "system":
            self._last_system = event  # 늦게 붙는 구독자용 sticky 상태 갱신
        targets = self._subs.get(channel, set()) | self._subs.get("*", set())
        for queue in targets:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(f"event bus queue full, dropping {channel} event")

bus = EventBus()

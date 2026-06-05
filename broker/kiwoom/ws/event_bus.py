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
        event = {"channel":channel, "payload": payload}
        targets = self._subs.get(channel, set()) | self._subs.get("*", set())
        for queue in targets:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(f"event bus queue full, dropping {channel} event")

bus = EventBus()

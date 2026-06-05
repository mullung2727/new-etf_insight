import asyncio
import logging

from kiwoom.ws.manager import KiwoomWSManager
from kiwoom.ws.event_bus import bus

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def main():
    q = asyncio.Queue()
    bus.subscribe("system", q)

    mgr = KiwoomWSManager()
    await mgr.start()

    try:
        event = await asyncio.wait_for(q.get(), timeout=15)
        print(">>> system 이벤트:", event)
    except asyncio.TimeoutError:
        print(">>> 15초 내 system 이벤트 없음 (연결/로그인 실패 의심)")

    # PING echo 동작 관찰 (10초 유지)
    print(">>> 10초 연결 유지하며 관찰...")
    await asyncio.sleep(10)

    await mgr.stop()
    print(">>> stop 완료")


asyncio.run(main())

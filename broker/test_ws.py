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
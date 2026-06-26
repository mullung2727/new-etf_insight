"""Single source of truth for Kiwoom TR codes + endpoints.

⚠️ VERIFY before real-money use. The official guide (openapi.kiwoom.com/guide)
is login-gated and JS-rendered, so these values are taken from third-party
wrappers and community docs and may differ for your account/version. If a TR
call returns return_code != 0 with "없는 TR" / param errors, fix the code here
— every call routes through these constants, so one edit fixes the whole app.
"""

from __future__ import annotations

# --- REST endpoints (grouped by domain path) ---
EP_STKINFO = "/api/dostk/stkinfo"  # 종목 정보/시세
EP_MRKCOND = "/api/dostk/mrkcond"  # 시세/호가
EP_CHART = "/api/dostk/chart"      # 차트 (일/주/월봉)
EP_ACNT = "/api/dostk/acnt"        # 계좌 (잔고/예수금)
EP_ORDR = "/api/dostk/ordr"        # 주문 (매수/매도/정정/취소)

# --- Quotes (시세) ---
TR_STOCK_INFO = "ka10001"   # 주식기본정보요청 (현재가 포함) → EP_STKINFO
TR_ORDERBOOK = "ka10004"    # 주식호가요청 → EP_MRKCOND
TR_DAILY_CHART = "ka10081"  # 주식일봉차트조회요청 → EP_CHART
TR_WATCHLIST_QUOTE = "ka10095"  # 관심종목정보요청 (복수종목 일괄시세, 등록 불요·stateless) → EP_STKINFO

# --- Account (계좌) ---
TR_BALANCE = "kt00018"      # 계좌평가잔고내역요청 → EP_ACNT
TR_DEPOSIT = "kt00001"      # 예수금상세현황요청 → EP_ACNT
TR_SETTLED_BALANCE = "kt00005"  # 체결잔고요청 (예수금D+0/D+1/D+2 + 보유종목) → EP_ACNT
TR_CNTR_HIST = "kt00007"    # 계좌별주문체결내역상세요청 (당일 체결 대조용) → EP_ACNT
TR_UNFILLED = "ka10075"     # 미체결요청 (체결확인·중복가드·백스톱용, G2부터 사용) → EP_ACNT
TR_RLZT_PL_TODAY = "ka10077"  # 당일실현손익상세요청 (수수료·세금 차감 net 손익) → EP_ACNT

# --- Orders (주문) ---
TR_ORDER_BUY = "kt10000"    # 주식 매수주문 → EP_ORDR
TR_ORDER_SELL = "kt10001"   # 주식 매도주문 → EP_ORDR
TR_ORDER_CANCEL = "kt10003" # 주식 취소주문 → EP_ORDR

# --- Condition search (조건검색) — WebSocket only, not REST ---
# Sent over wss as {"trnm": ...}. ka10171/ka10172 are the underlying TR ids.
WS_LOGIN = "LOGIN"
WS_CONDITION_LIST = "CNSRLST"  # 조건검색 목록조회 (ka10171)
WS_CONDITION_REQ = "CNSRREQ"   # 조건검색 단발요청 (ka10172)

# --- Realtime subscription (실시간 등록/해제) ---
WS_REG = "REG"        # 실시간 등록
WS_REMOVE = "REMOVE"  # 실시간 해제

# --- Realtime channel ids (실시간 항목 type) ---
RT_FILL = "00"        # 주문체결
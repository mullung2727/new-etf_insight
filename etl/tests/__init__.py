"""테스트 전역 안전장치 — 실제 Discord/Telegram 전송 차단.

**러너 무관**하게 걸려야 한다. 이 저장소는 `unittest` 로 테스트를 돌리는데 차단 장치가
`conftest.py`(pytest 전용 픽스처)에만 있어서, 실행 1회당 실제 웹훅이 69건 나가고 있었다.
테스트 패키지 import 시점에 거는 이 모듈이 두 러너를 모두 커버한다.

방어는 두 겹이다. env 만으로는 못 막는다는 걸 실측으로 확인했다:

1. **env 를 빈 문자열로 고정** — sender 들이 URL·토큰이 비면 전송을 스킵한다.
   삭제가 아니라 빈 값인 이유: `load_dotenv()` 는 이미 존재하는 키를 덮어쓰지 않으므로
   (override=False 기본) 테스트 도중 재주입돼도 빈 값이 유지된다. 삭제하면 그 자리에
   실제 URL 이 다시 들어온다.

2. **HTTP 계층 백스톱** — 1번이 뚫리는 경로가 실재한다. `patch.dict(os.environ, ...)`
   컨텍스트가 끝나며 **진입 시점 스냅샷을 통째로 복원**하는데, 그 스냅샷이 실제 URL 을
   담고 있으면 env 가 되살아난다(테스트가 서로를 오염시킨다). 그래서 discord.com /
   api.telegram.org 로 나가는 요청만 어댑터 단에서 가로채 가짜 204 를 돌려준다.
   **호스트를 특정해 막으므로 broker·KRX 등 다른 HTTP 호출은 그대로 나간다.**

백스톱이 발동하면 경고를 찍는다 — 조용히 막으면 "안 나갔다"와 "차단됐다"를 구분 못 한다.
"""
import os
import urllib.parse

from requests.adapters import HTTPAdapter
from requests.models import Response

NOTIFY_ENV = (
    "DISCORD_WEBHOOK_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_REPORT_TO_DISCORD_WEBHOOK_URL",
)

# 이 호스트로 나가는 요청만 막는다. 다른 호스트는 건드리지 않는다.
BLOCKED_HOSTS = frozenset({"discord.com", "discordapp.com", "api.telegram.org"})

# 로컬 상시 서버(broker:8001, api:8000, broker-web:3100)로 나가는 쓰기 요청을 막는다.
#
# 알림 백스톱이 호스트 기준이라 broker 는 통과했고, 그 구멍으로 run_close_bet 의
# 투자노트 기록이 운영 노트 DB 에 테스트 데이터를 5건 만들었다. 새 broker 호출을
# 추가할 때마다 같은 사고가 나므로 로컬 서버의 상태를 바꾸는 요청을 막는다.
#
# 외부 호스트는 제외한다 — KRX OpenAPI 는 조회를 POST 로 하고, 그건 정상 트래픽이다.
# 조용히 삼키면 테스트가 통과해 버리니 예외로 즉시 실패시킨다 — patch 하라는 신호다.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

blocked_count = 0


def block_real_notifications() -> None:
    """알림 관련 env 를 빈 값으로 고정. 여러 번 불러도 안전."""
    for key in NOTIFY_ENV:
        os.environ[key] = ""


def _install_http_backstop() -> None:
    if getattr(HTTPAdapter.send, "_notify_backstop", False):
        return

    original_send = HTTPAdapter.send

    def send(self, request, *args, **kwargs):
        global blocked_count
        host = (urllib.parse.urlparse(request.url).hostname or "").lower()
        if host in BLOCKED_HOSTS:
            blocked_count += 1
            print(
                f"[tests] BLOCKED outbound notification to {host} "
                f"(env guard leaked - see tests/__init__.py)"
            )
            resp = Response()
            resp.status_code = 204
            resp.url = request.url
            resp.request = request
            resp._content = b""
            return resp
        if host in LOCAL_HOSTS and (request.method or "").upper() in WRITE_METHODS:
            blocked_count += 1
            raise AssertionError(
                f"[tests] BLOCKED {request.method} {request.url} — "
                "테스트가 로컬 서버의 상태를 바꾸려 했다. broker 를 호출하는 함수는 "
                "테스트에서 patch 해라 (tests/__init__.py)"
            )
        return original_send(self, request, *args, **kwargs)

    send._notify_backstop = True
    HTTPAdapter.send = send


block_real_notifications()
_install_http_backstop()

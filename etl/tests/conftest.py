"""테스트 전역 안전장치 — 실제 Discord/Telegram 전송 차단.

sender 는 웹훅/토큰 env 가 없으면 스킵(notify.send_discord/send_telegram 등).
개별 테스트의 env pop 은 다른 모듈의 load_dotenv() 재주입으로 깨져 픽스처(2026-06-15)가
실제 웹훅으로 샜다. 여기서 매 테스트마다 관련 env 를 지워 근본 차단한다.
(requests.post 는 건드리지 않는다 — 전역 공유라 broker/KRX 호출까지 오염됨.)
"""
import pytest

_NOTIFY_ENV = (
    "DISCORD_WEBHOOK_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_REPORT_TO_DISCORD_WEBHOOK_URL",
)


@pytest.fixture(autouse=True)
def _block_real_notifications(monkeypatch):
    for key in _NOTIFY_ENV:
        monkeypatch.delenv(key, raising=False)
    yield

"""pytest 러너용 얇은 래퍼 — 실제 차단 로직은 `tests/__init__.py` 에 있다.

패키지 import 시점 차단만으로도 충분하지만, 테스트가 직접 env 를 채워 넣는 경우를
대비해 매 테스트마다 다시 빈 값으로 고정한다. unittest 러너에는 이 파일이 안 걸리므로
차단의 본체는 `tests/__init__.py` 여야 한다.
"""
import pytest

from . import block_real_notifications


@pytest.fixture(autouse=True)
def _block_real_notifications():
    block_real_notifications()
    yield

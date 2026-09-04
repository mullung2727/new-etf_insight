# 눌림목 장전 청산 방지 설계 및 구현 계획

> **For Hermes:** 이 문서는 구현 인수인계용이다. 아래 범위만 TDD로 수정하고, 종가베팅·공용 주문함수·전략 파라미터에는 손대지 않는다.

**Goal:** 눌림목 청산 워커가 09:00 이전 예상체결가/장전 호가를 TP·SL로 판정해 시장가 매도를 선제 제출하지 못하게 한다.

**Architecture:** Windows Task는 08:50에 그대로 시작해 프로세스 기동 여유를 유지하되, `run_pullback_exit.py` 내부에 09:00 정규장 시작 가드를 둔다. 09:00 전에는 체결 정산·시세 조회·잔고 조회·TP/SL 판정·신규 매도 주문을 모두 건너뛰고 대기한다. 이 정책은 눌림목 청산 프로세스에만 적용한다.

**Tech Stack:** Python 3.14 (`pyproject.toml`은 `requires-python = ">=3.12"`), `unittest`, Windows Task Scheduler, PowerShell runner, Kiwoom broker REST

---

## 1. 결론

현재 눌림목 청산 워커는 08:50부터 실행되지만 TP/SL 판단에 정규장 시작 시각 가드가 없다. 그 결과 장전 예상호가가 TP 기준을 넘으면 09:00 전에 시장가 매도 주문을 제출하고, 주문은 시초가 단일가에서 TP 기준보다 훨씬 낮은 가격으로 체결될 수 있다.

2026-09-04 써니전자(004770) 사례로 이 결함이 실계좌에서 확인됐다.

- 매수가: 2,050원 × 48주
- TP 4% 기준가: 2,132원
- 매도 주문번호: `0004668`
- Kiwoom 주문 시각: 08:53:14
- 주문 종류: KRX 시장가 매도
- 실제 체결가: 2,055원 × 48주
- 가격차익: +240원(+0.2439%)
- 수수료 20원 + 세금 196원
- 최종 순실현손익: +24원(+0.02%)
- 원장 기록: `exit_reason='tp'`

즉 원장은 “4% 익절”로 기록했지만 실제 체결 수익률은 거의 0%였다. 이번 수정은 TP 비율을 바꾸는 작업이 아니라 **TP/SL 판단에 사용할 수 없는 장전 호가를 청산 신호에서 제외하는 작업**이다.

---

## 2. 확인된 사실과 원인

### 2.1 운영 증거

1. 등록된 `\OpenClaw\trading-exit` 작업은 활성화 상태이며 시작 시각은 08:50이다.
2. 작업은 `ops/scheduled-tasks/run-trading-exit.ps1`을 실행한다.
3. runner는 `etl/scripts/run_pullback_exit.py`를 `--dry-run false`, 5초 폴링으로 실행한다.
4. `run_pullback_exit.py::main`은 09:00 이전 여부를 확인하지 않고 매 루프마다 `settle_sell_orders()`와 `run_cycle()`을 호출한다.
5. `run_cycle()`은 장전에도 `fetch_best_bids()` 결과를 `decide_exit()`에 넣고, TP/SL이면 `market_order(..., "sell", "pullback_exit", ...)`를 호출한다.
6. 써니전자 매도 주문은 실제로 08:53:14에 접수됐고 2,055원에 전량 체결됐다.

### 2.2 근본 원인

스케줄 시작 시각 08:50 자체가 직접 원인은 아니다. **워커 내부에 “신규 청산 판단은 09:00 이후만 허용”이라는 도메인 가드가 없는 것**이 근본 원인이다.

스케줄만 09:00으로 옮기면 수동 실행, 재시작, 다른 스케줄 등록에서 같은 결함이 재발할 수 있다. 따라서 정책은 반드시 Python 워커 내부에서 강제해야 한다.

### 2.3 이전 검토와의 관계

`docs/PULLBACK_PREOPEN_EXIT_REVIEW.md`는 2026-08-07 장전 SL 사례 2건에서 조기 매도가 손실을 줄였다는 관찰을 근거로 수정을 보류했다. 그 관찰은 유효하지만 다음을 보장하지 않는다.

- 장전 예상호가가 실제 시가/체결가와 일치한다.
- 장전 TP 판정이 TP 수준의 수익으로 체결된다.
- 향후 장전 SL 판정도 항상 손실을 줄인다.

써니전자 사례는 반대 방향의 실제 증거다. 장전 TP 신호가 4% 수익을 보장하지 못했고 원장 의미까지 왜곡했다. 따라서 이전 문서의 “재검토 전까지 변경하지 않는다” 결정은 이 설계문서로 대체한다.

---

## 3. 수정 정책

### 3.1 확정 정책

- 눌림목 TP/SL·만기 청산의 **신규 판단 및 주문은 09:00:00 이상**에서만 허용한다.
- 09:00 전에는 다음 작업을 실행하지 않는다.
  - `settle_sell_orders()`
  - `run_cycle()`
  - 시세 조회
  - 잔고 조회
  - 보유일 차감
  - TP/SL/강제청산 판정
  - 신규 매도 주문
- Windows Task 시작 시각 08:50은 유지한다.
- 15:19 만기 강제청산, 15:25 종료, 5초 폴링은 유지한다.
- TP +4%, SL -4%, 최대 3거래일 설정은 변경하지 않는다.

**가드 밖에 남기는 것:** `expire_stale_orders()`는 루프 진입 **전** 1회 호출이며 가드를 적용하지
않는다(`run_pullback_exit.py:255`). 전일 미체결을 `expired`로 표시하는 `UPDATE` 한 건일 뿐
broker를 호출하지 않아(`:52-67`) "09:00 이전 broker 조회·청산 판단·매도 주문 금지"에 걸리지
않는다. 여기까지 막으면 전일 미체결 정리가 09:00으로 밀리므로 **의도적으로 제외한다.**

### 3.2 왜 08:50 스케줄을 유지하는가

- Python/환경 로딩 실패를 정규장 전에 드러낼 수 있다.
- 09:00 스케줄 지연이나 Task Scheduler 등록 차이에 정책을 의존하지 않는다.
- 재시작·수동 실행에서도 Python 내부 가드가 동일하게 적용된다.
- XML 재등록 없이도 수정 효과를 낼 수 있다.

### 3.3 범위 밖

다음은 이번 수정에서 하지 않는다.

- TP를 4%에서 5%로 변경
- 장전 SL만 예외적으로 허용
- 시장가를 지정가로 변경
- 연속 2회 신호 확인, 슬리피지 제한 등 신규 전략 추가
- 종가베팅 청산 워커 변경
- `trading_batch_common.py` 또는 공용 `market_order()` 변경
- broker 주문 라우트·키움 wire 변경
- 기존 원장 행의 `exit_reason` 또는 손익 backfill
- Windows Task 시작 시각 변경

장전 SL만 허용하는 비대칭 정책은 과거 2건에 과적합될 수 있고 “예상호가는 실제 체결 가능 가격이 아니다”라는 동일한 문제를 남기므로 채택하지 않는다.

---

## 4. 제안 구현

### Task 1: 정규장 시작 시각 판정 함수 추가

**Objective:** 문자열 직접 비교가 루프 곳곳에 흩어지지 않도록 순수 경계 판정 함수를 추가한다.

**Files:**
- Modify: `etl/scripts/run_pullback_exit.py`
- Test: `etl/tests/test_pullback_exit.py`

**Step 1: 실패 테스트 작성**

`test_pullback_exit.py`에 경계값을 고정한 테스트를 추가한다.

```python
from datetime import datetime

from scripts.run_pullback_exit import is_exit_window_started


def _t(hms: str) -> datetime:
    return datetime.fromisoformat(f"2026-09-04T{hms}+09:00")


class ExitWindowTest(unittest.TestCase):
    def test_preopen_is_blocked(self):
        self.assertFalse(is_exit_window_started(_t("08:50:00"), "09:00:00"))
        self.assertFalse(is_exit_window_started(_t("08:59:59"), "09:00:00"))

    def test_regular_session_start_is_allowed(self):
        self.assertTrue(is_exit_window_started(_t("09:00:00"), "09:00:00"))
        self.assertTrue(is_exit_window_started(_t("15:19:00"), "09:00:00"))
```

**Step 2: 테스트가 실패하는지 확인**

```bash
PYTHONPATH=etl uv run --project etl python -m unittest etl.tests.test_pullback_exit.ExitWindowTest -v
```

Expected: `ImportError` 또는 함수 미정의로 FAIL.

**Step 3: 최소 구현**

`run_pullback_exit.py`에 다음 의미의 순수 함수를 추가한다.

```python
def is_exit_window_started(now: datetime, start_hms: str) -> bool:
    return now.strftime("%H:%M:%S") >= start_hms
```

기존 코드가 이미 시각 문자열을 `HH:MM:SS`로 비교하므로 같은 패턴을 재사용한다. 새 시간 라이브러리나 공용 추상화는 만들지 않는다.

**Step 4: 경계 테스트 통과 확인**

위 테스트를 다시 실행해 PASS를 확인한다.

---

### Task 2: 루프 1회분을 호출 가능하게 분리

**Objective:** 가드가 `settle_sell_orders()`·`run_cycle()`보다 **앞에 있다**는 사실을 테스트가 붙잡을 수 있게 한다.

Task 1의 `is_exit_window_started()`만으로는 부족하다. 그건 문자열 비교만 검증하므로, 가드를
`settle_sell_orders()` **뒤로** 옮겨도 통과한다. 이 수정의 실질은 경계 판정이 아니라
**09:00 전에 그 두 함수가 호출되지 않는 것**이므로, 호출 여부를 단언할 수 있는 지점이 필요하다.

**Files:**
- Modify: `etl/scripts/run_pullback_exit.py` (`main()`)
- Test: `etl/tests/test_pullback_exit.py`

**Step 1: 실패 테스트 작성**

가짜 `settle`/`cycle`을 주입해 **호출 횟수**를 단언한다. 문자열이 아니라 계약을 본다.

```python
class ExitLoopStepTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.args = argparse.Namespace(
            window_start="09:00:00", force_exit_time="15:19:00", stop_time="15:25:00",
        )

    def _step(self, hms, counted=False):
        now = datetime.fromisoformat(f"2026-09-04T{hms}+09:00")
        return run_loop_step(
            now, self.args, "http://broker", {}, True, counted,
            settle=lambda *a: self.calls.append("settle"),
            cycle=lambda *a: self.calls.append("cycle"),
        )

    def test_preopen_touches_no_broker_function(self):
        self.assertEqual(self._step("08:53:14")[0], "wait")
        self.assertEqual(self.calls, [])

    def test_regular_session_runs_both(self):
        self.assertEqual(self._step("09:00:00")[0], "ran")
        self.assertEqual(self.calls, ["settle", "cycle"])

    def test_stop_time_wins_over_window(self):
        self.assertEqual(self._step("15:25:00")[0], "stop")
        self.assertEqual(self.calls, [])
```

**Step 2: 테스트가 실패하는지 확인**

```bash
PYTHONPATH=etl uv run --project etl python -m unittest etl.tests.test_pullback_exit.ExitLoopStepTest -v
```

Expected: `run_loop_step` 미정의로 FAIL.

**Step 3: 최소 구현**

`main()`의 루프 본문을 그대로 옮긴 함수 하나를 만든다. 새 클래스·새 추상화는 만들지 않는다.

```python
def run_loop_step(now, args, broker_url, config, dry_run, counted,
                  settle=settle_sell_orders, cycle=run_cycle) -> tuple[str, bool]:
    """루프 1회분 → (동작, 갱신된 counted). 동작은 stop / wait / ran.

    settle·cycle 을 인자로 받는 건 테스트가 '09:00 전에는 둘 다 안 불린다'를
    단언하기 위해서다. 운영 호출부는 기본값을 그대로 쓴다.
    """
    hms = now.strftime("%H:%M:%S")
    if hms >= args.stop_time:
        return "stop", counted
    if not is_exit_window_started(now, args.window_start):
        return "wait", counted
    today = now.strftime("%Y%m%d")
    settle(DEFAULT_WATCHLIST_DB, broker_url, today)
    force = not counted and hms >= args.force_exit_time
    cycle(DEFAULT_WATCHLIST_DB, broker_url, config, today, force, dry_run)
    return "ran", counted or force
```

`main()`은 이걸 부르기만 한다.

```python
parser.add_argument("--window-start", default="09:00:00")
...
while True:
    action, counted = run_loop_step(now_seoul(), args, broker_url, config, dry_run, counted)
    if action == "stop":
        break
    time.sleep(args.poll_sec)
```

**Step 4: force 의미 보존 확인**

기존 동작이 유지되는지 테스트로 확인한다.

- 09:00 전에는 `counted`가 바뀌지 않는다 → `("08:53:14", counted=False)` 반환값이 `False`
- 15:19에 force가 서고 `counted`가 True로 바뀐다 → 반환 `("ran", True)`
- 이미 `counted=True`면 15:19에도 force는 서지 않는다 → `cycle` 인자 `force=False`
- `advance_holding_day()`는 기존처럼 `run_cycle(force=True)` 안에서만 돈다(`:212-213`)

---

### Task 3: 운영 runner에 정책값 명시

**Objective:** Python 기본값뿐 아니라 운영 action에서도 정규장 시작 정책이 보이게 한다.

**Files:**
- Modify: `ops/scheduled-tasks/run-trading-exit.ps1`
- Test: `etl/tests/test_pullback_schedule.py`

**Step 1: 실패 테스트 작성**

```python
def test_trading_exit_declares_regular_session_start(self):
    text = (OPS / "run-trading-exit.ps1").read_text(encoding="utf-8")
    self.assertIn('"--window-start", "09:00:00"', text)
```

**Step 2: runner 인자 추가**

`$commonArgs`에 다음을 추가한다.

```powershell
"--window-start", "09:00:00",
```

기존 `--force-exit-time 15:19:00`, `--stop-time 15:25:00`은 유지한다.

**Step 3: XML은 변경하지 않는다**

`ops/scheduled-tasks/trading-exit.xml`의 08:50 `StartBoundary`와 등록 작업은 그대로 둔다. runner 파일은 매 실행 시 직접 읽히므로 XML 재등록은 필요하지 않다.

---

### Task 4: 기존 회귀 테스트 실행

**Objective:** 눌림목 주문·검증·청산과 스케줄 계약에 영향이 없는지 확인한다.

**Commands:**

```bash
PYTHONPATH=etl uv run --project etl python -m unittest \
  etl.tests.test_pullback_exit \
  etl.tests.test_pullback_schedule -v
```

전체 눌림목 회귀:

```bash
PYTHONPATH=etl uv run --project etl python -m unittest discover \
  -s etl/tests -p 'test_*pullback*.py' -v
```

PowerShell 구문 확인:

```bash
powershell.exe -NoProfile -NonInteractive -Command \
  '$e=$null; [System.Management.Automation.Language.Parser]::ParseFile("ops/scheduled-tasks/run-trading-exit.ps1",[ref]$null,[ref]$e) | Out-Null; if($e.Count){$e | Out-String; exit 1}'
```

Expected:

- 신규 08:59:59 차단 / 09:00:00 허용 테스트 PASS
- 기존 TP/SL, 보유일 차감, 체결 정산, missing/expired 테스트 PASS
- 기존 스케줄 격리 테스트 PASS
- PowerShell AST 오류 0건

---

### Task 5: 배포 후 읽기 전용 검증

**Objective:** 다음 거래일에 주문을 유발하지 않는 방식으로 정책 적용을 확인한다.

1. 등록 작업이 계속 `Ready/Running`, `Enabled=True`, 08:50 시작인지 확인한다.
2. 실제 action이 `run-trading-exit.ps1`을 가리키는지 확인한다.
3. 08:50~08:59 로그에서 다음이 없는지 확인한다.
   - `[pullback-exit] SELL`
   - broker `POST /orders/strategy` 또는 매도 주문번호
4. 09:00 이후 정상적으로 시세/잔고 조회와 TP/SL 감시가 시작되는지 확인한다.
5. Kiwoom 당일 주문내역에서 눌림목 청산 주문 시각이 모두 09:00:00 이후인지 확인한다.
6. 종가베팅 `close-bet-exit` 작업의 주문 시각과 동작이 바뀌지 않았는지 확인한다.

검증 목적으로 작업을 수동 실행하지 않는다. 장중 실행은 실제 주문을 낼 수 있다.

---

## 5. 요구사항-테스트 매핑

- 09:00 경계 판정 → `ExitWindowTest.test_preopen_is_blocked`, `ExitWindowTest.test_regular_session_start_is_allowed`
- **09:00 전 broker 조회·청산 판단 실행 금지** → `ExitLoopStepTest.test_preopen_touches_no_broker_function` (가드가 `settle_sell_orders()` 뒤로 밀리면 이 테스트가 깨진다)
- 09:00 이후 정상 실행 → `ExitLoopStepTest.test_regular_session_runs_both`
- 15:25 종료가 window보다 우선 → `ExitLoopStepTest.test_stop_time_wins_over_window`
- force·counted 의미 보존 → `ExitLoopStepTest.test_force_marks_counted_once`
- 운영 runner 정책 명시 → `test_trading_exit_declares_regular_session_start`
- 눌림목만 변경 → 기존 `test_trading_wrappers_call_only_pullback_scripts`
- force/보유일 의미 유지 → 기존 `test_holding_day_decrements_once_per_confirmed_market_day`, `test_third_market_day_sets_expiry_and_returns_due_count`
- 중복 매도 방지 유지 → 기존 `test_sell_order_state_blocks_duplicate_and_fill_closes_position`

---

## 6. 위험과 한계

1. **09:00 이후에도 시장가 슬리피지는 존재한다.** 이번 수정은 장전 예상호가 문제만 제거하며 TP 가격 체결을 보장하지 않는다.
2. **과거 장전 SL의 손실 회피 효과는 포기한다.** 기존 2건에서는 유리했지만 일반화할 표본과 체결 가능성 보장이 없다.
3. **첫 09:00 호가 변동성이 크다.** 연속 신호 확인이나 지정가 전환은 별도 실험 없이 추가하지 않는다.
4. **로그 buffering에 주의한다.** 현재 runner의 `Start-Process -Wait` + stdout redirect 구조에서는 실행 중 파일이 비어 보일 수 있다. 적용 확인은 Kiwoom 주문시각과 종료 후 로그를 함께 본다.
5. **과거 원장은 수정하지 않는다.** 써니전자 행은 실제로 해당 reason으로 주문된 역사이므로 `exit_reason='tp'`를 소급 변경하지 않는다.

---

## 7. 완료 기준

- `run_pullback_exit.py`가 09:00 이전 broker 조회·청산 판단·매도 주문을 수행하지 않는다.
- 09:00 이후 기존 TP +4%, SL -4%, 3거래일, 15:19 강제청산이 그대로 동작한다.
- 운영 runner가 `--window-start 09:00:00`을 명시한다.
- 눌림목 관련 테스트와 PowerShell AST 검증이 통과한다.
- 종가베팅·공용 주문함수·broker·전략 config에는 diff가 없다.
- 다음 거래일 Kiwoom 주문내역에서 눌림목 매도 주문 시각이 09:00 이전에 존재하지 않는다.

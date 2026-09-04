# Git Workflow Guide

Use this skill when committing, branching, opening a PR, or handling CodeRabbit review comments.

이 저장소는 CodeRabbit 리뷰를 쓴다. CodeRabbit 은 **PR 에서만** 돈다 —
`main` 직접 커밋은 리뷰 없이 들어간다.

리뷰는 **자동으로 시작되지 않는다.** 코멘트로 직접 트리거해야 한다 (아래 `## 리뷰 트리거`).

`main` 커밋은 PreToolUse hook(`.claude/hooks/block-main-commit.sh`)이 하드 차단한다.
차단 메시지가 뜨면 브랜치를 따라는 뜻이다.

## 브랜치 따기 전

```bash
git log origin/main..main
```

**비어 있어야 한다.** 남아 있으면 그 커밋들이 내 PR 브랜치에 딸려 들어가 함께 리뷰·머지된다.
먼저 사용자에게 `! git push origin main` 을 요청해 정리한 뒤 브랜치를 딴다.

> 2026-09-03 PR #2: 푸시 안 된 로컬 커밋 3개가 딸려 들어갔고, rebase 머지가 그 3개까지
> 새 해시로 다시 써서 로컬 main 이 원격과 갈라졌다.

## 커밋

**이 저장소는 공개(public) 전제다.** 커밋하는 모든 것은 누구나 읽는다고 보고 판단한다.

- **검증된 유효전략은 커밋하지 않는다.** 백테스트나 실운용에서 성과가 확인된 전략의
  진입·청산 조건, 튜닝된 파라미터 값, 그 결과 문서는 `research/private/` 에 둔다
  (`.gitignore` 처리됨). 공개되면 알파가 사라진다.
- 성과가 검증되지 않은 탐색·실패 기록은 공개해도 된다. 그대로 `research/` 에 커밋한다.
- 유효/무효 판단이 애매하면 커밋하지 말고 사용자에게 묻는다.
- 자격증명은 어떤 경우에도 커밋하지 않는다 (`.env` 는 루트 하나, `.gitignore` + hook 차단).

- 주제별로 쪼갠다. 한 PR 에 여러 주제가 들어가도 되지만 커밋은 섞지 않는다.
- 커밋 메시지 본문은 **왜**를 쓴다. 무엇을 바꿨는지는 diff 에 있다.
- 리뷰 지적을 반려했으면 그 사유를 커밋 메시지에 남긴다 — 재리뷰 때 문맥이 된다.

## push

push 는 사용자가 한다. 에이전트는 커밋까지만 하고 `! git push -u origin <branch>` 를 안내한다.
(과거 wincredman 크래시 이력. 실패하면 사용자에게 넘긴다.)

**push 뒤에 뭘 할지 같이 적는다.** PR 생성은 되돌리기 번거로운 외부 행동이다 —
공개 PR 이 생기고, 사용자 계정으로 남고, 유료 리뷰 한도를 소진한다.
push 만 부탁하고 말없이 PR 을 만들면 사용자는 통보받지 못한 채 그 일을 당한다.

```text
! git push -u origin <branch>
→ push 되면 PR 만들고 리뷰 감시 띄운다
```

PR 이 이미 열려 있으면 PR 을 새로 만들지 않는다. push 뒤에 리뷰를 다시 트리거한다.

```text
! git push
→ PR #<번호> 에 리뷰 트리거하고 감시 띄운다
```

## 리뷰 트리거

**PR 을 만들거나 push 한 것만으로는 리뷰가 시작되지 않는다.** 매번 코멘트를 달아야 한다.

```bash
gh pr comment <num> --body "@coderabbitai review"
```

이유: 이 저장소는 CodeRabbit OSS 무료 티어를 쓴다. 플랜 기능은 Team(라인별 리뷰 포함)이지만,
**스타 10개 미만인 공개 저장소는 자동 리뷰 대상에서 빠진다.** 스타가 10개를 넘으면 그때부터
PR 이벤트로 자동으로 돈다.

### 한도가 있다 — 아껴 쓴다

**시간당 1회.** 소진되면 `Review limit reached. Next included review available in N minutes.`
가 뜨고 그 리뷰는 아예 돌지 않는다.

- 한 PR 에 트리거는 **한 번만**. 지적을 고쳐 push 한 뒤 재리뷰가 필요할 때 나머지 한도를 쓴다.
- `@coderabbitai full review` 는 쓰지 않는다. 이미 리뷰한 커밋까지 다시 보느라 한도만 태운다.
  기본 `review` 가 증분 리뷰다.
- 한도가 없는데 리뷰가 꼭 필요하면 사용자에게 "N분 뒤 재트리거" 와 "리뷰 없이 머지" 중 고르게 한다.
  문서·설정만 바뀐 PR 이면 후자를 먼저 권한다.

## 리뷰 기다리기

리뷰를 트리거한 직후에 감시를 띄운다. 트리거 없이 띄우면 오지 않는 리뷰를 기다린다.
사용자가 "리뷰 왔나" 묻지 않아도 도착하는 즉시 알림이 온다.

```
Monitor(command="bash scripts/watch_coderabbit.sh", persistent=false, timeout_ms=1800000)
```

**끄지 않아도 된다.** 리뷰를 기다리는 PR 이 없어지면 스크립트가 스스로 종료한다
(리뷰 도착, 열린 PR 이 전부 머지되거나 닫힘, 또는 30분 타임아웃).
세션에 남아 도는 폴링을 만들지 않는다.

알림 문구가 `지적 없음` 이면 CodeRabbit 이 리뷰를 마쳤고 지적이 0건이라는 뜻이다
(그때는 review 객체가 생기지 않고 요약 코멘트만 갱신된다). 따로 확인하지 않아도 된다.

이미 리뷰가 끝난 상태에서 띄우면 첫 바퀴에 바로 끝난다 — 그때는 리뷰 내용을 직접 읽는다.

## CodeRabbit 지적 다루기

**그대로 수용하지 않는다.** 답하기 전에 세 가지를 코드로 확인한다.

1. **이 PR 의 diff 안인가** — `git diff main...HEAD -- <file>` 로 확인.
   CodeRabbit 은 diff 밖 기존 코드까지 지적한다.
2. **전제가 사실인가** — 스키마 컬럼 타입, 서버 바인딩 주소, 호스트 해석 여부 등
   지적이 깔고 있는 사실을 직접 확인한다.
3. **의도된 설계를 문맥 없이 지적한 건 아닌가** — `git log -S "<코드>"` 로
   그 줄이 왜 그렇게 됐는지 찾는다.

확인 없이 "맞다"고 답하지 않는다. 사용자가 그 전제로 잘못된 수정을 하게 된다.

> 2026-09-03 PR #2: Critical 로 올라온 "수동 매수에 금액 상한 없음"은 `74d54c8` 에서
> 일부러 나눈 설계였고 PR diff 밖이었다. broker 는 127.0.0.1 바인딩이라 "public" 도 아니었다.

읽기 명령:

```bash
gh pr view <num> --json reviews,comments
gh api repos/<owner>/<repo>/pulls/<num>/comments --paginate \
  --jq '.[] | "\(.path):\(.line // .original_line) | \(.body | split("\n")[2])"'
```

증분 리뷰 결과는 **새 review 가 아니라 기존 요약 코멘트 갱신**으로 들어올 수 있다.
`gh pr view --json reviews` 만 보면 놓친다 — `issues/<num>/comments` 의 `updated_at` 도 본다.

## 머지 후

rebase 머지는 커밋 해시를 새로 쓴다. 로컬 main 이 원격과 갈라지므로 재정렬한다.

```bash
git fetch --prune origin
git log --oneline origin/main..main   # 남은 게 있으면 원격에 같은 트리가 있는지 먼저 확인
git reset --hard origin/main
```

`git reset --hard` 전에 로컬 전용 커밋의 트리 해시가 원격 쪽 커밋과 같은지 대조한다
(`git rev-parse <sha>^{tree}`). 다르면 리셋하지 말고 사용자에게 알린다.

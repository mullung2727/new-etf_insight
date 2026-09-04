#!/usr/bin/env bash
# 열린 PR 중 CodeRabbit 리뷰를 아직 못 받은 게 있으면 기다렸다가, 도착하면 한 줄 찍는다.
# 기다릴 PR 이 없어지면 스스로 종료한다 — 끄는 걸 사람이 기억하지 않아도 되게.
#
# Claude Code 의 Monitor 도구로 띄운다. stdout 한 줄이 알림 하나다.
#   Monitor(command="bash scripts/watch_coderabbit.sh", persistent=false, timeout_ms=1800000)
#
# "리뷰 받음" 판정: PR 최신 커밋 시각보다 나중에 CodeRabbit 활동이 있으면 끝난 것.
# 지적이 있으면 review 객체로, 지적이 0건이면 요약 코멘트 갱신으로만 오기 때문에 둘 다 본다.
set -u

REPO="${REPO:-mullung2727/new-etf_insight}"
INTERVAL="${INTERVAL:-60}"

declare -A seen  # PR 번호 -> 마지막으로 알린 CodeRabbit 활동 시각

latest_activity() {  # 이 PR 의 가장 최근 CodeRabbit 활동 시각(ISO8601). 없으면 빈 문자열
  local pr="$1"
  {
    # 제출 안 된 리뷰는 submitted_at 이 null 이다. 걸러내지 않으면 "null" 이 정렬 맨 뒤로
    # 밀려 최신 활동으로 뽑히고, 리뷰가 오기도 전에 도착으로 오판한다.
    gh api --paginate "repos/$REPO/pulls/$pr/reviews" \
      --jq '.[] | select(.user.login=="coderabbitai[bot]") | select(.submitted_at) | .submitted_at' 2>/dev/null
    # 처리 중 안내는 아직 리뷰가 아니다
    gh api --paginate "repos/$REPO/issues/$pr/comments" \
      --jq '.[] | select(.user.login=="coderabbitai[bot]")
            | select(.body | test("Currently processing") | not) | .updated_at' 2>/dev/null
  } | sort | tail -1
}

headline() {  # $1=PR 번호, $2=이번에 감지한 활동 시각. 알림에 붙일 한 줄
  local pr="$1" ts="$2" h
  # 감지된 시각과 일치하는 리뷰만 고른다. 무조건 최신 리뷰를 읽으면 요약 코멘트
  # 갱신으로 감지된 건에 지난 리뷰 제목이 딸려 나온다 — 지적 0건이 "지적 1건"이 되던 원인.
  # 배열로 모아 last 를 쓰지 않는다. --paginate 는 페이지마다 --jq 를 따로 돌려서
  # last 가 "마지막 페이지의 마지막"이 된다. 항목 단위로 뽑아야 페이지 수와 무관하다.
  # body 가 없을 수 있으니 split 전에 빈 문자열로 받는다.
  h=$(gh api --paginate "repos/$REPO/pulls/$pr/reviews" \
    --jq ".[] | select(.user.login==\"coderabbitai[bot]\") | select(.submitted_at==\"$ts\")
          | (.body // \"\") | split(\"\n\")[0]" 2>/dev/null | tail -1)
  if [ -n "$h" ]; then
    printf '%s' "$h"
    return
  fi
  # 지적 0건이면 리뷰 객체 자체가 안 생긴다 — 결과가 요약 코멘트에만 적힌다.
  # 여기서 구분해두지 않으면 "지적 없음"과 "조회 실패"가 똑같이 빈 줄로 보인다.
  if gh api --paginate "repos/$REPO/issues/$pr/comments" \
       --jq '.[] | select(.user.login=="coderabbitai[bot]") | .body' 2>/dev/null \
     | grep -q 'No actionable comments'; then
    printf '지적 없음'
  else
    printf '요약 코멘트 갱신 — 내용 직접 확인'
  fi
}

warned_list_failure=0

while true; do
  # 조회 실패를 빈 목록으로 넘기면 pending 이 0 이 되어 감시가 조용히 종료된다.
  # 네트워크 끊김이나 gh 인증 만료로 감시가 사라지는 게 제일 나쁜 실패다.
  if ! prs=$(gh pr list --repo "$REPO" --state open --limit 100 --json number --jq '.[].number' 2>/dev/null); then
    if [ "$warned_list_failure" -eq 0 ]; then
      printf 'PR 목록 조회 실패 — 재시도 중 (gh 인증/네트워크 확인)\n'
      warned_list_failure=1
    fi
    sleep "$INTERVAL"
    continue
  fi
  warned_list_failure=0

  pending=0
  for pr in $prs; do
    last_commit=$(gh pr view "$pr" --repo "$REPO" --json commits \
      --jq '.commits | last | .committedDate' 2>/dev/null)
    latest=$(latest_activity "$pr")

    # 활동이 없거나 마지막 커밋보다 이전이면 아직 기다리는 중
    if [ -z "$latest" ] || [ -z "$last_commit" ] || [[ "$latest" < "$last_commit" ]]; then
      pending=$((pending + 1))
      continue
    fi
    # 여기 온 것 = 현재 코드에 대한 리뷰가 끝났다. 알림 조건을 종료 판정과 같게 둬야
    # 감시를 새로 띄웠을 때 커밋 이전의 지난 리뷰를 새 도착으로 찍지 않는다.
    if [ "$latest" != "${seen[$pr]:-}" ]; then
      seen[$pr]="$latest"
      printf 'PR #%s — CodeRabbit 리뷰 도착: %s\n' "$pr" "$(headline "$pr" "$latest")"
    fi
  done

  [ "$pending" -eq 0 ] && exit 0
  sleep "$INTERVAL"
done

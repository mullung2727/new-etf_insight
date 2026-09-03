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
    gh api "repos/$REPO/pulls/$pr/reviews" \
      --jq '.[] | select(.user.login=="coderabbitai[bot]") | .submitted_at' 2>/dev/null
    # 처리 중 안내는 아직 리뷰가 아니다
    gh api "repos/$REPO/issues/$pr/comments" \
      --jq '.[] | select(.user.login=="coderabbitai[bot]")
            | select(.body | test("Currently processing") | not) | .updated_at' 2>/dev/null
  } | sort | tail -1
}

headline() {  # 가장 최근 리뷰의 첫 줄. 지적 0건이면 요약 코멘트만 갱신되므로 비어 있다
  gh api "repos/$REPO/pulls/$1/reviews" \
    --jq '[.[] | select(.user.login=="coderabbitai[bot]")] | last | .body | split("\n")[0]' 2>/dev/null
}

while true; do
  pending=0
  for pr in $(gh pr list --repo "$REPO" --state open --json number --jq '.[].number' 2>/dev/null); do
    last_commit=$(gh pr view "$pr" --repo "$REPO" --json commits \
      --jq '.commits | last | .committedDate' 2>/dev/null)
    latest=$(latest_activity "$pr")

    if [ -n "$latest" ] && [ "$latest" != "${seen[$pr]:-}" ]; then
      seen[$pr]="$latest"
      printf 'PR #%s — CodeRabbit 리뷰 도착: %s\n' "$pr" "$(headline "$pr")"
    fi
    # 활동이 없거나 마지막 커밋보다 이전이면 아직 기다리는 중
    if [ -z "$latest" ] || [ -z "$last_commit" ] || [[ "$latest" < "$last_commit" ]]; then
      pending=$((pending + 1))
    fi
  done

  [ "$pending" -eq 0 ] && exit 0
  sleep "$INTERVAL"
done

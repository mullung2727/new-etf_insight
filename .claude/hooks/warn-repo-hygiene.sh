#!/usr/bin/env bash
# 2026-10-17: research/ 아래 미커밋 리서치 폴더 5개(10-06~13), 머지된 로컬 브랜치 4개, 메인 폴더 미커밋 파일 3개가
# 쌓여 있었다. 규칙은 git-workflow 스킬에 있지만 커밋 안 하는 세션은 스킬을 안 읽는다 → 세션 시작마다 경고. 경고만, 차단 아님.
# jq 비의존 — git 출력만 sed/grep/awk, 결과는 printf. 네트워크 금지(로컬 origin/main 기준).
cat >/dev/null

# 메인 checkout 경로 (worktree 목록 첫 줄). 실패하면 조용히 종료
main=$(git worktree list --porcelain 2>/dev/null | sed -n -E 's/^worktree //p' | head -n 1)
[ -z "$main" ] && exit 0

# 1. research/ 미커밋 (untracked 중 research/private/ 제외)
r1=''
st_r=$(git -C "$main" status --porcelain --untracked-files=normal -- research 2>/dev/null)
if [ -n "$st_r" ]; then
  r1=$(printf '%s\n' "$st_r" | sed -n -E 's/^\?\? //p' | grep -v -E '^research/private/' 2>/dev/null)
fi

# 2. 메인 폴더 미커밋 변경 (1번에 나온 research untracked 줄 제외)
r2=''
st_all=$(git -C "$main" status --porcelain 2>/dev/null)
if [ -n "$st_all" ]; then
  r2=$(printf '%s\n' "$st_all" | grep -v -E '^\?\? research/' 2>/dev/null)
fi

# 3. 머지된 로컬 브랜치 (main 제외, cherry +줄 0개). origin/main 없으면 건너뜀
r3=''
if git -C "$main" rev-parse --verify --quiet origin/main >/dev/null 2>&1; then
  wt=$(git -C "$main" worktree list --porcelain 2>/dev/null)
  branches=$(git -C "$main" for-each-ref --format='%(refname:short)' refs/heads 2>/dev/null)
  for b in $branches; do
    [ "$b" = "main" ] && continue
    n=$(git -C "$main" cherry origin/main "$b" 2>/dev/null | grep -c -E '^\+' 2>/dev/null)
    [ "$n" = "0" ] || continue
    # cherry 는 머지 커밋을 안 셈 → origin/main 에 없는 머지 커밋(충돌 해결 등)이 있으면 머지됨 아님 (PR #104 리뷰)
    [ -z "$(git -C "$main" rev-list --merges "origin/main..$b" 2>/dev/null)" ] || continue
    # 그 브랜치를 쓰는 worktree 경로 (branch 줄보다 앞의 가장 가까운 worktree 줄)
    wtp=$(printf '%s\n' "$wt" | awk -v br="branch refs/heads/$b" '/^worktree /{w=substr($0,10)} $0==br{print w; exit}' 2>/dev/null)
    line="$b"
    [ -n "$wtp" ] && line="$b ($wtp)"
    if [ -z "$r3" ]; then
      r3="$line"
    else
      r3="$r3
$line"
    fi
  done
fi

# 셋 다 없으면 조용히 종료
if [ -z "$r1" ] && [ -z "$r2" ] && [ -z "$r3" ]; then
  exit 0
fi

# 항목 블록 출력 (최대 10줄 + 초과분): $1=제목 $2=내용 $3=안내
print_block() {
  total=$(printf '%s\n' "$2" | grep -c '^' 2>/dev/null)
  printf '■ %s %s개\n' "$1" "$total"
  printf '%s\n' "$2" | head -n 10
  if [ "$total" -gt 10 ] 2>/dev/null; then
    printf '… 외 %s개\n' "$((total - 10))"
  fi
  printf '→ %s\n' "$3"
}

printf '[저장소 정리 필요] 세션 시작 점검 (경고만)\n'
if [ -n "$r1" ]; then
  print_block 'research/ 미커밋' "$r1" '리서치 결과는 research/private/ 로, 기각은 REJECTED.md 한 절 + 폴더 삭제 (git-workflow 스킬)'
fi
if [ -n "$r2" ]; then
  print_block '메인 폴더 미커밋 변경' "$r2" '메인 폴더 변경은 worktree 브랜치로 옮겨 커밋하거나, 다른 세션 작업인지 확인'
fi
if [ -n "$r3" ]; then
  print_block '머지된 로컬 브랜치' "$r3" "머지 끝난 브랜치 — cherry 0 확인됨, worktree 링크 검사 후 정리 (git-workflow 스킬 '머지한 브랜치·worktree 바로 정리')"
fi
exit 0

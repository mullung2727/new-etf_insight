"""플라시보: 같은 날 수만큼 무작위 추출한 분포에서 필터 평균의 백분위."""
import json
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")
random.seed(42)
ITER = 10000
for tag in ("d_train", "d_val", "d_oos"):
    d = json.load(open(f"research/cluster_closebet/results/{tag}_daily.json",
                       encoding="utf-8"))
    print("==", tag)
    for v in ("CSR70", "CSR75", "CSR80", "CSS50"):
        hits, rows = 0, 0
        line = []
        for cell in d:
            base = [x[1] for x in d[cell]["base"]["open1"]]
            sel = [x[1] for x in d[cell][v]["open1"]]
            if not sel or len(base) <= len(sel):
                continue
            m = sum(sel) / len(sel)
            k = len(sel)
            worse = sum(1 for _ in range(ITER)
                        if sum(random.sample(base, k)) / k < m)
            p = 1 - worse / ITER          # 무작위가 더 좋을 확률
            rows += 1
            hits += p < 0.05
            line.append(f"{cell.replace('_N','/')}:{m*100:+.2f}(p{p:.2f},n{k})")
        print(f" {v:6s} p<0.05 {hits}/{rows}  " + " ".join(line))

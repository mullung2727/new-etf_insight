"""Smoke: @unrealtech RSS 최근 3개 수집 + LangGraph 분석."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from scripts.collect_youtube import (
        DEFAULT_DB,
        ensure_schema,
        fetch_transcript,
        list_channel_videos_rss,
        published_to_date_kst,
        upsert_videos,
    )
    from scripts.youtube_langgraph.youtube_analysis_langgraph import (
        ensure_summary_schema,
        run_video,
    )
    from scripts.youtube_stock_insights import ensure_schema as ensure_insights
except ImportError:
    from collect_youtube import (
        DEFAULT_DB,
        ensure_schema,
        fetch_transcript,
        list_channel_videos_rss,
        published_to_date_kst,
        upsert_videos,
    )
    from youtube_langgraph.youtube_analysis_langgraph import (
        ensure_summary_schema,
        run_video,
    )
    from youtube_stock_insights import ensure_schema as ensure_insights

CH = "UCeN2YeJcBCRJoXgzF_OU3qw"


def main() -> int:
    phase = (sys.argv[1] if len(sys.argv) > 1 else "all").strip()

    if phase in ("collect", "all"):
        items = list_channel_videos_rss(CH)[:3]
        print("=== TOP 3 RSS ===")
        for e in items:
            print(e["video_id"], e["published_at_utc"], e["title"])

        db = Path(DEFAULT_DB)
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db))
        ensure_schema(con)
        ensure_summary_schema(con)
        ensure_insights(con)

        rows = []
        for e in items:
            text, lang, src = fetch_transcript(e["video_id"])
            d = published_to_date_kst(e["published_at_utc"])
            n = len(text) if text else 0
            print(
                f"transcript {e['video_id']}: len={n} lang={lang} src={src} date_kst={d}"
            )
            rows.append(
                {
                    "channel_id": CH,
                    "video_id": e["video_id"],
                    "title": e["title"],
                    "published_at_utc": e["published_at_utc"],
                    "date_kst": d,
                    "url": e["url"],
                    "transcript": text,
                    "transcript_lang": lang,
                    "transcript_source": src,
                    "raw_json": json.dumps(e, ensure_ascii=False),
                }
            )
        ins, upd = upsert_videos(con, rows)
        con.commit()
        con.close()
        print(f"upsert inserted={ins} updated={upd} db={db}")

        # write video list for analyze phase
        Path("runs").mkdir(exist_ok=True)
        Path("runs/smoke_youtube_top3.json").write_text(
            json.dumps(
                [{"channel_id": CH, "video_id": e["video_id"], "title": e["title"]} for e in items],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    if phase in ("analyze", "all"):
        list_path = Path("runs/smoke_youtube_top3.json")
        if not list_path.exists():
            print("missing runs/smoke_youtube_top3.json — run collect first", file=sys.stderr)
            return 1
        videos = json.loads(list_path.read_text(encoding="utf-8"))
        db = str(DEFAULT_DB)
        for v in videos:
            print(f"\n=== ANALYZE {v['video_id']} {v.get('title', '')} ===")
            try:
                r = run_video(
                    channel_id=v["channel_id"],
                    video_id=v["video_id"],
                    db_path=db,
                    force=True,
                )
                print(
                    f"skip={r.get('skip')} llm_calls={r.get('llm_calls')} "
                    f"persisted={r.get('persisted')} stocks={len(r.get('stock_mentions') or [])}"
                )
                print(f"warnings={r.get('warnings')}")
                if r.get("summary_obj"):
                    s = r["summary_obj"]
                    print("headline:", s.get("headline"))
                    print("issues:", len(s.get("issues") or []))
                    for iss in (s.get("issues") or [])[:5]:
                        print(" -", iss.get("title"), "|", (iss.get("summary") or "")[:120])
                    print("bullets:", s.get("bullets"))
                    print("risk:", s.get("risk_or_caveat"))
            except Exception as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                import traceback

                traceback.print_exc()
                return 1

        # dump saved rows
        con = sqlite3.connect(db)
        print("\n=== DB youtube_video_summaries ===")
        for row in con.execute(
            "SELECT video_id, date_kst, substr(summary_json,1,200) FROM youtube_video_summaries "
            "WHERE channel_id=? ORDER BY updated_at DESC LIMIT 5",
            (CH,),
        ):
            print(row[0], row[1], row[2])
        con.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

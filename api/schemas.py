"""Pydantic response models.

Field names and types mirror the TypeScript interfaces in
``web/lib/queries.ts`` exactly. Do not rename a field here without updating
the TS side as well — the BFF relies on a 1:1 JSON shape.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class EtfListItem(_Base):
    etf_key: str
    fund_name: str | None = None
    asset_manager: str | None = None
    index_name: str | None = None
    primary_country: str | None = None
    theme_status: str | None = None
    theme_bucket: str | None = None
    structure_tags: list[str] | None = None
    classification_confidence: float | None = None
    first_rcept_dt: str | None = None
    is_pre_listing_etf: bool | None = None
    revision_count: int | None = None
    has_holdings: bool = False


class EtfDetail(EtfListItem):
    route: str | None = None
    index_provider: str | None = None
    index_description: str | None = None
    holdings_available_in_pdf: bool | None = None
    holdings_summary: str | None = None
    classification_evidence: str | None = None
    keywords: list[str] | None = None
    trend_summary: str | None = None
    missing_info: list[str] | None = None
    rcept_no: str | None = None
    rcept_dt: str | None = None
    corp_code: str | None = None
    corp_name: str | None = None
    report_nm: str | None = None
    fund_code: str | None = None
    pdf_path: str | None = None


class Holding(_Base):
    name: str
    ticker: str | None = None
    exchange: str | None = None
    weight: str | None = None


class HoldingStat(_Base):
    name: str
    avg_weight: float
    etf_count: int


class StatsSummary(_Base):
    total_etfs: int
    with_any_holdings: int


class LlmScore(_Base):
    """One row of llm_scores (watchlist.sqlite3). Mirrors the 16-column table."""

    date: str
    ticker: str
    name: str | None = None
    ratio: float | None = None
    today_volume: int | None = None
    avg5_volume: int | None = None
    trading_value: int | None = None
    close: int | None = None
    score: int | None = None
    category: str | None = None
    reason_summary: str | None = None
    final_opinion: str | None = None
    evidence_board: str | None = None
    evidence_news: str | None = None
    evidence_web: str | None = None
    sources: Any | None = None


class OhlcvCandle(_Base):
    """One daily candle from krx_ohlcv.duckdb (ohlcv table)."""

    date: str
    open: int | None = None
    high: int | None = None
    low: int | None = None
    close: int | None = None
    volume: int | None = None
    trading_value: int | None = None


class MetricInfo(_Base):
    """랭킹 셀렉터 항목 (financial_indicators)."""

    key: str
    label: str
    unit: str          # "pct" | "won"
    source: str        # "indicators" | "accounts"
    default_order: str  # "asc" | "desc"


class PeriodInfo(_Base):
    """적재된 기간 (연간/분기)."""

    year: str
    reprt: str
    label: str          # "2025 연간" / "2026 1분기"


class RankingRow(_Base):
    """랭킹 한 행."""

    rank: int
    stock_code: str | None = None
    corp_name: str | None = None
    value: float | None = None


class DigestItem(_Base):
    """텔레그램 크로스채널 요약의 종목 한 건 (analysis JSON 파싱 결과)."""

    ticker: str
    name: str
    channels: int                       # 언급 채널 수
    change_type: str | None = None      # "new" | "continued" | ...
    change_summary: str | None = None
    themes: list[str] = []


class DigestLatest(_Base):
    """가장 최근 (date, session)의 분석완료 종목요약. 데이터 없으면 엔드포인트가 null 반환."""

    date: str
    session: str
    count: int
    items: list[DigestItem]


class TelegramMention(_Base):
    """한 종목의 텔레그램 언급 1건(= 1 date_kst/session). analysis 없으면 change_* 는 null."""

    date_kst: str
    session: str
    channels: list[str] = []            # 소스채널명
    post_refs: list[str] = []           # 원문 post_ref ("channel/postid")
    change_type: str | None = None
    change_summary: str | None = None
    themes: list[str] = []


class ThemePeer(_Base):
    """대상 종목과 텔레그램 테마를 공유하는 다른 종목."""

    ticker: str
    name: str
    themes: list[str] = []              # 공유 테마만


class YoutubeMention(_Base):
    """한 종목의 유튜브 언급 1건(= 1 date_kst). session 없음."""

    date_kst: str
    name: str
    channels: list[str] = []
    video_ids: list[str] = []
    discovery_reason: str = ""
    analysis: str | None = None


class YoutubeSummaryStock(_Base):
    """요약에 붙은 종목.

    ticker 있으면 KRX 확정(허브 링크 가능). 없으면 표시 전용(미매칭·해외 등).
    """

    ticker: str | None = None
    name: str
    note: str | None = None


class YoutubeVideoSummary(_Base):
    """영상 1건 통합 이슈 요약 (reduce 결과)."""

    channel_id: str
    channel_label: str | None = None  # youtube_channels.json label/handle
    video_id: str
    date_kst: str
    title: str | None = None
    url: str
    headline: str | None = None
    issues: list[Any] = []
    bullets: list[str] = []
    risk_or_caveat: str | None = None
    transcript_chars: int | None = None  # 원본 대본 길이 (영상 행 삭제 시 None)
    # discovery 추출 결과 조인. 없거나 비discovery면 []
    stocks: list[YoutubeSummaryStock] = []


class YoutubePendingItem(_Base):
    """미요약 영상 (대기 목록). 수집됐으나 요약 없음."""

    channel_id: str
    channel_label: str | None = None
    video_id: str
    date_kst: str
    title: str | None = None
    url: str
    transcript_chars: int | None = None
    has_transcript: bool = False
    # ready = 대본 있어 요약 가능 / no_transcript = 자막 없음
    status: str = "ready"


class YoutubeCollectRequest(_Base):
    from_date: str  # date_kst
    to_date: str
    channel_ids: list[str] | None = None


class YoutubeCollectUrlRequest(_Base):
    """영상 URL 1건 가져오기 (watch/shorts/youtu.be)."""

    url: str


class YoutubeCollectUrlResult(_Base):
    video_id: str
    channel_id: str
    date_kst: str
    title: str
    status: str  # inserted | updated | already_summarized
    has_transcript: bool = False
    url: str = ""


class YoutubeCatalogRequest(_Base):
    """선택 채널 RSS 영상 목록(+duration). 수동 조회용."""

    channel_ids: list[str]
    with_duration: bool = True


class YoutubeCatalogItem(_Base):
    channel_id: str
    channel_label: str | None = None
    video_id: str
    title: str = ""
    published_at_utc: str = ""
    date_kst: str = ""
    url: str
    duration_sec: int | None = None


class YoutubeSelectedVideo(_Base):
    channel_id: str
    video_id: str
    title: str | None = None
    published_at_utc: str | None = None
    date_kst: str | None = None
    url: str | None = None


class YoutubeCollectSelectedRequest(_Base):
    """목록에서 고른 영상만 자막 수집 (수동)."""

    videos: list[YoutubeSelectedVideo]


class YoutubeSummarizeRequest(_Base):
    from_date: str | None = None
    to_date: str | None = None
    channel_ids: list[str] | None = None
    video_ids: list[str] | None = None
    force: bool = False


class YoutubeSummarizeJobRequest(_Base):
    """단일 영상 요약 백그라운드 잡."""

    channel_id: str
    video_id: str
    title: str | None = None
    force: bool = False


class YoutubeSummarizeJob(_Base):
    job_id: str
    status: str  # queued | running | done | error
    channel_id: str
    video_id: str
    title: str | None = None
    force: bool = False
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    message: str | None = None
    result: dict | None = None

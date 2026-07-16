import StockSearch from "@/components/stock/stock-search";

// 종목 분석 검색 랜딩. 선택 시 stock_code로 허브(재무 탭) 진입.
export default function StockSearchPage() {
  return (
    <div className="fin-scope min-h-screen bg-background font-terminal">
      <div className="fixed inset-0 pointer-events-none bg-grid" />
      <div className="relative max-w-[1200px] mx-auto px-6 py-10">
        <StockSearch />
      </div>
    </div>
  );
}

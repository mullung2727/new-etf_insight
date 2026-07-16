"use client";

import { useRouter } from "next/navigation";
import CompareSearchBar from "@/components/financial/CompareSearchBar";
import { stockHubHref } from "@/lib/stock-hub";

// 종목 검색바 + 허브 이동. 랜딩(/stock)·허브(/stock/[code]) 공용 → 검색 후에도 남음.
export default function StockSearch() {
  const router = useRouter();
  return (
    <CompareSearchBar
      variant="stock"
      loading={false}
      onSearch={(_corp, name, stock) =>
        router.push(stockHubHref(stock, { tab: "financial", name }))
      }
    />
  );
}

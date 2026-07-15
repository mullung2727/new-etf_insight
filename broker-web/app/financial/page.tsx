import { redirect } from "next/navigation";

// 재무제표 페이지는 종목 분석 허브로 일원화. 라우트는 유지(북마크 무손상)하고 리다이렉트.
export default function FinancialRedirect() {
  redirect("/stock");
}

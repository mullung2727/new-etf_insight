import { redirect } from "next/navigation";

// 리포트 페이지는 종목 분석 허브 리포트 탭으로 일원화. 라우트 유지 + 리다이렉트.
export default function ResearchRedirect() {
  redirect("/stock");
}

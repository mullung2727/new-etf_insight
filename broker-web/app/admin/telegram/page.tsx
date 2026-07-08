import { redirect } from "next/navigation";

// 텔레그램 관리는 /admin/settings 텔레그램 탭으로 통합됨. 구 경로는 리다이렉트.
export default function TelegramAdminPage() {
  redirect("/admin/settings");
}

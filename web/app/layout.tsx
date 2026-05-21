import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "ETF Insight",
  description: "신규 상장예정 ETF 공시 분석",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="ko"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <header className="border-b">
          <nav className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3 text-sm font-medium">
            <Link href="/" className="text-foreground hover:text-foreground/70">
              목록
            </Link>
            <Link href="/stats" className="text-foreground hover:text-foreground/70">
              통계
            </Link>
          </nav>
        </header>
        <main className="flex-1">{children}</main>
      </body>
    </html>
  );
}

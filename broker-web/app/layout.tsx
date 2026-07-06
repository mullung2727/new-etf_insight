import type { Metadata } from "next";
import { Rubik, Space_Grotesk, Space_Mono } from "next/font/google";
import "./globals.css";
import { Nav } from "@/components/common/nav";
import { FillToast } from "@/components/common/fill-toast";
import { BrokerEventsProvider } from "@/lib/use-broker-events";

// Sentry 타이포 (DESIGN.md): Rubik=UI, Space Grotesk=디스플레이/헤딩, Space Mono=코드
const rubik = Rubik({
  variable: "--font-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

const spaceGrotesk = Space_Grotesk({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["500", "700"],
});

const spaceMono = Space_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["400", "700"],
});

export const metadata: Metadata = {
  title: "ETF Insight",
  description: "ETF 분석 + 키움증권 트레이딩",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="ko"
      className={`${rubik.variable} ${spaceGrotesk.variable} ${spaceMono.variable} h-full antialiased dark`}
    >
      <body className="min-h-full flex flex-col">
        <BrokerEventsProvider>
          <Nav />
          <main className="flex-1">{children}</main>
          <FillToast />
        </BrokerEventsProvider>
      </body>
    </html>
  );
}

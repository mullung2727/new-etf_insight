import Link from "next/link";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export default function Home() {
  return (
    <div className="flex flex-col items-center justify-center flex-1 gap-8 px-4 py-20 text-center">
      <div className="space-y-2">
        <p className="text-4xl">↗</p>
        <h1 className="text-3xl font-bold tracking-tight">ETF Insight</h1>
        <p className="text-muted-foreground text-sm">신규 상장예정 ETF 분석 + 키움증권 트레이딩</p>
      </div>
      <div className="flex gap-3">
        <Link href="/etfs" className={cn(buttonVariants({ variant: "default" }))}>ETF 분석</Link>
        <Link href="/trading" className={cn(buttonVariants({ variant: "outline" }))}>트레이딩</Link>
      </div>
    </div>
  );
}

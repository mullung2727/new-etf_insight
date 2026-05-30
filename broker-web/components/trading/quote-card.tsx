"use client";

import { useState } from "react";
import { brokerClient, type Quote } from "@/lib/broker-client";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { formatKrw } from "@/lib/formatters";

interface QuoteCardProps {
  onSymbolSelect?: (symbol: string, price: number) => void;
}

export function QuoteCard({ onSymbolSelect }: QuoteCardProps) {
  const [symbol, setSymbol] = useState("");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const lookup = async () => {
    const s = symbol.trim();
    if (!s) return;
    setLoading(true);
    setError(null);
    try {
      const q = await brokerClient.getQuote(s);
      setQuote(q);
      if (q.price) onSymbolSelect?.(s, q.price);
    } catch (e) {
      setError(String(e));
      setQuote(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">시세 조회</h2>

      <div className="flex gap-2">
        <Input
          placeholder="종목코드 (예: 005930)"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && lookup()}
          className="font-mono"
          maxLength={6}
        />
        <Button onClick={lookup} disabled={loading || !symbol.trim()}>
          {loading ? "조회중..." : "조회"}
        </Button>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}

      {quote && (
        <Card className="bg-secondary border-border">
          <CardContent className="pt-4 pb-4">
            <div className="flex items-end justify-between">
              <div>
                <p className="text-xs text-muted-foreground">{quote.symbol}</p>
                <p className="text-3xl font-bold tabular-nums mt-1">
                  {quote.price != null ? formatKrw(quote.price) : "-"}
                </p>
              </div>
              {quote.price && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => onSymbolSelect?.(quote.symbol, quote.price!)}
                >
                  주문에 적용
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

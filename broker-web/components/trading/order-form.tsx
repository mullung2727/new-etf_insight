"use client";

import { useEffect, useState } from "react";
import { brokerClient, type OrderResult } from "@/lib/broker-client";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { formatKrw } from "@/lib/formatters";
import { cn } from "@/lib/utils";

interface OrderFormProps {
  symbol?: string;
  price?: number;
  side: "buy" | "sell";
  onSideChange: (side: "buy" | "sell") => void;
}

export function OrderForm({ symbol: initSymbol = "", price: initPrice = 0, side, onSideChange }: OrderFormProps) {
  const [orderType, setOrderType] = useState<"limit" | "market">("limit");
  const [symbol, setSymbol] = useState(initSymbol);
  const [price, setPrice] = useState(initPrice > 0 ? String(initPrice) : "");
  const [qty, setQty] = useState("1");
  const [result, setResult] = useState<OrderResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => { setSymbol(initSymbol); }, [initSymbol]);
  useEffect(() => { if (initPrice > 0) setPrice(String(initPrice)); }, [initPrice]);

  const numPrice = parseInt(price.replace(/,/g, ""), 10) || 0;
  const numQty = parseInt(qty, 10) || 0;
  const estimated = orderType === "limit" ? numPrice * numQty : null;

  const submit = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const r = await brokerClient.placeOrder({
        symbol,
        side,
        qty: numQty,
        price: numPrice,
        order_type: orderType,
      });
      setResult(r);
    } catch (e) {
      const msg = String(e);
      if (msg.includes("Failed to fetch") || msg.includes("NetworkError")) {
        setError("서버 연결 실패");
      } else {
        // Strip leading "Error: " prefix; 422 detail is already clean
        setError(msg.replace(/^Error:\s*/, ""));
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">주문</h2>

      <Tabs value={side} onValueChange={(v) => onSideChange(v as "buy" | "sell")}>
        <TabsList className="w-full">
          <TabsTrigger value="buy" className="flex-1 data-[state=active]:bg-buy data-[state=active]:text-white">
            매수
          </TabsTrigger>
          <TabsTrigger value="sell" className="flex-1 data-[state=active]:bg-sell data-[state=active]:text-white">
            매도
          </TabsTrigger>
        </TabsList>

        {(["buy", "sell"] as const).map((s) => (
          <TabsContent key={s} value={s} className="mt-4 flex flex-col gap-3">
            <div>
              <Label>종목코드</Label>
              <Input
                className="font-mono mt-1"
                placeholder="005930"
                maxLength={6}
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
              />
            </div>

            <div className="flex gap-2">
              {(["limit", "market"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setOrderType(t)}
                  className={cn(
                    "flex-1 py-1 rounded text-sm border transition-colors",
                    orderType === t
                      ? "bg-primary text-primary-foreground border-primary"
                      : "border-border text-muted-foreground hover:border-primary"
                  )}
                >
                  {t === "limit" ? "지정가" : "시장가"}
                </button>
              ))}
            </div>

            {orderType === "limit" && (
              <div>
                <Label>가격 (원)</Label>
                <Input
                  className="font-mono mt-1"
                  placeholder="0"
                  value={price}
                  onChange={(e) => setPrice(e.target.value)}
                />
              </div>
            )}

            <div>
              <Label>수량 (주)</Label>
              <div className="flex gap-1 mt-1">
                <Button variant="outline" size="sm" onClick={() => setQty(String(Math.max(1, numQty - 1)))}>-</Button>
                <Input
                  className="font-mono text-center"
                  value={qty}
                  onChange={(e) => setQty(e.target.value)}
                />
                <Button variant="outline" size="sm" onClick={() => setQty(String(numQty + 1))}>+</Button>
              </div>
            </div>

            {estimated != null && estimated > 0 && (
              <p className="text-sm text-muted-foreground">
                예상금액: <span className="text-foreground font-medium tabular-nums">{formatKrw(estimated)}</span>
              </p>
            )}

            {error && <p className="text-xs text-status-loss">{error}</p>}
            {result && (
              <Badge variant="outline" className="text-status-profit border-status-profit/30 self-start">
                주문접수 {result.order_no ? `#${result.order_no}` : ""}
              </Badge>
            )}

            <Button
              className={cn("w-full mt-1 text-white", s === "buy" ? "bg-buy hover:bg-buy/90" : "bg-sell hover:bg-sell/90")}
              onClick={submit}
              disabled={loading || !symbol}
            >
              {loading ? "처리중..." : s === "buy" ? "매수" : "매도"}
            </Button>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}

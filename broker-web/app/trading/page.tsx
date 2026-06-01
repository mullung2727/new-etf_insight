"use client";

import { useState } from "react";
import { AccountPanel } from "@/components/trading/account-panel";
import { QuoteCard } from "@/components/trading/quote-card";
import { OrderForm } from "@/components/trading/order-form";
import { ConditionPanel } from "@/components/trading/condition-panel";

export default function TradingPage() {
  const [symbol, setSymbol] = useState("");
  const [price, setPrice] = useState(0);
  const handleSymbolSelect = (s: string, p?: number) => {
    setSymbol(s);
    if (p) setPrice(p);
  };

  return (
    <div className="grid grid-cols-[320px_1fr_320px] gap-4 p-6 h-[calc(100vh-57px)] overflow-hidden">
        {/* LEFT: account */}
        <div className="overflow-y-auto min-h-0">
          <AccountPanel />
        </div>

        {/* CENTER: quote + order */}
        <div className="flex flex-col gap-6 overflow-y-auto min-h-0">
          <QuoteCard onSymbolSelect={handleSymbolSelect} />
          <div className="border-t border-border pt-4">
            <OrderForm symbol={symbol} price={price} />
          </div>
        </div>

        {/* RIGHT: condition search */}
        <div className="overflow-y-auto min-h-0">
          <ConditionPanel onSymbolSelect={handleSymbolSelect} />
        </div>
    </div>
  );
}

"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { CorpCode } from "@/types/dart";

interface Props {
  onSearch: (corpCode: string, corpName: string, stockCode: string) => void;
  loading: boolean;
  // "financial"(기본): DART 재무 비교 문구. "stock": 종목 분석 허브 진입용 중립 문구.
  variant?: "financial" | "stock";
}

export default function CompareSearchBar({ onSearch, loading, variant = "financial" }: Props) {
  const [query, setQuery] = useState("");
  const [corpCode, setCorpCode] = useState("");
  const [suggestions, setSuggestions] = useState<CorpCode[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [allCorps, setAllCorps] = useState<CorpCode[]>([]);
  const [corpsLoaded, setCorpsLoaded] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);

  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/api/corps")
      .then(r => r.json())
      .then((data: CorpCode[]) => {
        setAllCorps(data.filter(c => c.stock_code?.trim()));
        setCorpsLoaded(true);
      })
      .catch(() => setCorpsLoaded(true));
  }, []);

  const handleQueryChange = useCallback((value: string) => {
    setQuery(value);
    setCorpCode("");
    setActiveIndex(-1);
    const q = value.trim();
    if (!q) { setSuggestions([]); setShowDropdown(false); return; }
    const filtered = allCorps
      .filter(c => c.corp_name.includes(q) || c.stock_code.includes(q))
      .sort((a, b) => Number(b.stock_code === q) - Number(a.stock_code === q))
      .slice(0, 10);
    setSuggestions(filtered);
    setShowDropdown(filtered.length > 0);
  }, [allCorps]);

  const handleSelect = useCallback((corp: CorpCode) => {
    setQuery(corp.corp_name);
    setCorpCode(corp.corp_code);
    setSuggestions([]);
    setShowDropdown(false);
    onSearch(corp.corp_code, corp.corp_name, corp.stock_code);
  }, [onSearch]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!showDropdown) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActiveIndex(i => Math.min(i + 1, suggestions.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActiveIndex(i => Math.max(i - 1, 0)); }
    else if (e.key === "Enter" && activeIndex >= 0) { e.preventDefault(); handleSelect(suggestions[activeIndex]); }
    else if (e.key === "Escape") setShowDropdown(false);
  };

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current?.contains(e.target as Node) || inputRef.current?.contains(e.target as Node)) return;
      setShowDropdown(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  return (
    <div
      className="w-full relative overflow-visible font-terminal bg-card border border-fin-gold/25 rounded-[2px]"
      style={{
        boxShadow: "0 0 0 1px rgba(194,239,78,0.08), 0 20px 60px rgba(0,0,0,0.5)",
      }}
    >
      <div className="absolute top-0 left-0 right-0 h-[2px]"
        style={{ background: "linear-gradient(90deg, transparent, var(--color-fin-gold) 30%, var(--color-fin-gold) 70%, transparent)" }} />

      <div className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-fin-gold/[0.12]">
        <div className="flex items-center gap-3">
          <div className="w-[6px] h-[6px] rounded-full bg-fin-gold" style={{ boxShadow: "0 0 8px var(--color-fin-gold)" }} />
          <span className="text-fin-gold text-fin-sm tracking-[0.2em] font-semibold uppercase">
            {variant === "stock" ? "종목 검색" : "DART Financial Compare · 연간 5년"}
          </span>
        </div>
        <span
          data-testid={corpsLoaded ? "corps-ready" : undefined}
          className={`text-fin-xs tracking-[0.15em] ${corpsLoaded ? "text-fin-gold/50" : "text-fin-muted"}`}
        >
          {corpsLoaded ? `${allCorps.length.toLocaleString()} CORPS INDEXED` : "LOADING INDEX..."}
        </span>
      </div>

      <div className="px-6 py-5 flex items-end gap-3">
        <div className="flex-1 min-w-0 relative">
          <label className="block text-fin-gold/60 text-fin-xs tracking-[0.2em] mb-[6px] uppercase">
            기업명 / 종목코드
          </label>
          <div className="relative">
            <input
              ref={inputRef}
              data-testid="corp-search-input"
              type="text"
              value={query}
              onChange={e => handleQueryChange(e.target.value)}
              onKeyDown={handleKeyDown}
              onFocus={() => query && suggestions.length > 0 && setShowDropdown(true)}
              placeholder="삼성전자, 005930..."
              autoComplete="off"
              style={{
                width: "100%",
                background: "rgba(255,255,255,0.04)",
                border: corpCode ? "1px solid var(--color-fin-gold)" : "1px solid rgba(194,239,78,0.2)",
                borderBottom: corpCode ? "2px solid var(--color-fin-gold)" : "1px solid rgba(194,239,78,0.2)",
                borderRadius: "1px",
                padding: "10px 40px 10px 12px",
                color: corpCode ? "var(--color-fin-gold)" : "rgba(255,255,255,0.85)",
                fontSize: "13px",
                letterSpacing: "0.02em",
                outline: "none",
                fontFamily: "inherit",
              }}
            />
            {loading && (
              <div className="absolute right-[10px] top-1/2 -translate-y-1/2">
                <svg width="12" height="12" viewBox="0 0 24 24" style={{ animation: "spin 0.8s linear infinite" }}>
                  <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
                  <circle cx="12" cy="12" r="10" style={{ stroke: "var(--color-fin-gold)" }} strokeWidth="3" fill="none" strokeDasharray="31.4" strokeDashoffset="10" />
                </svg>
              </div>
            )}
            {corpCode && !loading && (
              <div className="absolute right-[10px] top-1/2 -translate-y-1/2 text-fin-gold text-xs">✓</div>
            )}
          </div>

          {showDropdown && (
            <div
              ref={dropdownRef}
              className="absolute top-[calc(100%+4px)] left-0 right-0 border border-fin-gold/30 rounded-[1px] z-50 max-h-60 overflow-y-auto"
              style={{ background: "var(--color-card)", boxShadow: "0 8px 32px rgba(0,0,0,0.6)" }}
            >
              {suggestions.map((corp, i) => (
                <div
                  key={corp.corp_code}
                  data-testid="autocomplete-item"
                  onMouseDown={() => handleSelect(corp)}
                  onMouseEnter={() => setActiveIndex(i)}
                  className="flex items-center justify-between px-3 py-[9px] cursor-pointer border-b border-white/[0.04] transition-colors duration-100"
                  style={{ background: i === activeIndex ? "rgba(194,239,78,0.12)" : "transparent" }}
                >
                  <span className="text-xs font-[inherit]"
                    style={{ color: i === activeIndex ? "var(--color-fin-gold)" : "rgba(255,255,255,0.8)" }}>
                    {corp.corp_name}
                  </span>
                  <span className="text-fin-gold/40 text-fin-sm tracking-[0.1em] font-[inherit]">
                    {corp.stock_code}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="px-6 py-2 flex items-center gap-2 border-t border-fin-gold/[0.08] bg-black/20">
        <span className={`text-fin-xs tracking-[0.15em] font-[inherit] ${corpCode ? "text-fin-gold/50" : "text-fin-ghost"}`}>
          {variant === "stock"
            ? "기업명을 입력하고 선택하면 종목 분석 허브로 이동합니다"
            : corpCode
            ? `CORP_CODE: ${corpCode} · fs_div 자동 · 연간 5년`
            : "기업명을 입력하고 자동완성에서 선택하면 즉시 조회됩니다"}
        </span>
      </div>
    </div>
  );
}

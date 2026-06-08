"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { CorpCode } from "@/types/dart";

interface SearchBarProps {
  onSearch: (params: {
    corpCode: string;
    corpName: string;
    year: string;
    reprtCode: string;
    fsDiv: string;
  }) => void;
  loading: boolean;
}

const YEARS = Array.from({ length: 12 }, (_, i) => String(2026 - i));

const REPRT_OPTIONS = [
  { label: "사업보고서 (연간)", value: "11011" },
  { label: "반기보고서", value: "11012" },
  { label: "1분기보고서", value: "11013" },
  { label: "3분기보고서", value: "11014" },
];

const FS_DIV_OPTIONS = [
  { label: "연결재무제표", value: "CFS" },
  { label: "별도재무제표", value: "OFS" },
];

export default function SearchBar({ onSearch, loading }: SearchBarProps) {
  const [query, setQuery] = useState("");
  const [corpCode, setCorpCode] = useState("");
  const [suggestions, setSuggestions] = useState<CorpCode[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [year, setYear] = useState("2023");
  const [reprtCode, setReprtCode] = useState("11011");
  const [fsDiv, setFsDiv] = useState("CFS");
  const [allCorps, setAllCorps] = useState<CorpCode[]>([]);
  const [corpsLoaded, setCorpsLoaded] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);

  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/api/corps")
      .then((r) => r.json())
      .then((data: CorpCode[]) => {
        setAllCorps(data.filter((c) => c.stock_code && c.stock_code.trim() !== ""));
        setCorpsLoaded(true);
      })
      .catch(() => setCorpsLoaded(true));
  }, []);

  const handleQueryChange = useCallback(
    (value: string) => {
      setQuery(value);
      setCorpCode("");
      setActiveIndex(-1);

      if (value.length < 1) {
        setSuggestions([]);
        setShowDropdown(false);
        return;
      }

      const filtered = allCorps.filter((c) => c.corp_name.includes(value)).slice(0, 10);
      setSuggestions(filtered);
      setShowDropdown(filtered.length > 0);
    },
    [allCorps]
  );

  const handleSelect = (corp: CorpCode) => {
    setQuery(corp.corp_name);
    setCorpCode(corp.corp_code);
    setSuggestions([]);
    setShowDropdown(false);
    inputRef.current?.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!showDropdown) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && activeIndex >= 0) {
      e.preventDefault();
      handleSelect(suggestions[activeIndex]);
    } else if (e.key === "Escape") {
      setShowDropdown(false);
    }
  };

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node) &&
        !inputRef.current?.contains(e.target as Node)
      ) {
        setShowDropdown(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const canSearch = !!corpCode && !loading;

  const handleSearch = () => {
    if (!canSearch) return;
    onSearch({ corpCode, corpName: query, year, reprtCode, fsDiv });
  };

  return (
    <div
      className="w-full relative overflow-visible font-terminal border border-primary/25 rounded-[2px]"
      style={{
        background: "linear-gradient(135deg, #0A1628 0%, #0f1f3d 50%, #0A1628 100%)",
        boxShadow: "0 0 0 1px rgba(240,180,41,0.08), 0 20px 60px rgba(0,0,0,0.5)",
      }}
    >
      {/* 상단 골드 라인 */}
      <div
        className="absolute top-0 left-0 right-0 h-[2px]"
        style={{
          background: "linear-gradient(90deg, transparent, #F0B429 30%, #F0B429 70%, transparent)",
        }}
      />

      {/* 헤더 */}
      <div className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-primary/[0.12]">
        <div className="flex items-center gap-3">
          <div
            className="w-[6px] h-[6px] rounded-full bg-primary"
            style={{ boxShadow: "0 0 8px #F0B429" }}
          />
          <span className="text-primary text-[10px] tracking-[0.2em] font-semibold uppercase">
            DART Financial Query
          </span>
        </div>
        <span className={`text-[9px] tracking-[0.15em] ${corpsLoaded ? "text-primary/50" : "text-white/20"}`}>
          {corpsLoaded ? `${allCorps.length.toLocaleString()} CORPS INDEXED` : "LOADING INDEX..."}
        </span>
      </div>

      {/* 컨트롤 영역 */}
      <div className="px-6 py-5 flex flex-col gap-4 lg:flex-row lg:items-end lg:gap-3">
        {/* 기업 검색 입력 */}
        <div className="flex-1 min-w-0 relative">
          <label className="block text-primary/60 text-[9px] tracking-[0.2em] mb-[6px] uppercase">
            기업명
          </label>
          <div className="relative">
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => handleQueryChange(e.target.value)}
              onKeyDown={handleKeyDown}
              onFocus={() => query && suggestions.length > 0 && setShowDropdown(true)}
              placeholder="삼성전자, SK하이닉스..."
              autoComplete="off"
              style={{
                width: "100%",
                background: "rgba(255,255,255,0.04)",
                border: corpCode ? "1px solid #F0B429" : "1px solid rgba(240,180,41,0.2)",
                borderBottom: corpCode ? "2px solid #F0B429" : "1px solid rgba(240,180,41,0.2)",
                borderRadius: "1px",
                padding: "10px 40px 10px 12px",
                color: corpCode ? "#F0B429" : "rgba(255,255,255,0.85)",
                fontSize: "13px",
                letterSpacing: "0.02em",
                outline: "none",
                transition: "all 0.2s ease",
                fontFamily: "inherit",
              }}
              onFocusCapture={(e) => {
                (e.target as HTMLInputElement).style.borderColor = "rgba(240,180,41,0.6)";
                (e.target as HTMLInputElement).style.background = "rgba(255,255,255,0.06)";
              }}
              onBlurCapture={(e) => {
                (e.target as HTMLInputElement).style.borderColor = corpCode
                  ? "#F0B429"
                  : "rgba(240,180,41,0.2)";
                (e.target as HTMLInputElement).style.background = "rgba(255,255,255,0.04)";
              }}
            />
            {corpCode && (
              <div className="absolute right-[10px] top-1/2 -translate-y-1/2 text-primary text-xs">
                ✓
              </div>
            )}
          </div>

          {/* 자동완성 드롭다운 */}
          {showDropdown && (
            <div
              ref={dropdownRef}
              className="absolute top-[calc(100%+4px)] left-0 right-0 border border-primary/30 rounded-[1px] z-50 max-h-60 overflow-y-auto"
              style={{
                background: "#0D1B35",
                boxShadow: "0 8px 32px rgba(0,0,0,0.6)",
              }}
            >
              {suggestions.map((corp, i) => (
                <div
                  key={corp.corp_code}
                  data-testid="autocomplete-item"
                  onMouseDown={() => handleSelect(corp)}
                  onMouseEnter={() => setActiveIndex(i)}
                  className="flex items-center justify-between px-3 py-[9px] cursor-pointer border-b border-white/[0.04] transition-colors duration-100"
                  style={{
                    background: i === activeIndex ? "rgba(240,180,41,0.12)" : "transparent",
                  }}
                >
                  <span
                    className="text-xs font-[inherit]"
                    style={{ color: i === activeIndex ? "#F0B429" : "rgba(255,255,255,0.8)" }}
                  >
                    {corp.corp_name}
                  </span>
                  <span className="text-primary/40 text-[10px] tracking-[0.1em] font-[inherit]">
                    {corp.stock_code}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 연도 선택 */}
        <div className="min-w-[100px]">
          <label className="block text-primary/60 text-[9px] tracking-[0.2em] mb-[6px] uppercase">
            사업연도
          </label>
          <SelectControl value={year} onChange={setYear} options={YEARS.map((y) => ({ label: y, value: y }))} />
        </div>

        {/* 보고서 구분 */}
        <div className="min-w-[160px]">
          <label className="block text-primary/60 text-[9px] tracking-[0.2em] mb-[6px] uppercase">
            보고서 구분
          </label>
          <SelectControl value={reprtCode} onChange={setReprtCode} options={REPRT_OPTIONS} />
        </div>

        {/* 재무제표 구분 */}
        <div className="min-w-[130px]">
          <label className="block text-primary/60 text-[9px] tracking-[0.2em] mb-[6px] uppercase">
            재무제표
          </label>
          <SelectControl value={fsDiv} onChange={setFsDiv} options={FS_DIV_OPTIONS} />
        </div>

        {/* 검색 버튼 */}
        <div>
          <div className="h-[21px]" />
          <button
            onClick={handleSearch}
            disabled={!canSearch}
            className="flex items-center justify-center gap-2 px-6 py-[10px] rounded-[1px] text-[11px] font-bold tracking-[0.15em] uppercase whitespace-nowrap transition-all duration-200 font-[inherit]"
            style={{
              background: canSearch
                ? "linear-gradient(135deg, #F0B429, #d49a1f)"
                : "rgba(240,180,41,0.15)",
              border: "none",
              color: canSearch ? "#0A1628" : "rgba(240,180,41,0.3)",
              cursor: canSearch ? "pointer" : "not-allowed",
              boxShadow: canSearch ? "0 4px 20px rgba(240,180,41,0.3)" : "none",
            }}
          >
            {loading ? (
              <>
                <Spinner />
                조회중
              </>
            ) : (
              <>
                <span>▶</span>
                조회
              </>
            )}
          </button>
        </div>
      </div>

      {/* 하단 상태바 */}
      <div className="px-6 py-2 flex items-center gap-2 border-t border-primary/[0.08] bg-black/20">
        <span
          className={`text-[9px] tracking-[0.15em] font-[inherit] ${
            corpCode ? "text-primary/50" : "text-white/15"
          }`}
        >
          {corpCode
            ? `CORP_CODE: ${corpCode} · READY`
            : "기업명을 입력하고 자동완성 목록에서 선택하세요"}
        </span>
      </div>
    </div>
  );
}

function SelectControl({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { label: string; value: string }[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-[1px] text-white/85 text-xs tracking-[0.02em] outline-none cursor-pointer font-[inherit]"
      style={{
        background: "rgba(255,255,255,0.04)",
        border: "1px solid rgba(240,180,41,0.2)",
        padding: "10px 28px 10px 12px",
        appearance: "none",
        backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='rgba(240,180,41,0.4)'/%3E%3C/svg%3E")`,
        backgroundRepeat: "no-repeat",
        backgroundPosition: "right 10px center",
      }}
    >
      {options.map((opt) => (
        <option
          key={opt.value}
          value={opt.value}
          style={{ background: "#0D1B35", color: "rgba(255,255,255,0.85)" }}
        >
          {opt.label}
        </option>
      ))}
    </select>
  );
}

function Spinner() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" style={{ animation: "spin 0.8s linear infinite" }}>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      <circle
        cx="12"
        cy="12"
        r="10"
        stroke="#0A1628"
        strokeWidth="3"
        fill="none"
        strokeDasharray="31.4"
        strokeDashoffset="10"
      />
    </svg>
  );
}

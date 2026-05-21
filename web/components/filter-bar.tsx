"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

function defaultBegin(): string {
  const d = new Date();
  d.setMonth(d.getMonth() - 1);
  return d.toISOString().slice(0, 10);
}

function toInputDate(value: string | null): string {
  if (!value) return defaultBegin();
  if (/^\d{8}$/.test(value)) {
    return `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`;
  }
  return value;
}

function toOptionalInputDate(value: string | null): string {
  if (!value) return "";
  return toInputDate(value);
}

function toQueryDate(value: string): string {
  return value.replaceAll("-", "");
}

interface FilterBarProps {
  countries: string[];
}

export function FilterBar({ countries }: FilterBarProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const begin = toInputDate(searchParams.get("begin"));
  const end = toOptionalInputDate(searchParams.get("end"));
  const [country, setCountry] = useState(searchParams.get("country") ?? "");

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const formBegin = String(form.get("begin") ?? "");
    const formEnd = String(form.get("end") ?? "");
    const params = new URLSearchParams();
    if (formBegin) params.set("begin", toQueryDate(formBegin));
    if (formEnd) params.set("end", toQueryDate(formEnd));
    if (country) params.set("country", country);
    router.push(`?${params.toString()}`);
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3">
      <div className="flex flex-col gap-1">
        <label htmlFor="begin" className="text-xs text-muted-foreground">
          시작일
        </label>
        <input
          id="begin"
          name="begin"
          type="date"
          defaultValue={begin}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="end" className="text-xs text-muted-foreground">
          종료일(비우면 오늘)
        </label>
        <input
          id="end"
          name="end"
          type="date"
          defaultValue={end}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs text-muted-foreground">국가</label>
        <Select value={country} onValueChange={(value) => setCountry(value ?? "")}>
          <SelectTrigger className="w-32">
            <SelectValue placeholder="전체" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">전체</SelectItem>
            {countries.map((c) => (
              <SelectItem key={c} value={c}>
                {c}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <Button type="submit">조회</Button>
    </form>
  );
}

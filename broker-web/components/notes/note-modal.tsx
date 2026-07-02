"use client";

import { useEffect, useState } from "react";
import { brokerClient, type NoteDetail, type NoteStatus, type EventType } from "@/lib/broker-client";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { formatKrw } from "@/lib/formatters";

const EVENT_LABEL: Record<EventType, string> = {
  buy: "매수",
  add_buy: "추가매수",
  partial_sell: "분할매도",
  sell: "전량매도",
};

const STATUS_LABEL: Record<NoteStatus, string> = {
  open: "진행중",
  partial: "분할매도",
  closed: "종료",
};

interface NoteModalProps {
  uid: string | "new" | null;
  symbol?: string;
  onClose: () => void;
  onSaved: () => void;
}

export function NoteModal({ uid, symbol, onClose, onSaved }: NoteModalProps) {
  const isNew = uid === "new";

  const [detail, setDetail] = useState<NoteDetail | null>(null);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [fSymbol, setFSymbol] = useState("");
  const [fStatus, setFStatus] = useState<NoteStatus>("open");
  const [fTargetPrice, setFTargetPrice] = useState("");
  const [fHoldingPeriod, setFHoldingPeriod] = useState("");
  const [fBuyReason, setFBuyReason] = useState("");
  const [fMemo, setFMemo] = useState("");

  useEffect(() => {
    if (uid === null) return;
    if (isNew) {
      setFSymbol(symbol ?? "");
      setFTargetPrice("");
      setFHoldingPeriod("");
      setFBuyReason("");
      setFMemo("");
      setError(null);
      return;
    }
    setLoading(true);
    setDetail(null);
    setMode("view");
    setError(null);
    brokerClient
      .getNote(uid)
      .then(setDetail)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [uid, isNew, symbol]);

  useEffect(() => {
    if (mode === "edit" && detail) {
      setFStatus(detail.status);
      setFTargetPrice(detail.target_price != null ? String(detail.target_price) : "");
      setFHoldingPeriod(detail.holding_period ?? "");
      setFBuyReason(detail.buy_reason ?? "");
      setFMemo(detail.memo ?? "");
      setError(null);
    }
  }, [mode, detail]);

  const handleCreate = async () => {
    setSaving(true);
    setError(null);
    try {
      await brokerClient.createNote({
        symbol: fSymbol.trim(),
        target_price: fTargetPrice ? Number(fTargetPrice) : null,
        holding_period: fHoldingPeriod || null,
        buy_reason: fBuyReason || null,
        memo: fMemo || null,
      });
      onSaved();
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleUpdate = async () => {
    if (!detail) return;
    setSaving(true);
    setError(null);
    try {
      await brokerClient.updateNote(detail.uid, {
        status: fStatus,
        target_price: fTargetPrice ? Number(fTargetPrice) : null,
        holding_period: fHoldingPeriod || null,
        buy_reason: fBuyReason || null,
        memo: fMemo || null,
      });
      const updated = await brokerClient.getNote(detail.uid);
      setDetail(updated);
      setMode("view");
      onSaved();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!detail) return;
    if (!confirm(`${detail.name ?? detail.symbol} 노트를 삭제하시겠습니까?`)) return;
    setSaving(true);
    try {
      await brokerClient.deleteNote(detail.uid);
      onSaved();
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={uid !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>
            {isNew
              ? "새 투자노트"
              : detail
                ? `${detail.name ?? detail.symbol} 투자노트`
                : "투자노트"}
          </DialogTitle>
        </DialogHeader>

        {loading && <p className="text-sm text-muted-foreground">불러오는 중...</p>}
        {error && <p className="text-xs text-status-loss">{error}</p>}

        {isNew && (
          <div className="flex flex-col gap-3">
            <div>
              <Label>종목코드 *</Label>
              <Input
                className="mt-1 font-mono"
                placeholder="005930"
                maxLength={6}
                value={fSymbol}
                onChange={(e) => setFSymbol(e.target.value)}
              />
            </div>
            <div>
              <Label>목표가</Label>
              <Input
                className="mt-1 font-mono tabular-nums"
                type="number"
                placeholder="85000"
                value={fTargetPrice}
                onChange={(e) => setFTargetPrice(e.target.value)}
              />
            </div>
            <div>
              <Label>예상 보유기간</Label>
              <Input
                className="mt-1"
                placeholder="3개월"
                value={fHoldingPeriod}
                onChange={(e) => setFHoldingPeriod(e.target.value)}
              />
            </div>
            <div>
              <Label>매수 이유</Label>
              <textarea
                className="mt-1 w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm outline-none focus-visible:border-ring resize-none"
                rows={3}
                placeholder="매수 thesis를 입력하세요"
                value={fBuyReason}
                onChange={(e) => setFBuyReason(e.target.value)}
              />
            </div>
            <div>
              <Label>메모</Label>
              <textarea
                className="mt-1 w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm outline-none focus-visible:border-ring resize-none"
                rows={2}
                placeholder="추가 메모"
                value={fMemo}
                onChange={(e) => setFMemo(e.target.value)}
              />
            </div>
            <DialogFooter>
              <Button variant="ghost" onClick={onClose} disabled={saving}>취소</Button>
              <Button onClick={handleCreate} disabled={saving || !fSymbol.trim()}>
                {saving ? "저장 중..." : "저장"}
              </Button>
            </DialogFooter>
          </div>
        )}

        {!isNew && detail && mode === "view" && (
          <div className="flex flex-col gap-4">
            <div className="flex items-center gap-2">
              <Badge variant="outline">{STATUS_LABEL[detail.status]}</Badge>
            </div>
            <div className="flex flex-col gap-2 text-sm">
              {detail.target_price != null && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">목표가</span>
                  <span className="font-mono tabular-nums">{formatKrw(detail.target_price)}</span>
                </div>
              )}
              {detail.holding_period && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">보유기간</span>
                  <span>{detail.holding_period}</span>
                </div>
              )}
              {detail.buy_reason && (
                <div className="flex flex-col gap-1">
                  <span className="text-muted-foreground">매수 이유</span>
                  <p className="text-foreground/90 leading-relaxed">{detail.buy_reason}</p>
                </div>
              )}
              {detail.memo && (
                <div className="flex flex-col gap-1">
                  <span className="text-muted-foreground">메모</span>
                  <p className="text-foreground/90">{detail.memo}</p>
                </div>
              )}
            </div>

            {detail.events.length > 0 && (
              <div className="flex flex-col gap-2 border-t border-border pt-3">
                <span className="text-xs font-medium text-muted-foreground">거래 기록</span>
                {detail.events.map((ev) => (
                  <div key={ev.id} className="flex items-center justify-between text-xs tabular-nums">
                    <span className="text-muted-foreground w-20">{ev.executed_at.slice(0, 10)}</span>
                    <span className="w-16">{EVENT_LABEL[ev.event_type]}</span>
                    <span className="font-mono">{formatKrw(ev.price)} × {ev.qty}주</span>
                  </div>
                ))}
              </div>
            )}

            <DialogFooter>
              <Button variant="destructive" size="sm" onClick={handleDelete} disabled={saving}>삭제</Button>
              <Button variant="outline" size="sm" onClick={() => setMode("edit")} disabled={saving}>수정</Button>
            </DialogFooter>
          </div>
        )}

        {!isNew && detail && mode === "edit" && (
          <div className="flex flex-col gap-3">
            <div>
              <Label>상태</Label>
              <Select value={fStatus} onValueChange={(v) => setFStatus(v as NoteStatus)}>
                <SelectTrigger className="mt-1 w-full">
                  <span className="flex flex-1 text-left text-sm">{STATUS_LABEL[fStatus]}</span>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="open">진행중</SelectItem>
                  <SelectItem value="partial">분할매도</SelectItem>
                  <SelectItem value="closed">종료</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>목표가</Label>
              <Input
                className="mt-1 font-mono tabular-nums"
                type="number"
                value={fTargetPrice}
                onChange={(e) => setFTargetPrice(e.target.value)}
              />
            </div>
            <div>
              <Label>예상 보유기간</Label>
              <Input
                className="mt-1"
                value={fHoldingPeriod}
                onChange={(e) => setFHoldingPeriod(e.target.value)}
              />
            </div>
            <div>
              <Label>매수 이유</Label>
              <textarea
                className="mt-1 w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm outline-none focus-visible:border-ring resize-none"
                rows={3}
                value={fBuyReason}
                onChange={(e) => setFBuyReason(e.target.value)}
              />
            </div>
            <div>
              <Label>메모</Label>
              <textarea
                className="mt-1 w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm outline-none focus-visible:border-ring resize-none"
                rows={2}
                value={fMemo}
                onChange={(e) => setFMemo(e.target.value)}
              />
            </div>
            <DialogFooter>
              <Button variant="ghost" onClick={() => setMode("view")} disabled={saving}>취소</Button>
              <Button onClick={handleUpdate} disabled={saving}>
                {saving ? "저장 중..." : "저장"}
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

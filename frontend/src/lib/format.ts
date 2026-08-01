/* Display formatting only — all financial math comes from the server. */
import type { CSSProperties } from "react";

export function money(v: number | null | undefined, opts: { sign?: boolean } = {}): string {
  if (v == null || isNaN(Number(v))) return "—";
  const n = Number(v);
  const abs = Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: Math.abs(n) < 100 ? 2 : 0,
  });
  const sign = opts.sign === false ? (n < 0 ? "−" : "") : n > 0 ? "+" : n < 0 ? "−" : "";
  return `${sign}$${abs}`;
}

export function pct(v: number | null | undefined, digits = 1): string {
  if (v == null || isNaN(Number(v))) return "—";
  return Number(v).toFixed(digits) + "%";
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v == null || isNaN(Number(v))) return "—";
  return Number(v).toFixed(digits);
}

export function pnlClass(v: number | null | undefined): "win" | "loss" | "flat" {
  if (v == null || Number(v) === 0 || isNaN(Number(v))) return "flat";
  return Number(v) > 0 ? "win" : "loss";
}

export function fmtDay(d: string): string {
  const dt = new Date(d + "T00:00:00");
  if (isNaN(dt.getTime())) return d;
  return dt.toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

export function fmtDayShort(d: string): string {
  const dt = new Date(d + "T00:00:00");
  if (isNaN(dt.getTime())) return d;
  return dt.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function timeShort(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** snake_case strategy shape → Title Case ("put_credit_spread" → "Put Credit Spread"). */
export function strategyLabel(direction?: string | null): string {
  if (!direction) return "";
  return direction
    .split("_")
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

export const conviction = (n?: number | null): string =>
  n ? "▲".repeat(n) + "△".repeat(Math.max(0, 5 - n)) : "";

export function dteFrom(expiration?: string | null): number | null {
  if (!expiration) return null;
  const exp = new Date(expiration + "T16:00:00");
  if (isNaN(exp.getTime())) return null;
  return Math.max(0, Math.ceil((exp.getTime() - Date.now()) / 86_400_000));
}

/** Human line for the auto-tracker sync result. */
export function syncMessage(r: {
  synced?: number;
  trades_created?: number;
  fills_attached?: number;
  unmatched?: number;
  dismissed_pre_epoch?: number;
  trades_expired?: number;
}): string {
  const parts: string[] = [];
  if (r.trades_created) parts.push(`${r.trades_created} new trade${r.trades_created === 1 ? "" : "s"}`);
  if (r.fills_attached) parts.push(`${r.fills_attached} fill${r.fills_attached === 1 ? "" : "s"} attached`);
  if (r.trades_expired) parts.push(`${r.trades_expired} expired`);
  if (r.unmatched) parts.push(`${r.unmatched} unmatched → repair in Settings`);
  if (r.dismissed_pre_epoch) parts.push(`${r.dismissed_pre_epoch} pre-epoch dismissed`);
  const head = `${r.synced ?? 0} fill${(r.synced ?? 0) === 1 ? "" : "s"} synced`;
  return parts.length ? `${head} → ${parts.join(", ")}` : `${head} — journal already current`;
}

/** Inline style for a tag badge/chip from its stored #rrggbb color. */
export function tagBadgeStyle(color?: string | null): CSSProperties | undefined {
  if (!color) return undefined;
  return { color, background: color + "26" };
}

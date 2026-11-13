import type { Unit } from "./api";

const compact = (currency: string) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency, notation: "compact", minimumFractionDigits: 0, maximumFractionDigits: 2 });
const full = (currency: string) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: 0 });

export function fmt(value: number | null | undefined, unit: Unit, currency = "USD"): string {
  if (value === null || value === undefined) return "n/a";
  if (!Number.isFinite(value)) return "∞";
  switch (unit) {
    case "currency":
      return Math.abs(value) >= 100_000 ? compact(currency).format(value) : full(currency).format(value);
    case "percent":
      return `${(value * 100).toFixed(1)}%`;
    case "months":
      return `${value.toFixed(1)} mo`;
    case "multiple":
      return `${value.toFixed(2)}x`;
    case "count":
      return Math.round(value).toLocaleString("en-US");
    default:
      return value.toFixed(2);
  }
}

export function fmtDelta(a: number | null, b: number | null, unit: Unit, currency = "USD"): string {
  if (a === null || b === null || !Number.isFinite(a) || !Number.isFinite(b)) return "";
  const d = b - a;
  if (Math.abs(d) < 1e-9) return "no change";
  const sign = d > 0 ? "+" : "−";
  if (unit === "percent") return `${sign}${Math.abs(d * 100).toFixed(1)} pts`;
  return `${sign}${fmt(Math.abs(d), unit, currency)}`;
}

export function monthLabel(iso: string): string {
  const [y, m] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" });
}

export function humanize(key: string): string {
  return key.replace(/_/g, " ").replace(/\b(sm|cac|arpa|mrr|arr|wacc|ltv|nrr|grr)\b/gi, (m) => m.toUpperCase());
}

/** Local date/time for display. Rendered on the server and in the browser, so callers mark the
 *  element with suppressHydrationWarning: the viewer's timezone wins after hydration. */
export function localTime(iso: string, withTime = true): string {
  const d = new Date(iso);
  return withTime
    ? d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

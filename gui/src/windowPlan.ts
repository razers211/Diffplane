// Change windows: picking a window in the browser's time zone. Times are stored in UTC by the
// orchestrator; the fabric's usual window comes from it as UTC instants (`upcoming`), so the browser
// never converts another zone itself.

import type { Span } from "./api";

export const MAX_DAYS = 7;       // at most 7 days between approval and window
export const STEP_MIN = 15;      // the picker moves in quarter hours
export const DAY_MIN = 24 * 60;

export interface Pick {
  day: number;     // 0 = today, in the browser's zone
  start: number;   // minutes after that day's midnight
  end: number;     // up to 1440
}

export const zoneName = () => Intl.DateTimeFormat().resolvedOptions().timeZone || "local time";

export function midnight(now: Date, day: number): Date {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() + day);
}

export function spanOf(pick: Pick, now: Date): { start: Date; end: Date } {
  const base = midnight(now, pick.day).getTime();
  return { start: new Date(base + pick.start * 60_000), end: new Date(base + pick.end * 60_000) };
}

/** A usual opening as a pick, when it starts and ends on one local day within reach. */
export function pickOf(span: Span, now: Date): Pick | null {
  const start = new Date(span.start), end = new Date(span.end);
  for (let day = 0; day < MAX_DAYS; day++) {
    const base = midnight(now, day).getTime();
    const s = (start.getTime() - base) / 60_000, e = (end.getTime() - base) / 60_000;
    if (s >= 0 && s < DAY_MIN && e <= DAY_MIN) return { day, start: Math.round(s), end: Math.round(e) };
  }
  return null;
}

/** The first usual opening that has not started yet, as a pick; else tomorrow 02:00-05:00. */
export function defaultPick(upcoming: Span[], now: Date): Pick {
  for (const span of upcoming) {
    const pick = new Date(span.start) > now ? pickOf(span, now) : null;
    if (pick) return pick;
  }
  return { day: 1, start: 120, end: 300 };
}

export function insideUsual(pickSpan: { start: Date; end: Date }, upcoming: Span[]): boolean {
  return upcoming.some((u) => new Date(u.start) <= pickSpan.start && pickSpan.end <= new Date(u.end));
}

/** Days (0..6) on which a usual opening starts. */
export function usualDays(upcoming: Span[], now: Date): Set<number> {
  return new Set(upcoming.map((u) => pickOf(u, now)?.day).filter((d): d is number => d !== undefined));
}

/** The earliest start allowed on a day: not in the past. */
export function earliest(day: number, now: Date): number {
  return day === 0 ? Math.ceil((now.getHours() * 60 + now.getMinutes() + 1) / STEP_MIN) * STEP_MIN : 0;
}

/** Keep a pick inside its day, after now, at least one step long. */
export function clampPick(p: Pick, now: Date): Pick {
  const low = earliest(p.day, now);
  const length = Math.max(STEP_MIN, p.end - p.start);
  const start = Math.min(Math.max(low, p.start), DAY_MIN - STEP_MIN);
  return { day: p.day, start, end: Math.min(DAY_MIN, Math.max(start + STEP_MIN, start + length)) };
}

export const hhmm = (minutes: number) =>
  `${String(Math.floor(minutes / 60) % 24).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;

export function dayLabel(day: number, now: Date): string {
  const d = midnight(now, day);
  if (day === 0) return "Today";
  if (day === 1) return "Tomorrow";
  return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
}

/** "in 2 days", "in 5 h", "in 12 min", "now". */
export function until(at: Date, now: Date): string {
  const minutes = Math.round((at.getTime() - now.getTime()) / 60_000);
  if (minutes <= 0) return "now";
  if (minutes < 60) return `in ${minutes} min`;
  if (minutes < 48 * 60) return `in ${Math.round(minutes / 60)} h`;
  return `in ${Math.round(minutes / (24 * 60))} days`;
}

/** A window as people read it: "Sat 31 Oct, 02:00–05:00". */
export function windowText(start: Date, end: Date): string {
  const day = start.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
  const t = (d: Date) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${day}, ${t(start)}–${t(end)}`;
}

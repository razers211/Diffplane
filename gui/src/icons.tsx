// Shared icons: two-tone, a soft filled shape under a 2 px outline, on a 24 px grid, in the current colour.

import type { ReactElement } from "react";

const props = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true } as const;
const soft = { fill: "currentColor", stroke: "none", opacity: 0.18 } as const;

export const ICONS: Record<string, ReactElement> = {
  overview: (
    <svg {...props}><path {...soft} d="M4 11l8-7 8 7v9H4z" /><path d="M4 11l8-7 8 7v9h-5v-6H9v6H4z" /></svg>
  ),
  topology: (
    <svg {...props}><circle {...soft} cx="12" cy="5" r="3" /><circle {...soft} cx="5" cy="19" r="3" /><circle {...soft} cx="19" cy="19" r="3" />
      <circle cx="12" cy="5" r="3" /><circle cx="5" cy="19" r="3" /><circle cx="19" cy="19" r="3" /><path d="M12 8v4m0 0-5 4.5M12 12l5 4.5" /></svg>
  ),
  devices: (
    <svg {...props}><rect {...soft} x="3" y="7" width="18" height="10" rx="3" /><rect x="3" y="7" width="18" height="10" rx="3" /><path d="M7 12h1m3 0h1m3 0h2" /></svg>
  ),
  segments: (
    <svg {...props}><rect {...soft} x="3" y="4" width="18" height="16" rx="3" /><rect x="3" y="4" width="18" height="16" rx="3" /><path d="M7 9h10M7 15h6" /></svg>
  ),
  routes: <svg {...props}><circle {...soft} cx="6" cy="18" r="3" /><circle cx="6" cy="18" r="3" /><path d="M9 18h5a4 4 0 000-8H9a3 3 0 010-6h9m-3-3 3 3-3 3" /></svg>,
  inventory: (
    <svg {...props}><path {...soft} d="M3 8l9-5 9 5v8l-9 5-9-5z" /><path d="M3 8l9-5 9 5v8l-9 5-9-5zM3 8l9 5 9-5M12 13v8" /></svg>
  ),
  routing: <svg {...props}><path {...soft} d="M5 20v-8a5 5 0 015-5h5v5h-5v8z" /><path d="M5 20v-8a5 5 0 015-5h9m-4-4 4 4-4 4" /></svg>,
  drift: <svg {...props}><path {...soft} d="M3 12h4l2-5 4 10 2-5h6v8H3z" /><path d="M3 12h4l2-5 4 10 2-5h6" /></svg>,
  diffs: (
    <svg {...props}><rect {...soft} x="4" y="3" width="16" height="18" rx="3" /><rect x="4" y="3" width="16" height="18" rx="3" /><path d="M9 9h6M12 6v6M9 16h6" /></svg>
  ),
  deploys: <svg {...props}><path {...soft} d="M4 15h16v5H4z" /><path d="M12 3v11m-4-4 4 4 4-4M4 15v5h16v-5" /></svg>,
  alerts: <svg {...props}><path {...soft} d="M6 16v-5a6 6 0 0112 0v5l2 2H4z" /><path d="M6 16v-5a6 6 0 0112 0v5l2 2H4zm4 5h4" /></svg>,
  fabric: (
    <svg {...props}><rect {...soft} x="3" y="3" width="8" height="8" rx="2" /><rect {...soft} x="13" y="13" width="8" height="8" rx="2" />
      <rect x="3" y="3" width="8" height="8" rx="2" /><rect x="13" y="13" width="8" height="8" rx="2" /><path d="M11 7h6v6M7 11v6h6" /></svg>
  ),
  admin: (
    <svg {...props}><path {...soft} d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z" /><path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z" /><path d="M9 12l2 2 4-4" /></svg>
  ),
  sun: (
    <svg {...props}><circle {...soft} cx="12" cy="12" r="4" /><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4" /></svg>
  ),
  moon: (
    <svg {...props}><path {...soft} d="M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z" /><path d="M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z" /></svg>
  ),
  audit: (
    <svg {...props}><circle {...soft} cx="11" cy="11" r="7" /><circle cx="11" cy="11" r="7" /><path d="M16 16l5 5M8 11h6" /></svg>
  ),
};

/** The brand mark: two planes, the intended one filled above the running one. */
export function BrandMark() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 2.5 21.5 7.75 12 13 2.5 7.75z" style={{ fill: "var(--brand, var(--accent))" }} />
      <path d="M2.5 12.5 12 17.75 21.5 12.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      <path d="M2.5 16.75 12 22 21.5 16.75" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" opacity="0.4" />
    </svg>
  );
}

import type { ReactElement } from "react";
import { ICONS } from "./icons";

// Status tiles: a label with its icon, a figure, a one-line explanation, under a rule in the tile's tone. Shared by the overview and every page that summarises itself.

export type Tone = "good" | "warn" | "bad" | "info";

export interface TileData {
  key: string;
  label: string;
  value: number;
  of?: number;
  sub: string;
  tone: Tone;
  icon: keyof typeof ICONS;
}

export function Tiles({ tiles, label }: { tiles: TileData[]; label: string }): ReactElement {
  return (
    <section className={`strip n${tiles.length}`} aria-label={label}>
      {tiles.map((t) => (
        <div key={t.key} className={`tile ${t.tone}`}>
          <span className="label"><span className="ico">{ICONS[t.icon]}</span>{t.label}</span>
          <span className="value">
            {t.value}
            {t.of !== undefined && <small>/{t.of}</small>}
          </span>
          <span className="sub">{t.sub}</span>
        </div>
      ))}
    </section>
  );
}

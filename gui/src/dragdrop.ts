// Drag and drop (less typing). Pointer events, so
// mouse, pen and touch all work; a tap (or Enter) on a draggable picks it up and the next tap on a
// zone that takes it puts it there, for touch screens and keyboards. From the approved prototype.
//
//   draggable:  data-drag='{"type":"segment","label":"web",...}'   (data-no-pick: a click stays a click)
//   drop zone:  data-drop="<zone>" data-accept="segment trunk" data-target="<anything>"
//   a page:     useDrops({ <zone>: (payload, target, point) => ... })

import { useEffect } from "react";

export interface Payload { type: string; label: string; [key: string]: unknown }
export interface Point { x: number; y: number }
export type DropHandler = (payload: Payload, target: string, point: Point) => void;

const handlers = new Map<string, DropHandler>();

/** The drop handlers of a page, for as long as it is shown. */
export function useDrops(zones: Record<string, DropHandler>) {
  useEffect(() => {
    for (const [zone, handler] of Object.entries(zones)) handlers.set(zone, handler);
    return () => { for (const zone of Object.keys(zones)) if (handlers.get(zone) === zones[zone]) handlers.delete(zone); };
  });
}

/** The attributes of a draggable. */
export const draggable = (payload: Payload) => ({ "data-drag": JSON.stringify(payload) });
/** The attributes of a drop zone taking `accept` (space-separated payload types). */
export const dropZone = (zone: string, accept: string, target: string) => ({ "data-drop": zone, "data-accept": accept, "data-target": target });

export const accepts = (accept: string | undefined, type: string) => (accept ?? "").split(" ").includes(type);

type Zone = HTMLElement | SVGElement;
const zoneOf = (el: Element | null, p: Payload): Zone | null => {
  let at = el as Zone | null;
  while (at && !(at.dataset?.drop && accepts(at.dataset.accept, p.type))) at = at.parentElement as Zone | null;
  return at;
};
const zones = () => [...document.querySelectorAll<HTMLElement>("[data-drop]")];
const mark = (p: Payload | null) => zones().forEach((z) => z.classList.toggle("can", !!p && accepts(z.dataset.accept, p.type)));
const place = (p: Payload, zone: Zone, point: Point) => { mark(null); handlers.get(zone.dataset.drop!)?.(p, zone.dataset.target ?? "", point); };
const payloadOf = (el: Element) => JSON.parse((el as HTMLElement).dataset.drag!) as Payload;

let drag: { src: HTMLElement; p: Payload; x0: number; y0: number; on: boolean; over: Zone | null; ghost: HTMLElement | null } | null = null;
let picked: { src: HTMLElement; p: Payload } | null = null;
let justDragged: HTMLElement | null = null;

export function cancelPick() {
  if (!picked) return;
  picked.src.classList.remove("picked");
  mark(null);
  picked = null;
  document.getElementById("pickbar")?.remove();
}

let installed = false;
/** Installs the document listeners once. */
export function installDragDrop() {
  if (installed || typeof document === "undefined") return;
  installed = true;
  document.addEventListener("pointerdown", (e) => {
    const src = (e.target as Element).closest<HTMLElement>("[data-drag]");
    if (!src || e.button > 0) return;
    drag = { src, p: payloadOf(src), x0: e.clientX, y0: e.clientY, on: false, over: null, ghost: null };
  });
  document.addEventListener("pointermove", (e) => {
    if (!drag) return;
    if (!drag.on) {
      if (Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) < 5) return;
      drag.on = true;
      document.body.classList.add("dragging");
      mark(drag.p);
      cancelPick();
      if (drag.src instanceof SVGElement) {   // a drawn switch: a label, since its shape cannot leave its SVG
        drag.ghost = document.createElement("span");
        drag.ghost.className = "drag-ghost drag-label";
        drag.ghost.textContent = drag.p.label;
      } else {
        drag.ghost = drag.src.cloneNode(true) as HTMLElement;
        drag.ghost.classList.add("drag-ghost");
        drag.ghost.removeAttribute("data-drag");
      }
      document.body.append(drag.ghost);
    }
    e.preventDefault();
    drag.ghost!.style.left = `${e.clientX}px`;
    drag.ghost!.style.top = `${e.clientY}px`;
    const over = zoneOf(document.elementFromPoint(e.clientX, e.clientY), drag.p);
    if (over !== drag.over) { drag.over?.classList.remove("over"); over?.classList.add("over"); drag.over = over; }
  }, { passive: false });
  document.addEventListener("pointerup", (e) => {
    const d = drag;
    drag = null;
    if (!d?.on) return;   // a click: the click listener handles it
    document.body.classList.remove("dragging");
    d.ghost?.remove();
    d.over?.classList.remove("over");
    mark(null);
    const zone = zoneOf(document.elementFromPoint(e.clientX, e.clientY), d.p);
    if (zone) place(d.p, zone, { x: e.clientX, y: e.clientY });
    justDragged = d.src;
    setTimeout(() => { justDragged = null; }, 0);
  });
  document.addEventListener("click", (e) => {
    const target = e.target as Element;
    if (picked) {
      const zone = zoneOf(target, picked.p);
      if (zone) {
        e.preventDefault(); e.stopPropagation();
        const p = picked.p;
        cancelPick();
        return place(p, zone, { x: e.clientX, y: e.clientY });
      }
    }
    const src = target.closest<HTMLElement>("[data-drag]");
    if (src && src === justDragged) { e.preventDefault(); e.stopPropagation(); return; }   // the end of a drag, not a click
    if (!src || src.dataset.noPick !== undefined) return;
    e.preventDefault(); e.stopPropagation();
    if (picked?.src === src) return cancelPick();
    cancelPick();
    picked = { src, p: payloadOf(src) };
    src.classList.add("picked");
    mark(picked.p);
    const bar = document.createElement("div");
    bar.id = "pickbar"; bar.className = "pickbar"; bar.setAttribute("role", "status");
    const text = document.createElement("span");
    text.textContent = `Now tap where ${picked.p.label} goes`;
    const cancel = document.createElement("button");
    cancel.textContent = "Cancel";
    cancel.onclick = (ev) => { ev.stopPropagation(); cancelPick(); };
    bar.append(text, cancel);
    document.body.append(bar);
  }, true);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") return cancelPick();
    if (!picked || (e.key !== "Enter" && e.key !== " ")) return;
    const zone = zoneOf(e.target as Element, picked.p);   // keyboard: Enter on a focused zone puts it there
    if (!zone) return;
    e.preventDefault(); e.stopPropagation();
    const p = picked.p, r = zone.getBoundingClientRect();
    cancelPick();
    place(p, zone, { x: r.x + r.width / 2, y: r.y + r.height / 2 });
  }, true);
}

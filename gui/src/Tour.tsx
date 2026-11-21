import { useEffect, useLayoutEffect, useState } from "react";
import { TOURS, visibleSteps, type TourStep } from "./tours";
import type { Route } from "./nav";

// "How does this page work?": a spotlight on one part of the page at a time, with a short
// explanation. Esc or Close ends it.

export function TourButton({ route }: { route: Route }) {
  const [steps, setSteps] = useState<TourStep[] | null>(null);
  const all = TOURS[route.page];
  useEffect(() => setSteps(null), [route]);
  if (!all) return null;
  const start = () => {
    const shown = visibleSteps(all, (s) => document.querySelector(s) !== null);
    setSteps(shown.length ? shown : null);
  };
  return (
    <>
      <button className="tour-button" onClick={start} aria-haspopup="dialog">How does this page work?</button>
      {steps && <Spotlight steps={steps} onClose={() => setSteps(null)} />}
    </>
  );
}

function Spotlight({ steps, onClose }: { steps: TourStep[]; onClose: () => void }) {
  const [i, setI] = useState(0);
  const [box, setBox] = useState<DOMRect | null>(null);
  const step = steps[i];

  useLayoutEffect(() => {
    const target = document.querySelector(step.selector);
    if (!target) return setBox(null);
    target.scrollIntoView({ block: "center", behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    const measure = () => setBox(target.getBoundingClientRect());
    const timer = window.setTimeout(measure, 350);
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [step.selector]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);

  const pad = 8;
  const below = box ? box.bottom + 14 + 170 < window.innerHeight : true;
  const cardTop = box ? (below ? box.bottom + 14 : Math.max(12, box.top - 184)) : 80;
  const cardLeft = box ? Math.min(window.innerWidth - 336, Math.max(12, box.left)) : 12;
  return (
    <div className="tour" role="dialog" aria-label="Walk-through of this page">
      {box && <div className="tour-spot" style={{ left: box.left - pad, top: box.top - pad, width: box.width + 2 * pad, height: box.height + 2 * pad }} />}
      <div className="tour-card" style={{ left: cardLeft, top: cardTop }}>
        <span className="tour-step">Step {i + 1} of {steps.length}</span>
        <p>{step.text}</p>
        <div className="tour-row">
          <button onClick={onClose}>Close</button>
          <span className="inline">
            {i > 0 && <button onClick={() => setI(i - 1)}>Back</button>}
            <button className="primary" autoFocus onClick={() => (i + 1 < steps.length ? setI(i + 1) : onClose())}>
              {i + 1 < steps.length ? "Next" : "Done"}
            </button>
          </span>
        </div>
      </div>
    </div>
  );
}

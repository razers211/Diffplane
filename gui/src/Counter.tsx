import { useEffect, useRef, useState } from "react";

// A number that counts to its new value: 600 ms, eased; instant when the
// viewer asks for reduced motion.

const REDUCED = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export function Counter({ value }: { value: number }) {
  const [shown, setShown] = useState(REDUCED ? value : 0);
  const from = useRef(REDUCED ? value : 0);
  useEffect(() => {
    if (REDUCED) return setShown(value);
    const start = performance.now(), begin = from.current;
    let frame = 0;
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / 600);
      const v = Math.round(begin + (value - begin) * (1 - (1 - t) ** 3));
      setShown(v);
      from.current = v;
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [value]);
  return <>{shown}</>;
}

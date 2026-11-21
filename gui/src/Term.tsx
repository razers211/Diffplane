import type { ReactNode } from "react";
import { GLOSSARY } from "./glossary";

/** A fabric word that explains itself on hover or keyboard focus. */
export function Term({ k, children }: { k: keyof typeof GLOSSARY | string; children: ReactNode }) {
  const text = GLOSSARY[k];
  if (!text) return <>{children}</>;
  return (
    <span className="term" tabIndex={0}>
      {children}
      <span className="term-tip" role="tooltip">{text}</span>
    </span>
  );
}

// Classify lines of `annet diff` output for colouring. Pure, so it is unit tested.

export type DiffLineKind = "add" | "remove" | "context";

export function classify(line: string): DiffLineKind {
  const trimmed = line.trimStart();
  if (trimmed.startsWith("+")) return "add";
  if (trimmed.startsWith("-")) return "remove";
  return "context";
}

// Where a diff came from: a diff or drift job, or the verification of a deploy.
export function jobHref(kind: string | null, id: number): string {
  return kind === "deploy" ? `#/changes/deploys/${id}` : `#/changes/checks/${id}`;
}

export function jobLabel(kind: string | null, id: number): string {
  return kind === "deploy" ? `deploy #${id}` : `#${id}`;
}

export function diffLines(text: string): { kind: DiffLineKind; text: string }[] {
  return text
    .replace(/\n$/, "")
    .split("\n")
    .filter((line) => line.length > 0)
    .map((line) => ({ kind: classify(line), text: line }));
}

// -- patches (what a deploy sends): VRP commands, not +/- lines -----------------

export type PatchLineKind = "add" | "remove" | "context" | "nav";

const indent = (line: string) => line.length - line.trimStart().length;

/** Per line of a patch: `undo ...` removes, `quit` only navigates, a line that opens a block
 * (the next line is indented deeper) only says where, every other line adds or changes. */
export function patchLines(text: string): { kind: PatchLineKind; text: string }[] {
  const lines = text.replace(/\n$/, "").split("\n").filter((l) => l.trim().length > 0);
  return lines.map((line, i) => {
    const trimmed = line.trim();
    if (trimmed === "quit") return { kind: "nav", text: line };
    if (trimmed.startsWith("undo ")) return { kind: "remove", text: line };
    const next = lines[i + 1];
    if (next !== undefined && indent(next) > indent(line)) return { kind: "context", text: line };
    return { kind: "add", text: line };
  });
}

/** Lines a patch adds and removes. */
export function patchStats(text: string | null): { added: number; removed: number } {
  const lines = text ? patchLines(text) : [];
  return {
    added: lines.filter((l) => l.kind === "add").length,
    removed: lines.filter((l) => l.kind === "remove").length,
  };
}

/** Lines an `annet diff` would add (+) and remove (-) on the switch. */
export function diffStats(text: string | null): { added: number; removed: number } {
  const lines = text ? diffLines(text) : [];
  return {
    added: lines.filter((l) => l.kind === "add").length,
    removed: lines.filter((l) => l.kind === "remove").length,
  };
}

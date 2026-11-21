"""Pre-commit hook: refuse files containing any value of a deny-list.

The list lives outside the repository (one literal per line, `#` comments; matched
case-insensitively). Its path comes from DIFFPLANE_DENYLIST. Skipped when that is unset or in CI,
where the list is not available.
"""

import os
import sys


def load(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [line.strip().lower() for line in f if line.strip() and not line.startswith("#")]


def offending(files: list[str], words: list[str]) -> list[tuple[str, int]]:
    """(file, line number) of every hit; never the value itself, so CI logs cannot leak it."""
    hits = []
    for name in files:
        try:
            with open(name, encoding="utf-8", errors="ignore") as f:
                for number, line in enumerate(f, 1):
                    low = line.lower()
                    if any(word in low for word in words):
                        hits.append((name, number))
        except (FileNotFoundError, IsADirectoryError):
            continue
    return hits


def main(argv: list[str]) -> int:
    path = os.environ.get("DIFFPLANE_DENYLIST")
    if os.environ.get("CI") or not path:
        print("deny-list check skipped (DIFFPLANE_DENYLIST unset or CI)")
        return 0
    hits = offending(argv, load(path))
    for name, number in hits:
        print(f"{name}:{number}: contains a deny-listed value")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

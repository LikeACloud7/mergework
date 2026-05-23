from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 12 * 1024


def main() -> int:
    agents = ROOT / "AGENTS.md"
    if not agents.exists():
        print("AGENTS.md is missing")
        return 1
    size = agents.stat().st_size
    if size > MAX_BYTES:
        print(f"AGENTS.md is {size} bytes; limit is {MAX_BYTES}")
        return 1
    print(f"AGENTS.md ok ({size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Entry point: rigenera gli indici Obsidian."""
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Rigenera indici README.md Obsidian.")
    parser.add_argument("--root-path", type=Path, default=None)
    args = parser.parse_args()

    root = args.root_path or Path(__file__).parent.parent
    from scripts.obsidian.indexes import rebuild
    result = rebuild(root)
    print(f"Indice globale: {result['global_index']}")
    print(f"Task: {result['task_indexes']}  Call: {result['calls']}")


if __name__ == "__main__":
    main()

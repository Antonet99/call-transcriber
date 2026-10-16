"""Compare watcher operations with a Git revision using synthetic files only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import types

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["CALL_PIPELINE_NO_DOTENV"] = "1"
sys.dont_write_bytecode = True

from run_isolated import guard

sys.addaudithook(guard)

from scripts import recording_catalog, recordings, watch_calls


def baseline_module(ref: str, filename: str):
    result = subprocess.run(
        ["git", "show", f"{ref}:scripts/{filename}.py"], cwd=ROOT,
        check=True, capture_output=True,
    )
    module = types.ModuleType(f"baseline_{filename}")
    module.__package__ = "scripts"
    module.__file__ = str(ROOT / "scripts" / f"{filename}.py")
    exec(compile(result.stdout, module.__file__, "exec"), module.__dict__)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default="b561cb6")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--sizes", type=int, nargs="+", default=[10, 100, 300])
    parser.add_argument("--catalog-sizes", type=int, nargs="+", default=[20, 200])
    parser.add_argument("--temp-dir", type=Path)
    args = parser.parse_args()
    if args.runs < 1 or any(size < 1 for size in args.sizes + args.catalog_sizes):
        parser.error("runs and sizes must be positive")
    variants = {
        "before": (baseline_module(args.baseline_ref, "watch_calls"),
                   baseline_module(args.baseline_ref, "recording_catalog")),
        "after": (watch_calls, recording_catalog),
    }
    samples = {}

    def measure(label, name, operation):
        cpu, start = time.process_time(), time.perf_counter()
        value = operation()
        samples.setdefault(label, {}).setdefault(name, []).append({
            "wall_ms": (time.perf_counter() - start) * 1000,
            "cpu_ms": (time.process_time() - cpu) * 1000,
        })
        return value

    def order(run):
        return list(variants) if run % 2 == 0 else list(reversed(variants))

    for count in args.sizes:
        with tempfile.TemporaryDirectory(prefix="watch-benchmark-", dir=args.temp_dir) as directory:
            root = Path(directory)
            incoming = root / "da_processare"
            incoming.mkdir()
            for index in range(count):
                (incoming / f"{index:05d}.mp4").write_bytes(b"synthetic")
            for run in range(args.runs):
                for name in order(run):
                    for filename in ("watcher-state.json", "watcher-state.json.bak"):
                        (root / ".pipeline" / filename).unlink(missing_ok=True)
                    handler = variants[name][0]._CallHandler(root, {}, readiness_interval_seconds=3600)
                    try:
                        measure(f"initial_scan:{count}", name, handler.scan)
                        measure(f"incremental_scan:{count}", name, handler.scan)
                        assert len(handler.snapshot()) == count
                    finally:
                        handler.stop()

    for count in args.catalog_sizes:
        with tempfile.TemporaryDirectory(prefix="catalog-benchmark-", dir=args.temp_dir) as directory:
            root = Path(directory)
            task = root / "completate" / "Task" / "Alpha"
            task.mkdir(parents=True)
            (task / "README.md").write_text("# Alpha\n", encoding="utf-8")
            for index in range(count):
                call = task / f"2026-10-15 09.30 - Synthetic {index}"
                call.mkdir()
                (call / f"Synthetic {index}.m4a").write_bytes(b"synthetic")
                marker = recordings._marker_data(f"{index:024x}", hashlib.sha256(b"synthetic").hexdigest(), "save")
                (call / recordings._MARKER).write_text(json.dumps(marker), encoding="utf-8")
                (call / f"Synthetic {index}.md").write_text("# Synthetic\n", encoding="utf-8")
            for run in range(args.runs):
                reference = None
                for name in order(run):
                    items = measure(f"catalog_saved:{count}", name,
                                    lambda: variants[name][1].list_unprocessed(root))
                    assert len(items) == count
                    if reference is not None:
                        assert items == reference
                    reference = items

    for label, results in samples.items():
        summary = {}
        for name, values in results.items():
            wall = [sample["wall_ms"] for sample in values]
            summary[name] = {
                "runs": len(values), "median_ms": statistics.median(wall),
                "min_ms": min(wall), "max_ms": max(wall),
                "cpu_median_ms": statistics.median(sample["cpu_ms"] for sample in values),
                "samples": values,
            }
        print(json.dumps({"scenario": label, "results": summary}), flush=True)


if __name__ == "__main__":
    main()

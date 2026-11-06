"""Partition complete test files across isolated CI jobs, never a shared database."""

import argparse
import subprocess
import sys
from pathlib import Path


def partition(files, count):
    if count < 1:
        raise ValueError("Shard count must be positive")
    groups, weights = [[] for _ in range(count)], [0] * count
    # File size is a cheap stable approximation; whole files preserve module fixtures.
    for path in sorted(files, key=lambda p: (-p.stat().st_size, p.as_posix())):
        target = min(range(count), key=lambda i: (weights[i], i))
        groups[target].append(path)
        weights[target] += path.stat().st_size
    return [sorted(group) for group in groups]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()
    if not 0 <= args.index < args.count:
        parser.error("Shard index must be between zero and count - 1")
    backend = Path(__file__).resolve().parents[2] / "backend"
    files = list((backend / "tests").rglob("test_*.py"))
    files += list((backend / "tests").rglob("*_test.py"))
    groups = partition(sorted(set(files)), args.count)
    selected = groups[args.index]
    if not selected:
        raise SystemExit("Refusing an empty test shard")
    print(
        f"Shard {args.index + 1}/{args.count}: {len(selected)}/{len(set(files))} test files",
        flush=True,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--durations=10",
            *[str(p.relative_to(backend)) for p in selected],
        ],
        cwd=backend,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

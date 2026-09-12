"""Run a list of subprocesses in order, streaming their output, stopping at the first failure.

The UI shows one job with one log; underneath, processing a session is build.py then report.py.
Chaining them here rather than in the server keeps the server's job model to one process.
"""
from __future__ import annotations

import json
import subprocess
import sys


def main() -> int:
    steps = json.loads(sys.argv[1])
    for i, argv in enumerate(steps, 1):
        print(f"--- step {i}/{len(steps)}: {' '.join(argv[2:3]) or argv[0]}", flush=True)
        code = subprocess.call(argv)
        if code != 0:
            print(f"!! step {i} exited {code}", flush=True)
            return code
    print("--- all steps done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

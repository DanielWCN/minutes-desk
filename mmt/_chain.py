"""Run a list of subprocesses in order, streaming their output, stopping at the first failure.

The UI shows one job with one log; underneath, processing a session is build.py then report.py.
Chaining them here rather than in the server keeps the server's job model to one process.

A step whose first word is "?" may fail without taking the rest of the chain down. That is
for work that is a bonus rather than the point: drafting the minutes with a model needs a
network, a key or a local server, and none of those are reasons to leave a person without
the transcript and the document that were already built.
"""
from __future__ import annotations

import json
import subprocess
import sys


def main() -> int:
    steps = json.loads(sys.argv[1])
    for i, argv in enumerate(steps, 1):
        soft = bool(argv) and argv[0] == "?"
        if soft:
            argv = argv[1:]
        print(f"--- step {i}/{len(steps)}: {' '.join(argv[2:3]) or argv[0]}", flush=True)
        code = subprocess.call(argv)
        if code != 0:
            print(f"!! step {i} exited {code}"
                  + ("\uff08\u8df3\u8fc7\uff0c\u4e0d\u5f71\u54cd\u5176\u4ed6"
                     "\u6b65\u9aa4\uff09" if soft else ""), flush=True)
            if not soft:
                return code
    print("--- all steps done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

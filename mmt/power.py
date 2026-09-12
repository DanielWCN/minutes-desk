"""
Keep the machine awake for the length of a recording, and be honest about the lid.

Two different things can put the machine to sleep:

  idle sleep     -> ES_SYSTEM_REQUIRED vetoes it. This is the common case: a long
                    meeting where you stop touching the keyboard. Handled automatically.
  closing the lid -> a power *policy action*, not an idle timeout. No user-space API can
                    veto it. On this machine LIDACTION is even hidden from powercfg by
                    the OEM, so we cannot reliably read it either.

So instead of pretending, we do three honest things:
  1. veto idle sleep while recording
  2. detect afterwards that a suspend happened, and how long the hole is
  3. print the one command that fixes the lid policy, and let the human decide
"""
from __future__ import annotations

import ctypes
import re
import subprocess
import time

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_AWAYMODE_REQUIRED = 0x00000040

LID_FIX = (
    'powercfg -attributes SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 -ATTRIB_HIDE & '
    'powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0 & '
    'powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0 & '
    'powercfg /S SCHEME_CURRENT'
)


class KeepAwake:
    """Veto idle sleep for as long as this context is open."""

    def __init__(self) -> None:
        self.ok = False
        self.mode = "none"

    def __enter__(self):
        try:
            k = ctypes.windll.kernel32
            if k.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED):
                self.ok, self.mode = True, "system+away"
            elif k.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
                self.ok, self.mode = True, "system"
        except Exception:  # noqa: BLE001
            pass
        return self

    def __exit__(self, *_exc) -> None:
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception:  # noqa: BLE001
            pass


def lid_action() -> str:
    """'safe' (lid does nothing) / 'risky' (lid sleeps) / 'hidden' (OEM hid the setting)."""
    try:
        out = subprocess.run(
            ["powercfg", "-q", "SCHEME_CURRENT", "SUB_BUTTONS", "LIDACTION"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace").stdout or ""
    except Exception:  # noqa: BLE001
        return "hidden"
    idx = re.findall(r"Current (?:AC|DC) Power Setting Index:\s*0x([0-9a-fA-F]+)", out)
    if not idx:
        idx = re.findall(r"设置索引[:：]\s*0x([0-9a-fA-F]+)", out)
    if not idx or "LIDACTION" not in out.upper():
        return "hidden"
    return "safe" if all(int(v, 16) == 0 for v in idx) else "risky"


class SuspendWatch:
    """
    Notice that the machine was suspended. Wall clock keeps counting through a suspend,
    the audio stream does not, so a jump in wall time between two loop iterations that
    are supposed to be 0.5 s apart means we lost that much audio.
    """

    def __init__(self, jump_s: float = 8.0) -> None:
        self.jump_s = jump_s
        self.last = time.time()
        self.events: list[dict] = []

    def tick(self, elapsed: float) -> dict | None:
        now = time.time()
        gap = now - self.last
        self.last = now
        if gap > self.jump_s:
            ev = {"at_s": round(elapsed, 1), "lost_s": round(gap, 1),
                  "wall": time.strftime("%H:%M:%S", time.localtime(now))}
            self.events.append(ev)
            return ev
        return None

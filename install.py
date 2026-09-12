#!/usr/bin/env python3
"""Install Minutes Desk on this machine, unattended.

Written to be run by an agent as much as by a person: no questions, no prompts, one exit
code, and every step idempotent so running it twice is harmless and running it after an
update just tops things up.

    python install.py                    everything: venv, packages, speech model, config
    python install.py --no-model         skip the 1.6 GB model download for now
    python install.py --no-seed          do not read the local calendar
    python install.py --dry              print the plan and touch nothing

What it deliberately does NOT do: turn on the OneDrive archive, accept any
send-my-transcript-to-a-service terms, or put anything on the network beyond PyPI and the
speech model. Those are the user's calls and the app asks for them in place, once.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
MIN_PY = (3, 10)
LAUNCHER = "Minutes Desk.bat"


# A Windows console is usually not UTF-8, and one un-encodable character in a status line
# must not take down a working install at step six of six.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")     # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")     # type: ignore[union-attr]
except Exception:                                                  # noqa: BLE001
    pass


def say(s: str = "") -> None:
    try:
        print(s, flush=True)
    except Exception:                                              # noqa: BLE001
        print(s.encode("ascii", "replace").decode("ascii"), flush=True)


def step(n: int, total: int, s: str) -> None:
    say(f"\n[{n}/{total}] {s}")


def vpy() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(cmd: list[str], what: str) -> None:
    p = subprocess.run(cmd, cwd=str(ROOT))                          # noqa: S603
    if p.returncode:
        raise SystemExit(f"\n{what} failed (exit {p.returncode}). Nothing was left half done "
                         f"that a second run cannot fix - read the error above, then run "
                         f"this file again.")


def check_python() -> None:
    if sys.version_info < MIN_PY:
        raise SystemExit(f"Python {MIN_PY[0]}.{MIN_PY[1]} or newer is needed; this is "
                         f"{sys.version.split()[0]}. Install a newer Python and re-run.")
    say(f"  {sys.version.split()[0]} at {sys.executable}")


def make_venv(dry: bool) -> None:
    if vpy().exists():
        say(f"  already there: {VENV}")
        return
    say(f"  creating {VENV}")
    if not dry:
        run([sys.executable, "-m", "venv", str(VENV)], "creating the virtual environment")


def install_packages(dry: bool) -> None:
    req = ROOT / "requirements.txt"
    say(f"  pip install -r {req.name}  (about 400 MB the first time)")
    if dry:
        return
    run([str(vpy()), "-m", "pip", "install", "--upgrade", "--quiet", "pip"], "upgrading pip")
    run([str(vpy()), "-m", "pip", "install", "-r", str(req)], "installing packages")


def fetch_model(dry: bool) -> None:
    say("  large-v3-turbo, about 1.6 GB, cached in your Hugging Face folder")
    if dry:
        return
    code = ("from faster_whisper import WhisperModel;"
            "WhisperModel('large-v3-turbo', device='cpu', compute_type='int8');"
            "print('model ready')")
    t = time.time()
    run([str(vpy()), "-c", code], "downloading the speech model")
    say(f"  took {time.time() - t:.0f}s")


def first_run(dry: bool, seed: bool) -> None:
    say("  config, private glossary, and the names already on this machine's calendar")
    if dry:
        return
    cmd = [str(vpy()), "firstrun.py"] + ([] if seed else ["--no-seed"])
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    p = subprocess.run(cmd, cwd=str(ROOT / "mmt"), env=env)         # noqa: S603
    if p.returncode:
        say("  first-run setup reported a problem; the app will retry on its own next start")


def shortcut(dry: bool) -> None:
    target = ROOT / LAUNCHER
    if not target.exists():
        say(f"  {LAUNCHER} is missing, skipping the shortcut")
        return
    desktop = Path(os.path.expanduser("~/Desktop"))
    if not desktop.is_dir():
        say("  no Desktop folder, skipping the shortcut")
        return
    link = desktop / "Minutes Desk.lnk"
    say(f"  {link}")
    if dry or os.name != "nt":
        return
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
          f"$s.TargetPath='{target}';$s.WorkingDirectory='{ROOT}';$s.Save()")
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",              # noqa: S603
                    "-ExecutionPolicy", "Bypass", "-Command", ps],
                   capture_output=True)


def report(dry: bool) -> None:
    if dry:
        return
    # Keep this ASCII: the report exists to be readable in whatever console the user has.
    code = ("import doctor;r=doctor.run(levels=False);"
            "print('selfcheck %s/%s passed, state=%s, ready=%s'"
            " % (r.get('ok'),r.get('total'),r.get('state'),r.get('ready')))")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    p = subprocess.run([str(vpy()), "-c", code], cwd=str(ROOT / "mmt"),               # noqa: S603
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    out = [l for l in (p.stdout or "").splitlines() if l.startswith("selfcheck")]
    say("  " + (out[-1] if out else "self-check did not answer; open the app and look at it"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Install Minutes Desk, unattended")
    ap.add_argument("--no-model", action="store_true", help="skip the 1.6 GB speech model")
    ap.add_argument("--no-seed", action="store_true", help="do not read the local calendar")
    ap.add_argument("--no-shortcut", action="store_true")
    ap.add_argument("--dry", action="store_true", help="print the plan, change nothing")
    a = ap.parse_args()

    total = 6
    say(f"Minutes Desk - installing into {ROOT}")
    step(1, total, "Python")
    check_python()
    step(2, total, "private environment")
    make_venv(a.dry)
    step(3, total, "packages")
    install_packages(a.dry)
    step(4, total, "speech model")
    if a.no_model:
        say("  skipped; the app offers a one-click download in its self-check list")
    else:
        fetch_model(a.dry)
    step(5, total, "this machine's own settings")
    first_run(a.dry, not a.no_seed)
    step(6, total, "shortcut and self-check")
    if not a.no_shortcut:
        shortcut(a.dry)
    report(a.dry)

    say("\n" + "-" * 62)
    say(f"Done. Double-click  {LAUNCHER}  (or the Desktop shortcut) to start.")
    say("It opens in your browser at http://127.0.0.1:8760 and is ready to record.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

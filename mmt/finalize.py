"""
Recover a session that was interrupted, and finish any session on demand.

record.py writes recording.lock when it starts and deletes it on a clean stop. So:

    recording.lock present, session.json missing  -> the recorder died (crash, power
        cut, forced reboot). The WAVs are intact because TrackWriter flushes every
        block, but nobody ever wrote the metadata, so transcribe.py in --live mode
        would wait for a stop signal that never comes.

This rebuilds session.json from the WAV length and the lock, then runs the normal
offline chain: transcribe (large-v3) -> build -> report.

Usage:  finalize.py <session folder>        finish one session
        finalize.py --scan                  find and finish every stuck session
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import soundfile as sf

HERE = Path(__file__).parent


def _dur(p: Path) -> float:
    try:
        return float(sf.info(str(p)).duration)
    except Exception:  # noqa: BLE001
        return 0.0


def repair(ses: Path) -> dict | None:
    """Write a session.json for a crashed session. Returns the metadata, or None."""
    if (ses / "session.json").exists():
        return json.loads((ses / "session.json").read_text(encoding="utf-8"))
    lock = ses / "recording.lock"
    info = {}
    if lock.exists():
        try:
            info = json.loads(lock.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            info = {}
    tracks = {}
    for kind, name in (("loopback", "others.wav"), ("mic", "mic.wav")):
        p = ses / name
        if p.exists():
            tracks[kind] = {"file": name, "seconds": round(_dur(p), 2)}
    if not tracks:
        return None
    dur = max((t["seconds"] for t in tracks.values()), default=0.0)
    meta = {
        "session": ses.name,
        "title": info.get("title") or ses.name,
        "started_local": info.get("started_local")
        or datetime.fromtimestamp(ses.stat().st_mtime).isoformat(timespec="seconds"),
        "duration_s": dur, "target_sr": 16000,
        "sensitive": bool(info.get("sensitive")),
        "recovered": True,
        "recovery_note": "session.json was rebuilt by finalize.py because the recorder "
                         "did not stop cleanly. Duration comes from the WAV length. "
                         "Live marks (private ranges, bookmarks) were lost.",
        "marks": [], "suspend_events": [], "endpoint_switches": [], "tracks": tracks,
    }
    (ses / "session.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    lock.unlink(missing_ok=True)
    print(f"  repaired session.json  ({dur:.1f}s of audio recovered)")
    return meta


def finish(ses: Path, model: str) -> int:
    py = sys.executable
    meta = repair(ses)
    if meta is None:
        print(f"  nothing to recover in {ses} (no WAV files)")
        return 1
    for step in (["transcribe.py", str(ses), "--model", model],
                 ["build.py", str(ses)]):
        rc = subprocess.run([py, str(HERE / step[0]), *step[1:]]).returncode
        if rc != 0:
            print(f"  {step[0]} failed with {rc}")
            return rc
    if (ses / "minutes.json").exists():
        subprocess.run([py, str(HERE / "report.py"), str(ses)])
        # a --sensitive session only stops being a liability once the audio is gone
        if meta.get("sensitive"):
            import purge
            purge.purge(ses)
    else:
        print("  no minutes.json yet - transcript is ready, ask your AI assistant to write the minutes")
        if meta.get("sensitive"):
            print("  NOTE: sensitive session - the WAVs are still on disk. They are deleted "
                  "automatically once minutes.json exists, or run purge.py now.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?", default="")
    ap.add_argument("--scan", action="store_true", help="finish every stuck session")
    ap.add_argument("--model", default="large-v3")
    args = ap.parse_args()

    if args.scan:
        root = HERE.parent / "sessions"
        stuck = sorted(p for p in root.glob("*")
                       if (p / "recording.lock").exists() and not (p / "session.json").exists())
        if not stuck:
            print("no stuck sessions")
            return 0
        print(f"{len(stuck)} stuck session(s):")
        rc = 0
        for p in stuck:
            print(f"\n== {p.name}")
            rc |= finish(p, args.model)
        return rc

    if not args.session:
        ap.error("give a session folder or --scan")
    return finish(Path(args.session), args.model)


if __name__ == "__main__":
    raise SystemExit(main())

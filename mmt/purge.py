"""
Delete the audio of a session once the text is safe.

The point of `record.py --sensitive` is that the WAVs are the liability, not the
transcript. A 60-minute meeting is ~110 MB of raw voice sitting in a Desktop folder that
gets backed up, synced and searched. This removes it and leaves the text.

Refuses to run unless the text actually exists, because deleting the audio before the
transcript is written destroys the meeting. Pass --force to override.

Usage:
    purge.py <session>            delete mic.wav / others.wav for one session
    purge.py --scan               every session marked sensitive
    purge.py --scan --older 30    every session (sensitive or not) older than 30 days
    purge.py <session> --keep-mic keep your own track, drop the far end
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

HERE = Path(__file__).parent
WAVS = ("mic.wav", "others.wav")


def _meta(ses: Path) -> dict:
    p = ses / "session.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def purge(ses: Path, force: bool = False, keep_mic: bool = False,
          dry: bool = False) -> int:
    """-> bytes freed. 0 means nothing was done."""
    if (ses / "recording.lock").exists():
        print(f"  {ses.name}: still recording (recording.lock present) - skipped")
        return 0
    has_text = (ses / "transcript.md").exists() or (ses / "transcript.json").exists()
    if not has_text and not force:
        print(f"  {ses.name}: no transcript yet - refusing to delete the audio. "
              f"Run transcribe.py + build.py first, or pass --force.")
        return 0

    targets = [ses / w for w in WAVS if (ses / w).exists()]
    if keep_mic:
        targets = [p for p in targets if p.name != "mic.wav"]
    if not targets:
        print(f"  {ses.name}: no audio left")
        return 0

    freed = sum(p.stat().st_size for p in targets)
    names = ", ".join(p.name for p in targets)
    if dry:
        print(f"  {ses.name}: would delete {names}  ({freed/1e6:.1f} MB)")
        return freed

    for p in targets:
        p.unlink()
    meta = _meta(ses)
    if meta:
        meta["audio_purged"] = {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": [p.name for p in targets],
            "bytes_freed": freed,
            "note": "audio deleted by purge.py; the transcript and minutes are the record now",
        }
        (ses / "session.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # the audio bar in minutes.html has to disappear too, or it renders a broken player
    if (ses / "minutes.html").exists() and (ses / "transcript.json").exists():
        import subprocess
        import sys
        subprocess.run([sys.executable, str(HERE / "report.py"), str(ses)],
                       capture_output=True)
    print(f"  {ses.name}: deleted {names}  ({freed/1e6:.1f} MB freed)")
    return freed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?", default="")
    ap.add_argument("--scan", action="store_true",
                    help="every session marked sensitive")
    ap.add_argument("--older", type=float, default=None, metavar="DAYS",
                    help="with --scan: any session older than this many days, "
                         "sensitive or not")
    ap.add_argument("--keep-mic", action="store_true", help="keep your own track")
    ap.add_argument("--force", action="store_true",
                    help="delete even if no transcript exists (loses the meeting)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.scan:
        root = HERE.parent / "sessions"
        cand = []
        for p in sorted(root.glob("*")):
            if not p.is_dir():
                continue
            if not any((p / w).exists() for w in WAVS):
                continue
            m = _meta(p)
            if m.get("sensitive"):
                cand.append(p)
            elif args.older is not None:
                age_d = (time.time() - p.stat().st_mtime) / 86400
                if age_d > args.older:
                    cand.append(p)
        if not cand:
            print("nothing to purge")
            return 0
        print(f"{len(cand)} session(s) to purge:")
        total = sum(purge(p, args.force, args.keep_mic, args.dry_run) for p in cand)
        print(f"total {total/1e6:.1f} MB" + (" (dry run)" if args.dry_run else " freed"))
        return 0

    if not args.session:
        ap.error("give a session folder, or --scan")
    purge(Path(args.session), args.force, args.keep_mic, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

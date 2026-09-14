"""
Delete the audio of a session once the text is safe.

The point of `record.py --sensitive` is that the WAVs are the liability, not the
transcript. A 60-minute meeting is ~110 MB of raw voice sitting in a Desktop folder that
gets backed up, synced and searched. This removes it and leaves the text.

Refuses to run unless the text actually exists, because deleting the audio before the
transcript is written destroys the meeting. Pass --force to override.

The screen recording counts as audio here. screen.mp4 is the single biggest file a
session produces (~340 MB/hour) and it is a picture of whatever was on the screen -
dashboards, inboxes, numbers - so leaving it behind on a "sensitive" session would
defeat the whole point. The extracted key frames in frames/ are the readable record
that survives, the same way the transcript survives the wav; --frames drops those too.

Usage:
    purge.py <session>            delete mic.wav / others.wav / screen.mp4
    purge.py --scan               every session marked sensitive
    purge.py --scan --older 30    every session (sensitive or not) older than 30 days
    purge.py <session> --keep-mic   keep your own track, drop the far end
    purge.py <session> --keep-video keep screen.mp4
    purge.py <session> --frames     also delete frames/ (the key frames and sheets)
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

HERE = Path(__file__).parent
WAVS = ("mic.wav", "others.wav")
MEDIA = WAVS + ("screen.mp4",)


def _meta(ses: Path) -> dict:
    p = ses / "session.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def purge(ses: Path, force: bool = False, keep_mic: bool = False,
          dry: bool = False, keep_video: bool = False, frames: bool = False) -> int:
    """-> bytes freed. 0 means nothing was done."""
    if (ses / "recording.lock").exists():
        print(f"  {ses.name}: still recording (recording.lock present) - skipped")
        return 0
    has_text = (ses / "transcript.md").exists() or (ses / "transcript.json").exists()
    if not has_text and not force:
        print(f"  {ses.name}: no transcript yet - refusing to delete the audio. "
              f"Run transcribe.py + build.py first, or pass --force.")
        return 0

    targets = [ses / w for w in MEDIA if (ses / w).exists()]
    if keep_mic:
        targets = [p for p in targets if p.name != "mic.wav"]
    if keep_video:
        targets = [p for p in targets if p.name != "screen.mp4"]
    if frames:
        targets += sorted((ses / "frames").glob("*.png"))
    if not targets:
        print(f"  {ses.name}: no media left")
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
            "note": "media deleted by purge.py; the transcript, minutes and key frames "
                    "are the record now",
        }
        (ses / "session.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # the audio bar and the <video> in minutes.html have to disappear too, or the page
    # renders two broken players
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
    ap.add_argument("--keep-video", action="store_true", help="keep screen.mp4")
    ap.add_argument("--frames", action="store_true",
                    help="also delete frames/ (key frames and contact sheets)")
    ap.add_argument("--force", action="store_true",
                    help="delete even if no transcript exists (loses the meeting)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.scan:
        # sessions do not have to live next to the code; config decides where they land
        try:
            import config  # noqa: PLC0415
            root = config.staging(config.load())
        except Exception:  # noqa: BLE001
            root = HERE.parent / "sessions"
        if not root.exists():
            root = HERE.parent / "sessions"
        cand = []
        for p in sorted(root.glob("*")):
            if not p.is_dir():
                continue
            if not any((p / w).exists() for w in MEDIA):
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
        total = sum(purge(p, args.force, args.keep_mic, args.dry_run,
                          args.keep_video, args.frames) for p in cand)
        print(f"total {total/1e6:.1f} MB" + (" (dry run)" if args.dry_run else " freed"))
        return 0

    if not args.session:
        ap.error("give a session folder, or --scan")
    purge(Path(args.session), args.force, args.keep_mic, args.dry_run,
          args.keep_video, args.frames)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

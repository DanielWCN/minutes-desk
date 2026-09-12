"""
Make sure a session has ASR output before build.py runs.

Why this exists: build.py reads <track>.segments.json. If nobody ran
transcribe.py, build.py silently produces an EMPTY transcript and the minutes
come out blank. Analysis now always goes through here.

  - segments already complete and covering the whole WAV -> skip (instant)
  - a live transcriber is still running (--wait N) -> wait for it to finish
  - otherwise -> run transcribe.py one-shot, inheriting stdout so the UI log
    shows the progress lines
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRACKS = ("others.wav", "mic.wav")
TAIL_TOLERANCE_S = 60.0      # trailing silence is normal; don't demand exact coverage


def _wav_seconds(wav: Path) -> float:
    try:
        import soundfile as sf
        info = sf.info(str(wav))
        return float(info.frames) / float(info.samplerate or 16000)
    except Exception:                                          # noqa: BLE001
        return 0.0


def track_state(ses: Path, wav_name: str) -> dict:
    wav = ses / wav_name
    seg = ses / f"{wav.stem}.segments.json"
    st = {"track": wav.stem, "wav": wav.exists(), "ok": False, "segments": 0, "reason": ""}
    if not wav.exists():
        st["ok"] = True
        st["reason"] = "no wav"
        return st
    if not seg.exists():
        st["reason"] = "no segments.json"
        return st
    try:
        d = json.loads(seg.read_text(encoding="utf-8"))
    except Exception as exc:                                   # noqa: BLE001
        st["reason"] = f"unreadable segments.json ({type(exc).__name__})"
        return st
    segs = d.get("segments") or []
    st["segments"] = len(segs)
    if not d.get("complete"):
        st["reason"] = "still transcribing"
        return st
    # transcribe.py only writes complete=true after the loop reached the end of the
    # file, so that flag is the authoritative signal. Coverage is NOT: a track can
    # be legitimately silent for its whole tail (e.g. the mic of someone who stopped
    # talking), and demanding coverage made us re-transcribe good sessions.
    dur = _wav_seconds(wav)
    last = max((float(s.get("end") or 0) for s in segs), default=0.0)
    st["ok"] = True
    st["reason"] = "up to date"
    if dur and last < dur - TAIL_TOLERANCE_S:
        st["reason"] = f"up to date (speech ends {last:.0f}s of {dur:.0f}s, tail silent)"
    return st


def missing(ses: Path) -> list[dict]:
    return [s for s in (track_state(ses, t) for t in TRACKS) if not s["ok"]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--wait", type=float, default=0.0,
                    help="seconds to wait for a live transcriber already running")
    ap.add_argument("--force", action="store_true", help="transcribe again from scratch")
    args = ap.parse_args()

    ses = Path(args.session)
    if not ses.is_dir():
        print(f"no such session: {ses}")
        return 2

    if not args.force:
        bad = missing(ses)
        if not bad:
            for t in TRACKS:
                st = track_state(ses, t)
                if st["wav"]:
                    print(f"  语音识别已完成 {st['track']}: {st['segments']} 段")
            print("跳过转写（已有结果）")
            return 0
        if args.wait > 0:
            print(f"录音期间的实时转写还没收尾，等它完成（最多 {int(args.wait/60)} 分钟）…")
            t0 = time.time()
            while time.time() - t0 < args.wait:
                time.sleep(5.0)
                bad = missing(ses)
                if not bad:
                    print(f"实时转写完成，用了 {time.time()-t0:.0f}s")
                    return 0
            print("等太久了，改成自己重新转写")
        for st in bad:
            print(f"  需要转写 {st['track']}: {st['reason']}")

    argv = [sys.executable, "-u", str(HERE / "transcribe.py"), str(ses), "--model", args.model]
    print("开始语音识别（1 分钟音频约 10-20 秒，请等）")
    print("$ " + " ".join(argv))
    t0 = time.time()
    code = subprocess.call(argv)
    print(f"语音识别结束，用了 {time.time()-t0:.0f}s，exit={code}")
    if code != 0:
        return code
    bad = missing(ses)
    if bad:
        for st in bad:
            print(f"!! {st['track']} 还是不行: {st['reason']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

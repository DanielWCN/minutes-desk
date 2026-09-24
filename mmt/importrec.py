"""
Turn a recording somebody else made into a session this tool can work on.

The live recorder captures two audio tracks and the screen, and the tracks are why speaker
separation works. A file out of Zoom, Teams or OBS has one mixed track, so that is what it
gets: `others.wav`, one voice channel, everything in it. Said out loud in the UI, because a
manual built from a two-person mixed recording will attribute steps to nobody in
particular, and for a one-person walkthrough - which is what gets imported - that costs
nothing at all.

What this does, and nothing more:
    others.wav      16 kHz mono, decoded and resampled with PyAV, which is already here
                    because the recorder writes screen.mp4 with it. No ffmpeg.exe anywhere.
    screen.mp4      the pictures, REMUXED not re-encoded: same H.264 packets, mp4 container,
                    so a 40-minute walkthrough costs seconds and loses no quality. A codec
                    an mp4 cannot hold is left behind rather than transcoded, and the
                    manual then has no pictures and says so.
    session.json    shaped like the recorder's, plus origin:"import". That flag is the only
                    thing downstream reads to tell the two apart - never the file name,
                    which a person is free to change.

Then, unless told not to, it runs the normal pipeline on the session it just made, so one
click ends with something readable rather than a folder.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import wave
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import config                            # noqa: E402

TARGET_SR = 16000                        # what transcribe.py wants; the same as the recorder
# Codecs an mp4 container can legally carry. Anything else (VP8/VP9 out of a webm, for
# instance) would need a real re-encode, which is minutes of CPU and a quality decision
# nobody asked for, so it is refused out loud instead.
MP4_OK = {"h264", "hevc", "av1", "mpeg4", "vp9"}
MEDIA = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".wmv", ".flv",
         ".m4a", ".mp3", ".wav", ".aac", ".ogg", ".opus", ".wma"}


def slug(title: str) -> str:
    s = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()
    return re.sub(r"[\s_]+", "-", s).strip("-")


def audio_out(src: Path, out: Path) -> dict:
    """Decode whatever is in there to 16 kHz mono PCM."""
    import av                            # noqa: PLC0415
    n = 0
    with av.open(str(src)) as c:
        st = next((s for s in c.streams if s.type == "audio"), None)
        if st is None:
            return {"error": "这个文件里没有声音轨，没有声音就没有手册"}
        rs = av.AudioResampler(format="s16", layout="mono", rate=TARGET_SR)
        w = wave.open(str(out), "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TARGET_SR)
        try:
            for frame in c.decode(st):
                for o in rs.resample(frame):
                    b = o.to_ndarray().tobytes()
                    w.writeframes(b)
                    n += len(b) // 2
            for o in rs.resample(None):   # the resampler holds a tail; without this the
                b = o.to_ndarray().tobytes()   # last fraction of a second is missing
                w.writeframes(b)
                n += len(b) // 2
        finally:
            w.close()
    return {"samples": n, "seconds": round(n / TARGET_SR, 2)}


def video_out(src: Path, out: Path) -> dict:
    """Same packets, mp4 container. Never a re-encode."""
    import av                            # noqa: PLC0415
    try:
        with av.open(str(src)) as i:
            vs = next((s for s in i.streams if s.type == "video"), None)
            if vs is None:
                return {"skipped": "no video stream"}
            name = (vs.codec_context.name or "").lower()
            if name not in MP4_OK:
                return {"skipped": f"codec {name} cannot go in an mp4 without re-encoding"}
            n = 0
            with av.open(str(out), "w") as o:
                ostream = o.add_stream_from_template(vs)
                for pk in i.demux(vs):
                    if pk.dts is None:    # the flush packet at the end of a demux
                        continue
                    pk.stream = ostream
                    o.mux(pk)
                    n += 1
            return {"frames": n, "codec": name,
                    "size": f"{vs.codec_context.width}x{vs.codec_context.height}",
                    "bytes": out.stat().st_size if out.exists() else 0,
                    "started_at_s": 0.0}
    except Exception as exc:              # noqa: BLE001
        out.unlink(missing_ok=True)
        return {"skipped": str(exc)[:200]}


def probe(src: Path) -> dict:
    import av                            # noqa: PLC0415
    with av.open(str(src)) as c:
        dur = float(c.duration or 0) / 1e6
        return {"duration_s": round(dur, 2),
                "has_video": any(s.type == "video" for s in c.streams),
                "has_audio": any(s.type == "audio" for s in c.streams)}


def do_import(src: Path, title: str, cfg: dict, root: Path | None = None) -> dict:
    if not src.is_file():
        return {"error": f"找不到文件：{src}"}
    if src.suffix.lower() not in MEDIA:
        return {"error": f"不认识 {src.suffix} 这种文件，要的是录屏或录音"}
    root = root or config.staging(cfg)
    # The recording's own date, not today's: a walkthrough imported a week later belongs
    # where it happened in the list, and mtime is the only date the file actually carries.
    when = datetime.fromtimestamp(src.stat().st_mtime)
    ttl = title.strip() or src.stem
    sl = slug(ttl)
    stamp = when.strftime("%Y-%m-%d_%H%M")
    ses = root / (f"{stamp}_{sl}" if sl else stamp)
    i = 2
    while ses.exists():
        ses = root / f"{stamp}_{sl or 'import'}-{i}"
        i += 1
    ses.mkdir(parents=True, exist_ok=True)
    print(f"session : {ses}", flush=True)
    print(f"source  : {src}", flush=True)

    info = probe(src)
    a = audio_out(src, ses / "others.wav")
    if a.get("error"):
        return {"error": a["error"], "session": ses.name}
    print(f"audio   : {a['seconds']:.1f}s -> others.wav", flush=True)
    v = video_out(src, ses / "screen.mp4") if info.get("has_video") else {"skipped": "audio only"}
    if v.get("skipped"):
        print(f"video   : none ({v['skipped']}) - the manual will have no pictures",
              flush=True)
    else:
        print(f"video   : {v['frames']} packets  {v['size']}  "
              f"{v['bytes']/1e6:.1f} MB -> screen.mp4", flush=True)

    meta = {
        "session": ses.name, "title": ttl, "others": "",
        "started_local": when.isoformat(timespec="seconds"),
        "duration_s": a["seconds"] or info.get("duration_s") or 0,
        "target_sr": TARGET_SR, "sensitive": False,
        # the whole point of this key: where the material came from, so the archive can put
        # walkthroughs somewhere other than meetings without guessing from a file name
        "origin": "import",
        "source_file": src.name, "source_path": str(src),
        "imported_at": datetime.now().isoformat(timespec="seconds"),
        "video": (None if v.get("skipped") else
                  {"file": "screen.mp4", **{k: v[k] for k in
                   ("frames", "codec", "size", "bytes", "started_at_s") if k in v},
                   "error": None}),
        "tracks": {"others": {"file": "others.wav", "seconds": a["seconds"],
                              "device_events": 0, "error": None, "warn": None,
                              "mixed": True}},
    }
    (ses / "session.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    return {"ok": True, "session": ses.name, "path": str(ses),
            "seconds": a["seconds"], "video": not v.get("skipped"),
            "video_skipped": v.get("skipped") or ""}


def chain(ses: Path, sop: bool) -> int:
    """Transcribe, build, and - unless the engine cannot be driven - write the manual. The
    manual step is soft: a machine that cannot reach an assistant still ends up with a
    transcript and the pictures, and the button in the app finishes the job."""
    py = sys.executable
    steps: list[list[str]] = [
        [py, "-u", str(HERE / "transcribe.py"), str(ses)],
        [py, "-u", str(HERE / "build.py"), str(ses)],
    ]
    if sop:
        steps.append(["?", py, "-u", str(HERE / "sop.py"), str(ses), "--draft"])
    return subprocess.call([py, "-u", str(HERE / "_chain.py"),
                            json.dumps(steps, ensure_ascii=False)])


def main() -> int:
    ap = argparse.ArgumentParser(description="import a recording someone else made")
    ap.add_argument("file")
    ap.add_argument("--title", default="")
    ap.add_argument("--no-chain", action="store_true",
                    help="create the session, run nothing")
    ap.add_argument("--no-sop", action="store_true",
                    help="transcribe, but do not write the manual")
    a = ap.parse_args()
    cfg = config.load()
    r = do_import(Path(a.file).expanduser(), a.title, cfg)
    print(json.dumps(r, ensure_ascii=False, indent=1), flush=True)
    if r.get("error"):
        return 1
    # A sentinel the app reads out of the job log to know which session was just created.
    # The name cannot be known before this runs - it comes from the recording's own date -
    # so it has to travel back somehow, and one stable line is the cheapest way.
    print(f"== session {r['session']}", flush=True)
    if a.no_chain:
        return 0
    return chain(Path(r["path"]), not a.no_sop)


if __name__ == "__main__":
    raise SystemExit(main())

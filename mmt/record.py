"""
Meeting Minutes Tool - dual-track recorder (v2).

Track A  mic.wav     : your microphone            -> always "you", no diarization needed
Track B  others.wav  : WASAPI loopback of the system output -> everyone else

Why loopback: it is a Windows mixer-level tap. Zoom / Teams / Chime / Meet / a browser
never see a recording event, no host permission is needed, and no participant is told
anything. Works identically whatever meeting app you use.

Robustness:
  * follows the default output device if it changes mid-meeting (headphones plugged in)
  * SILENCE WATCHDOG: if the loopback track stays silent, it probes the other render
    endpoints and switches to whichever one actually has audio. This covers the real
    trap that Zoom has its own speaker setting, which may not be the Windows default.
  * vetoes idle sleep for the whole recording, and detects afterwards if the machine
    was suspended anyway (closing the lid cannot be vetoed from user space)
  * recording.lock survives a crash, so finalize.py can recover an interrupted session

Live keys while recording:
  Enter / q  stop
  p          toggle PRIVATE. Your mic keeps recording (we cannot know if you muted in
             Zoom) but everything inside a private range is cut from the transcript.
             Use it when you turn away to talk to someone in the room.
  m          drop a bookmark at this second, so "go back to what I marked" works later
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import sys
import threading
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import soundcard as sc
import soundfile as sf

sys.path.insert(0, str(Path(__file__).parent))
import power  # noqa: E402

try:
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows
    msvcrt = None

warnings.filterwarnings("ignore", module="soundcard")

TARGET_SR = 16000
BLOCK = 2048
DEVICE_POLL_S = 4.0
SILENCE_BEFORE_PROBE_S = 45.0   # loopback quiet this long -> go hunting for the real endpoint
PROBE_S = 1.6                   # how long to listen to each candidate endpoint
SIGNAL_DBFS = -55.0             # above this counts as "there is audio here"


def _now() -> float:
    return time.monotonic()


def _mono(block: np.ndarray) -> np.ndarray:
    a = np.asarray(block, dtype=np.float32)
    return a.mean(axis=1) if a.ndim == 2 else a


def _dbfs(x: np.ndarray) -> float:
    if x.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return 20.0 * np.log10(max(rms, 1e-12))


def probe_endpoints(exclude: str | None = None) -> list[tuple[str, float]]:
    """Listen briefly to every render endpoint. Returns [(name, peak dBFS)] loudest first."""
    out = []
    for spk in sc.all_speakers():
        if exclude and spk.name == exclude:
            continue
        try:
            mic = sc.get_microphone(spk.name, include_loopback=True)
            buf = []
            with mic.recorder(samplerate=TARGET_SR, channels=1, blocksize=1024) as r:
                end = _now() + PROBE_S
                while _now() < end:
                    buf.append(_mono(r.record(numframes=1024)))
            x = np.concatenate(buf) if buf else np.zeros(1, np.float32)
            peak = 20 * np.log10(max(float(np.max(np.abs(x))), 1e-12))
            out.append((spk.name, peak))
        except Exception:  # noqa: BLE001
            out.append((spk.name, -240.0))
    out.sort(key=lambda t: -t[1])
    return out


class TrackWriter(threading.Thread):
    def __init__(self, path: Path, q: "queue.Queue"):
        super().__init__(daemon=True, name=f"w:{path.name}")
        self.path, self.q, self.frames = path, q, 0

    def run(self) -> None:
        with sf.SoundFile(self.path, mode="w", samplerate=TARGET_SR, channels=1,
                          subtype="PCM_16") as f:
            while True:
                b = self.q.get()
                if b is None:
                    break
                f.write(b)
                self.frames += len(b)
                f.flush()


class Capture(threading.Thread):
    def __init__(self, kind: str, q: "queue.Queue", stop: threading.Event, state: dict,
                 pinned: str | None = None):
        super().__init__(daemon=True, name=f"c:{kind}")
        self.kind, self.q, self.stop, self.state = kind, q, stop, state
        self.pinned = pinned
        self.t0: float | None = None
        self.written = 0
        self.events: list[dict] = []
        self.error: str | None = None
        self.want: str | None = pinned          # endpoint the watchdog wants us on
        self.current: str | None = None
        self.last_loud = _now()

    def _resolve(self):
        if self.kind == "mic":
            d = sc.default_microphone()
            return d, d.name
        name = self.want or sc.default_speaker().name
        return sc.get_microphone(name, include_loopback=True), name

    def _pad(self, target: int) -> None:
        gap = target - self.written
        if gap > 0:
            self.q.put(np.zeros(gap, dtype=np.float32))
            self.written += gap

    def elapsed(self) -> float:
        return 0.0 if self.t0 is None else _now() - self.t0

    def run(self) -> None:
        try:
            self._loop()
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.q.put(None)

    def _loop(self) -> None:
        last_poll = 0.0
        while not self.stop.is_set():
            try:
                dev, label = self._resolve()
            except Exception as exc:  # noqa: BLE001
                self.state[f"{self.kind}_device"] = f"<unavailable: {exc}>"
                time.sleep(1.0)
                continue
            self.current = label
            self.state[f"{self.kind}_device"] = label
            self.events.append({"t": round(self.elapsed(), 2), "device": label})
            try:
                with dev.recorder(samplerate=TARGET_SR, channels=1, blocksize=BLOCK) as rec:
                    if self.t0 is None:
                        self.t0 = _now()
                    while not self.stop.is_set():
                        blk = _mono(rec.record(numframes=BLOCK))
                        self.q.put(blk)
                        self.written += len(blk)
                        db = _dbfs(blk)
                        self.state[f"{self.kind}_db"] = db
                        self.state[f"{self.kind}_frames"] = self.written
                        if db > SIGNAL_DBFS:
                            self.last_loud = _now()
                        if _now() - last_poll > DEVICE_POLL_S:
                            last_poll = _now()
                            if self.kind == "loopback":
                                if self.want and self.want != label:
                                    break
                                if not self.pinned and not self.want:
                                    try:
                                        if sc.default_speaker().name != label:
                                            break
                                    except Exception:  # noqa: BLE001
                                        pass
            except Exception as exc:  # noqa: BLE001
                self.state[f"{self.kind}_warn"] = f"{type(exc).__name__}: {exc}"
                time.sleep(0.4)
            if self.t0 is not None:
                self._pad(int((_now() - self.t0) * TARGET_SR))


class ScreenGrab(threading.Thread):
    """Record the whole desktop to H.264, using the PyAV that faster-whisper already pulls in.

    The desktop, not a single window: capturing one window via PrintWindow returns black frames
    for anything GPU-composited, which is exactly a Zoom video canvas. The desktop is the
    already-composed image, so whatever is on screen is what lands in the file. During a demo
    the screen is visible anyway, so that costs nothing.

    Measured at these defaults (3 fps, crf 32, 1280 wide): 226 MB/hour from a 1707x960
    display, 343 MB/hour from a 1920x1200 one. Same order as the two audio tracks we already
    keep (232 MB/hour). The cursor is drawn on purpose - a demo without the pointer is
    unreadable.

    Timestamps come from the wall clock, not from a frame counter. gdigrab does not promise
    exactly `fps` frames a second, so numbering frames 0,1,2... drifts away from the audio;
    and leaving pts as None is worse still - libav then writes 0 into every single frame, the
    container reports a 0.3 second video, and every key frame frames.py extracts is stamped
    00:00:00, which is the one thing the pictures are for.

    A private range stops the capture too. Muting the transcript while still filming the
    screen would be a promise the tool does not keep.
    """

    def __init__(self, path: Path, stop: threading.Event, state: dict,
                 fps: int = 3, crf: int = 32, width: int = 1280,
                 private: dict | None = None):
        super().__init__(daemon=True)
        self.path, self.stop, self.state = path, stop, state
        self.fps, self.crf, self.width = fps, crf, width
        self.private = private
        self.frames, self.dropped, self.error = 0, 0, None

    def run(self) -> None:
        try:
            import av
            from fractions import Fraction
        except Exception as exc:                               # noqa: BLE001
            self.error = f"PyAV unavailable: {exc}"
            self.state["video_error"] = self.error
            return
        ic = oc = None
        try:
            ic = av.open("desktop", format="gdigrab",
                         options={"framerate": str(self.fps), "draw_mouse": "1"})
            ist = ic.streams.video[0]
            w0, h0 = ist.codec_context.width, ist.codec_context.height
            w = min(self.width, w0) // 2 * 2
            h = int(round(h0 * w / w0)) // 2 * 2
            oc = av.open(str(self.path), "w")
            os_ = oc.add_stream("libx264", rate=self.fps)
            os_.width, os_.height, os_.pix_fmt = w, h, "yuv420p"
            tb = Fraction(1, 1000)                             # milliseconds, see the docstring
            os_.time_base = tb
            os_.options = {"crf": str(self.crf), "preset": "veryfast", "tune": "stillimage"}
            self.state["video_size"] = f"{w}x{h}"
            t0 = None
            last_pts = -1
            for frame in ic.decode(ist):
                if self.stop.is_set():
                    break
                if t0 is None:
                    t0 = _now()                                # anchor: frame 1 of the video
                if self.private is not None and self.private.get("on"):
                    self.dropped += 1                          # filmed nothing; the gap shows
                    self.state["video_dropped"] = self.dropped
                    continue
                f = frame.reformat(width=w, height=h, format="yuv420p")
                pts = max(last_pts + 1, int((_now() - t0) * 1000))
                last_pts = pts
                f.pts, f.time_base = pts, tb
                for pkt in os_.encode(f):
                    oc.mux(pkt)
                self.frames += 1
                self.state["video_frames"] = self.frames
                if self.frames % self.fps == 0 and self.path.exists():
                    self.state["video_bytes"] = self.path.stat().st_size
            for pkt in os_.encode():
                oc.mux(pkt)
        except Exception as exc:                               # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            self.state["video_error"] = self.error
        finally:
            for h_ in (oc, ic):
                try:
                    if h_ is not None:
                        h_.close()
                except Exception:                              # noqa: BLE001
                    pass
            if self.path.exists():
                self.state["video_bytes"] = self.path.stat().st_size


class Watchdog(threading.Thread):
    """If the loopback track is silent for too long, find the endpoint that isn't."""

    def __init__(self, cap: Capture, stop: threading.Event, state: dict):
        super().__init__(daemon=True, name="watchdog")
        self.cap, self.stop, self.state = cap, stop, state
        self.switches: list[dict] = []

    def run(self) -> None:
        if self.cap.pinned:
            return
        while not self.stop.wait(5.0):
            if self.cap.t0 is None or self.cap.elapsed() < SILENCE_BEFORE_PROBE_S:
                continue
            if _now() - self.cap.last_loud < SILENCE_BEFORE_PROBE_S:
                continue
            found = probe_endpoints(exclude=self.cap.current)
            self.state["last_probe"] = found
            if found and found[0][1] > SIGNAL_DBFS:
                name, peak = found[0]
                self.switches.append({"t": round(self.cap.elapsed(), 1),
                                      "from": self.cap.current, "to": name,
                                      "peak_dbfs": round(peak, 1)})
                self.cap.want = name
                self.cap.last_loud = _now()


def _hms(t: float) -> str:
    return f"{int(t//60):02d}:{int(t%60):02d}"


def _meter(db: float, w: int = 20) -> str:
    n = int(max(0.0, min(1.0, (db + 60.0) / 60.0)) * w)
    return "#" * n + "." * (w - n)


def main() -> int:
    ap = argparse.ArgumentParser(description="Dual-track meeting recorder")
    ap.add_argument("--title", default="")
    ap.add_argument("--others", default="", help="who else was in the room, comma separated")
    ap.add_argument("--out", default="")
    ap.add_argument("--seconds", type=float, default=0.0, help="0 = record until Enter")
    ap.add_argument("--no-mic", action="store_true")
    ap.add_argument("--loopback-device", default="", help="pin an output endpoint by name")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--video", action="store_true",
                    help="also record the desktop to screen.mp4, for demos you want an SOP from")
    ap.add_argument("--video-fps", type=int, default=3)
    ap.add_argument("--video-crf", type=int, default=32)
    ap.add_argument("--video-width", type=int, default=1280)
    ap.add_argument("--control", default="",
                    help="poll this file for stop/mark/private commands, and write status.json "
                         "beside it, so a UI can drive the recording without a console")
    ap.add_argument("--sensitive", action="store_true",
                    help="mark the session sensitive: audio is deleted once the minutes "
                         "are written, and minutes.html ships without the audio player")
    args = ap.parse_args()

    if args.list_devices:
        print("render endpoints (probing 1.6s each for signal):")
        for n, p in probe_endpoints():
            print(f"  {p:8.1f} dBFS  {n}{'   <== has audio' if p > SIGNAL_DBFS else ''}")
        print(f"\ndefault output: {sc.default_speaker().name}")
        print(f"default mic   : {sc.default_microphone().name}")
        return 0

    root = Path(args.out) if args.out else Path(__file__).resolve().parent.parent / "sessions"
    slug = "".join(c if c.isalnum() or c in " -_" else "_" for c in args.title).strip()
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")            # folder names travel; keep them plain
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    session = root / (f"{stamp}_{slug}" if slug else stamp)
    session.mkdir(parents=True, exist_ok=True)

    print(f"session : {session}")
    if args.sensitive:
        print("mode    : SENSITIVE - audio is deleted after the minutes are written")
    lid = power.lid_action()
    if lid == "risky":
        print("\n!! WARNING: closing the lid will suspend this machine and cut the recording.")
        print("   Windows gives no way to veto that. Either keep the lid open, or run once")
        print("   in an ADMIN prompt:\n   " + power.LID_FIX + "\n")
    elif lid == "hidden":
        print("note    : the lid policy is hidden by the OEM, so it cannot be checked.")
        print("          Idle sleep IS blocked. Keep the lid open to be safe.")
    try:
        print(f"mic     : {sc.default_microphone().name}")
        print(f"loopback: {args.loopback_device or sc.default_speaker().name}"
              f"{' (pinned)' if args.loopback_device else ' (default output, auto-follow on)'}")
    except Exception as exc:  # noqa: BLE001
        print(f"!! device probe failed: {exc}")
        return 2

    lock = session / "recording.lock"
    lock.write_text(json.dumps(
        {"title": args.title, "started_local": datetime.now().isoformat(timespec="seconds"),
         "pid": os.getpid(), "sensitive": bool(args.sensitive)},
        ensure_ascii=False, indent=1), encoding="utf-8")

    stop, state = threading.Event(), {}
    marks: list[dict] = []
    private = {"on": False, "since": 0.0}
    tracks, wd = [], None
    specs = [("loopback", session / "others.wav")]
    if not args.no_mic:
        specs.append(("mic", session / "mic.wav"))

    awake = power.KeepAwake().__enter__()
    print(f"sleep   : idle sleep blocked ({awake.mode})" if awake.ok
          else "sleep   : !! could not block idle sleep")

    for kind, path in specs:
        q: queue.Queue = queue.Queue(maxsize=1024)
        w = TrackWriter(path, q)
        c = Capture(kind, q, stop, state, args.loopback_device or None if kind == "loopback" else None)
        w.start(); c.start()
        if kind == "loopback":
            wd = Watchdog(c, stop, state); wd.start()
        tracks.append((kind, path, c, w))

    # A dict, not a plain name: screen recording can also be switched on in the middle
    # of the meeting ("video" on the control channel / v on the keyboard), because people
    # only realise they need the screen once someone starts sharing it.
    grab_ref: dict = {"g": None, "start_s": 0.0}

    wall_t0 = datetime.now()
    started = _now()
    susp = power.SuspendWatch()

    def elapsed() -> float:
        return _now() - started

    def start_video() -> None:
        if grab_ref["g"] is not None:
            print("\n  video already recording\n")
            return
        g = ScreenGrab(session / "screen.mp4", stop, state,
                       args.video_fps, args.video_crf, args.video_width, private)
        g.start()
        grab_ref.update(g=g, start_s=round(elapsed(), 2))
        state["video_on"] = True
        print(f"\n  [{_hms(elapsed())}] video on: screen.mp4 @ {args.video_fps} fps, "
              f"crf {args.video_crf}, {args.video_width}px wide\n")

    def toggle_private() -> None:
        if private["on"]:
            marks.append({"kind": "private_end", "t": round(elapsed(), 2)})
            private["on"] = False
            print(f"\n  [{_hms(elapsed())}] private OFF - back in the transcript\n")
        else:
            private["on"] = True
            private["since"] = elapsed()
            marks.append({"kind": "private_start", "t": round(elapsed(), 2)})
            print(f"\n  [{_hms(elapsed())}] PRIVATE ON - cut from the transcript"
                  f"{', screen capture paused' if grab_ref['g'] is not None else ''}\n")

    def keys() -> None:
        while not stop.is_set():
            if msvcrt is None:
                sys.stdin.readline(); stop.set(); return
            if not msvcrt.kbhit():
                time.sleep(0.08); continue
            try:
                ch = msvcrt.getwch()
            except Exception:  # noqa: BLE001
                continue
            if ch in ("\r", "\n", "q", "Q"):
                stop.set(); return
            if ch in ("p", "P"):
                toggle_private()
            elif ch in ("v", "V"):
                start_video()
            elif ch in ("m", "M"):
                marks.append({"kind": "bookmark", "t": round(elapsed(), 2)})
                print(f"\n  [{_hms(elapsed())}] bookmark #{sum(1 for x in marks if x['kind']=='bookmark')}\n")

    ctl = Path(args.control) if args.control else None
    status_path = (ctl.parent / "status.json") if ctl else None

    def drain_control() -> None:
        """One command per line; the file is truncated once read, so nothing replays."""
        if ctl is None or not ctl.exists():
            return
        try:
            cmds = ctl.read_text(encoding="utf-8").split("\n")
            ctl.write_text("", encoding="utf-8")
        except Exception:                                      # noqa: BLE001
            return
        for c in (x.strip().lower() for x in cmds):
            if c in ("stop", "q"):
                stop.set()
            elif c == "mark":
                marks.append({"kind": "bookmark", "t": round(elapsed(), 2)})
            elif c == "private":
                toggle_private()
            elif c == "video":
                start_video()

    def write_status() -> None:
        if status_path is None:
            return
        try:
            status_path.write_text(json.dumps({
                "session": session.name, "elapsed_s": round(elapsed(), 1),
                "private": private["on"],
                "bookmarks": sum(1 for m in marks if m["kind"] == "bookmark"),
                "tracks": {k: {"db": round(state.get(f"{k}_db", -120.0), 1),
                               "seconds": round(state.get(f"{k}_frames", 0) / TARGET_SR, 1),
                               "device": (c.events[-1]["device"] if c.events else "")}
                           for k, _p, c, _w in tracks},
                "video": ({"frames": state.get("video_frames", 0),
                           "bytes": state.get("video_bytes", 0),
                           "size": state.get("video_size", ""),
                           "error": state.get("video_error")} if grab_ref["g"] else None),
                "suspends": len(susp.events),
                "endpoint_switches": (wd.switches if wd else []),
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:                                      # noqa: BLE001
            import traceback
            (status_path.parent / "status.err").write_text(
                traceback.format_exc(), encoding="utf-8")

    if args.video:
        start_video()

    print("\nrecording.  Enter/q = stop   p = private on/off   m = bookmark   v = screen on\n")
    if args.seconds <= 0:
        threading.Thread(target=keys, daemon=True).start()

    try:
        while not stop.is_set():
            time.sleep(0.5)
            drain_control()
            write_status()
            ev = susp.tick(elapsed())
            if ev:
                print(f"\n  !! the machine was suspended at {_hms(ev['at_s'])} and lost "
                      f"{ev['lost_s']:.0f}s of audio. Recording continues.\n")
            if args.seconds > 0 and elapsed() >= args.seconds:
                stop.set(); break
            row = []
            for kind, _p, _c, _w in tracks:
                db = state.get(f"{kind}_db", -120.0)
                row.append(f"{kind:8s}[{_meter(db)}]{db:6.1f}dB "
                           f"{state.get(f'{kind}_frames',0)/TARGET_SR:6.1f}s")
            tag = "  PRIVATE" if private["on"] else ""
            sys.stdout.write("\r" + " | ".join(row) + tag + "  ")
            sys.stdout.flush()
    except KeyboardInterrupt:
        stop.set()
    if private["on"]:
        marks.append({"kind": "private_end", "t": round(elapsed(), 2)})

    print("\n\nstopping...")
    if grab_ref["g"] is not None:
        grab_ref["g"].join(timeout=15)
    for _k, _p, c, _w in tracks:
        c.join(timeout=6)
    for _k, _p, _c, w in tracks:
        w.join(timeout=20)

    meta = {
        "session": session.name, "title": args.title, "others": args.others,
        "started_local": wall_t0.isoformat(timespec="seconds"),
        "duration_s": round(_now() - started, 2), "target_sr": TARGET_SR,
        "sensitive": bool(args.sensitive),
        "keep_awake": awake.mode if awake.ok else "FAILED",
        "lid_policy": lid,
        "suspend_events": susp.events,
        "marks": marks,
        "endpoint_switches": (wd.switches if wd else []),
        "last_probe": state.get("last_probe"),
        "video": ({"file": "screen.mp4", "frames": grab_ref["g"].frames,
                   "dropped_private": grab_ref["g"].dropped,
                   "fps": args.video_fps, "crf": args.video_crf,
                   "size": state.get("video_size", ""),
                   "bytes": state.get("video_bytes", 0),
                   "started_at_s": grab_ref["start_s"],
                   "error": grab_ref["g"].error} if grab_ref["g"] is not None else None),
        "tracks": {},
    }
    ok = True
    for kind, path, c, w in tracks:
        info = {"file": path.name, "seconds": round(w.frames / TARGET_SR, 2),
                "device_events": c.events, "error": c.error,
                "warn": state.get(f"{kind}_warn")}
        if path.exists():
            data, _sr = sf.read(path, dtype="float32")
            pk = float(round(20 * np.log10(max(float(np.max(np.abs(data))), 1e-12)), 1)) if data.size else -120.0
            info["peak_dbfs"] = pk
            info["mean_dbfs"] = float(round(_dbfs(data), 1))
            info["silent"] = bool(pk < SIGNAL_DBFS)
            if info["silent"]:
                ok = False
        meta["tracks"][kind] = info

    (session / "session.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    lock.unlink(missing_ok=True)          # clean stop -> nothing for finalize.py to recover
    for kind, info in meta["tracks"].items():
        print(f"  {kind:8s} {info['seconds']:7.1f}s  peak={info.get('peak_dbfs')}dBFS  "
              f"{'SILENT - nothing captured' if info.get('silent') else 'ok'}")
    if meta.get("video"):
        v = meta["video"]
        if v["error"]:
            print(f"  video    !! failed: {v['error']}")
        else:
            print(f"  video    {v['frames']} frames  {v['size']}  {v['bytes']/1e6:.1f} MB"
                  + (f"  ({v['dropped_private']} dropped in private ranges)"
                     if v.get("dropped_private") else ""))
    if meta["endpoint_switches"]:
        print(f"  endpoint switches: {meta['endpoint_switches']}")
    if susp.events:
        print(f"  !! SUSPENDED {len(susp.events)}x, lost "
              f"{sum(e['lost_s'] for e in susp.events):.0f}s of audio: {susp.events}")
    pr = sum(1 for m in marks if m["kind"] == "private_start")
    bm = sum(1 for m in marks if m["kind"] == "bookmark")
    if pr or bm:
        print(f"  marks: {pr} private range(s), {bm} bookmark(s)")
    awake.__exit__()
    if status_path is not None:
        try:
            status_path.write_text(json.dumps(
                {"session": session.name, "done": True,
                 "elapsed_s": meta["duration_s"]}, ensure_ascii=False), encoding="utf-8")
        except Exception:                                      # noqa: BLE001
            pass
    print(f"\n  -> {session / 'session.json'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

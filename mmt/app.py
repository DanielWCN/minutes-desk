"""The local app: a small HTTP server plus one page, so nothing about this tool is a black box.

Why a browser and not a native window: report.py already produces HTML, http.server is in the
standard library, and the alternative (Tauri/Electron) buys a nicer window frame at the cost of
a build chain. Zero new dependencies matters more here, because this whole thing has to install
on a colleague's laptop.

The anti-black-box rule is enforced in the API, not the CSS: every long job streams its real
stdout to the page line by line, and every artifact stays a plain file in the session folder
that the user can open and check by hand. Nothing is computed and thrown away.

Nothing here ever talks to the internet except pip and the model download, both of which the
user starts by clicking a button. Analysis of the recording is a separate, human-initiated step.
"""
from __future__ import annotations

import importlib.util
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import archive
import config
import diarize
import doctor
import llm
import minutes as M
import outlook

HERE = Path(__file__).resolve().parent

# The page is read from disk on every refresh; the server is not. So a window left open
# from yesterday serves new HTML against old Python, and the symptoms look like data
# bugs. Bump this whenever app.py changes shape, and the page will say so out loud.
BUILD = "2026-09-14h"
ROOT = HERE.parent
UI = HERE / "ui.html"
PY = sys.executable

_jobs: dict[str, dict] = {}
_rec: dict = {"proc": None, "session": None, "control": None, "status": None, "asr": None, "asr_session": None}
_doc_cache: dict = {"at": 0.0, "data": None}


# ------------------------------------------------------------------------------------ jobs
class Job:
    """A subprocess whose stdout is kept as lines the UI can render as it goes."""

    def __init__(self, jid: str, argv: list[str], cwd: Path, label: str):
        self.id, self.argv, self.cwd, self.label = jid, argv, cwd, label
        self.lines: list[str] = []
        self.state = "running"
        self.code: int | None = None
        self.started = time.time()
        _jobs[jid] = self.public()
        threading.Thread(target=self._run, daemon=True).start()

    def public(self) -> dict:
        return {"id": self.id, "label": self.label, "state": self.state,
                "code": self.code, "lines": self.lines,
                "elapsed": round(time.time() - self.started, 1)}

    def _run(self) -> None:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        try:
            p = subprocess.Popen(self.argv, cwd=str(self.cwd), env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace", bufsize=1)
            for line in p.stdout:                              # type: ignore[union-attr]
                line = line.rstrip("\n")
                if line.strip():
                    self.lines.append(line)
                    if len(self.lines) > 800:
                        del self.lines[:200]
                _jobs[self.id] = self.public()
            self.code = p.wait()
        except Exception as exc:                               # noqa: BLE001
            self.lines.append(f"!! {type(exc).__name__}: {exc}")
            self.code = -1
        self.state = "done" if self.code == 0 else "failed"
        _jobs[self.id] = self.public()


def start_job(label: str, argv: list[str], cwd: Path | None = None) -> dict:
    jid = f"{int(time.time()*1000)}"
    Job(jid, argv, cwd or HERE, label)
    return _jobs[jid]


# -------------------------------------------------------------------------------- sessions
def list_sessions(cfg: dict) -> list[dict]:
    out = []
    for d in sorted(config.staging(cfg).iterdir(), reverse=True):
        if not d.is_dir():
            continue
        real = archive.resolve(d)
        st = archive.state(d)
        meta: dict = {}
        try:
            meta = json.loads((real / "session.json").read_text(encoding="utf-8"))
        except Exception:                                      # noqa: BLE001
            pass
        files = []
        if real.is_dir():
            for f in sorted(real.iterdir()):
                if f.is_file() and f.name != "recording.lock":
                    files.append({"name": f.name, "mb": round(f.stat().st_size / 1e6, 2)})
        out.append({
            "name": d.name, "state": st, "path": str(real),
            "title": meta.get("title") or "",
            "others": meta.get("others") or "",
            "started": meta.get("started_local") or "",
            "duration_s": meta.get("duration_s") or 0,
            "video": bool(meta.get("video")),
            "has_video": (real / "screen.mp4").exists(),
            "frames": len(list((real / "frames").glob("*.png"))) if (real / "frames").is_dir() else 0,
            "has_minutes": (real / "minutes.html").exists(),
            "has_transcript": (real / "transcript.md").exists(),
            **_asr_state(real),
            "files": files,
            "mb": round(sum(f["mb"] for f in files), 1),
        })
    return out


def _jload(p: Path, default: dict | None = None) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return dict(default or {})


def _count(v) -> int:
    """low_conf_lines is an int in some builds and a list in others."""
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return len(v or [])
    except TypeError:
        return 0


def _wav_secs(p: Path) -> float:
    """16 kHz mono s16 -> 32000 bytes per second. Good enough to spot a silent tail."""
    try:
        return max(0.0, (p.stat().st_size - 44) / 32000.0)
    except OSError:
        return 0.0


def _issues(d: Path, tr: dict, n_people: int) -> list[dict]:
    """
    Everything the machine noticed but cannot decide alone, as a checklist.

    The bar for appearing here is a decision the reader can actually make. A notice
    nobody can act on - "5 segments were dropped as noise", "these words have more
    than one meaning" - is noise itself, and noise is what teaches people to skip
    the whole list. Those two are deliberately not raised any more.
    """
    out: list[dict] = []

    def add(i, level, title, detail="", act="无需处理，仅供备查。"):
        # Every notice states whether it wants an action. A checklist that does not is
        # a pile of facts, and the reader is left guessing what is expected of them.
        out.append({"id": i, "level": level, "title": title, "detail": detail, "act": act})

    # Two tracks, one meeting. A silent tail only matters when *both* sides are silent:
    # the local mic going quiet while the far end keeps talking is what listening sounds
    # like, and flagging it would be flagging a normal meeting.
    tracks: dict[str, dict] = {}
    for t, who in (("others", "对端（会议中其他人）"), ("mic", "本端（本机麦克风）")):
        wav = d / (t + ".wav")
        if not wav.exists():
            continue
        segs = (_jload(d / (t + ".segments.json")).get("segments") or [])
        dur = _wav_secs(wav)
        tracks[t] = {"who": who, "dur": dur, "n": len(segs),
                     "last": max((float(x.get("end") or 0) for x in segs), default=0.0)}
        if not segs:
            add(f"silent_{t}", "warn", f"{who}整轨未识别出语音",
                f"录制 {dur/60:.0f} 分钟，识别结果 0 段。录制设备选择错误，"
                f"或该轨全程静音，均会如此。",
                "需处理：试听该轨音频。有声音则为识别环节的问题；"
                "无声音则为录制设备选择错误，请在「设置」中更换设备后重新录制。")

    live = [v for v in tracks.values() if v["n"]]
    if live:
        dur = max(v["dur"] for v in live)
        last = max(v["last"] for v in live)
        if dur - last > 120:
            add("tail", "warn",
                f"录音末尾 {(dur-last)/60:.0f} 分钟{'两轨均' if len(live) > 1 else ''}无人发言",
                f"最后一次发言在第 {last/60:.0f} 分钟，录音总长 {dur/60:.0f} 分钟。"
                f"会议结束后未停止录制，或该时段确实无人发言，均会如此。",
                "需判断：若该时段本应有内容，请试听音频后重新执行 Analysis；"
                "若属会议结束后未及时停止录制，则无需处理。")

    n = _count(tr.get("low_conf_lines"))
    if n:
        add("low_conf", "warn", f"{n} 行识别置信度偏低",
            "该部分文字未必准确，逐字稿中已逐行标注。",
            "建议核查：若纪要结论依赖这几行，请对照录音复核后再定稿。")
    langs = tr.get("languages") or {}
    if len(langs) > 1:
        add("langs", "info", "识别到 " + str(len(langs)) + " 种语言：" + "、".join(langs),
            "中英混说时个别词会被判为另一种语言，通常不影响纪要。")
    if float(tr.get("private_cut_s") or 0) > 0:
        add("private", "info",
            f"敏感段落已剪除 {float(tr['private_cut_s'])/60:.1f} 分钟"
            f"（{_count(tr.get('private_cut_lines'))} 行）",
            "录制过程中按下过「私密」，该部分不进入逐字稿，也不进入纪要。",
            "无需处理。仅提示纪要将缺失该时段内容。")
    if _count(tr.get("suspend_events")):
        add("suspend", "warn", f"录制期间系统休眠 {_count(tr.get('suspend_events'))} 次",
            "对应时段音频缺失，纪要中该时段不会有内容。",
            "建议核查：若缺失时段恰为做出决定的时段，需在纪要中人工补充。")
    if n_people > 2:
        add("speakers", "info", f"与会 {n_people} 人，对端仅一路音轨",
            "系统声音为一路混合音频，无法区分对端具体发言人，"
            "因此纪要不标注「某某说」，只记录结论与责任人。",
            "无需处理。这是录制方式决定的，并非内容缺失。"
            "第 1 组的勾选即纪要的与会人名单。")
    return out


def _asr_state(real: Path) -> dict:
    """
    The UI used to say "minutes ready" just because minutes.html existed, even when
    the transcript had zero lines because nobody ran speech recognition. Never again.
    """
    out = {"asr": "none", "asr_segments": 0, "lines": 0, "speech_min": 0.0,
           "minutes_written": False, "needs_confirm": 0, "confirmed_at": ""}
    if not real.is_dir():
        return out
    segs = 0
    have = done = 0
    for t in ("others", "mic"):
        if not (real / f"{t}.wav").exists():
            continue
        have += 1
        f = real / f"{t}.segments.json"
        if not f.exists():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                                      # noqa: BLE001
            continue
        segs += len(d.get("segments") or [])
        if d.get("complete"):
            done += 1
    out["asr_segments"] = segs
    out["asr"] = "none" if done == 0 and segs == 0 else ("done" if done >= have and have else "partial")
    tj = real / "transcript.json"
    if tj.exists():
        try:
            d = json.loads(tj.read_text(encoding="utf-8"))
            out["lines"] = len(d.get("lines") or [])
            out["speech_min"] = round(sum(float(v or 0) for v in
                                          (d.get("talk_time_s") or {}).values()) / 60.0, 1)
            out["needs_confirm"] = _count(d.get("low_conf_lines")) \
                + _count(d.get("phonetic_guesses"))
        except Exception:                                      # noqa: BLE001
            pass
    md = real / "minutes.md"
    if md.exists():
        try:                       # the scaffold is full of TBD; a written one has none
            out["minutes_written"] = "TBD" not in md.read_text(encoding="utf-8")
        except Exception:                                      # noqa: BLE001
            pass
    cf = real / "confirm.json"
    if cf.exists():
        try:
            out["confirmed_at"] = str(json.loads(cf.read_text(encoding="utf-8")).get("at") or "")
        except Exception:                                      # noqa: BLE001
            pass
    return out


def _openable(cfg: dict, p: Path) -> bool:
    """True only for a folder that belongs to this tool: the staging root, the archive root,
    or something inside them. Used to keep os.startfile away from arbitrary paths."""
    if not p.is_dir():
        return False
    roots = [config.staging(cfg)]
    for key in ("staging_dir", "archive_dir"):
        v = str(cfg.get(key) or "").strip()
        if v:
            roots.append(Path(v))
    try:
        p = p.resolve()
    except Exception:                                          # noqa: BLE001
        return False
    for r in roots:
        try:
            r = r.resolve()
        except Exception:                                      # noqa: BLE001
            continue
        if p == r or r in p.parents:
            return True
    return False


# ------------------------------------------------------------------------------------- api
def _speaker_steps(cfg: dict, d: Path) -> list:
    """The steps that turn one anonymous "Others" into names, or nothing at all.

    Splitting the far-end track apart costs minutes of CPU and needs two model files, so
    every reason to skip is settled here, while the chain is being built, rather than
    failing inside it. No far-end track (a solo recording) and there is nothing to split.
    No sherpa-onnx or no models and the step cannot run at all. And a diarization already
    newer than the audio is still valid, so a second press of "generate documents" does
    not pay for it twice.

    Re-splitting renumbers the clusters, which makes any existing speakers.json describe
    voices that no longer exist -- so naming always re-runs with it. That is also why
    naming is skipped when speakers.json is already there and the split was not redone:
    it is the one file a person may have corrected by hand.
    """
    out: list = []
    wav = d / "others.wav"
    if not wav.exists():
        return out
    diar = d / "diarization.json"
    try:
        fresh = diar.exists() and diar.stat().st_mtime >= wav.stat().st_mtime
    except OSError:
        fresh = False
    if not fresh:
        if importlib.util.find_spec("sherpa_onnx") is None:
            return out
        if not (diarize.SEG_MODEL.exists() and diarize.EMB_MODEL.exists()):
            return out
        out.append(["?", PY, "-u", str(HERE / "diarize.py"), str(d)])
    if llm.can_auto(cfg) and (not fresh or not (d / "speakers.json").exists()):
        out.append(["?", PY, "-u", str(HERE / "whois.py"), str(d)])
    return out


def _draft_step(cfg: dict, d: Path) -> list:
    """
    The chain step that writes the minutes with a model, or nothing at all.

    Nothing at all in two cases. On the `assistant` engine with no `cli` in assistants.json
    there is no model to call: the person holds the prompt and pastes the answer back, by
    design, and that is still the shipped default. And once minutes.md says
    something a person wrote or approved, a rebuild must not quietly replace it -- pressing
    "generate documents" again is a request to re-render, not to re-write. So the model is
    only let near a file that is still the untouched scaffold, or missing.
    """
    if not llm.can_auto(cfg):
        return []
    md = d / "minutes.md"
    if md.exists():
        try:
            if not M.is_scaffold(md.read_text(encoding="utf-8")):
                return []
        except OSError:
            return []
    return [["?", PY, "-u", str(HERE / "llm.py"), str(d), "--draft", "--no-report"]]


def _translate_step(cfg: dict, d: Path) -> list:
    """The chain step that writes the other language, or nothing at all.

    The decision cannot be made here. These steps are assembled before the chain starts,
    and on a first run minutes.md does not exist yet -- it is written by the step above
    this one. So the step is always added when a model can be called, and translate.py
    decides for itself: no minutes, a blank scaffold, or a translation that is already on
    disk all end in an immediate no-op. Soft, like the draft: a translation that fails
    still leaves a transcript, a set of minutes and a document.
    """
    if not llm.can_auto(cfg):
        return []
    return [["?", PY, "-u", str(HERE / "translate.py"), str(d)]]


class H(BaseHTTPRequestHandler):
    server_version = "MeetingTool"

    def log_message(self, *a) -> None:                         # noqa: D102
        pass

    # -- who is allowed to talk to this server
    def _same_origin(self) -> bool:
        """Binding to 127.0.0.1 keeps other machines out; it does NOT keep other WEB PAGES
        out. Any site open in the browser can post to a localhost port, and the reply being
        unreadable to it does not undo the side effect. So every /api call must look like it
        came from our own page:

          Sec-Fetch-Site   sent by the browser itself and unforgeable by script; cross-site
                           and same-site (a different port) are both refused.
          Origin           if present it must be this exact server.
          Host             blocks DNS rebinding, where a name that resolves to 127.0.0.1
                           lends an attacker page our origin.
          Content-Type     a cross-origin POST cannot set application/json without a
                           preflight, and no CORS headers are ever sent, so the preflight
                           fails. This is the belt to the Sec-Fetch-Site braces, for any
                           client that does not send fetch metadata.
        """
        port = self.server.server_address[1]
        ok_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        host = (self.headers.get("Host") or "").strip().lower()
        if host and host not in ok_hosts:
            return False
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site and site not in ("same-origin", "none"):
            return False
        origin = (self.headers.get("Origin") or "").strip().lower()
        if origin and origin not in {f"http://{h}" for h in ok_hosts}:
            return False
        if self.command == "POST":
            ct = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ct != "application/json":
                return False
        return True

    def _guarded(self, path: str) -> bool:
        if path in ("/", "/index.html", "/i18n.js") or self._same_origin():
            return True
        self._json({"error": "这个请求不像是从本机的工具页面发出的，已拒绝。"
                             " / refused: not a same-origin request"}, 403)
        return False

    # -- helpers
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:                                      # noqa: BLE001
            return {}

    # -- GET
    def do_GET(self) -> None:                                  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = unquote(u.path)
        if not self._guarded(path):
            return

        if path in ("/", "/index.html"):
            self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
        elif path == "/i18n.js":
            self._send(200, (HERE / "i18n.js").read_bytes(),
                       "application/javascript; charset=utf-8")
        elif path == "/api/state":
            cfg = config.load()
            fresh = float(q.get("doctor", ["0"])[0]) == 1 or _doc_cache["data"] is None
            if fresh:
                _doc_cache.update(at=time.time(), data=doctor.run(cfg))
            self._json({"build": BUILD,
                        "config": cfg, "doctor": _doc_cache["data"],
                        "sessions": list_sessions(cfg),
                        "recording": self._rec_status(),
                        "jobs": list(_jobs.values())[-6:],
                        "onedrive": config.suggested_archive()})
        elif path == "/doc/sop":
            p = HERE / "profiles" / "sop.md"
            self._send(200, p.read_bytes() if p.exists() else b"missing",
                       "text/plain; charset=utf-8")
        elif path == "/api/confirm":
            self._json(self._confirm_read(config.load(), str(q.get("session", [""])[0])))
        elif path == "/api/outlook":
            self._json(outlook.day(config.load(), str(q.get("day", [""])[0])))
        elif path == "/api/invite":
            self._json(self._invite(config.load(), str(q.get("session", [""])[0])))
        elif path == "/api/engine":
            self._json(self._engine(config.load()))
        elif path == "/api/prompt":
            self._json(self._prompt(config.load(), str(q.get("session", [""])[0]),
                                   str(q.get("file", ["minutes.md"])[0])))
        elif path == "/api/record/status":
            self._json(self._rec_status())
        elif path.startswith("/api/job/"):
            self._json(_jobs.get(path.rsplit("/", 1)[-1], {"state": "unknown"}))
        elif path.startswith("/files/"):
            self._serve_file(path[len("/files/"):])
        else:
            self._json({"error": "not found"}, 404)

    def _serve_file(self, rel: str) -> None:
        cfg = config.load()
        parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
        if len(parts) < 2:
            self._json({"error": "bad path"}, 400)
            return
        base = archive.resolve(config.staging(cfg) / parts[0])
        f = base.joinpath(*parts[1:])
        try:
            f = f.resolve()
            f.relative_to(base.resolve())
        except Exception:                                      # noqa: BLE001
            self._json({"error": "outside session"}, 403)
            return
        if not f.is_file():
            self._json({"error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if f.suffix in (".md", ".txt", ".json"):
            ctype = "text/plain; charset=utf-8"
        if f.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        self._send_range(f, ctype)

    def _send_range(self, f: Path, ctype: str) -> None:
        """Stream, and honour Range. An hour of screen.mp4 is ~340 MB: reading that into a
        bytes object to answer one request is bad enough, but without Accept-Ranges the
        player also cannot seek, which is exactly what clicking a key frame asks it to do."""
        size = f.stat().st_size
        start, end, partial = 0, size - 1, False
        m = re.match(r"bytes=(\d*)-(\d*)$", (self.headers.get("Range") or "").strip())
        if m and size:
            lo, hi = m.group(1), m.group(2)
            if lo:
                start, end = int(lo), (int(hi) if hi else size - 1)
            elif hi:
                start = max(0, size - int(hi))           # "the last N bytes"
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            partial = True
        n = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(n))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            with f.open("rb") as fh:
                fh.seek(start)
                left = n
                while left > 0:
                    chunk = fh.read(min(262144, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    # -- POST
    def do_POST(self) -> None:                                 # noqa: N802
        path = unquote(urlparse(self.path).path)
        if not self._guarded(path):
            return
        b = self._body()
        cfg = config.load()

        if path == "/api/config":
            saved = config.save({**cfg, **b})
            _doc_cache.update(at=time.time(), data=doctor.run(saved))
            self._json({"ok": True, "config": saved, "doctor": _doc_cache["data"]})

        elif path == "/api/doctor":
            _doc_cache.update(at=time.time(), data=doctor.run(cfg, levels=bool(b.get("levels"))))
            self._json(_doc_cache["data"])

        elif path == "/api/install":
            pkgs = b.get("packages") or []
            if not pkgs:
                self._json({"error": "nothing to install"}, 400)
                return
            self._json(start_job("安装依赖包", [PY, "-m", "pip", "install", "--upgrade", *pkgs]))

        elif path == "/api/model":
            code = ("import sys;from faster_whisper import WhisperModel;"
                    "print('downloading', sys.argv[1], flush=True);"
                    "WhisperModel(sys.argv[1], device='cpu', compute_type='int8');"
                    "print('model ready', flush=True)")
            self._json(start_job("下载语音模型", [PY, "-u", "-c", code, cfg["model"]]))

        elif path == "/api/record/start":
            self._json(self._start_record(cfg, b))

        elif path == "/api/record/cmd":
            self._json(self._record_cmd(str(b.get("cmd", ""))))

        elif path == "/api/process":
            self._json(self._process(cfg, b))

        elif path == "/api/confirm":
            self._json(self._confirm_write(cfg, b))

        elif path == "/api/frames":
            self._json(self._frames(cfg, b))

        elif path == "/api/rename":
            self._json(self._rename(cfg, b))
        elif path == "/api/minutes":
            self._json(self._minutes_write(cfg, b))
        elif path == "/api/engine/test":
            eng = str(b.get("engine") or cfg.get("engine") or "assistant")
            self._json(llm.ping_cli() if eng == "assistant"
                       else llm.ping({**cfg, **b}))
        elif path == "/api/minutes/draft":
            self._json(self._draft(cfg, b))
        elif path == "/api/minutes/paste":
            self._json(self._paste(cfg, b))

        elif path == "/api/archive":
            d = config.staging(cfg) / str(b.get("session", ""))
            self._json(archive.move(d, cfg, keep_audio=bool(b.get("keep_audio", True))))

        elif path == "/api/open":
            target = str(b.get("path") or "")
            p = Path(target)
            if not p.exists():
                self._json({"error": f"不存在：{target}"}, 404)
            elif not _openable(cfg, p):
                # startfile launches whatever Windows associates with the path, an .exe
                # included, so it only ever gets a folder this tool owns.
                self._json({"error": f"只能打开工具自己的文件夹，拒绝：{target}"}, 403)
            else:
                os.startfile(str(p))                           # noqa: S606
                self._json({"ok": True})

        else:
            self._json({"error": "not found"}, 404)

    # -- recording
    def _rec_status(self) -> dict:
        proc = _rec["proc"]
        alive = proc is not None and proc.poll() is None
        st: dict = {"active": alive, "session": _rec["session"]}
        sp = _rec["status"]
        if sp and Path(sp).exists():
            try:
                st.update(json.loads(Path(sp).read_text(encoding="utf-8")))
            except Exception:                                  # noqa: BLE001
                pass
        if proc is not None and not alive:
            st["exit_code"] = proc.returncode
        return st

    def _start_record(self, cfg: dict, b: dict) -> dict:
        if _rec["proc"] is not None and _rec["proc"].poll() is None:
            return {"error": "已经在录音了"}
        d = doctor.run(cfg)
        if not d["ready"]:
            bad = [c["name"] for c in d["checks"] if c["state"] == "fail"]
            return {"error": "自检没过：" + "、".join(bad)}
        stage = config.staging(cfg)
        ctl = stage / "control"
        ctl.write_text("", encoding="utf-8")
        (stage / "status.json").unlink(missing_ok=True)         # never show the last meeting's clock
        argv = [PY, "-u", str(HERE / "record.py"), "--out", str(stage),
                "--control", str(ctl)]
        title = str(b.get("title") or "").strip()
        if title:
            argv += ["--title", title]
        others = str(b.get("others") or "").strip()
        if others:
            argv += ["--others", others]
        if cfg.get("loopback_device"):
            argv += ["--loopback-device", cfg["loopback_device"]]
        # settings always travel, so screen recording can still be switched on mid-meeting
        argv += ["--video-fps", str(cfg["video_fps"]),
                 "--video-crf", str(cfg["video_crf"]),
                 "--video-width", str(cfg["video_width"])]
        if b.get("video", cfg.get("video")):
            argv.append("--video")
        if b.get("sensitive"):
            argv.append("--sensitive")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        proc = subprocess.Popen(argv, cwd=str(HERE), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)
        # the recorder owns the real folder name; status.json reports it back within 0.5 s.
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        slug = re.sub(r"[\s_]+", "-",
                      "".join(c if c.isalnum() or c in " -_" else "_" for c in title)).strip("-")
        _rec.update(proc=proc, control=str(ctl), status=str(stage / "status.json"),
                    session=(f"{stamp}_{slug}" if slug else stamp), asr=None, asr_session=None)
        if cfg.get("live_asr", True):
            threading.Thread(target=self._live_asr, args=(cfg, stage), daemon=True).start()
        return {"ok": True, "session": _rec["session"], "video": bool(b.get("video", cfg.get("video")))}

    @staticmethod
    def _live_asr(cfg: dict, stage: Path) -> None:
        """
        Transcribe while the meeting is still going. transcribe.py --live tails the
        growing WAVs at BELOW_NORMAL priority, so pressing stop costs a minute of
        waiting instead of twenty.
        """
        sp = stage / "status.json"
        name = ""
        for _ in range(120):
            time.sleep(0.5)
            try:
                name = str(json.loads(sp.read_text(encoding="utf-8")).get("session") or "")
            except Exception:                                  # noqa: BLE001
                name = ""
            if name:
                break
        ses = stage / name
        if not name or not ses.is_dir():
            return
        argv = [PY, "-u", str(HERE / "transcribe.py"), str(ses), "--live",
                "--model", str(cfg.get("model") or "large-v3-turbo")]
        try:
            log = open(ses / "asr.live.log", "w", encoding="utf-8")            # noqa: SIM115
            proc = subprocess.Popen(argv, cwd=str(HERE),
                                    env=dict(os.environ, PYTHONIOENCODING="utf-8",
                                             PYTHONUNBUFFERED="1"),
                                    stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL)
        except Exception:                                      # noqa: BLE001
            return
        _rec.update(asr=proc, asr_session=name)

    def _record_cmd(self, cmd: str) -> dict:
        if cmd not in ("stop", "mark", "private", "video"):
            return {"error": "unknown command"}
        ctl = _rec["control"]
        if not ctl:
            return {"error": "没有在录音"}
        with open(ctl, "a", encoding="utf-8") as fh:
            fh.write(cmd + "\n")
        return {"ok": True, "cmd": cmd}

    # -- processing
    def _process(self, cfg: dict, b: dict) -> dict:
        name = str(b.get("session") or "")
        d = archive.resolve(config.staging(cfg) / name)
        if not d.is_dir():
            return {"error": f"找不到会话 {name}"}
        others = str(b.get("others") or "").strip()
        if not others:
            try:
                others = str(json.loads(
                    (d / "session.json").read_text(encoding="utf-8")).get("others") or "").strip()
            except Exception:                                  # noqa: BLE001
                others = ""
        asr_argv = [PY, "-u", str(HERE / "asr.py"), str(d),
                    "--model", str(cfg.get("model") or "large-v3-turbo")]
        live = _rec.get("asr")
        if live is not None and live.poll() is None and _rec.get("asr_session") == d.name:
            asr_argv += ["--wait", "1800"]
        if b.get("retranscribe"):
            asr_argv.append("--force")
        bld = [PY, "-u", str(HERE / "build.py"), str(d)]
        if others:
            bld += ["--others", others]
        # who said what has to be settled BEFORE build.py, the step that writes the
        # labels into the transcript
        steps = [asr_argv] + _speaker_steps(cfg, d) + [bld]
        # A model that is already configured should not need a second click. The draft runs
        # inside the chain, before the document is rendered, so one press turns a recording
        # into minutes. It is a soft step: no network, no key, no local server, still a
        # transcript and still a document.
        steps += _draft_step(cfg, d)
        steps += _translate_step(cfg, d)
        steps.append([PY, "-u", str(HERE / "report.py"), str(d)]
                     + (["--me", cfg["me"]] if cfg.get("me") else []))
        return start_job(f"处理 {name}", [PY, "-u", str(HERE / "_chain.py"),
                                        json.dumps(steps, ensure_ascii=False)])

    # -- the minutes are a draft until a person has read them. Saving rewrites the
    #    source and re-renders the document through the same report.py the chain uses,
    #    so an edited file and a generated one are the same kind of file.
    def _minutes_write(self, cfg: dict, b: dict) -> dict:
        which = str(b.get("file") or "minutes.md")
        if which not in ("minutes.md", "minutes.zh.md"):
            return {"error": f"不允许写入 {which}"}
        d = archive.resolve(config.staging(cfg) / str(b.get("session", "")))
        if not d.is_dir():
            return {"error": "找不到该会话"}
        text = str(b.get("text") or "")
        if not text.strip():
            return {"error": "正文为空，未写入"}
        (d / which).write_text(text, encoding="utf-8", newline="\n")
        argv = [PY, "-u", str(HERE / "report.py"), str(d)]
        if cfg.get("me"):
            argv += ["--me", cfg["me"]]
        r = subprocess.run(argv, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip()[-400:]
            return {"error": "已写入正文，但重排纪要失败：" + tail}
        return {"ok": True, "file": which}

    # -- the minutes engine. Three cards, one HTTP shape; see mmt/llm.py for why.
    def _engine(self, cfg: dict) -> dict:
        d = llm.detect()
        d["engine"] = cfg.get("engine") or "assistant"
        d["api_base"] = cfg.get("api_base") or ""
        d["api_model"] = cfg.get("api_model") or ""
        # never hand the key back to the page; only whether one is stored
        d["api_key_set"] = bool(cfg.get("api_key"))
        d["ol_model"] = cfg.get("ol_model") or ""
        d["ollama_base"] = llm.OLLAMA_BASE
        d["api_ack"] = bool(cfg.get("api_ack"))
        d["api_local"] = llm.is_local(str(cfg.get("api_base") or ""))
        d["can_auto"] = llm.can_auto(cfg)
        return d

    def _prompt(self, cfg: dict, name: str, which: str) -> dict:
        d = archive.resolve(config.staging(cfg) / name)
        if not d.is_dir():
            return {"error": "\u627e\u4e0d\u5230\u8be5\u4f1a\u8bdd"}
        pr = llm.build_prompt(d, which if which in ("minutes.md", "minutes.zh.md") else "minutes.md")
        if pr.get("error"):
            return pr
        return {"ok": True, "text": pr["one"], "chars": pr["chars"],
                "tokens_est": pr["tokens_est"]}

    def _draft(self, cfg: dict, b: dict) -> dict:
        """A cloud model answers in 20 s, a 7B on a CPU can take 8 minutes. So this is a
        job with a log, like transcription, not a request the page waits on."""
        if not llm.can_auto(cfg):
            return {"error": "\u5f53\u524d\u7eaa\u8981\u5f15\u64ce\u662f\u300c\u4ea4\u7ed9 AI \u52a9\u624b\u300d\uff0c"
                             "\u800c assistants.json \u91cc\u6ca1\u7ed9 cli \u547d\u4ee4\uff0c"
                             "\u6240\u4ee5\u5b83\u53ea\u80fd\u9760\u590d\u5236\u7c98\u8d34\u3002"
                             "\u7528\u300c\u590d\u5236\u63d0\u793a\u8bcd\u300d\u90a3\u4e2a\u6309\u94ae\uff0c"
                             "\u6216\u8005\u53bb\u8bbe\u7f6e\u91cc\u6362\u6210 API / Ollama"}
        if ((cfg.get("engine") or "") == "api"
                and not llm.is_local(str(cfg.get("api_base") or ""))
                and not cfg.get("api_ack")):
            return {"error": "\u8fd9\u4e2a\u63a5\u53e3\u4e0d\u5728\u672c\u673a\u3002"
                             "\u8bf7\u5148\u5728\u8bbe\u7f6e\u91cc\u52fe\u9009"
                             "\u300c\u53ef\u4ee5\u628a\u4f1a\u8bae\u5185\u5bb9"
                             "\u53d1\u7ed9\u8fd9\u4e2a\u670d\u52a1\u300d"}
        which = str(b.get("file") or "minutes.md")
        if which not in ("minutes.md", "minutes.zh.md"):
            return {"error": f"\u4e0d\u5141\u8bb8\u5199\u5165 {which}"}
        d = archive.resolve(config.staging(cfg) / str(b.get("session", "")))
        if not d.is_dir():
            return {"error": "\u627e\u4e0d\u5230\u8be5\u4f1a\u8bdd"}
        pr = llm.build_prompt(d, which)
        if pr.get("error"):
            return pr
        return start_job(f"\u751f\u6210\u7eaa\u8981\u8349\u7a3f {d.name}",
                         [PY, "-u", str(HERE / "llm.py"), str(d), "--draft", "--file", which])

    def _paste(self, cfg: dict, b: dict) -> dict:
        """The copy-paste path: the user pastes back a whole minutes.md. Same validation the API
        path gets, then the same write-and-re-render, so both routes end identically."""
        text = llm.clean(str(b.get("text") or ""))
        if not text.strip():
            return {"error": "\u7c98\u8fdb\u6765\u7684\u5185\u5bb9\u662f\u7a7a\u7684"}
        bad = llm.validate(text)
        if bad and not b.get("force"):
            return {"error": "\u8fd9\u6bb5\u5185\u5bb9\u4e0d\u50cf\u4e00\u4efd\u5b8c\u6574\u7684 minutes.md",
                    "problems": bad}
        return self._minutes_write(cfg, {**b, "text": text})

    # -- the confirm step: everything a human has to answer, in one place
    def _confirm_read(self, cfg: dict, name: str) -> dict:
        d = archive.resolve(config.staging(cfg) / name)
        if not d.is_dir():
            return {"error": f"找不到会话 {name}"}
        meta = _jload(d / "session.json")
        tr = _jload(d / "transcript.json")
        saved = _jload(d / "confirm.json")
        acc = saved.get("accept") or {}
        lines = tr.get("lines") or []

        def _cut(t: str, n: int = 260) -> str:
            t = " ".join(str(t or "").split())
            return t if len(t) <= n else t[:n] + "…"

        def _win(t: str, surface: str, n: int) -> str:
            """
            A window centred on the word, not on the start of the line.

            A single transcript turn runs to a thousand characters. Cutting it at the
            front is how the highlighted word ended up outside the excerpt it was
            supposed to be highlighted in.
            """
            t = " ".join(str(t or "").split())
            if len(t) <= n:
                return t
            i = t.lower().find(surface.lower())
            if i < 0:
                return t[:n] + "…"
            a = max(0, i - n // 3)
            b = min(len(t), a + n)
            return ("…" if a else "") + t[a:b] + ("…" if b < len(t) else "")

        def _spk(v) -> str:
            """
            A speaker label short enough to sit in front of a sentence.

            Sessions built before the label fix carry the whole invite list as the speaker
            of the loopback track, which pushed the excerpt out of the row entirely. Those
            sessions are on disk and nobody is going to rebuild them, so a roster is read
            here for what it is: the other end.
            """
            v = " ".join(str(v or "").split())
            if len(re.split(r"[;,\u3001]", v)) > 1 or len(v) > 24:
                return "\u5bf9\u65b9"
            return v

        def where(surface: str) -> list[dict]:
            """
            Every place the word was heard, with the line before and after it.

            One clipped fragment is not enough to remember what was being said. The
            answer to "did he mean the other spelling?" lives in the turn before it, so the turn
            before it is what gets sent.
            """
            low = surface.lower()
            hits = []
            for i, l in enumerate(lines):
                t = str(l.get("text") or "")
                if low not in t.lower():
                    continue
                hits.append({
                    "at": float(l.get("start") or 0),
                    "spk": _spk(l.get("speaker")),
                    "pre": _cut(lines[i - 1].get("text") if i else "", 240),
                    "pre_spk": _spk(lines[i - 1].get("speaker")) if i else "",
                    # short window for the collapsed row, a long one behind "expand"
                    "snip": _win(t, surface, 150),
                    "text": _win(t, surface, 700),
                    "post": _cut(lines[i + 1].get("text") if i + 1 < len(lines) else "", 240),
                    "post_spk": (_spk(lines[i + 1].get("speaker"))
                                 if i + 1 < len(lines) else ""),
                })
                if len(hits) >= 4:
                    break
            return hits

        def sample(surface: str) -> str:
            low = surface.lower()
            for l in lines:
                t = str(l.get("text") or "")
                i = t.lower().find(low)
                if i >= 0:
                    a = max(0, i - 60)
                    return ("…" if a else "") + t[a:i + len(surface) + 70].strip() + "…"
            return ""

        pairs = []
        for key, count in sorted((tr.get("phonetic_guesses") or {}).items(),
                                 key=lambda kv: -kv[1]):
            surface, _, canon = str(key).partition(" -> ")
            pairs.append({"key": key, "surface": surface.strip(), "canon": canon.strip(),
                          "count": count, "decided": acc.get(key),
                          "applied": False,
                          "sample": sample(surface.strip()),
                          "where": where(surface.strip())})
        for key, ok in acc.items():                    # already decided in an earlier round
            if not any(p["key"] == key for p in pairs):
                surface, _, canon = str(key).partition(" -> ")
                s, c = surface.strip(), canon.strip()
                # Decided in an earlier round, so the transcript already reads the new
                # way and the heard form is no longer on the page. Locate whichever of
                # the two is actually there, or the row comes back with no context at all.
                w = where(s)
                probe, applied = (s, False) if w else (c, True)
                pairs.append({"key": key, "surface": s, "canon": c,
                              "count": 0, "decided": ok, "applied": applied,
                              "sample": sample(probe), "where": where(probe)})
        others = str(meta.get("others") or "").strip()
        if not others:      # older sessions got the name on the command line, not in session.json
            mine = (cfg.get("me") or "").lower()
            others = "; ".join(k for k in (tr.get("talk_time_s") or {})
                               if k.lower() not in ("you", "others", "me", mine))

        # who was really in the room: the invite list is a starting point, not the answer,
        # so every name comes back as a tick box the person in the meeting can clear
        keep = saved.get("attendees")
        names = [n.strip() for n in re.split(r"[;,\n\u3001]+", others) if n.strip()]
        attendees = [{"name": n, "on": (n in keep) if isinstance(keep, list) else True}
                     for n in names]

        issues = _issues(d, tr, len(names))
        ack = saved.get("ack") or {}
        for it in issues:
            it["ack"] = bool(ack.get(it["id"]))
        return {"ok": True, "session": d.name,
                "title": meta.get("title") or "", "others": others,
                "sensitive": bool(meta.get("sensitive")),
                "me": cfg.get("me") or "", "duration_s": meta.get("duration_s") or 0,
                "lines": len(lines), "talk_time_s": tr.get("talk_time_s") or {},
                "languages": tr.get("languages") or {},
                "low_conf_lines": tr.get("low_conf_lines") or 0,
                "ambiguous": tr.get("ambiguous_terms_present") or [],
                "glossary_corrections": tr.get("glossary_corrections") or {},
                "dropped": len(tr.get("dropped_hallucinations") or []),
                "private_cut_s": tr.get("private_cut_s") or 0,
                "attendees": attendees, "issues": issues,
                "pairs": pairs, "confirmed_at": saved.get("at") or ""}

    def _invite(self, cfg: dict, name: str) -> dict:
        """
        Who was invited, straight from the calendar item this recording belongs to.
        Typing five Indian names from memory is how minutes get names wrong; the
        invite already has the right spelling.
        """
        d = archive.resolve(config.staging(cfg) / name)
        if not d.is_dir():
            return {"error": f"找不到会话 {name}"}
        meta = _jload(d / "session.json")
        started = str(meta.get("started_local") or meta.get("started") or "")
        if len(started) < 16:                        # fall back to the folder name
            started = d.name[:10] + "T" + d.name[11:13] + ":" + d.name[13:15]
        r = outlook.day(cfg, started[:10])
        if r.get("error"):
            return r
        want = re.sub(r"[^a-z0-9]+", "", (meta.get("title") or "").lower())
        hhmm = started[11:16]
        best, score = None, -1.0
        for m in r.get("meetings") or []:
            subj = re.sub(r"[^a-z0-9]+", "", (m.get("subject") or "").lower())
            sc = 0.0
            if want and subj:
                if want == subj:
                    sc += 3
                elif want in subj or subj in want:
                    sc += 2
            if hhmm and m.get("start"):                  # started within the booked slot
                if m["start"][11:16] <= hhmm <= (m.get("end") or "")[11:16]:
                    sc += 2
                elif abs(int(hhmm[:2]) * 60 + int(hhmm[3:5])
                         - int(m["start"][11:13]) * 60 - int(m["start"][14:16])) <= 20:
                    sc += 1
            if sc > score:
                best, score = m, sc
        if not best or score <= 0:
            return {"ok": True, "matched": "", "names": [],
                    "note": f"{started[:10]} 的日历里没有和这场对得上的会议"}
        return {"ok": True, "matched": best.get("subject") or "",
                "slot": (best.get("start") or "")[11:16] + "-" + (best.get("end") or "")[11:16],
                "names": best.get("names") or []}

    def _rename(self, cfg: dict, b: dict) -> dict:
        """The name a meeting is known by, kept in session.json.

        The folder name stays a timestamp. Every other file in the session, and the
        archive pointer, find this session by that folder name, so renaming the folder
        would be renaming the primary key. What the user sees is the title.
        """
        name = str(b.get("session") or "")
        d = archive.resolve(config.staging(cfg) / name)
        if not d.is_dir():
            return {"error": f"找不到会话 {name}"}
        p = d / "session.json"
        meta = _jload(p)
        if not meta:
            return {"error": "这一场没有 session.json，改不了名字"}
        title = " ".join(str(b.get("title") or "").split())[:160]
        was = meta.get("title") or ""
        if title == was:
            return {"ok": True, "title": title}
        meta["title"] = title
        meta.setdefault("edits", []).append(
            {"at": datetime.now().isoformat(timespec="seconds"), "was": {"title": was}})
        p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "title": title}

    def _confirm_write(self, cfg: dict, b: dict) -> dict:
        name = str(b.get("session") or "")
        d = archive.resolve(config.staging(cfg) / name)
        if not d.is_dir():
            return {"error": f"找不到会话 {name}"}
        if not any((d / f"{t}.segments.json").exists() for t in ("mic", "others")):
            return {"error": "这一场的识别中间文件已经不在了（归档时清掉的）。"
                             "重建会把现有逐字稿清空，所以这里不做。"}
        title = str(b.get("title") or "").strip()
        picked = b.get("attendees")
        if isinstance(picked, list):
            others = "; ".join(str(x).strip() for x in picked if str(x).strip())
        else:
            others = str(b.get("others") or "").strip()
        meta = _jload(d / "session.json")
        if meta:
            before = {"title": meta.get("title"), "others": meta.get("others"),
                      "sensitive": meta.get("sensitive")}
            meta["title"] = title or meta.get("title") or ""
            meta["others"] = others
            if "sensitive" in b:
                meta["sensitive"] = bool(b.get("sensitive"))
            if {"title": meta["title"], "others": meta["others"],
                    "sensitive": meta.get("sensitive")} != before:
                meta.setdefault("edits", []).append(
                    {"at": datetime.now().isoformat(timespec="seconds"), "was": before})
            (d / "session.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
        # Three possible answers per word, not two: take the proposal, keep what was heard,
        # or "neither" - in which case the value is the spelling the person typed.
        accept: dict[str, object] = {}
        for k, v in (b.get("accept") or {}).items():
            accept[str(k)] = v.strip() if isinstance(v, str) and v.strip() else bool(v)
        ack = {str(k): bool(v) for k, v in (b.get("ack") or {}).items()}
        (d / "confirm.json").write_text(json.dumps(
            {"at": datetime.now().isoformat(timespec="seconds"), "by": cfg.get("me") or "",
             "title": title, "others": others, "accept": accept, "ack": ack,
             "attendees": picked if isinstance(picked, list) else None},
            ensure_ascii=False, indent=1), encoding="utf-8")
        # "Save" and "save and generate" are two different intentions. Ticking the room
        # and coming back to it later is the common one, and it must not cost a rebuild of
        # every document.
        if b.get("save_only"):
            return {"ok": True, "saved": True,
                    "at": datetime.now().strftime("%H:%M:%S")}
        bld = [PY, "-u", str(HERE / "build.py"), str(d)]
        if others:
            bld += ["--others", others]
        steps = _speaker_steps(cfg, d) + [bld]
        steps += _draft_step(cfg, d)
        steps += _translate_step(cfg, d)
        steps.append([PY, "-u", str(HERE / "report.py"), str(d)]
                     + (["--me", cfg["me"]] if cfg.get("me") else []))
        return start_job(f"生成文档 {name}", [PY, "-u", str(HERE / "_chain.py"),
                                          json.dumps(steps, ensure_ascii=False)])

    def _frames(self, cfg: dict, b: dict) -> dict:
        """screen.mp4 plays in any browser; frames.py also pulls out still pictures."""
        name = str(b.get("session") or "")
        d = archive.resolve(config.staging(cfg) / name)
        if not (d / "screen.mp4").exists():
            return {"error": f"{name} 没有 screen.mp4（录制时没勾录屏）"}
        argv = [PY, "-u", str(HERE / "frames.py"), str(d),
                "--max", str(b.get("max") or 150),
                "--diff", str(b.get("diff") or 0.02)]
        return start_job(f"抽帧 {name}", argv)


class Srv(ThreadingHTTPServer):
    # http.server turns SO_REUSEADDR on by default. On Windows that does not mean "reuse a
    # port in TIME_WAIT", it means a second process can bind the very same address and the
    # requests then land in whichever one Windows feels like - which is how a window left
    # open from yesterday ends up answering today's page while the new window sits there
    # looking healthy. One port, one server: fail loudly instead.
    allow_reuse_address = False


def serve(port: int = 8760, open_browser: bool = True) -> int:
    url = f"http://127.0.0.1:{port}/"
    try:
        srv = Srv(("127.0.0.1", port), H)
    except OSError:
        # Double-clicking the .bat twice is normal. Point at the window that is already
        # running instead of dying with a stack trace - and say why it matters, because a
        # stale window serves the code from whenever it was started.
        print(f"already running  ->  {url}\n"
              f"port {port} is taken by an earlier window of this tool.\n"
              f"if the tool was just updated, close THAT window and start again, "
              f"otherwise it keeps serving the old version.")
        if open_browser:
            webbrowser.open(url)
        return 3            # non-zero, so the launcher window says so instead of "stopped"
    # A machine that has never run this gets a complete config, a private glossary and
    # a calendar harvest, without being asked anything. See mmt/firstrun.py.
    try:
        import firstrun
        firstrun.ensure(log=lambda s: print(f"  {s}"))
    except Exception as e:                                   # noqa: BLE001
        print(f"first-run setup skipped: {e}")
    print(f"Minutes Desk {BUILD}  ->  {url}\nclose this window to stop the app")
    if open_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Local meeting tool UI")
    ap.add_argument("--port", type=int, default=8760)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    sys.exit(serve(a.port, not a.no_browser))

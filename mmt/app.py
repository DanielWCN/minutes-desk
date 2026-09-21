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
import report

HERE = Path(__file__).resolve().parent

# One number for the whole tool, so that "which version are you on" has an answer a
# colleague can read off the title bar. Third digit for a fix, second for a new feature,
# first for a change of shape. Every release is listed in CHANGELOG.md.
#
# The page is read from disk on every refresh; the server is not. So a window left open
# from yesterday serves new HTML against old Python, and the symptoms look like data
# bugs. Move this and UI_VERSION in ui.html together, and the page will say so out loud.
VERSION = "2.4.2"

# When this process started, and whether any page has spoken to it yet. The launcher
# already ends the previous Python; these two let the browser side do the same for its
# tab. A page polls /api/record/status, sees `boot` change, and reloads itself into the
# new version - and because the server waits for that ping before it opens a browser, a
# restart re-uses the tab that is already open instead of leaving another one behind.
BOOT = int(time.time() * 1000)
SEEN_PAGE = threading.Event()

ROOT = HERE.parent
UI = HERE / "ui.html"
PY = sys.executable

_jobs: dict[str, dict] = {}
_rec: dict = {"proc": None, "session": None, "control": None, "status": None, "asr": None,
              "asr_session": None, "cap": None, "cap_session": None, "ended_at": None}
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
    # This used to say flatly that the far end cannot be told apart, which stopped being
    # true the day diarization and naming went in -- and a notice that says a working
    # feature is impossible is worse than no notice, because it stops the reader from ever
    # reporting that it is broken.
    spk = _jload(d / "speakers.json")
    named = _count(spk.get("named")) if spk else 0
    if spk:
        total = _count(spk.get("num_clusters"))
        cap = _count(spk.get("from_captions"))
        add("speakers", "info",
            f"与会 {n_people} 人，对端 {total} 个声音已分开，其中 {named} 个认出了名字"
            + (f"（{cap} 个来自会议字幕）" if cap else ""),
            ("其中会议字幕给出的名字不是推断：字幕显示名字的时刻，正是那个声音在说话的时刻。"
             if cap else "")
            + "对端是一路混合音频，所以名字不是靠声纹，而是靠会上人们互相怎么称呼推出来的："
            "认不出来的留作「Speaker 编号」，只是推测的会标上问号。",
            "建议核查：逐字稿里带问号的行，请对照录音确认一下是谁。"
            if _count(spk.get("guessed")) else "无需处理，仅供备查。")
    elif n_people > 2:
        add("speakers", "info", f"与会 {n_people} 人，对端还没有按人分开",
            "系统声音是一路混合音频，需要先按声音分轨再认名字；这一步没有跑成，"
            "所以逐字稿里对端只有一个标签。",
            "需处理：再执行一次 Analysis。若仍然如此，说明分轨模型没装好。")
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
    # The client's own captions, if this meeting had any, are laid over the clusters first: a
    # name Zoom or Slack printed at the moment a voice was talking is not a guess, so whois.py
    # is left with
    # only the clusters the captions could not settle. It also means naming works with no
    # model configured at all, which it never could before.
    caps = (d / "captions.jsonl").exists()
    if caps and (not fresh or not (d / "captions.match.json").exists()):
        out.append(["?", PY, "-u", str(HERE / "zmatch.py"), str(d)]
                   + (["--me", cfg["me"]] if cfg.get("me") else []))
    if (llm.can_auto(cfg) or caps) and (not fresh or not (d / "speakers.json").exists()):
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


def _doc_steps(cfg: dict, d: Path, bld: list, rpt: list, rebuilt: bool = False) -> list:
    """The order these steps have to run in, which is not the order they read in.

    whois.py reads transcript.json, and llm.py --draft reads minutes.md -- and both of those
    files are written by LATER steps of this same chain: build.py writes the transcript,
    report.py scaffolds the minutes. On a fresh recording neither exists yet, so naming and
    drafting failed on every first press and only worked on a second one. That is what turns
    a first transcript into "Speaker 1 .. Speaker 18" beside an all-TBD scaffold. Building
    and rendering once up front costs a few seconds and makes one press enough; a session
    that already has those files pays nothing.
    """
    steps = ([bld] if rebuilt or not (d / "transcript.json").exists() else []) \
        + _speaker_steps(cfg, d) + [bld]
    draft = _draft_step(cfg, d)
    if draft and not (d / "minutes.md").exists():
        steps.append(rpt)                  # writes the scaffold the draft then fills in
    steps += draft
    steps += _translate_step(cfg, d)
    steps.append(rpt)
    return steps


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
            self._json({"version": VERSION, "boot": BOOT,
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
                                   str(q.get("file", ["minutes.md"])[0]),
                                   str(q.get("mode", ["draft"])[0])))
        elif path == "/api/review":
            self._json(self._review_read(config.load(), str(q.get("session", [""])[0])))
        elif path == "/api/record/status":
            # The page's 3 s heartbeat. It carries `boot` back, which is how a tab left over
            # from the previous start finds out it is the old version and reloads itself.
            # `?v=` is what says the page is new enough to do that: a page from before this
            # existed would sit there stale while we decided not to open a fresh tab for it.
            if q.get("v"):
                SEEN_PAGE.set()
            self._json({**self._rec_status(), "boot": BOOT})
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
        if f.name == "minutes.html":
            f = self._doc_fresh(cfg, base, f)
        self._send_range(f, ctype)

    # -- a document rendered by an older version carries none of the current behaviour of
    #    the paper: marking a sentence is the document's own code, and no amount of new app
    #    around it puts that code into a file written last week. Rather than ask a person to
    #    re-save every finished meeting, re-render once, here, the moment the stale file is
    #    asked for. Failure is not fatal - the old page still opens, it just cannot be marked.
    def _doc_fresh(self, cfg: dict, base: Path, f: Path) -> Path:
        try:
            with f.open("rb") as fh:
                head = fh.read(800).decode("utf-8", "replace")
            m = re.search(r'data-rv="(\d+)"', head)
            if m and int(m.group(1)) >= report.RV:
                return f
            if not (base / "minutes.md").is_file():
                return f
            argv = [PY, "-u", str(HERE / "report.py"), str(base)]
            if cfg.get("me"):
                argv += ["--me", cfg["me"]]
            subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        except Exception:                                      # noqa: BLE001
            pass
        return f

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
        elif path == "/api/minutes/revise":
            self._json(self._revise(cfg, b))
        elif path == "/api/minutes/mirror":
            self._json(self._mirror(cfg, b))
        elif path == "/api/review":
            self._json(self._review_write(cfg, b))

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
            if not _rec.get("ended_at"):
                _rec["ended_at"] = time.time()
            cp = _rec.get("cap")
            # zcap.py stops itself when session.json appears. This is for the case where the
            # recorder died without writing one; 15 s first, so a caption still in flight lands.
            if cp is not None and cp.poll() is None and time.time() - _rec["ended_at"] > 15:
                try:
                    cp.terminate()
                except Exception:                              # noqa: BLE001
                    pass
        st["captions"] = self._cap_status()
        return st

    @staticmethod
    def _cap_status() -> dict:
        """One line for the recording panel: is a caption panel being read, and how much."""
        name, base = _rec.get("cap_session"), _rec.get("status")
        if not name or not base:
            return {"on": False}
        cp = _rec.get("cap")
        f = Path(base).parent / name / "captions.jsonl"
        # Two counts, because they mean different things to whoever is watching. "named" is
        # what zmatch.py can actually use. "lines" being ahead of it means the panel is being
        # read but the speaker is not on the line - which is the one failure worth seeing
        # while the meeting is still going, instead of discovering it afterwards.
        n = named = 0
        if f.exists():
            try:
                with f.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        n += 1
                        try:
                            if (json.loads(line).get("speaker") or "").strip():
                                named += 1
                        except ValueError:
                            pass
            except OSError:
                n = named = 0
        return {"on": cp is not None and cp.poll() is None, "lines": named, "raw": n}

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
                    session=(f"{stamp}_{slug}" if slug else stamp), asr=None, asr_session=None,
                    cap=None, cap_session=None, ended_at=None)
        if cfg.get("live_asr", True):
            threading.Thread(target=self._live_asr, args=(cfg, stage), daemon=True).start()
        if cfg.get("captions", True):
            threading.Thread(target=self._captions, args=(cfg, stage), daemon=True).start()
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
            with open(ses / "asr.live.log", "w", encoding="utf-8") as log:
                proc = subprocess.Popen(argv, cwd=str(HERE),
                                        env=dict(os.environ, PYTHONIOENCODING="utf-8",
                                                 PYTHONUNBUFFERED="1"),
                                        stdout=log, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL)
        except Exception:                                      # noqa: BLE001
            return
        _rec.update(asr=proc, asr_session=name)

    @staticmethod
    def _captions(cfg: dict, stage: Path) -> None:
        """
        Read the meeting client's caption panel while the meeting runs, if it is open.

        This is where the far end's NAMES come from. Everything else in the tool has to work
        the names out afterwards - from how people address each other - because the loopback
        track is one mixed stream. The client is the one participant that already knows,
        because it prints the roster name of whoever is speaking. zmatch.py later lays those
        names over the voice clusters by time.

        Never mandatory, never in the way: no caption panel and this writes nothing, and a
        failure here cannot touch the recording, which is a different process.
        """
        if importlib.util.find_spec("uiautomation") is None:
            return
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
        try:
            # closed here on purpose: the child has its own inherited handle, and a copy
            # left open in the server keeps the file locked, so deleting or archiving that
            # session folder later fails with a sharing violation.
            with open(ses / "captions.log", "w", encoding="utf-8") as log:
                proc = subprocess.Popen([PY, "-u", str(HERE / "zcap.py"), str(ses)],
                                        cwd=str(HERE),
                                        env=dict(os.environ, PYTHONIOENCODING="utf-8",
                                                 PYTHONUNBUFFERED="1"),
                                        stdout=log, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL)
        except Exception:                                      # noqa: BLE001
            return
        _rec.update(cap=proc, cap_session=name)

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
        rpt = [PY, "-u", str(HERE / "report.py"), str(d)] \
            + (["--me", cfg["me"]] if cfg.get("me") else [])
        # who said what has to be settled BEFORE the build that writes the labels into the
        # transcript, and a model that is already configured should not need a second click:
        # one press turns a recording into named minutes. Both are soft steps -- no network,
        # no key, no local server, still a transcript and still a document.
        steps = [asr_argv] + _doc_steps(cfg, d, bld, rpt, bool(b.get("retranscribe")))
        return start_job(f"处理 {name}", [PY, "-u", str(HERE / "_chain.py"),
                                        json.dumps(steps, ensure_ascii=False)])

    # -- the minutes are a draft until a person has read them. Saving rewrites the
    #    source and re-renders the document through the same report.py the chain uses,
    #    so an edited file and a generated one are the same kind of file.
    def _minutes_write(self, cfg: dict, b: dict) -> dict:
        which = str(b.get("file") or "minutes.md")
        if which not in llm.SHEETS:
            return {"error": f"不允许写入 {which}"}
        d = archive.resolve(config.staging(cfg) / str(b.get("session", "")))
        if not d.is_dir():
            return {"error": "找不到该会话"}
        text = str(b.get("text") or "")
        if not text.strip():
            return {"error": "正文为空，未写入"}
        before = (d / which).read_text(encoding="utf-8") if (d / which).exists() else ""
        # same promise the rewrite route makes: what was there is still there afterwards.
        # The paste box in particular holds a model's text, not the reader's.
        if before.strip():
            (d / (which + ".bak")).write_text(before, encoding="utf-8", newline="\n")
        (d / which).write_text(text, encoding="utf-8", newline="\n")
        # A word fixed here is the same word in the other language sheet. No model is
        # called for it - the two files spell a term, a name or a system identically - so
        # the fix is carried over before the document is re-rendered, not after.
        mir = {}
        if before.strip():
            try:
                mir = llm.mirror(d, cfg, which, [], before=before, terms_only=True)
            except Exception as exc:                           # noqa: BLE001
                mir = {"files": [], "error": str(exc)[:300]}
        argv = [PY, "-u", str(HERE / "report.py"), str(d)]
        if cfg.get("me"):
            argv += ["--me", cfg["me"]]
        r = subprocess.run(argv, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip()[-400:]
            return {"error": "已写入正文，但重排纪要失败：" + tail}
        return {"ok": True, "file": which, "mirror": mir,
                "mirror_pending": list(mir.get("pending") or [])}

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
        d["cli_ack"] = bool(cfg.get("cli_ack"))
        d["api_local"] = llm.is_local(str(cfg.get("api_base") or ""))
        d["can_auto"] = llm.can_auto(cfg)
        return d

    def _prompt(self, cfg: dict, name: str, which: str, mode: str = "draft") -> dict:
        """The text for the copy-paste route. `mode=revise` asks for the rewrite request
        instead of the first draft, so the paper's marks still work without a CLI.

        Always the full-rewrite wording, never the edit-block one the button uses: what
        comes back here is pasted into the editor by a person, and edit blocks would be a
        set of instructions with nothing to carry them out."""
        d = archive.resolve(config.staging(cfg) / name)
        if not d.is_dir():
            return {"error": "\u627e\u4e0d\u5230\u8be5\u4f1a\u8bdd"}
        which = which if which in ("minutes.md", "minutes.zh.md") else "minutes.md"
        pr = (llm.build_revise_prompt(d, which, None, "full") if mode == "revise"
              else llm.build_prompt(d, which))
        if pr.get("error"):
            return pr
        return {"ok": True, "text": pr["one"], "chars": pr["chars"],
                "tokens_est": pr["tokens_est"]}

    def _engine_gate(self, cfg: dict) -> dict:
        """What stops an automatic write, in words. Empty means go ahead.

        The assistant CLI needs its own acknowledgement even though a human wrote
        assistants.json: naming a program is not the same as agreeing that this meeting
        may be handed to it, and the model behind that program is not necessarily on this
        machine. `ask` tells the page to offer that agreement instead of a dead error.
        """
        eng = cfg.get("engine") or "assistant"
        if not llm.can_auto(cfg):
            return {"error": "当前纪要引擎是「交给 AI 助手」，而 assistants.json 里没给 cli 命令，"
                             "所以它只能靠复制粘贴。用「复制提示词」那个按钮，"
                             "或者去设置里换成 API / Ollama"}
        if eng == "assistant" and not cfg.get("cli_ack"):
            return {"error": "还没同意把会议内容交给这个助手程序", "ask": "cli",
                    "who": llm.detect().get("assistant_exe") or "assistants.json 里那个程序"}
        if (eng == "api" and not llm.is_local(str(cfg.get("api_base") or ""))
                and not cfg.get("api_ack")):
            return {"error": "这个接口不在本机。请先在设置里勾选「可以把会议内容发给这个服务」",
                    "ask": "api"}
        return {}

    def _draft(self, cfg: dict, b: dict) -> dict:
        """A cloud model answers in 20 s, a 7B on a CPU can take 8 minutes. So this is a
        job with a log, like transcription, not a request the page waits on."""
        gate = self._engine_gate(cfg)
        if gate:
            return gate
        which = str(b.get("file") or "minutes.md")
        if which not in llm.SHEETS:
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
        # against the headings this file already has, not against the English four: a
        # pasted-back Chinese sheet was being refused for "缺少 ## Summary"
        d0 = archive.resolve(config.staging(cfg) / str(b.get("session", "")))
        cur = d0 / str(b.get("file") or "minutes.md")
        heads = llm.heads_of(cur.read_text(encoding="utf-8")) if cur.exists() else None
        bad = llm.validate(text, heads)
        if bad and not b.get("force"):
            return {"error": "\u8fd9\u6bb5\u5185\u5bb9\u4e0d\u50cf\u4e00\u4efd\u5b8c\u6574\u7684 minutes.md",
                    "problems": bad}
        return self._minutes_write(cfg, {**b, "text": text})

    # -- the marks a reader left on the finished paper, and the one button that answers
    #    them. Rewriting re-reads the transcript and re-writes minutes.md; it does not
    #    re-run recognition, so a confirmed session stays confirmed.
    def _review_read(self, cfg: dict, name: str) -> dict:
        d = archive.resolve(config.staging(cfg) / name)
        if not d.is_dir():
            return {"error": "找不到该会话"}
        return llm.review(d)

    def _review_write(self, cfg: dict, b: dict) -> dict:
        d = archive.resolve(config.staging(cfg) / str(b.get("session", "")))
        if not d.is_dir():
            return {"error": "找不到该会话"}
        which = str(b.get("file") or "minutes.md")
        if which not in llm.SHEETS:
            return {"error": f"不允许标注 {which}"}
        op = str(b.get("op") or "add")
        if op == "add":
            return llm.review_add(d, str(b.get("quote") or ""), str(b.get("note") or ""), which)
        if op == "del":
            return llm.review_del(d, str(b.get("id") or ""))
        if op == "clear":
            return llm.review_clear(d)
        return {"error": f"未知操作 {op}"}

    def _mirror(self, cfg: dict, b: dict) -> dict:
        """Carry a hand-typed sentence to the other sheet. Called by the page right after a
        save, and only when the save said something was left over."""
        gate = self._engine_gate(cfg)
        if gate:
            return gate
        which = str(b.get("file") or "minutes.md")
        if which not in llm.SHEETS:
            return {"error": f"不允许写入 {which}"}
        d = archive.resolve(config.staging(cfg) / str(b.get("session", "")))
        if not d.is_dir():
            return {"error": "找不到该会话"}
        return start_job(f"同步另一份稿子 {d.name}",
                         [PY, "-u", str(HERE / "llm.py"), str(d), "--mirror",
                          "--file", which])

    def _revise(self, cfg: dict, b: dict) -> dict:
        gate = self._engine_gate(cfg)
        if gate:
            return gate
        which = str(b.get("file") or "minutes.md")
        if which not in llm.SHEETS:
            return {"error": f"不允许写入 {which}"}
        d = archive.resolve(config.staging(cfg) / str(b.get("session", "")))
        if not d.is_dir():
            return {"error": "找不到该会话"}
        pr = llm.build_revise_prompt(d, which)
        if pr.get("error"):
            return pr
        return start_job(f"按标注重写 {d.name}",
                         [PY, "-u", str(HERE / "llm.py"), str(d), "--revise",
                          "--file", which])

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
        rpt = [PY, "-u", str(HERE / "report.py"), str(d)] \
            + (["--me", cfg["me"]] if cfg.get("me") else [])
        steps = _doc_steps(cfg, d, bld, rpt)
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


def _open_when_needed(url: str, wait_s: float = 1.8) -> None:
    """Open a tab, unless a page is already talking to us.

    A tab from the previous start keeps polling; the moment its server went away the
    heartbeat speeds up, so it comes back within a second of this one binding the port,
    sees a different `boot` and reloads itself. Opening a second tab on top of that is
    exactly the pile-up we are trying to avoid. If nothing pings inside the window,
    nobody has the app open and we open it.
    """
    if SEEN_PAGE.wait(wait_s):
        print("a tab is already open - it reloads itself into this version")
        return
    webbrowser.open(url)


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
    print(f"Minutes Desk v{VERSION}  ->  {url}\nclose this window to stop the app")
    if open_browser:
        threading.Thread(target=_open_when_needed, args=(url,), daemon=True).start()
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

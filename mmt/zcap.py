"""
The meeting client's own captions, borrowed as a who-spoke-when timeline.

The far end arrives as one mixed loopback track, so diarize.py can split it into voices but
never knows a name. The client already knows every name: when captions are on, Zoom and Slack
both print "Alice Chen: the upload finished" on screen, because the name comes from the
meeting roster, not from guessing. This step reads that panel through the Windows
accessibility layer - the same interface a screen reader uses - and writes one line per
caption with the wall clock beside it.

Zoom and Slack are read the same way, and nothing here is specific to either beyond the
process name and how deep the panel sits. Measured on a real window of each: Zoom's native
panel is about six levels down and costs 0.5 to 1.1s a scan, Slack's is a Chromium DOM about
twenty-five levels down and costs 0.62s. Whichever one has captions open is the one that
answers; if both do, both are read.

What it is for, and what it is NOT for:

  * The transcript stays ours. A client's caption text is short, unpunctuated and drops
    words; large-v3-turbo is better. Nothing here replaces a single word of the transcript.
  * The names are the client's. A caption that says a name at 16:41:02 pins whichever
    diarization cluster was speaking at 16:41:02 to that person, with none of the guesswork
    whois.py has to do. Overlap in time is the only thing we take.

Nothing is sent anywhere and nothing is recorded but text the client had already drawn on
the screen. No screen capture, no video, no voiceprint.

Requirements: the caption or transcript panel must be open, and the meeting window must not
be minimised - a minimised window has no accessibility tree to read. If captions are off,
this step writes nothing and says so; it never interferes with the recording.

Usage:
    zcap.py --probe                 which clients are there, can their captions be read? JSON
    zcap.py --tree [--out F]        dump the accessibility tree of every meeting window
    zcap.py --tree --pid 1234       dump one process's windows instead (for testing)
    zcap.py <session>               follow the captions, append captions.jsonl
    zcap.py <session> --for 60      stop after 60 seconds
    zcap.py <session> --feed F      replay a text file instead of reading Zoom (a test)
"""
from __future__ import annotations

import argparse
import ctypes
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

POLL = 0.8            # seconds between reads; captions redraw about twice a second
SETTLE = 2.0          # a line that has not changed for this long is finished
# Zoom draws its panel with native controls, which sit about six levels down. Slack is a
# Chromium app and its captions are DOM nodes about twenty-five levels down, so a limit that
# was generous for Zoom saw almost nothing in Slack: measured on a real Slack window, depth 20
# reached 15 text nodes and depth 32 reached 132, for 0.62s instead of 0.15s. The budget rises
# with it, because a Slack window legitimately holds more nodes than a Zoom one.
MAX_DEPTH = 32        # deeper than any real caption panel; stops runaway walks
BUDGET = 12000        # nodes per window per scan; a tree this big is not a caption panel
MIN_TEXT = 2          # a one-character node is a decoration, not speech
FULL_EVERY = 25.0     # even when locked on, glance over the whole window this often
APP_EXE = {"zoom.exe": "zoom", "zoommeeting.exe": "zoom", "slack.exe": "slack"}
# Zoom's home window (the calendar and contacts shell) and its toasts are not where captions
# live, and reading them would drop the titles of the day's meetings into the session folder
# for no reason. The caption panel belongs to the meeting window. --all-windows overrides
# this, which is what to try first if a real caption panel ever turns up unread.
SKIP_CLS = ("ZPPTMainFrmWndClassEx", "ZPZoomToastNotifierWnd")
# Slack's window is the whole client, not just the call, so the message list, the sidebar and
# the thread pane are all readable text that has nothing to do with speech. Walking into them
# wastes the budget and, worse, gives the lock-on heuristic a wall of chat to mistake for
# captions. Captions live in the huddle panel, never inside any of these, so the walk stops at
# their doorstep. Every class here was read off a real Slack window, not guessed.
SKIP_SUB = re.compile(r"c-message_kit|c-message_list|p-message_pane|p-workspace__message"
                      r"|channel_sidebar|p-tab_rail|p-top_nav|c-virtual_list"
                      r"|p-threads_|p-file_|p-search")

# "Alice Chen: the upload finished"  /  "Alice Chen (Host): ..."
NAMED = re.compile(r"^\s*([^:：]{1,40}?)\s*(?:\((?:Host|主持人|Me|我)\)\s*)?[:：]\s*(.+)$", re.S)
# a caption panel row that carries a clock: "Alice Chen  16:41:02  the upload finished"
STAMPED = re.compile(r"^\s*(.{1,40}?)\s+(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?)\s+(.+)$", re.S)


def _pname(pid: int) -> str:
    """The executable name behind a window, without psutil."""
    PROCESS_QUERY_LIMITED = 0x1000
    k = ctypes.windll.kernel32
    h = k.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.c_ulong(1024)
        if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value.rsplit("\\", 1)[-1].lower()
        return ""
    finally:
        k.CloseHandle(h)


def _auto():
    try:
        import uiautomation as auto
    except Exception as e:                                     # pragma: no cover
        raise SystemExit("读不了窗口：缺 uiautomation（pip install uiautomation）\n  " + str(e))
    auto.SetGlobalSearchTimeout(0.5)
    try:
        auto.Logger.SetLogFile(str(Path(HERE.parent / "sessions" / ".uia.log")))
    except Exception:
        pass
    return auto


def windows(pid: int | None = None, every: bool = False) -> list:
    """Top-level windows belonging to a meeting client (or to one pid, for testing)."""
    auto = _auto()
    out = []
    for w in auto.GetRootControl().GetChildren():
        try:
            p = w.ProcessId
        except Exception:
            continue
        if pid is not None:
            if p == pid:
                out.append(w)
            continue
        if _pname(p) not in APP_EXE:
            continue
        if not every and (getattr(w, "ClassName", "") or "") in SKIP_CLS:
            continue
        out.append(w)
    return out


def walk(ctrl, depth: int = 0, rows: list | None = None, limit: int = MAX_DEPTH,
         budget: int = 20000) -> list:
    """Every node under ctrl as a flat list of dicts. Depth-limited, failure-tolerant."""
    rows = [] if rows is None else rows
    if len(rows) >= budget:
        return rows
    try:
        r = ctrl.BoundingRectangle
        rect = [r.left, r.top, r.right, r.bottom]
    except Exception:
        rect = None
    try:
        rows.append({
            "d": depth,
            "type": ctrl.ControlTypeName,
            "cls": getattr(ctrl, "ClassName", "") or "",
            "id": getattr(ctrl, "AutomationId", "") or "",
            "name": (ctrl.Name or "")[:400],
            "rect": rect,
        })
    except Exception:
        return rows
    if depth >= limit:
        return rows
    try:
        kids = ctrl.GetChildren()
    except Exception:
        kids = []
    for k in kids:
        walk(k, depth + 1, rows, limit, budget)
    return rows


HINT = re.compile(r"caption|subtitle|transcri|closed.?cc|\u5b57\u5e55", re.I)
TEXTY = ("TextControl", "ListItemControl", "DocumentControl", "EditControl", "StaticTextControl")
# words that sit in front of a colon on screen but are nobody
STOP = {"note", "warning", "chat", "everyone", "to", "from", "host", "me", "you",
        "topic", "time", "meeting id", "passcode", "\u5907\u6ce8", "\u804a\u5929"}


def _texty(row: dict) -> bool:
    return row["type"] in TEXTY and len(row["name"].strip()) >= MIN_TEXT


def scan_text(root, depth: int = 0, limit: int = MAX_DEPTH, hint=None,
              out: list | None = None, path: str = "", state: dict | None = None) -> list[dict]:
    """The text under one control, two property reads per node instead of five.

    Carries the nearest ancestor whose class or id smells of captions, so the follower can
    stop walking the whole window once it knows where the panel is.
    """
    out = [] if out is None else out
    state = {"n": 0} if state is None else state
    state["n"] += 1
    if state["n"] > BUDGET:                    # a tree this big is not a caption panel
        return out
    try:
        t = root.ControlTypeName
        name = root.Name or ""
    except Exception:
        return out
    try:
        tag = (getattr(root, "ClassName", "") or "") + "|" + (getattr(root, "AutomationId", "") or "")
    except Exception:
        tag = ""
    if hint is None and HINT.search(tag):
        hint = root
    if t in TEXTY and len(name.strip()) >= MIN_TEXT:
        try:
            r = root.BoundingRectangle
            key = f"{t}@{r.left},{r.top}"
        except Exception:
            key = f"{t}{path}"
        out.append({"type": t, "name": name[:600], "key": key, "hint": hint, "parent": root})
    if depth >= limit:
        return out
    if depth and hint is None and SKIP_SUB.search(tag):
        return out                             # chat, sidebar, threads: text, but not speech
    try:
        kids = root.GetChildren()
    except Exception:
        kids = []
    for i, k in enumerate(kids):
        scan_text(k, depth + 1, limit, hint, out, f"{path}/{i}", state)
    return out


def _speechy(t: str) -> bool:
    t = t.strip()
    return bool(split(t)[0]) or (len(t) >= 25 and " " in t)


class Reader:
    """Reads the client's caption text, and gets faster once it has found the panel."""

    def __init__(self, pid: int | None = None, every: bool = False):
        self.pid = pid
        self.every = every
        self._wins: list = []
        self._wins_t = 0.0
        self.panel = None            # the locked-on subtree
        self.panel_t = 0.0
        self.full_t = 0.0
        self.last: dict[str, str] = {}
        self.hits: dict[str, int] = {}
        self.cand: dict[str, object] = {}
        self.locked_by = ""
        self.lock_key = ""

    def wins(self) -> list:
        if not self._wins or time.time() - self._wins_t > 20:
            try:
                self._wins = windows(self.pid, self.every)
            except Exception:
                self._wins = []
            self._wins_t = time.time()
        return self._wins

    def _lock(self, rows: list[dict]) -> None:
        """A container that says it holds captions wins outright. Otherwise the one whose
        text keeps changing into something speech-shaped, and only after it has done so
        three times - a clock or an agenda line changes too, just far more slowly."""
        for r in rows:
            if r["hint"] is not None:
                if self.locked_by != "name":
                    self.panel, self.locked_by, self.lock_key = r["hint"], "name", r["key"]
                    self.panel_t = time.time()
                return
        for r in rows:
            prev = self.last.get(r["key"])
            if prev is not None and prev != r["name"] and _speechy(r["name"]):
                self.hits[r["key"]] = self.hits.get(r["key"], 0) + 1
                self.cand[r["key"]] = r["parent"]
        if not self.hits:
            return
        best = max(self.hits, key=lambda k: self.hits[k])
        if (self.hits[best] >= 3 and best != self.lock_key
                and self.hits[best] >= self.hits.get(self.lock_key, 0) + 2):
            self.panel, self.locked_by, self.lock_key = self.cand[best], "change", best
            self.panel_t = time.time()

    def texts(self) -> list[dict]:
        now = time.time()
        due = now - self.full_t > FULL_EVERY                    # look around again now and then
        if self.panel is not None and not due:
            rows = scan_text(self.panel)
            if rows:
                self.panel_t = now
                self.last.update({r["key"]: r["name"] for r in rows})
                return rows
            if now - self.panel_t < 15:
                return []
            self.panel, self.locked_by, self.lock_key = None, "", ""   # panel closed
        rows = []
        for w in self.wins():
            try:
                if w.BoundingRectangle.width() <= 0:            # minimised: nothing to read
                    continue
            except Exception:
                continue
            scan_text(w, out=rows)
        self.full_t = time.time()
        self._lock(rows)
        self.last = {r["key"]: r["name"] for r in rows}
        return rows


def read_texts(pid: int | None = None, every: bool = False) -> list[dict]:
    """Every readable piece of text in Zoom's windows right now. One shot, for --probe."""
    return Reader(pid, every).texts()


def split(text: str) -> tuple[str, str]:
    """Pull a speaker off the front of a caption line. Empty name when there is none."""
    t = " ".join(text.split())
    m = STAMPED.match(t)
    if m and not m.group(1).endswith((".", "?", "!", "。", "？")):
        return m.group(1).strip(), m.group(3).strip()
    m = NAMED.match(t)
    if m:
        who = m.group(1).strip()
        # "Note: we agreed" is not a person; a name has no sentence punctuation in it
        if (who and len(who.split()) <= 5 and who.lower() not in STOP
                and not re.search(r"[.?!。，,]", who)):
            return who, m.group(2).strip()
    return "", t


class Follower:
    """Turns a stream of redrawn caption text into finished lines, once each."""

    def __init__(self, out: Path):
        self.out = out
        self.pending: dict[str, dict] = {}                      # node key -> growing line
        self.seen: set[tuple[str, str]] = set()
        self.recent: list[tuple[str, str]] = []
        self.n = 0

    def _emit(self, rec: dict) -> None:
        sig = (rec["speaker"], rec["text"])
        if sig in self.seen:
            return
        for who, txt in self.recent:
            if who == rec["speaker"] and txt.startswith(rec["text"]):
                return                                          # a shorter cut of a line we have
        self.seen.add(sig)
        self.recent.append(sig)
        del self.recent[:-12]
        self.n += 1
        with self.out.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def offer(self, key: str, text: str, now: float) -> None:
        text = " ".join(text.split())
        if len(text) < MIN_TEXT:
            return
        p = self.pending.get(key)
        if p and (text.startswith(p["raw"]) or p["raw"].startswith(text)):
            if len(text) > len(p["raw"]):
                p["raw"], p["t_end"] = text, now
            return
        if p:
            self.flush(key, now)
        self.pending[key] = {"raw": text, "t0": now, "t_end": now}

    def flush(self, key: str, now: float) -> None:
        p = self.pending.pop(key, None)
        if not p:
            return
        who, body = split(p["raw"])
        self._emit({"t_wall": round(p["t0"], 3), "t_end": round(p["t_end"], 3),
                    "speaker": who, "text": body, "raw": p["raw"]})

    def settle(self, now: float) -> None:
        for key in [k for k, p in self.pending.items() if now - p["t_end"] >= SETTLE]:
            self.flush(key, now)

    def close(self, now: float) -> None:
        for key in list(self.pending):
            self.flush(key, now)


def probe(pid: int | None = None, every: bool = False) -> dict:
    """One look: is a client up, is a window readable, does anything look like a caption?"""
    t0 = time.time()
    wins = []
    for w in windows(pid, every):
        try:
            r = w.BoundingRectangle
            wins.append({"app": APP_EXE.get(_pname(w.ProcessId), "?"),
                         "cls": getattr(w, "ClassName", ""), "name": (w.Name or "")[:120],
                         "w": r.width(), "h": r.height()})
        except Exception:
            pass
    texts = read_texts(pid, every)
    named = [t for t in texts if split(t["name"])[0]]
    skipped = [(getattr(w, "ClassName", "") or "") for w in windows(pid, True)
               if (getattr(w, "ClassName", "") or "") in SKIP_CLS] if not every else []
    return {
        "app_windows": len(wins),
        "apps": sorted({w["app"] for w in wins}),
        "windows": wins[:12],
        "text_nodes": len(texts),
        "named_lines": len(named),
        "sample": [t["name"][:120] for t in texts[:15]],
        "named_sample": [t["name"][:120] for t in named[:8]],
        "skipped_windows": skipped,
        "read_s": round(time.time() - t0, 2),
    }


def follow(ses: Path, seconds: float | None, pid: int | None = None,
           feed: Path | None = None, every: bool = False) -> int:
    out = ses / "captions.jsonl"
    meta = ses / "captions.meta.json"
    f = Follower(out)
    t_start = time.time()
    meta.write_text(json.dumps({"t0_wall": t_start, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "source": "feed" if feed else "zoom-uia"},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    quiet = 0
    if feed:
        for i, line in enumerate(feed.read_text(encoding="utf-8").splitlines()):
            f.offer("feed", line, t_start + i * 0.5)
            f.settle(t_start + i * 0.5)
        f.close(t_start + 999)
        print(f"{f.n} 行字幕 -> {out.name}")
        return 0
    print(f"跟着 Zoom 字幕记（{POLL}s 一次），Ctrl-C 停")
    done = ses / "session.json"        # written when the recording stops; our cue to stop too
    rd = Reader(pid, every)
    said_panel = False
    try:
        while True:
            now = time.time()
            if seconds and now - t_start >= seconds:
                break
            if done.exists():
                print("录音结束了，字幕也停")
                break
            try:
                rows = rd.texts()
            except Exception as e:
                rows = []
                print("读窗口失败：" + str(e)[:120])
            for r in rows:
                f.offer(r["key"], r["name"], now)
            if rd.panel is not None and not said_panel:
                said_panel = True
                print(f"\u627e\u5230\u5b57\u5e55\u9762\u677f\u4e86\uff08{rd.locked_by}\uff09")
            f.settle(now)
            quiet = quiet + 1 if not rows else 0
            if quiet in (20, 100):
                print("还没看到任何字幕文字：Zoom 的字幕面板开着吗？窗口是不是最小化了？")
            time.sleep(POLL)
    except KeyboardInterrupt:
        pass
    f.close(time.time())
    print(f"{f.n} 行字幕 -> {out.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Zoom 字幕 -> 谁在什么时候说话")
    ap.add_argument("session", nargs="?", help="session folder")
    ap.add_argument("--probe", action="store_true", help="one look, print JSON, exit")
    ap.add_argument("--tree", action="store_true", help="dump the accessibility tree")
    ap.add_argument("--out", help="where --tree writes (default sessions/uia-tree.txt)")
    ap.add_argument("--pid", type=int, help="read this process instead of Zoom")
    ap.add_argument("--for", dest="secs", type=float, help="stop after N seconds")
    ap.add_argument("--feed", help="replay a text file instead of Zoom (a test)")
    ap.add_argument("--depth", type=int, default=MAX_DEPTH)
    ap.add_argument("--all-windows", dest="every", action="store_true",
                    help="\u8fde Zoom \u7684\u4e3b\u7a97\u53e3\u548c\u6d88\u606f\u6761\u4e5f\u8bfb\uff08\u6392\u67e5\u7528\uff09")
    a = ap.parse_args(argv)

    if a.probe:
        print(json.dumps(probe(a.pid, a.every), ensure_ascii=False, indent=1))
        return 0

    if a.tree:
        dest = Path(a.out) if a.out else HERE.parent / "sessions" / "uia-tree.txt"
        dest.parent.mkdir(parents=True, exist_ok=True)
        wins = windows(a.pid, True)
        if not wins:
            print("没找到 Zoom 的窗口（Zoom 开着吗？）" if a.pid is None
                  else f"pid {a.pid} 没有窗口")
            return 1
        lines = []
        for w in wins:
            lines.append("=" * 70)
            for r in walk(w, limit=a.depth):
                lines.append(f"{'  ' * r['d']}{r['type']:<18} cls={r['cls']!r:<26} "
                             f"id={r['id']!r:<20} {r['name']!r}")
        dest.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        print(f"{len(wins)} 个窗口，{len(lines)} 行 -> {dest}")
        return 0

    if not a.session:
        ap.error("给我一个 session 文件夹，或者用 --probe / --tree")
    ses = Path(a.session)
    if not ses.is_dir():
        print(f"no such session: {ses}")
        return 2
    return follow(ses, a.secs, a.pid, Path(a.feed) if a.feed else None, a.every)


if __name__ == "__main__":
    raise SystemExit(main())

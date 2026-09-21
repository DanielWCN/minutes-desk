"""
Zoom's captions, laid over the voice clusters: cluster 4 is whoever Zoom was naming then.

diarize.py says "these 158 turns came from 23 different voices" and cannot know a name.
whois.py works the names out of what people call each other, which is clever but is still
inference. When captions were running, there is no inference left to do: Zoom printed the
name of whoever was speaking, straight off the meeting roster, at a known moment. Lay the
two timelines on top of each other and every cluster that overlaps one name and no other
is settled.

The one hard part is the offset. A caption carries a wall clock, a diarization turn carries
seconds since the recording started, and the gap between those two is not a constant you
can look up: the recorder starts a moment before the caption reader, captions lag speech by
a second or two, and a paused recording shifts everything after it. So the offset is not
assumed, it is measured - try every shift in a wide range and keep the one that makes the
two timelines agree best, which is the shift where each cluster overlaps one name instead
of a smear of five. A shift that only wins by a hair wins nothing: the answer is then "the
captions do not line up", and naming falls back to whois.py.

Text is not taken from here. Zoom's caption text is rough and we already have a better
transcript; only the names and the clock are used.

Usage:
    zmatch.py <session>              write captions.match.json
    zmatch.py <session> --me "Name"  drop caption lines that are the microphone (you)
    zmatch.py <session> --show       print the table and write nothing
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from bisect import bisect_left, bisect_right
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent

GRID = 120.0          # seconds of shift to try either way; a paused meeting can be far off
STEP = 1.0            # coarse search, then refined at a tenth of this
PAD = 0.6             # a caption line is a little wider than the words it carries
MIN_OVERLAP = 4.0     # a cluster with less than this against a name is not evidence
MIN_SHARE = 0.6       # and the winning name has to own this much of the cluster's overlap
PEAK = 1.10           # the right shift is a sharp peak: it beats everything 5s away by this
SPREAD = 1.20         # and it stands above the run of the mill shifts by this


def _anchor(ses: Path) -> tuple[float, str]:
    """The wall-clock epoch of audio second zero, as well as we can know it up front."""
    try:
        d = json.loads((ses / "session.json").read_text(encoding="utf-8"))
        s = str(d.get("started_local") or "")
        if s:
            return datetime.fromisoformat(s).timestamp(), "session.json"
    except Exception:                                          # noqa: BLE001
        pass
    try:
        d = json.loads((ses / "captions.meta.json").read_text(encoding="utf-8"))
        return float(d["t0_wall"]), "captions.meta.json"
    except Exception:                                          # noqa: BLE001
        return 0.0, ""


def load_lines(ses: Path, me: str = "") -> tuple[list[dict], int]:
    """Caption lines that carry a name and are not you. Returns (lines, your line count)."""
    f = ses / "captions.jsonl"
    out, mine = [], 0
    if not f.exists():
        return out, 0
    me_low = (me or "").strip().lower()
    for row in f.read_text(encoding="utf-8").splitlines():
        row = row.strip()
        if not row:
            continue
        try:
            d = json.loads(row)
        except Exception:                                      # noqa: BLE001
            continue
        who = (d.get("speaker") or "").strip()
        if not who:
            continue
        if me_low and (who.lower() == me_low or who.lower() in me_low.split()):
            mine += 1
            continue
        try:
            a, b = float(d["t_wall"]), float(d.get("t_end") or d["t_wall"])
        except Exception:                                      # noqa: BLE001
            continue
        out.append({"who": who, "a": a, "b": max(b, a + 0.8)})
    return out, mine


def _turns(ses: Path) -> list[dict]:
    try:
        d = json.loads((ses / "diarization.json").read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return []
    return [t for t in (d.get("turns") or []) if t.get("cluster") is not None]


def score(lines: list[dict], turns: list[dict], anchor: float, delta: float) -> dict:
    """Seconds of overlap between each cluster and each name, for one candidate shift."""
    starts = [t["start"] for t in turns]
    ov: dict[int, dict[str, float]] = {}
    for ln in lines:
        a = ln["a"] - anchor - delta - PAD
        b = ln["b"] - anchor - delta + PAD
        i = max(0, bisect_left(starts, a) - 2)
        j = bisect_right(starts, b) + 2
        for t in turns[i:j]:
            lo, hi = max(a, t["start"]), min(b, t["end"])
            if hi > lo:
                ov.setdefault(int(t["cluster"]), {}).setdefault(ln["who"], 0.0)
                ov[int(t["cluster"])][ln["who"]] += hi - lo
    total = sum(max(names.values()) for names in ov.values()) if ov else 0.0
    return {"overlap": ov, "total": total}


def match(ses: Path, me: str = "") -> dict:
    lines, mine = load_lines(ses, me)
    turns = _turns(ses)
    anchor, src = _anchor(ses)
    out = {"lines": len(lines), "your_lines": mine, "turns": len(turns),
           "anchor": anchor, "anchor_from": src, "clusters": {}, "offset_s": None,
           "note": "名字来自 Zoom 自己的字幕，按时间对上声音聚类；文字不取自字幕。"}
    if not lines or not turns or not anchor:
        out["error"] = ("没有带名字的字幕行" if not lines else
                        "没有 diarization.json" if not turns else "不知道录音的起始时刻")
        return out

    grid = [(-GRID + i * STEP) for i in range(int(2 * GRID / STEP) + 1)]
    tried = [(d, score(lines, turns, anchor, d)["total"]) for d in grid]
    best_d, best_t = max(tried, key=lambda x: x[1])
    # A real alignment is a peak, not a high plateau: half a minute of one person talking
    # overlaps something at almost any shift, so "more overlap than average" proves nothing.
    # What only happens at the true shift is that moving five seconds away makes it worse.
    far = max([t for d, t in tried if abs(d - best_d) > 5.0] or [0.0])
    run = statistics.quantiles([t for _, t in tried], n=10)[8] if len(tried) > 10 else best_t
    fine = [best_d + k * (STEP / 10) for k in range(-10, 11)]
    best_d, best_t = max([(d, score(lines, turns, anchor, d)["total"]) for d in fine],
                         key=lambda x: x[1])
    out.update({"offset_s": round(best_d, 2), "agree_s": round(best_t, 1),
                "peak": round(best_t / far, 3) if far else None,
                "spread": round(best_t / run, 2) if run else None})
    if best_t < MIN_OVERLAP or (far and best_t < far * PEAK) or (run and best_t < run * SPREAD):
        out["error"] = "字幕和音频时间轴对不上（可能录音中途暂停过）"
        return out

    ov = score(lines, turns, anchor, best_d)["overlap"]
    for cid, names in sorted(ov.items()):
        tot = sum(names.values())
        who, sec = max(names.items(), key=lambda x: x[1])
        share = sec / tot if tot else 0.0
        row = {"name": who, "overlap_s": round(sec, 1), "share": round(share, 2),
               "sure": bool(sec >= MIN_OVERLAP and share >= MIN_SHARE)}
        rest = sorted(((n, s) for n, s in names.items() if n != who),
                      key=lambda x: -x[1])[:2]
        if rest:
            row["also"] = [[n, round(s, 1)] for n, s in rest]
        out["clusters"][str(cid)] = row
    out["sure"] = sum(1 for r in out["clusters"].values() if r["sure"])
    return out


def load(ses: Path) -> dict:
    """What a previous run decided; {} when there is nothing usable."""
    try:
        d = json.loads((ses / "captions.match.json").read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {}
    return d if isinstance(d, dict) and d.get("clusters") and not d.get("error") else {}


def sure_names(ses: Path) -> dict[int, str]:
    """cluster id -> name, only where the captions are unambiguous."""
    d = load(ses)
    return {int(c): r["name"] for c, r in (d.get("clusters") or {}).items() if r.get("sure")}


def main() -> int:
    ap = argparse.ArgumentParser(description="Zoom 字幕 -> 哪个聚类是谁")
    ap.add_argument("session")
    ap.add_argument("--me", default="", help="你自己的名字（字幕里你的行不参与匹配）")
    ap.add_argument("--show", action="store_true", help="只打印，不写文件")
    a = ap.parse_args()
    ses = Path(a.session)
    if not ses.is_dir():
        print(f"no such session: {ses}")
        return 2
    r = match(ses, a.me)
    if r.get("error"):
        print("字幕没能用上：" + r["error"])
        if not a.show and (ses / "captions.jsonl").exists():
            (ses / "captions.match.json").write_text(
                json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        return 1
    print(f"{r['lines']} 行字幕（你自己 {r['your_lines']} 行），偏移 {r['offset_s']}s，"
          f"{r['sure']}/{len(r['clusters'])} 个声音对上了名字")
    for cid, row in r["clusters"].items():
        flag = "  " if row["sure"] else " ?"
        print(f" {flag}C{cid:>3}  {row['overlap_s']:6.1f}s  {row['share']:.2f}  {row['name']}")
    if not a.show:
        (ses / "captions.match.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"-> {(ses / 'captions.match.json').name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

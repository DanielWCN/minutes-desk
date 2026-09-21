"""
Which voice is which person.

diarize.py can only say "these turns came from 27 different voices". Clustering never knows
a name; it knows that voice 5 is not voice 8. The names are in what was said, because in a
meeting people call each other by name constantly: someone says "Sam" and the next voice
to speak is Sam, someone says "Ravi, about the weekly upload" and the voice being
answered is Ravi. So this step hands the model a transcript whose lines carry cluster ids,
plus the attendee list that was ticked on the confirm desk, and asks which cluster is who.

Two independent signals that check each other: the voice decides which lines belong
together, the words decide whose they are. A cluster the model cannot place stays
"Speaker 7". A plausible name on the wrong turn is the one mistake that makes minutes worse
than no minutes at all, so "I don't know" has to be a permitted answer, and it is: an empty
name is expected output, not a failure.

No voiceprint is stored, ever. Clusters live and die with one meeting - recognising a
colleague across meetings would mean keeping biometric data, and this tool does not.

Usage:
    whois.py <session>              name the clusters, write speakers.json
    whois.py <session> --print      print the evidence and exit (no model call)
    whois.py <session> --min 12     raise the "too short to judge" cut (default 8s)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import llm                                                     # noqa: E402
import zmatch                                                  # noqa: E402

MIN_TALK = 8.0        # a cluster with less than this is a cough, a "Right." or crosstalk
OVERLAP_OK = 0.6      # same cut as build.apply_speakers: below it, two people are talking
# what build.apply_speakers leaves on a far-end line it has already split by voice:
# "Speaker 4", or "Speaker 4?" when the overlap was too weak to be sure
SPLIT = re.compile(r"^Speaker \d+\??$")
LINE_CAP = 400        # one line of evidence never needs to be longer than this
TOTAL_CAP = 200_000   # ~60k tokens of transcript is already far more than any real meeting

SPEC = """你要判断的是「哪个声音是哪个人」。

材料是一场会议的逐字稿。每行开头的 [C5] 是声音编号：同一个编号是同一个声音，但编号本身不带
任何身份信息。[YOU] 是录音的人自己。行末带 ~ 的，说明那一刻有人抢话、归属本身就不可靠，只能
当弱证据。

唯一的依据是会上人们互相怎么称呼。有人喊一声 "Sam"，紧接着某个编号开口，那个编号很可能就是
Sam。有人说 "thanks Lin" 或 "Ravi, about the weekly upload"，被称呼的往往是上一个或
下一个说话的编号。自我介绍（"this is Alex"、"我是小王"）是最强的证据。语音识别会把名字拼错，
读音接近就算（Robby / Ravee / Ravi 是同一个人）。

规则：
1. 名字只能从下面的参会名单里挑，不许创造名单外的人。
2. 拿不准就把 name 写成空字符串。名字贴错人比留着 Speaker 编号糟糕得多，空着是正常答案。
3. 同一个人可能被切成两个编号（声音相似度不够），所以两个编号给同一个名字是允许的。
4. confidence 只有两档：high = 有明确的称呼或自我介绍支撑；low = 只是推测。
5. why 用一句话说明依据，把原话里的那几个词引出来。

只输出 JSON，不要解释，不要代码围栏：
{"5": {"name": "Ravi Kumar", "confidence": "high", "why": "10:22 有人对他说 'Robby, about the weekly upload'"},
 "8": {"name": "", "confidence": "low", "why": "全场没有任何称呼指向这个声音"}}"""


def _load(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return default


def roster(ses: Path) -> list[str]:
    """Who was in the meeting, as confirmed on the confirm desk."""
    meta = _load(ses / "session.json", {}) or {}
    raw = (meta.get("others") or "").replace(",", ";")
    return [x.strip() for x in raw.split(";") if x.strip()]


def label_lines(ses: Path, fallback: str = "Others") -> tuple[list[dict], dict]:
    """Attach a cluster id to every far-end line, the same way build.py will.

    Returns the lines and the per-cluster talk time. The overlap rule is deliberately the
    same as apply_speakers(): the evidence the model reads has to be the labelling that
    ends up in the transcript, or it is reasoning about a different document.

    A transcript that has already been through one build carries the cluster on every
    far-end line and says "Speaker 4" where it used to say "Others". Those still have to
    count as far-end, or naming reads the whole meeting as the microphone track, tags every
    line [YOU], and then reports that no cluster was ever addressed by name - which is
    exactly what it did before this was fixed.
    """
    d = _load(ses / "diarization.json", {}) or {}
    turns = d.get("turns") or []
    tr = _load(ses / "transcript.json", {}) or {}
    lines = tr.get("lines") or []
    out = []
    for s in lines:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        row = {"start": float(s.get("start") or 0.0), "text": text[:LINE_CAP],
               "cluster": None, "sure": True}
        done = s.get("cluster")
        if done is not None:
            row["cluster"] = int(done)
            row["sure"] = (s.get("speaker_confidence") or "") != "low"
            out.append(row)
            continue
        who = s.get("speaker") or ""
        if who != fallback and not SPLIT.match(who):
            row["you"] = True
            out.append(row)
            continue
        a, b = float(s.get("start") or 0.0), float(s.get("end") or 0.0)
        span = max(1e-6, b - a)
        best, ov = None, 0.0
        for t in turns:
            o = max(0.0, min(b, float(t["end"])) - max(a, float(t["start"])))
            if o > ov:
                best, ov = int(t["cluster"]), o
        row["cluster"] = best
        row["sure"] = bool(best is not None and ov / span >= OVERLAP_OK)
        out.append(row)
    talk = {int(k): float(v) for k, v in (d.get("talk_time_s") or {}).items()}
    return out, talk


def evidence(ses: Path, lines: list[dict], talk: dict, me: str,
             min_talk: float = MIN_TALK) -> dict:
    """The question, as a prompt. `ask` is the clusters worth asking about."""
    people = roster(ses)
    ask = sorted((c for c, s in talk.items() if s >= min_talk), key=lambda c: -talk[c])
    who = "\n".join(f"- {p}" for p in people) or "- （名单是空的）"
    heads = "\n".join(f"- C{c}: {talk[c]:.0f} 秒" for c in ask)
    body = []
    for r in lines:
        mm, ss = int(r["start"]) // 60, int(r["start"]) % 60
        tag = "YOU" if r.get("you") else (f"C{r['cluster']}" if r["cluster"] is not None
                                          else "C?")
        body.append(f"{mm:02d}:{ss:02d} [{tag}] {r['text']}"
                    + ("" if r["sure"] else "  ~"))
    txt = "\n".join(body)
    if len(txt) > TOTAL_CAP:
        txt = txt[:TOTAL_CAP] + "\n（后面截断了）"
    user = (f"# 参会名单\n\n- {me}（就是 [YOU]，不用判断）\n{who}\n\n"
            f"# 需要你判断的声音（按说话时长排序）\n\n{heads}\n\n"
            f"# 逐字稿\n\n{txt}\n")
    one = ("下面是一份判断说话人的规范，以及一场会的材料。请按规范只输出 JSON。\n\n"
           "=============== 规范 ===============\n" + SPEC
           + "\n\n=============== 材料 ===============\n" + user)
    return {"system": SPEC, "user": user, "one": one, "ask": ask, "people": people,
            "chars": len(one), "tokens_est": int(len(one) / 3.2)}


def parse(reply: str) -> dict:
    """The model's JSON, however it chose to wrap it."""
    t = (reply or "").strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        d = json.loads(t[i:j + 1])
    except Exception:                                          # noqa: BLE001
        return {}
    return d if isinstance(d, dict) else {}


def match_name(name: str, people: list[str]) -> str:
    """A name the model returned, resolved to exactly one person on the roster, or "".

    First names are how people are addressed out loud, so "Ravi" has to resolve to
    "Ravi Kumar" - but only while it is unambiguous. Two Ravis on the roster and the
    honest answer is that we do not know which one.
    """
    n = (name or "").strip()
    if not n:
        return ""
    low = n.lower()
    exact = [p for p in people if p.lower() == low]
    if exact:
        return exact[0]
    tok = [p for p in people if any(w.lower() == low for w in p.split())]
    if len(tok) == 1:
        return tok[0]
    part = [p for p in people if low in p.lower()]
    return part[0] if len(part) == 1 else ""


def decide(ses: Path, cfg: dict, min_talk: float = MIN_TALK,
           timeout: float = 900.0) -> dict:
    if not (ses / "diarization.json").exists():
        return {"ok": False, "error": "没有 diarization.json，先跑 diarize.py"}
    if not (ses / "transcript.json").exists():
        return {"ok": False, "error": "没有 transcript.json，先跑一次语音识别"}
    lines, talk = label_lines(ses)
    ev = evidence(ses, lines, talk, cfg.get("me") or "You", min_talk)
    if not ev["ask"]:
        return {"ok": False, "error": f"没有说话超过 {min_talk:.0f} 秒的声音，不值得判断"}
    if not ev["people"]:
        return {"ok": False, "error": "参会名单是空的，先在确认台把与会人勾上"}
    # What Zoom's own captions already settled. These are not inference: Zoom printed a roster
    # name while that voice was talking, so they outrank anything the model works out below,
    # and the clusters they cover are not even shown to it.
    cap = {}
    for cid, who in zmatch.sure_names(ses).items():
        if cid in set(ev["ask"]):
            cap[cid] = match_name(who, ev["people"]) or who
    ask = [c for c in ev["ask"] if c not in cap]
    t0 = time.time()
    got: dict = {}
    raw = ""
    if ask:
        try:
            if (cfg.get("engine") or "assistant") == "assistant":
                if not llm.can_auto(cfg):
                    if not cap:
                        return {"ok": False,
                                "error": "当前引擎要手工粘贴，说话人判断只在能自动调用时做"}
                    raw = ""                                   # captions carry it on their own
                else:
                    raw = llm.chat_cli(ev["one"], timeout=timeout, cwd=str(ses))
            else:
                raw = llm.chat(cfg, ev["system"], ev["user"], timeout=min(timeout, 600.0))
        except Exception as exc:                               # noqa: BLE001
            if not cap:
                return {"ok": False, "error": str(exc)[:800]}
            raw = ""
        if raw:
            got = parse(raw)
            if not got and not cap:
                return {"ok": False, "error": "模型没给出可解析的 JSON",
                        "raw": raw[:600]}

    clusters, named, guessed, from_cap = {}, 0, 0, 0
    for cid in ev["ask"]:
        if cid in cap:
            clusters[str(cid)] = {
                "speaker": cap[cid], "confidence": "high", "cluster": int(cid),
                "talk_time_s": round(talk[cid], 1), "guess": cap[cid], "from": "caption",
                "why": "Zoom 字幕在这个时间显示的就是这个名字",
            }
            named += 1
            from_cap += 1
            continue
        g = got.get(str(cid)) or got.get(cid) or {}
        raw_name = (g.get("name") or "").strip() if isinstance(g, dict) else ""
        why = (g.get("why") or "").strip() if isinstance(g, dict) else ""
        conf = (g.get("confidence") or "").strip().lower() if isinstance(g, dict) else ""
        name = match_name(raw_name, ev["people"])
        if raw_name and not name:
            why = f"名单里对不上「{raw_name}」；{why}"
            conf = "low"
        if not name:
            row = {"speaker": f"Speaker {cid}", "confidence": "unknown"}
        elif conf == "high":
            row = {"speaker": name, "confidence": "high"}
            named += 1
        else:
            row = {"speaker": f"Speaker {cid} ({name}?)",
                   "speaker_full": f"Speaker {cid}（推测：{name}，置信度低）",
                   "confidence": "low"}
            guessed += 1
        row.update({"cluster": int(cid), "talk_time_s": round(talk[cid], 1),
                    "guess": name, "why": why[:300]})
        clusters[str(cid)] = row

    minor = {c: s for c, s in talk.items() if s < min_talk}
    caps = zmatch.load(ses)
    out = {
        "track": "others.wav",
        "min_talk_s": min_talk,
        "from_captions": from_cap,
        "captions": ({"lines": caps.get("lines"), "offset_s": caps.get("offset_s"),
                      "sure": caps.get("sure")} if caps else None),
        "num_clusters": len(clusters),
        "named": named, "guessed": guessed,
        "unknown": len(clusters) - named - guessed,
        "minor": {"count": len(minor), "talk_time_s": round(sum(minor.values()), 1)},
        "engine": ("caption" if from_cap and not got else cfg.get("engine") or "assistant"),
        "took_s": round(time.time() - t0, 1),
        "clusters": clusters,
        "note": ("名字来自会上互相的称呼，不是声纹比对：声音只决定哪些话是同一个人说的。"
                 "声纹用完即弃，没有留在硬盘上。"),
    }
    (ses / "speakers.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    out["ok"] = True
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="name the diarization clusters")
    ap.add_argument("session")
    ap.add_argument("--print", dest="show", action="store_true",
                    help="print the evidence prompt and exit, no model call")
    ap.add_argument("--min", type=float, default=MIN_TALK,
                    help=f"ignore clusters that talked less than this (default {MIN_TALK}s)")
    args = ap.parse_args()

    import config                                              # noqa: PLC0415
    cfg = config.load()
    ses = Path(args.session)
    if not ses.is_dir():
        print(f"no such session: {ses}")
        return 2
    if args.show:
        lines, talk = label_lines(ses)
        ev = evidence(ses, lines, talk, cfg.get("me") or "You", args.min)
        sys.stdout.write(ev["one"])
        return 0
    r = decide(ses, cfg, args.min)
    if not r.get("ok"):
        print("说话人判断没做成：" + str(r.get("error")))
        return 1
    print(f"{r['num_clusters']} 个声音：{r['named']} 个认出来了，"
          f"{r['guessed']} 个是推测，{r['unknown']} 个不知道"
          f"（另有 {r['minor']['count']} 个碎片声音共 {r['minor']['talk_time_s']}s 未参与判断）"
          f" · {r['took_s']}s")
    for cid, c in sorted(r["clusters"].items(), key=lambda kv: -kv[1]["talk_time_s"]):
        print(f"  C{cid:>3}  {c['talk_time_s']:6.1f}s  [{c['confidence']:>7}]  "
              f"{c['speaker']}   {c['why'][:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

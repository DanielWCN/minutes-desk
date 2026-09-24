"""Turn a walkthrough into an operating manual: pair every sentence with the screen it was
said in front of, then have the assistant write the steps from that pairing.

Why the pairing and not the pictures alone. frames.py keeps a picture whenever the desktop
changes enough, which is the right rule for a meeting and the wrong one for a manual: the
changes a manual cares about are small on screen and large in meaning. Filling a field,
ticking a box, a comment appearing in a dialog - measured on the sample walkthrough, the
whole-desktop metric misses two of ten such screens even at a twentieth of its default
threshold. The narration does not miss them, because the person says what they are doing.
So the manual is sampled by speech: one shot per sentence, taken a moment AFTER it was
said (the action has landed by then), deduplicated so a run of sentences on one screen
cites one picture.

That also makes the audio-only case fall out for free. Someone describing a procedure with
no screen at all ("open the link, look at column C, then compare it against...") produces
the same timeline minus the shots, and the manual quotes them instead of showing pictures.

    python sop.py <session> [--print-prompt | --draft] [--shots-only] [--no-report]

Writes into the session:
    shots/s###_HHMMSS.png   one picture per distinct screen state that was talked about
    shots/shots.json        which sentence each shot belongs to
    sop.timeline.md         the pairing, readable - this is the evidence the manual cites
    sop.md                  the manual (--draft; front matter + steps)
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

import build            # noqa: E402  glossary fixers, segment loading
import llm              # noqa: E402  the assistant plumbing

try:
    import lexicon      # noqa: E402
except Exception:       # noqa: BLE001
    lexicon = None

SHOT_LAG = 0.6          # seconds after a sentence ends: long enough for the click to land
DEDUP = 0.0015          # fraction of pixels that must change for a new picture to be kept
THUMB = 160             # the grid that fraction is measured on


def hhmmss(t: float) -> str:
    return f"{int(t // 3600):02d}{int(t % 3600 // 60):02d}{int(t % 60):02d}"


def clk(t: float) -> str:
    return f"{int(t // 3600):d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"


# --------------------------------------------------------------------------- the sentences
def read_segments(ses: Path, me: str = "") -> list[dict]:
    """Sentence-level lines with times, spelt the way the glossary says.

    transcript.json is not the source here: build.py merges consecutive turns into
    paragraphs, and a manual needs to know which sentence went with which screen. The
    per-track segment files keep that granularity, so they get the same glossary pass
    build.py would have given them.
    """
    gloss = lexicon.merged() if lexicon else build.load_glossary(HERE / "glossary.base.json")
    fixers = build.build_fixers(gloss)
    segs = (build.load_track(ses / "mic.segments.json", me or "You")
            + build.load_track(ses / "others.segments.json", "Others"))
    out = []
    for s in sorted(segs, key=lambda s: float(s.get("start") or 0)):
        text = (s.get("text") or "").strip()
        if not text:
            continue
        text, _ = build.apply_glossary(text, fixers)
        out.append({"start": round(float(s.get("start") or 0), 2),
                    "end": round(float(s.get("end") or 0), 2),
                    "speaker": s.get("speaker") or "?",
                    "lang": s.get("lang") or "",
                    "text": text})
    return out


def language(segs: list[dict]) -> str:
    """Which language the manual should be written in: the one most of it was said in."""
    tally: dict[str, float] = {}
    for s in segs:
        if s["lang"]:
            tally[s["lang"]] = tally.get(s["lang"], 0.0) + max(0.1, s["end"] - s["start"])
    if not tally:
        return "zh"
    return max(tally.items(), key=lambda kv: kv[1])[0]


# ------------------------------------------------------------------------------- the shots
def _thumb(img) -> "object":
    """A small COLOUR thumbnail, deliberately not grayscale.

    Measured on the sample walkthrough: a yellow highlight band down a table column, which
    is the whole point of the sentence that describes it, is 252,243,203 against 242,242,245
    - 1.4 grey levels apart. In grayscale that screen is identical to the one before it and
    deduplication throws it away. Selection blue, a green "done" row and a red figure over
    white all behave the same way. So compare hue as well as brightness.
    """
    import numpy as np
    return np.asarray(img.convert("RGB").resize((THUMB, THUMB)), dtype="int16")


def _changed(a, b) -> float:
    """Fraction of the picture that changed, not how much it changed on average. A mean over
    the whole desktop is what makes a dialog opening in one corner look like nothing."""
    import numpy as np
    return float(np.count_nonzero(np.abs(a - b).max(axis=2) > 10)) / float(THUMB * THUMB)


def pick_shots(ses: Path, segs: list[dict], voff: float = 0.0) -> list[dict]:
    """Two pictures per sentence: the screen while it was said, and the screen just after.

    One would not do. A step in a manual is an action and its result - "click Investigate"
    and "the dialog opens" - and those are two different screens. Which of the two a
    sentence lands on is also not predictable: people narrate before they click, after they
    click, and sometimes across the click. So take both moments and let the deduplication
    collapse whatever turned out to be the same screen. A run of sentences on one page
    still ends up citing one file.

    `voff` is how far into the meeting the screen capture started, the same offset
    frames.py takes, so shot times are meeting times and not video times.
    """
    video = ses / "screen.mp4"
    out = [{"before": "", "after": "", "t": None} for _ in segs]
    shots_dir = ses / "shots"
    shots_dir.mkdir(exist_ok=True)
    for old in shots_dir.glob("*.png"):
        old.unlink()
    if not video.exists() or not segs:
        return out
    import av

    want: list[tuple[float, int, str]] = []
    for i, s in enumerate(segs):
        want.append((max(0.0, s["start"] + 0.3 - voff), i, "before"))
        want.append((max(0.0, s["end"] + 1.2 - voff), i, "after"))
    want.sort(key=lambda w: w[0])

    picked: list[tuple[int, str, float, object]] = []
    with av.open(str(video)) as c:
        st = c.streams.video[0]
        tb = float(st.time_base or 0) or 1.0
        rate = float(st.guessed_rate or st.average_rate or 0) or 3.0
        if not 0.1 <= rate <= 120:
            rate = 3.0
        wi, n, prev = 0, 0, None                     # prev = (t, frame)
        for frame in c.decode(st):
            n += 1
            t = float(frame.pts * tb) if frame.pts else (n - 1) / rate
            while wi < len(want) and want[wi][0] <= t:
                src = prev if (prev is not None and want[wi][0] < t) else (t, frame)
                picked.append((want[wi][1], want[wi][2], src[0] + voff, src[1].to_image()))
                wi += 1
            prev = (t, frame)
            if wi >= len(want):
                break
        while wi < len(want):                        # wanted past the end of the video
            if prev is not None:
                picked.append((want[wi][1], want[wi][2], prev[0] + voff,
                               prev[1].to_image()))
            wi += 1

    last_thumb, last_name = None, ""
    for idx, kind, t, img in picked:
        th = _thumb(img)
        if last_thumb is not None and _changed(th, last_thumb) < DEDUP:
            out[idx][kind] = last_name
            img.close()
            continue
        if img.width > 1280:
            img = img.resize((1280, max(1, round(img.height * 1280 / img.width))))
        name = f"shot_{hhmmss(t)}.png"
        k = 2
        while (shots_dir / name).exists():
            name = f"shot_{hhmmss(t)}_{k}.png"
            k += 1
        img.save(shots_dir / name)
        img.close()
        out[idx][kind] = name
        if out[idx]["t"] is None:
            out[idx]["t"] = round(t, 2)
        last_thumb, last_name = th, name
    return out


# ---------------------------------------------------------------------------- the pairing
def timeline(segs: list[dict], shots: list[dict], has_video: bool) -> str:
    rows = []
    for s, sh in zip(segs, shots):
        if not has_video:
            pic = "-"
        elif sh["after"] and sh["after"] != sh["before"]:
            pic = f"shots/{sh['before']} -> shots/{sh['after']}"
        else:
            pic = f"shots/{sh['before'] or sh['after'] or '(none)'}"
        rows.append(f"[{clk(s['start'])}] {pic}\n    {s['speaker']}: {s['text']}")
    return "\n".join(rows)


def facts(ses: Path, segs: list[dict], shots: list[dict], lang: str) -> str:
    meta = {}
    p = ses / "session.json"
    if p.exists():
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                            # noqa: BLE001
            meta = {}
    distinct = sorted({n for s in shots for n in (s["before"], s["after"]) if n})
    dur = float(meta.get("duration_s") or (segs[-1]["end"] if segs else 0))
    return "\n".join([
        f"- session: {ses.name}",
        f"- title as recorded: {meta.get('title') or '(none)'}",
        f"- length: {dur / 60:.1f} min, {len(segs)} sentences",
        f"- screen recording: {'yes' if (ses / 'screen.mp4').exists() else 'NO - audio only'}",
        f"- pictures available: {len(distinct)}",
        f"- language of the recording: {lang}",
    ])


CONTRACT_ZH = """\
只输出这份手册的 Markdown，第一行就是 `---`。不要写任何开场白、不要用 ``` 把整份包起来。
每一步的「依据」只能引用上面时间轴里真实出现过的图片名和原话，一个字都不能编。
"""
CONTRACT_EN = """\
Output only the manual as Markdown, starting with `---` on the first line. No preamble, and
do not wrap the whole thing in a code fence. Every step's evidence line may only cite a
picture name and a quote that actually appear in the timeline above. Invent nothing.
"""


def spec_text() -> str:
    p = HERE / "profiles" / "sop.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def build_prompt(ses: Path, me: str = "") -> dict:
    segs = read_segments(ses, me)
    if not segs:
        return {"error": "\u6ca1\u6709\u9010\u5b57\u7a3f\u6bb5\u843d\uff0c\u5148\u8dd1\u8bed\u97f3\u8bc6\u522b"}
    shots = pick_shots(ses, segs, _voff(ses))
    lang = language(segs)
    has_video = (ses / "screen.mp4").exists()
    tl = timeline(segs, shots, has_video)
    write_evidence(ses, segs, shots, tl)
    system = spec_text() + "\n\n" + (CONTRACT_EN if lang == "en" else CONTRACT_ZH)
    user = ("# \u8fd9\u573a\u5f55\u5c4f\n\n" + facts(ses, segs, shots, lang)
            + "\n\n# \u65f6\u95f4\u8f74\uff08\u6bcf\u53e5\u8bdd + \u5f53\u65f6\u7684\u753b\u9762\uff09\n\n"
            + tl)
    one = ("\u4e0b\u9762\u662f\u4e00\u4efd\u5199\u64cd\u4f5c\u624b\u518c\u7684\u89c4\u8303\uff0c"
           "\u4ee5\u53ca\u4e00\u6bb5\u5f55\u5c4f\u7684\u6750\u6599\u3002\u8bf7\u6309\u89c4\u8303"
           "\u628a\u624b\u518c\u5199\u5b8c\u3002\n\n"
           "=============== \u89c4\u8303 ===============\n" + system
           + "\n\n=============== \u6750\u6599 ===============\n" + user)
    return {"system": system, "user": user, "one": one, "lang": lang,
            "segments": len(segs), "shots": len({n for s in shots for n in (s["before"], s["after"]) if n}),
            "chars": len(one), "tokens_est": int(len(one) / 3.2)}


def _voff(ses: Path) -> float:
    p = ses / "session.json"
    if not p.exists():
        return 0.0
    try:
        return float((json.loads(p.read_text(encoding="utf-8")).get("video")
                      or {}).get("started_at_s") or 0.0)
    except Exception:                                # noqa: BLE001
        return 0.0


def write_evidence(ses: Path, segs: list[dict], shots: list[dict], tl: str) -> None:
    """The pairing, kept on disk. When a step in the manual turns out to be wrong, this is
    the file that says which second to go back to."""
    (ses / "shots").mkdir(exist_ok=True)
    (ses / "shots" / "shots.json").write_text(json.dumps(
        [{"i": i, "start": s["start"], "end": s["end"], "speaker": s["speaker"],
          "text": s["text"], "before": sh["before"], "after": sh["after"],
          "t": sh["t"]}
         for i, (s, sh) in enumerate(zip(segs, shots))],
        ensure_ascii=False, indent=1), encoding="utf-8")
    (ses / "sop.timeline.md").write_text(
        "# \u65f6\u95f4\u8f74\uff1a\u6bcf\u53e5\u8bdd\u5bf9\u5e94\u7684\u753b\u9762\n\n"
        "\u8fd9\u4efd\u624b\u518c\u91cc\u6bcf\u4e00\u6b65\u7684\u300c\u4f9d\u636e\u300d"
        "\u90fd\u80fd\u5728\u8fd9\u91cc\u627e\u5230\u3002\n\n```\n" + tl + "\n```\n",
        encoding="utf-8", newline="\n")


# -------------------------------------------------------------------------------- the draft
STEP = re.compile(r"^###\s", re.M)
EVID = re.compile(r"^\s*-\s*(\u4f9d\u636e|Evidence)\s*[:\uff1a]", re.M | re.I)


def validate(text: str, shots: list[str]) -> list[str]:
    bad = []
    if not text.startswith("---"):
        bad.append("\u7f3a front matter\uff08\u7b2c\u4e00\u884c\u4e0d\u662f ---\uff09")
    n = len(STEP.findall(text))
    if n < 2:
        bad.append(f"\u53ea\u627e\u5230 {n} \u6b65")
    ev = len(EVID.findall(text))
    if ev < n:
        bad.append(f"{n} \u6b65\u91cc\u6709 {n - ev} \u6b65\u6ca1\u5199\u4f9d\u636e")
    for m in re.finditer(r"shots/(shot_\d{6}(?:_\d+)?\.png)", text):
        if m.group(1) not in shots:
            bad.append(f"\u5f15\u7528\u4e86\u4e0d\u5b58\u5728\u7684\u56fe {m.group(1)}")
    return bad


def draft(ses: Path, cfg: dict, timeout: float = 1800.0) -> dict:
    pr = build_prompt(ses, str(cfg.get("me") or ""))
    if pr.get("error"):
        return {"ok": False, "error": pr["error"]}
    t0 = time.time()
    try:
        if (cfg.get("engine") or "assistant") == "assistant":
            raw = llm.chat_cli(pr["one"], timeout=timeout, cwd=str(ses))
        else:
            raw = llm.chat(cfg, pr["system"], pr["user"], timeout=min(timeout, 900.0))
    except Exception as exc:                         # noqa: BLE001
        return {"ok": False, "error": str(exc)[:800]}
    text = llm.clean(raw)
    have = sorted(p.name for p in (ses / "shots").glob("*.png"))
    bad = validate(text, have)
    if bad:
        return {"ok": False, "problems": bad, "text": text,
                "took": round(time.time() - t0, 1)}
    p = ses / "sop.md"
    if p.exists():
        (ses / "sop.md.bak").write_text(p.read_text(encoding="utf-8"),
                                       encoding="utf-8", newline="\n")
    p.write_text(text, encoding="utf-8", newline="\n")
    return {"ok": True, "chars": len(text), "steps": len(STEP.findall(text)),
            "shots": pr["shots"], "lang": pr["lang"],
            "took": round(time.time() - t0, 1), "tokens_est": pr["tokens_est"]}


def main() -> int:
    ap = argparse.ArgumentParser(description="operating manual from a walkthrough")
    ap.add_argument("session")
    ap.add_argument("--print-prompt", action="store_true")
    ap.add_argument("--shots-only", action="store_true",
                    help="build the pairing and the pictures, call nothing")
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    a = ap.parse_args()

    import config
    cfg = config.load()
    ses = Path(a.session)
    if not ses.is_dir():
        print(f"no such session: {ses}")
        return 2

    if a.shots_only:
        segs = read_segments(ses, str(cfg.get("me") or ""))
        if not segs:
            print("no transcript segments; run transcribe.py first")
            return 1
        shots = pick_shots(ses, segs, _voff(ses))
        tl = timeline(segs, shots, (ses / "screen.mp4").exists())
        write_evidence(ses, segs, shots, tl)
        n = len({n for s in shots for n in (s["before"], s["after"]) if n})
        print(f"{len(segs)} sentences, {n} distinct screens, language {language(segs)}")
        print(f" -> {ses / 'sop.timeline.md'}")
        return 0
    if a.print_prompt:
        pr = build_prompt(ses, str(cfg.get("me") or ""))
        if pr.get("error"):
            print(pr["error"])
            return 1
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.write(pr["one"])
        return 0
    if a.draft:
        r = draft(ses, cfg)
        # One command, one finished artefact. Rendering here rather than as a second step
        # means the page cannot end up with a manual on disk and no page to read it in.
        # Imported inside the function because sopreport imports this module.
        if r.get("ok") and not a.no_report:
            try:
                import sopreport
                (ses / "sop.html").write_text(sopreport.render(ses), encoding="utf-8")
                r["html"] = "sop.html"
            except Exception as exc:                 # noqa: BLE001
                r["html_error"] = str(exc)[:300]
        print(json.dumps({k: v for k, v in r.items() if k != "text"},
                         ensure_ascii=False, indent=1))
        return 0 if r.get("ok") else 1
    ap.error("pick one of --shots-only / --print-prompt / --draft")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

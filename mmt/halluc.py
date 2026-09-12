"""
Hallucination filter.

Whisper invents text when it is handed audio that is not speech. The classic symptoms
are subtitle-site boilerplate ("Thanks for watching", "字幕由...提供") and a phrase
repeated until the segment ends.

MEASURED RISK ON THIS SETUP (2026-09-10), so the numbers below are calibration, not folklore:
    180 s of real room silence from a laptop's own mic (-93..-102 dBFS)  -> 0 segments
    60 s pink noise / aircon at -42 dBFS                               -> 0 segments
    60 s keyboard typing at -30 dBFS                                   -> 0 segments
    60 s music, 4-chord progression with vibrato at -26 dBFS           -> 0 segments
Silero VAD rejected all of it before the decoder ever ran. So this filter is a safety
net for the cases that cannot be synthesised (very quiet distant speech, phone-line
dropouts), not the load-bearing defence.

Nothing is deleted silently. Every drop is returned with its reason and shown in the
transcript header and in minutes.html.
"""
from __future__ import annotations

import re
import unicodedata

# Boilerplate Whisper learned from subtitle corpora. Matched on the whole segment only,
# so a real sentence that merely contains one of these words is never touched.
BOILERPLATE = [
    r"^\W*(thanks?|thank you)( (so|very) much)?( for)? (watching|listening)\W*$",
    r"^\W*(please )?(like|subscribe|share|comment)([ ,&and]+(like|subscribe|share|comment))*\W*$",
    r"^\W*(subtitles?|captions?)( (by|provided by|created by))?[^\n]{0,40}$",
    r"字幕(由|志願者|志愿者|組|组|提供)", r"請不吝(點贊|点赞)", r"訂閱|订阅",
    r"^\W*(明鏡|明镜)(與|与)(點點|点点)(欄目|栏目)\W*$",
    r"^\W*(轉|转)(發|发)(打賞|打赏)", r"^\W*多謝支持\W*$",
    r"amara\.org|www\.[a-z0-9.-]+|https?://",
    r"^\W*(MBC|SBS|KBS)\s*뉴스", r"^\W*(ご視聴|ご覧)いただき",
    r"^\W*(bye|goodbye|see you|the end|end of)\W*$",
    r"^\W*\[?(music|音楽|音乐|applause|拍手|laughter)\]?\W*$",
    r"^[\s.,!?~♪♫。、！？…\-]*$",
]
_BOIL = [re.compile(p, re.I) for p in BOILERPLATE]

CR_MAX = 2.6            # compression_ratio above this = the text is a repeated loop
NSP_DROP = 0.75         # no_speech_prob above this AND short text = nothing was said
SHORT_CHARS = 16
LOGPROB_FLOOR = -1.05   # below this the decoder had no idea; real speech never gets here
REPEAT_RUN = 3          # identical text this many times in a row = loop


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKC", (t or "").strip())
    return re.sub(r"\s+", " ", t)


def _chars(t: str) -> int:
    return len(re.sub(r"[\s.,!?~。、！？…\-]", "", t or ""))


def _self_repeat(t: str) -> bool:
    """'ok ok ok ok' / '的的的的' inside a single segment."""
    w = _norm(t).split()
    if len(w) >= 6 and len(set(w)) <= max(1, len(w) // 4):
        return True
    m = re.match(r"^(.{1,12}?)\1{3,}$", re.sub(r"\s", "", _norm(t)))
    return bool(m)


def judge(seg: dict) -> str | None:
    """-> reason string if this segment should be dropped, else None."""
    t = _norm(seg.get("text"))
    if not t or _chars(t) == 0:
        return "empty"
    for pat in _BOIL:
        if pat.search(t):
            return f"boilerplate /{pat.pattern[:34]}/"
    cr = float(seg.get("compression_ratio") or 0.0)
    if cr > CR_MAX:
        return f"repetition loop (compression_ratio {cr:.2f} > {CR_MAX})"
    if _self_repeat(t):
        return "repeated phrase inside the segment"
    nsp = float(seg.get("no_speech_prob") or 0.0)
    if nsp > NSP_DROP and _chars(t) < SHORT_CHARS:
        return f"no speech detected (no_speech_prob {nsp:.2f}) and only {_chars(t)} chars"
    lp = float(seg.get("avg_logprob") or 0.0)
    if lp < LOGPROB_FLOOR:
        return f"decoder confidence far too low (avg_logprob {lp:.2f})"
    dur = float(seg.get("end", 0)) - float(seg.get("start", 0))
    if dur > 0 and _chars(t) / dur > 28:
        return f"{_chars(t)} chars in {dur:.1f}s is faster than anyone speaks"
    return None


def filter_segments(segs: list[dict]) -> tuple[list[dict], list[dict]]:
    """-> (kept, dropped). dropped entries carry a human-readable `why`."""
    kept, dropped = [], []
    run_text, run_n = None, 0
    for s in segs:
        why = judge(s)
        t = _norm(s.get("text"))
        if why is None:
            if t and t == run_text:
                run_n += 1
                if run_n >= REPEAT_RUN:
                    why = f"same line repeated {run_n}x in a row"
            else:
                run_text, run_n = t, 1
        if why:
            dropped.append({"start": s.get("start"), "end": s.get("end"),
                            "text": s.get("text"), "why": why,
                            "track": s.get("track") or s.get("speaker")})
        else:
            kept.append(s)
    return kept, dropped

"""Fill this machine's glossary from the calendar that is already on it.

The single highest-value thing a speech model can know before a meeting is who is in it.
Those names are already on this machine, in the Outlook client the user is signed into,
so the tool asks for them instead of asking the user to type them. Nothing is uploaded
and nothing is downloaded: this is one PowerShell round trip to the local client.

Two destinations, on purpose:

  hotwords     a SMALL number of name tokens, only ones seen in several real meetings.
               This list biases the decoder while it listens, and a long list makes
               recognition worse, so it is capped and rare-word filtered.
  _candidates  everything else worth a look later - full display names, recurring
               meeting words. Never used for decoding; the glossary editor shows them
               so a human can promote what is actually useful.

Deliberately never written here: fix_after. A variant only belongs there once someone
has seen the tool mis-hear the word, and that evidence comes from the confirm desk.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lexicon                                                # noqa: E402
import outlook                                                # noqa: E402

DAYS_BACK = 180             # half a year of real meetings is plenty to see who matters
DAYS_FWD = 30
SLICE_DAYS = 45             # one Restrict per slice, so no single call can time out
MIN_SEEN = 4                # a name in fewer meetings than this is noise, not a colleague
SMALL_MEETING = 8           # names are only SPOKEN in small meetings; a 40-person review
                            # tells you nothing about who gets said out loud
TOKEN_MIN_LEN = 3
ZIPF_MAX = 3.8              # skip tokens that are ordinary words; they collide when decoding
TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]+$")
STOP_SUBJECT = re.compile(r"^(hold|holiday|ooo|out of office|focus|lunch|block|busy|"
                          r"pto|vacation|no meetings?)\b", re.I)


def _zipf(w: str) -> float:
    try:
        from wordfreq import zipf_frequency                    # noqa: PLC0415
        return max(zipf_frequency(w, "en"), zipf_frequency(w, "zh"))
    except Exception:                                          # noqa: BLE001
        return 0.0


def name_tokens(name: str) -> list[str]:
    out = []
    for tok in re.split(r"[\s.]+", name or ""):
        tok = tok.strip("'\"-,()")
        if len(tok) >= TOKEN_MIN_LEN and TOKEN_RE.match(tok):
            out.append(tok)
    return out


def scan(days_back: int = DAYS_BACK, days_fwd: int = DAYS_FWD, me: str = "",
         log=lambda s: None) -> dict:
    """Walk the calendar in slices and count people and recurring subject words."""
    people: Counter = Counter()
    subjects: Counter = Counter()
    meetings = 0
    start = -abs(days_back)
    while start < days_fwd:
        stop = min(start + SLICE_DAYS, days_fwd)
        try:
            items = outlook._ps(start, stop)                    # noqa: SLF001
        except Exception as e:                                  # noqa: BLE001
            log(f"  calendar {start:+d}..{stop:+d} d: {e}")
            start = stop
            continue
        log(f"  calendar {start:+d}..{stop:+d} d: {len(items)} appointments")
        for a in items:
            subj = str(a.get("subject") or "").strip()
            if a.get("allday") or STOP_SUBJECT.match(subj):
                continue
            meetings += 1
            who = outlook.names(str(a.get("required") or ""),
                                str(a.get("optional") or ""), me, cap=60)
            org = outlook.person(str(a.get("organizer") or ""), me)
            if org and org not in who:
                who.append(org)
            if len(who) <= SMALL_MEETING:          # big invite lists say nothing about speech
                for n in who:
                    people[n] += 1
            for w in re.findall(r"[A-Za-z][A-Za-z+.\-]{2,}", subj):
                subjects[w] += 1
        start = stop
    return {"people": people, "subjects": subjects, "meetings": meetings}


def plan(found: dict, existing: list[str] | None = None) -> dict:
    """Decide what may enter hotwords, and park everything else as a candidate."""
    have = {w.lower() for w in (existing if existing is not None
                                else lexicon.merged().get("hotwords") or [])}
    room = max(0, lexicon.HOTWORD_CAP - len(have))
    tok: Counter = Counter()
    for name, n in found["people"].items():
        if n < MIN_SEEN:
            continue
        for t in name_tokens(name):
            tok[t] = max(tok[t], n)
    picks, skipped = [], []
    for t, n in tok.most_common():
        if t.lower() in have or any(t.lower() == p.lower() for p in picks):
            continue
        if _zipf(t) > ZIPF_MAX:
            skipped.append(t)
            continue
        if len(picks) < room:
            picks.append(t)
        else:
            skipped.append(t)
    cand = {}
    for name, n in found["people"].most_common():
        if n >= 2:
            cand[name] = {"n": n, "src": "calendar"}
    for w, n in found["subjects"].most_common(60):
        if n >= 3 and _zipf(w) <= 3.0 and w.lower() not in have:
            cand[w] = {"n": n, "src": "subject"}
    return {"hotwords": picks, "candidates": cand, "skipped": skipped,
            "meetings": found["meetings"]}


def apply(p: dict) -> dict:
    d = lexicon.load_user()
    have = {w.lower() for w in d["hotwords"]}
    added = [w for w in p["hotwords"] if w.lower() not in have]
    d["hotwords"].extend(added)
    for k, v in p["candidates"].items():
        d["_candidates"].setdefault(k, v)
    lexicon.save_user(d)
    return {"hotwords_added": added, "candidates": len(p["candidates"])}


def run(days_back: int = DAYS_BACK, days_fwd: int = DAYS_FWD, me: str = "",
        log=lambda s: None) -> dict:
    me = me or outlook.whoami()
    log(f"seeding the glossary from this machine's calendar (me = {me or 'unknown'})")
    found = scan(days_back, days_fwd, me, log)
    p = plan(found)
    r = apply(p)
    r["me"] = me
    r["meetings"] = p["meetings"]
    r["skipped"] = len(p["skipped"])
    log(f"  {p['meetings']} meetings scanned, "
        f"{len(r['hotwords_added'])} names into hotwords, "
        f"{r['candidates']} candidates parked, {r['skipped']} common words skipped")
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed the glossary from the local Outlook calendar")
    ap.add_argument("--back", type=int, default=DAYS_BACK)
    ap.add_argument("--fwd", type=int, default=DAYS_FWD)
    ap.add_argument("--me", default="")
    ap.add_argument("--dry", action="store_true", help="show what would be added, write nothing")
    a = ap.parse_args()
    me = a.me or outlook.whoami()
    found = scan(a.back, a.fwd, me, print)
    p = plan(found)
    print(f"me            {me or '(unknown)'}")
    print(f"meetings      {p['meetings']}")
    print(f"hotwords +    {', '.join(p['hotwords']) or '(none)'}")
    print(f"skipped       {', '.join(p['skipped'][:20]) or '(none)'}")
    print(f"candidates    {len(p['candidates'])}")
    if not a.dry:
        r = apply(p)
        print(f"written       {len(r['hotwords_added'])} hotwords, {r['candidates']} candidates")
        print(f"              {lexicon.user_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

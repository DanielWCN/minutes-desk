"""
Phonetic near-miss annotator.

Whisper is *confidently* wrong on proper nouns: in the real accented-speech test all
8 wrong-jargon segments had avg_logprob well above the low-confidence threshold, so
avg_logprob cannot catch them. The glossary catches variants we have already seen.
This module catches the ones we have NOT seen yet.

Deliberate design choice: it NEVER rewrites the text.
    "bundle" is a real English word. Silently turning "a bundle of features" into
    "a Bundel of features" corrupts the transcript invisibly, which is worse than
    leaving the error. So a phonetic hit is written as   bundle [Bundel?]
and the LLM step decides from the surrounding conversation. Once you confirm a pair,
move it into the glossary -> fix_after as an exact variant and it becomes automatic.

Calibrated on the real errors in one recording of accented speech. The vocabulary was a
private one, so the pairs below are described by shape rather than named; what matters is
where the thresholds had to sit, not which words they were:
    surname heard as a similar English word     mratio 75   hit
    product name heard as a similar word        mratio 75   hit
    product name heard as a common noun         mratio 100  hit
    two-word system name heard as two words     mratio 100  hit
    compound heard with the halves swapped      mratio 83   hit
    unrelated word vs a product name            mratio 29   correctly rejected
    surname sharing no phonemes with what was
      heard                                     mratio 44   not catchable phonetically;
                                                            needs the roster + the LLM
"""
from __future__ import annotations

import re
import unicodedata

try:
    from jellyfish import metaphone
    from rapidfuzz import fuzz
    HAVE = True
except Exception:                                    # pragma: no cover
    HAVE = False

try:
    from wordfreq import zipf_frequency
except Exception:                                    # pragma: no cover
    def zipf_frequency(w, lang):                     # no guard available -> allow
        return 0.0

MRATIO_MIN = 70      # fuzzy ratio of the two metaphone codes (single word)
MRATIO_MIN_MULTI = 80  # n-grams have more characters, so coincidence is likelier
RATIO_MIN = 50       # plus a floor on raw spelling similarity, kills wild pairs
MIN_LEN = 5          # shorter tokens are too noisy to be worth annotating
SAME_FIRST_SOUND = True   # the initial consonant is almost never mis-heard

# Frequency guard. Without it an ordinary plural gets flagged as a product name whose
# metaphone code differs by one letter (NTS vs ANTS = 86), and a common verb matches a
# surname exactly (STL vs STL = 100). Measured zipf values:
#   real errors:        3.93  3.50  2.25   <- want these kept
#   false positives:    4.84  4.79  4.65  4.51  4.37   <- want these gone
# Person names are far rarer than jargon, so they get the stricter floor.
ZIPF_MAX_TERM = 4.2
ZIPF_MAX_NAME = 3.0

# Measured on every real error and every observed false positive in that recording. Pairs
# are described rather than named, for the reason given in the module docstring.
#   case                                    mr    rr   zipf  same1st   verdict
#   common noun / product name             100  83.3  3.93   yes       KEEP  (real error)
#   English word / surname                  75  62.5  2.25   yes       KEEP  (real error)
#   English word / product name             75  60.0  3.50   yes       KEEP  (real error)
#   compound with halves swapped            83  80.0  1.61   yes       KEEP  (real error)
#   plural of a common word / product       86  80.0  3.78   NO        drop
#   very common noun / product              86  60.0  4.84   NO        drop
#   common verb / product name              75  66.7  4.79   NO        drop
#   two common words / two-word term        71  50.0  4.82   NO        drop
#   common adjective / product name         80  62.5  4.43   yes       drop (word too common)
#   common verb / surname                  100  33.3  4.37   yes       drop (raw ratio +
#                                                                            name floor)

_WORD = re.compile(r"[A-Za-z][A-Za-z'+-]*")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def build_vocab(gloss: dict) -> list[tuple[str, str, int, bool]]:
    """-> [(canonical, normalised, word_count, is_person)] worth phonetic matching."""
    skip = {_norm(x) for x in (gloss.get("phonetic_skip") or [])}
    skip |= {_norm(k) for k in (gloss.get("ambiguous") or {}) if k != "_note"}

    terms: dict[str, bool] = {t: False for t in (gloss.get("fix_after") or {})}
    for t in (gloss.get("hotwords") or []):
        terms.setdefault(t, False)
    for alias, disp in (gloss.get("people") or {}).items():
        if alias == "_note":
            continue
        for part in re.split(r"[\s()]+", disp):
            if len(part) >= MIN_LEN:
                terms[part] = True

    out = []
    for t in sorted(terms):
        n = _norm(t)
        if not n or n in skip or len(n.replace(" ", "")) < MIN_LEN:
            continue
        # all-caps acronyms are spelled out, not mis-heard phonetically
        if t.isupper():
            continue
        out.append((t, n, len(n.split()), terms[t]))
    return out


def score(a: str, b: str) -> tuple[float, float]:
    ma, mb = metaphone(a), metaphone(b)
    return fuzz.ratio(ma, mb), fuzz.ratio(a, b)


def annotate(text: str, vocab, seen: set[str] | None = None):
    """Return (annotated_text, [(surface, canonical, mratio)])."""
    if not HAVE or not vocab or not text:
        return text, []

    words = [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(text)]
    if not words:
        return text, []
    known = {v[1] for v in vocab}

    hits = []            # (start, end, surface, canon, mratio)
    used: list[tuple[int, int]] = []

    for size in (3, 2, 1):                       # longest n-gram wins
        for i in range(len(words) - size + 1):
            s, e = words[i][1], words[i + size - 1][2]
            if any(not (e <= us or s >= ue) for us, ue in used):
                continue
            surface = text[s:e]
            n = _norm(surface)
            if not n or len(n.replace(" ", "")) < MIN_LEN:
                continue
            if n in known:                       # already a canonical term, leave alone
                continue
            zipf = zipf_frequency(n, "en") if size == 1 else 0.0
            mn = metaphone(n)
            floor = MRATIO_MIN if size == 1 else MRATIO_MIN_MULTI
            best = None
            for canon, cn, cw, is_person in vocab:
                if cw != size:
                    continue
                if zipf > (ZIPF_MAX_NAME if is_person else ZIPF_MAX_TERM):
                    continue                     # ordinary English word, not a mis-hear
                mc = metaphone(cn)
                if SAME_FIRST_SOUND and mn[:1] != mc[:1]:
                    continue                     # e.g. two common words that both mis-hear to one term
                mr, rr = fuzz.ratio(mn, mc), fuzz.ratio(n, cn)
                if mr >= floor and rr >= RATIO_MIN and (best is None or mr > best[1]):
                    best = (canon, mr)
            if best:
                used.append((s, e))
                hits.append((s, e, surface, best[0], best[1]))

    if not hits:
        return text, []

    out, prev, listed = [], 0, []
    for s, e, surface, canon, mr in sorted(hits):
        key = f"{surface.lower()}|{canon}"
        out.append(text[prev:e])
        if seen is None or key not in seen:
            out.append(f" [{canon}?]")
            if seen is not None:
                seen.add(key)
        listed.append((surface, canon, mr))
        prev = e
    out.append(text[prev:])
    return "".join(out), listed

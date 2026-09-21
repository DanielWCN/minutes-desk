"""The glossary in layers, so replacing the program never wipes what you taught it.

Two files, merged every time they are read:

  mmt/glossary.base.json        ships with the program. REPLACED on every update, so
                                nothing personal may live here. Public product names
                                and ordinary business vocabulary only.
  <user dir>/glossary.user.json this machine's own terms - colleague names, your team's
                                jargon, everything harvested from your own meetings.
                                Never published, never overwritten by an update.

The user layer wins. Lists are unioned base-first, so shipped terms keep their position
and yours are appended; dicts are merged key by key, and a key present in both takes the
user value whole, which is exactly what you want when correcting a shipped spelling.

Why a cap on hotwords and not on fix_after: hotwords biases the decoder while it is
listening, and a long list makes recognition WORSE (measured; see glossary.base.json's
_readme). fix_after runs on finished text and is safe to grow without limit, so anything
harvested automatically belongs there.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    import config
except Exception:                                            # noqa: BLE001
    config = None                                            # type: ignore[assignment]

LIST_TABLES = ("hotwords", "phonetic_skip")
DICT_TABLES = ("fix_after", "ambiguous", "people")
DOC_KEYS = ("_readme", "_layers", "_prompt_rule")
HOTWORD_CAP = 80            # advisory, for the editor UI; nothing here truncates


def base_path() -> Path:
    return Path(__file__).with_name("glossary.base.json")


def user_path() -> Path:
    if config is not None:
        return config.user_dir() / "glossary.user.json"
    return Path(__file__).with_name("glossary.user.json")     # standalone fallback


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:                                        # noqa: BLE001
        return {}                                            # a broken layer must not brick a meeting


def blank_user() -> dict:
    return {"_readme": ["This machine's own terms. Never leaves this machine.",
                        "Merged on top of mmt/glossary.base.json; keys here win."],
            "hotwords": [], "fix_after": {}, "ambiguous": {}, "people": {},
            "phonetic_skip": [], "_candidates": {}}


def merged() -> dict:
    """One glossary dict, the same shape every consumer already expects."""
    base, user = _read(base_path()), _read(user_path())
    out: dict = {}
    for k in DOC_KEYS:
        if k in base:
            out[k] = base[k]
    for k in LIST_TABLES:
        seen, acc = set(), []
        for layer in (base, user):
            for w in layer.get(k) or []:
                if isinstance(w, str) and w.lower() not in seen:
                    seen.add(w.lower())
                    acc.append(w)
        out[k] = acc
    for k in DICT_TABLES:
        acc: dict = {}
        for layer in (base, user):
            v = layer.get(k)
            if not isinstance(v, dict):
                continue
            for key, val in v.items():
                # fix_after keeps a list of heard forms per canonical spelling, so teaching
                # the tool one more form for a term base already knows must ADD to that
                # list. Replacing it silently threw away everything base knew about the
                # term - one new variant for "headcount" would have dropped the other six.
                if isinstance(val, list) and isinstance(acc.get(key), list):
                    have = {str(x).lower() for x in acc[key]}
                    acc[key] = acc[key] + [x for x in val
                                           if isinstance(x, str) and x.lower() not in have]
                else:
                    acc[key] = val
        out[k] = acc
    return out


def save_user(d: dict) -> dict:
    """Write the user layer, creating the user directory if this is the first term."""
    p = user_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return d


def load_user() -> dict:
    d = _read(user_path())
    if not d:
        d = blank_user()
    for k in LIST_TABLES:
        d.setdefault(k, [])
    for k in DICT_TABLES:
        d.setdefault(k, {})
    d.setdefault("_candidates", {})
    return d


def add_fix(canon: str, variant: str) -> bool:
    """Teach the tool one heard form. Lands in fix_after, never in hotwords."""
    canon, variant = (canon or "").strip(), (variant or "").strip()
    if not canon or not variant or canon == variant:
        return False
    d = load_user()
    lst = d["fix_after"].setdefault(canon, [])
    if any(v.lower() == variant.lower() for v in lst):
        return False
    lst.append(variant)
    save_user(d)
    return True


if __name__ == "__main__":
    g = merged()
    print(f"base {base_path()}\nuser {user_path()}  ({'present' if user_path().exists() else 'absent'})")
    for k in LIST_TABLES + DICT_TABLES:
        print(f"  {k:14s} {len(g.get(k) or [])}")

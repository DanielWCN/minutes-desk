"""
The same minutes in the other language.

A set of minutes goes out to people who do not all read the same language, and writing
them twice from the transcript produces two documents that disagree: two summaries, two
sets of decisions, two chances to be wrong. So this step never summarises. It takes the
finished minutes.md - the one a person has read and approved - and translates it, item
for item, into minutes.zh.md (or minutes.en.md when the original is Chinese). Same number
of decisions, same number of action rows, same owners, same dates.

Two things are deliberately NOT translated. Names stay exactly as they are spelt in the
attendee list, because a transliterated name is a name nobody can search for in their
mailbox. And every term in the glossary stays in its original form: the vocabulary of the
work - system names, report names, the metrics people argue about - is the vocabulary the
team already uses out loud, in English, in the middle of a Chinese sentence.

The front matter is not handed back by the model: it is rebuilt here from the original,
line by line, and only greeting / intro / facts / signoff take the translated text. That
way the date, the duration, the attendee list and the distribution list cannot drift, and
a translation that goes wrong goes wrong in the prose, never in the facts.

Usage:
    translate.py <session>            write the other language, if it is not there yet
    translate.py <session> --force    redo it over an existing file (keeps a .bak)
    translate.py <session> --to en    pick the target language explicitly
    translate.py <session> --print    print the prompt and exit (no model call)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import lexicon                                                   # noqa: E402
import llm                                                       # noqa: E402
import minutes as M                                              # noqa: E402

LANG_NAME = {"zh": "\u4e2d\u6587", "en": "\u82f1\u6587"}
FILE_OF = {"zh": "minutes.zh.md", "en": "minutes.en.md"}
SUBJECT = {"zh": "\u4f1a\u8bae\u7eaa\u8981", "en": "Meeting notes"}

# The four headings the writing spec fixes, so the translation of a heading is a lookup
# rather than a judgement and the two files stay comparable side by side.
HEADINGS = {
    "zh": {"Summary": "\u6458\u8981", "Decisions": "\u51b3\u5b9a",
           "Action items": "\u884c\u52a8\u9879", "Open questions": "\u5f85\u89e3\u51b3\u95ee\u9898"},
    "en": {"\u6458\u8981": "Summary", "\u51b3\u5b9a": "Decisions",
           "\u884c\u52a8\u9879": "Action items", "\u5f85\u89e3\u51b3\u95ee\u9898": "Open questions"},
}
# Only these four carry prose. Everything else in the front matter is a fact.
PROSE_KEYS = ("greeting", "intro", "facts", "signoff")


def keep_words(fm: dict) -> list[str]:
    """The terms that must come out the other side unchanged, longest first.

    Two sources, both already curated by hand elsewhere: the glossary this machine uses to
    correct the transcript, and the attendee list this meeting confirmed.
    """
    g = lexicon.merged()
    out: list[str] = []
    seen = set()
    for w in list(g.get("hotwords") or []) + list((g.get("fix_after") or {}).keys()) \
            + list((g.get("ambiguous") or {}).keys()):
        w = str(w).strip()
        if w and not w.startswith("_") and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    people = fm.get("attendees")
    people = people if isinstance(people, list) else []
    for p in people + [fm.get("organizer") or ""]:
        p = str(p).strip(" -")
        if p and p.lower() not in seen and p.upper() != "TBD":
            seen.add(p.lower())
            out.append(p)
    return sorted(out, key=lambda s: (-len(s), s.lower()))


def spec(lang: str, keep: list[str]) -> str:
    name = LANG_NAME.get(lang, lang)
    hmap = "\uff1b".join(f"{k} \u2192 {v}" for k, v in HEADINGS.get(lang, {}).items())
    return f"""\u628a\u4e0b\u9762\u8fd9\u4efd\u4f1a\u8bae\u7eaa\u8981\u7ffb\u8bd1\u6210{name}\u3002

\u8fd9\u662f\u7ffb\u8bd1\uff0c\u4e0d\u662f\u91cd\u5199\uff0c\u4e5f\u4e0d\u662f\u91cd\u65b0\u603b\u7ed3\u3002\u539f\u6587\u6709\u51e0\u6761\uff0c\u8bd1\u6587\u5c31\u6709\u51e0\u6761\uff1b
\u6bb5\u843d\u3001\u5217\u8868\u3001\u8868\u683c\u3001\u6807\u9898\u5c42\u7ea7\u3001\u7c97\u4f53\u4f4d\u7f6e\uff0c\u4e00\u5f8b\u7167\u642c\u3002\u4e0d\u5f97\u5220\u6761\uff0c\u4e0d\u5f97\u5408\u6761\uff0c
\u4e0d\u5f97\u81ea\u5df1\u52a0\u4e00\u53e5\u539f\u6587\u6ca1\u6709\u7684\u8bdd\u3002

\u89c4\u5219\uff1a
1. \u4eba\u540d\u4e00\u5f8b\u539f\u6837\u4e0d\u52a8\uff0c\u4e0d\u97f3\u8bd1\u3001\u4e0d\u6539\u62fc\u5199\u3002
2. \u3008\u4fdd\u7559\u539f\u8bcd\u3009\u91cc\u7684\u8bcd\uff0c\u51fa\u73b0\u65f6\u539f\u6837\u4fdd\u7559\u3002\u8868\u4e4b\u5916\u7684\u7cfb\u7edf\u540d\u3001\u9879\u76ee\u540d\u3001\u62a5\u8868\u540d\u3001
   \u7f29\u5199\uff08\u4e09\u4e2a\u5b57\u6bcd\u4ee5\u5185\u7684\u5927\u5199\uff09\u540c\u6837\u4fdd\u7559\u539f\u8bcd\u3002\u62ff\u4e0d\u51c6\u5c31\u4fdd\u7559\u3002
3. \u65e5\u671f\u3001\u6570\u5b57\u3001\u767e\u5206\u6bd4\u3001\u7f16\u53f7\uff08A1 A2 \u2026\uff09\u3001\u5355\u4f4d\u4e00\u5f8b\u4e0d\u52a8\u3002
4. front matter \u91cc\u53ea\u7ffb\u8bd1 greeting\u3001intro\u3001facts\u3001signoff \u56db\u4e2a\u5b57\u6bb5\uff1b\u5176\u4f59\u5b57\u6bb5\u539f\u6837\u8f93\u51fa\u3002
   facts \u6bcf\u6761\u662f\u300c\u6807\u9898 | \u5185\u5bb9\u300d\uff0c\u4e24\u8fb9\u90fd\u8981\u8bd1\uff0c`|` \u672c\u8eab\u4fdd\u7559\uff0c\u6761\u6570\u4e0d\u53d8\u3002
5. \u7ae0\u8282\u6807\u9898\u6309\u8fd9\u4e2a\u5bf9\u7167\u8bd1\uff1a{hmap}\u3002
6. Markdown \u8868\u683c\u7684\u8868\u5934\u8981\u8bd1\uff0c\u5217\u6570\u4e0d\u53d8\uff0c\u5206\u9694\u884c\uff08|---|\uff09\u539f\u6837\u4fdd\u7559\u3002
7. \u8bed\u6c14\u662f\u7ed9\u540c\u4e8b\u770b\u7684\u4f1a\u8bae\u7eaa\u8981\uff1a\u4e66\u9762\u3001\u7b80\u6d01\u3001\u4e0d\u52a0\u8bed\u6c14\u8bcd\u3001\u4e0d\u52a0\u4f60\u81ea\u5df1\u7684\u5224\u65ad\u3002
8. \u53ea\u8f93\u51fa\u6574\u4efd Markdown \u6587\u4ef6\uff0c\u4ece `---` \u5f00\u59cb\uff0c\u4e0d\u8981\u4ee3\u7801\u56f4\u680f\uff0c\u4e0d\u8981\u4efb\u4f55\u8bf4\u660e\u3002

\u3008\u4fdd\u7559\u539f\u8bcd\u3009
{" \u00b7 ".join(keep)}"""


def shape(text: str) -> dict:
    """The countable structure of a set of minutes: what a translation must not change."""
    _, body = M.parse_front_matter(text)
    rows = [l.strip() for l in body.split("\n")]
    return {"h2": sum(1 for l in rows if l.startswith("## ")),
            "bullet": sum(1 for l in rows if l.startswith("- ")),
            "table": sum(1 for l in rows if l.startswith("|")),
            "fence": sum(1 for l in rows if l.startswith("```"))}


def problems(src: str, out: str, lang: str) -> list[str]:
    bad: list[str] = []
    fm, body = M.parse_front_matter(out)
    if not out.startswith("---") or not fm:
        bad.append("\u8bd1\u6587\u5f00\u5934\u6ca1\u6709 front matter")
    a, b = shape(src), shape(out)
    for k, label in (("h2", "\u7ae0\u8282"), ("bullet", "\u5217\u8868\u9879"),
                     ("table", "\u8868\u683c\u884c")):
        if a[k] != b[k]:
            bad.append(f"{label}\u6570\u91cf\u4e0d\u5bf9\uff1a\u539f\u6587 {a[k]}\uff0c\u8bd1\u6587 {b[k]}")
    if b["fence"]:
        bad.append("\u6b63\u6587\u91cc\u7559\u7740\u4ee3\u7801\u56f4\u680f\uff08```\uff09")
    cjk = sum(1 for ch in body if "\u4e00" <= ch <= "\u9fff")
    if lang == "zh" and cjk < 40:
        bad.append("\u8bd1\u6587\u51e0\u4e4e\u6ca1\u6709\u4e2d\u6587\uff0c\u6a21\u578b\u53ef\u80fd\u628a\u539f\u6587\u9000\u56de\u6765\u4e86")
    if lang == "en" and cjk > 40:
        bad.append("\u8bd1\u6587\u91cc\u8fd8\u5927\u7247\u662f\u4e2d\u6587")
    return bad


def missing_names(src: str, out: str, keep: list[str]) -> list[str]:
    """Names that were in the original body and are not in the translation.

    A warning, not a failure: a document that names one person wrong is still worth having
    next to the original, and refusing to write it would leave nothing to compare against.
    """
    _, sb = M.parse_front_matter(src)
    _, ob = M.parse_front_matter(out)
    gone = [w for w in keep if " " in w and w in sb and w not in ob]
    return gone[:8]


def rebuild(src: str, new_fm: dict, lang: str) -> str:
    """The original front matter with the prose swapped in, then the translated body.

    Line by line over the ORIGINAL text rather than re-serialising a parsed dict: the front
    matter is the part a person hand-edits, and a round trip through a dict would quietly
    reflow their blocks and drop their comments.
    """
    m = M.FM_RE.match(src)
    raw = m.group(1).split("\n") if m else []
    fm0, _ = M.parse_front_matter(src)
    title = str(fm0.get("title") or "")
    date = str(fm0.get("date") or "")
    _, body = M.parse_front_matter(new_fm.pop("__text__", "") or "")

    out: list[str] = [f"lang: {lang}"]
    i, n = 0, len(raw)
    while i < n:
        ln = raw[i]
        key = None
        if ln[:1] not in (" ", "\t", "-", "") and ":" in ln:
            key = ln.split(":", 1)[0].strip()
        if key == "lang":
            i += 1
            continue
        if key == "subject":
            sub = SUBJECT.get(lang, "Meeting notes")
            out.append(f"subject: {sub} | {title} | {date}" if date else f"subject: {sub} | {title}")
            i += 1
            continue
        if key in ("greeting", "intro") and str(new_fm.get(key) or "").strip():
            out.append(f"{key}: {str(new_fm[key]).strip()}")
            i += 1
            continue
        if key == "facts":
            j, items = i + 1, []
            while j < n and raw[j].lstrip().startswith("- "):
                items.append(raw[j])
                j += 1
            new = new_fm.get("facts")
            if isinstance(new, list) and len(new) == len(items) and items:
                out.append("facts:")
                out += ["  - " + str(x).strip() for x in new]
            else:
                out.append(ln)
                out += items
            i = j
            continue
        if key == "signoff":
            j, blk = i + 1, []
            while j < n and (not raw[j].strip() or raw[j][:1] in (" ", "\t")):
                blk.append(raw[j])
                j += 1
            new = str(new_fm.get("signoff") or "").strip("\n")
            if new.strip():
                out.append("signoff: |")
                out += ["  " + x for x in new.split("\n")]
            else:
                out.append(ln)
                out += blk
            i = j
            continue
        out.append(ln)
        i += 1
    return "---\n" + "\n".join(out) + "\n---\n\n" + body.strip() + "\n"


def target_of(src_fm: dict, forced: str) -> str:
    if forced:
        return forced
    return "en" if str(src_fm.get("lang") or "en").lower().startswith("zh") else "zh"


def main() -> int:
    ap = argparse.ArgumentParser(description="the same minutes in the other language")
    ap.add_argument("session")
    ap.add_argument("--to", default="", choices=["", "zh", "en"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args()

    ses = Path(args.session)
    if not ses.is_dir():
        print(f"no such session: {ses}")
        return 2
    src_p = ses / "minutes.md"
    if not src_p.exists():
        print("$ \u8fd8\u6ca1\u6709 minutes.md\uff0c\u6ca1\u4e1c\u897f\u53ef\u8bd1")
        return 0
    src = src_p.read_text(encoding="utf-8")
    if M.is_scaffold(src):
        print("$ minutes.md \u8fd8\u662f\u7a7a\u767d\u6a21\u677f\uff0c\u5148\u628a\u7eaa\u8981\u5199\u5b8c")
        return 0
    fm0, _ = M.parse_front_matter(src)
    lang = target_of(fm0, args.to)
    out_p = ses / FILE_OF[lang]
    if out_p.exists() and not args.force:
        print(f"$ {out_p.name} \u5df2\u7ecf\u5728\u4e86\uff0c\u6ca1\u52a8\u5b83"
              "\uff08\u8981\u91cd\u7ffb\u5c31\u5220\u6389\u5b83\uff0c\u6216\u8005\u52a0 --force\uff09")
        return 0

    keep = keep_words(fm0)
    system = spec(lang, keep)
    user = src.strip()
    one = (system + "\n\n=============== \u539f\u6587 ===============\n" + user)
    if args.show:
        sys.stdout.write(one)
        return 0

    import config                                                # noqa: PLC0415
    cfg = config.load()
    if not llm.can_auto(cfg):
        print("$ \u6ca1\u914d\u53ef\u81ea\u52a8\u8c03\u7528\u7684\u6a21\u578b\uff0c\u8df3\u8fc7\u7ffb\u8bd1")
        return 0

    print("$ \u7ffb\u8bd1\u6210 %s\uff0c\u63d0\u793a\u8bcd\u7ea6 %d tokens\uff0c\u4fdd\u7559\u539f\u8bcd %d \u4e2a"
          % (LANG_NAME.get(lang, lang), int(len(one) / 3.2), len(keep)))
    t0 = time.time()
    text, bad = "", ["\u6ca1\u62ff\u5230\u8bd1\u6587"]
    for attempt in (1, 2):
        prompt = one if attempt == 1 else (
            one + "\n\n=============== \u4e0a\u4e00\u7248\u7684\u95ee\u9898 ===============\n"
            + "\n".join("- " + x for x in bad)
            + "\n\u91cd\u65b0\u8f93\u51fa\u6574\u4efd\u6587\u4ef6\uff0c\u628a\u8fd9\u51e0\u70b9\u6539\u5bf9\u3002")
        try:
            if (cfg.get("engine") or "assistant") == "assistant":
                raw = llm.chat_cli(prompt, timeout=1800.0, cwd=str(ses))
            else:
                raw = llm.chat(cfg, system, user if attempt == 1 else prompt, timeout=900.0)
        except Exception as exc:                                 # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(exc)[:600]},
                             ensure_ascii=False, indent=2))
            return 1
        text = llm.clean(raw)
        bad = problems(src, text, lang)
        if not bad:
            break
        print("$ \u7b2c %d \u7248\u4e0d\u5408\u683c\uff1a%s" % (attempt, "\uff1b".join(bad)))
    if bad:
        print(json.dumps({"ok": False, "problems": bad}, ensure_ascii=False, indent=2))
        return 1

    new_fm, _ = M.parse_front_matter(text)
    new_fm["__text__"] = text
    final = rebuild(src, new_fm, lang)
    if out_p.exists():
        (ses / (out_p.name + ".bak")).write_text(out_p.read_text(encoding="utf-8"),
                                                 encoding="utf-8", newline="\n")
    out_p.write_text(final, encoding="utf-8", newline="\n")
    gone = missing_names(src, final, keep)
    if gone:
        print("$ \u6ce8\u610f\uff1a\u8fd9\u51e0\u4e2a\u540d\u5b57\u5728\u8bd1\u6587\u91cc\u627e\u4e0d\u5230\u4e86\uff0c\u6838\u4e00\u4e0b\uff1a"
              + "\u3001".join(gone))
    print(json.dumps({"ok": True, "file": out_p.name, "chars": len(final),
                      "took": round(time.time() - t0, 1),
                      "names_missing": gone}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

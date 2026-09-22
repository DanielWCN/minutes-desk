"""
minutes.md is the source of truth for everything a human wrote, and it is a plain
Markdown file on purpose: you open it, fix a name or a number, and re-render. No JSON,
no schema errors, no tool in the way.

Structure (front matter + fixed H2 sections). Section order in the file is the order
rendered. Unknown sections are kept and rendered after the known ones, so adding
"## Context" costs nothing.

    ---
    title: ...
    date: 2026-09-10
    time: 15:18-15:33 (CST, UTC+8)
    duration: 15 min
    organizer: ...
    attendees:
      - Alex Smith (Platform) - notes
      - Maria Garcia (Operations)
    distribution: Maria Garcia
    subject: <email subject line>
    greeting: Hi Maria,
    signoff: |
      Best regards,
      Alex
    ---

    ## Summary
    One to three sentences. Prose, not bullets.

    ## Decisions
    - **Decision** - one line of rationale.

    ## Action items
    | # | Action | Owner | Due |
    |---|--------|-------|-----|
    | A1 | ... | Alex | 2026-09-26 |

    ## Open questions
    - ...

Why sections are parsed as raw Markdown blocks rather than into objects: the moment the
parser understands the content it also constrains it, and the person editing the file
should be free to write a paragraph where a table was. The renderer only needs to know
"this is a table" or "this is a list", which Markdown already tells it.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


# --------------------------------------------------------------------------- parsing
def _scalar(v: str):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


def parse_front_matter(text: str) -> tuple[dict, str]:
    """A deliberately small YAML subset: key: value, key: | block, and - list items.

    Bringing in PyYAML for eight keys would add a dependency to the one file a human is
    most likely to hand-edit, and a real YAML parser fails loudly on a stray colon in a
    meeting title. This one does not.
    """
    m = FM_RE.match(text)
    if not m:
        return {}, text
    fm, body = {}, text[m.end():]
    key, block, block_ind = None, None, 0
    for raw in m.group(1).split("\n"):
        if block is not None:
            if raw.strip() and (len(raw) - len(raw.lstrip())) >= block_ind:
                block.append(raw[block_ind:])
                continue
            fm[key] = "\n".join(block).rstrip()
            block = None
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.lstrip().startswith("- ") and key:
            fm.setdefault(key, [])
            if isinstance(fm[key], str) and not fm[key]:
                fm[key] = []
            if isinstance(fm[key], list):
                fm[key].append(_scalar(raw.lstrip()[2:]))
            continue
        if ":" not in raw:
            continue
        k, _, v = raw.partition(":")
        key = k.strip()
        v = v.strip()
        if v in ("|", ">", "|-", ">-"):
            block, block_ind = [], 2
            continue
        fm[key] = _scalar(v) if v else ""
    if block is not None:
        fm[key] = "\n".join(block).rstrip()
    return fm, body


def split_sections(body: str) -> list[tuple[str, str]]:
    """[(heading, raw markdown), ...] in file order. Text before the first H2 is dropped."""
    out, cur, buf = [], None, []
    for line in body.split("\n"):
        if line.startswith("## "):
            if cur is not None:
                out.append((cur, "\n".join(buf).strip()))
            cur, buf = line[3:].strip(), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out.append((cur, "\n".join(buf).strip()))
    return out


def load(p: Path) -> dict:
    text = p.read_text(encoding="utf-8")
    fm, body = parse_front_matter(text)
    return {"meta": fm, "sections": split_sections(body)}


# ------------------------------------------------------------------- markdown -> html
_INLINE = [
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"), r"<em>\1</em>"),
    (re.compile(r"`(.+?)`"), r"<code>\1</code>"),
    (re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)"), r'<a href="\2">\1</a>'),
]


def inline(s: str) -> str:
    s = html.escape(str(s or ""))
    for rx, rep in _INLINE:
        s = rx.sub(rep, s)
    return s


def md_blocks(md: str) -> list[dict]:
    """Only the four block types minutes actually use: p, ul, ol, table.

    Anything more (nested lists, blockquotes, images) belongs in the transcript or an
    attachment, not in a set of minutes, and pretending to support it would mean
    shipping a Markdown engine we then have to trust.
    """
    blocks, i = [], 0
    lines = md.split("\n")
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        # table: a header row followed by a |---| separator
        if ln.lstrip().startswith("|") and i + 1 < len(lines) \
                and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            def cells(r: str) -> list[str]:
                return [c.strip() for c in r.strip().strip("|").split("|")]
            head = cells(ln)
            i += 2
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(cells(lines[i]))
                i += 1
            blocks.append({"k": "table", "head": head, "rows": rows})
            continue
        m = re.match(r"^\s*(?:[-*+]|(\d+)[.)])\s+", ln)
        if m:
            kind = "ol" if m.group(1) else "ul"
            items = []
            while i < len(lines):
                m2 = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$", lines[i])
                if m2:
                    items.append(m2.group(1))
                    i += 1
                elif lines[i].strip() and lines[i].startswith(("  ", "\t")) and items:
                    items[-1] += " " + lines[i].strip()   # continuation line
                    i += 1
                else:
                    break
            blocks.append({"k": kind, "items": items})
            continue
        para = []
        while i < len(lines) and lines[i].strip() and not lines[i].lstrip().startswith("|") \
                and not re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", lines[i]):
            para.append(lines[i].strip())
            i += 1
        blocks.append({"k": "p", "text": " ".join(para)})
    return blocks


# ------------------------------------------------------------------------ scaffolding
# Both fences matter: parse_front_matter needs the closing --- or every key here is
# dropped in silence and the page falls back to the session id as its title.
# `facts:` is optional (label | value, up to four); it renders as the strip of numbers
# above the summary. Delete the line if the meeting had no number worth lifting out.
TEMPLATE = """---
title: {title}
date: {date}
time: {time}
duration: {duration}
organizer: {organizer}
attendees:
{attendees}
distribution: {distribution}
subject: {subject}
greeting: Hi all,
intro: TBD - one neutral line: which meeting these minutes cover, plus a request for corrections.
facts:
  - TBD label | TBD value
signoff: |
  Best regards,
  {me}
---

## Summary

TBD - 3 to 5 sentences, third person. Why the meeting happened, and where it landed.
Not what was discussed: what came out of it. Name people; never write "I" or "you".

## Decisions

- TBD - one line each, max 6, only things actually settled, third person.

## Action items

| # | Action | Owner | Due |
|---|--------|-------|-----|
| A1 | TBD | TBD | TBD |

## Open questions

- TBD - one question each, max 5, third person.
"""

# These four sections are the whole document. A minute is a summary a reader gets
# through once; the verbatim record already ships folded inside minutes.html, so any
# section that describes the discussion instead of its outcome belongs there, not here.
# Voice is third person throughout: a set of minutes is read by everyone in the room and
# by strangers six months later, so "I" and "you" never name anybody.
# The rules an author (or an AI) has to follow are in profiles/minutes.md.


def is_scaffold(text: str) -> bool:
    """
    True when nobody has written into this file yet.

    Compared against the template's own body, word for word, because the front matter is
    exactly the part that has to be allowed to change: the header is rebuilt from the
    confirmed facts (who was really in the room) every time the documents are generated,
    right up until the moment a person starts writing. Testing for the string "TBD" would
    not do - a finished set of minutes can legitimately say an owner or a due date is TBD.
    """
    body = lambda t: t.split("---", 2)[-1].strip()      # noqa: E731
    return body(text) == body(TEMPLATE)


def _people(meta: dict, talk: dict, me: str) -> list[str]:
    """
    The names of the other people in the meeting, one per entry.

    The attendee list belongs to the invite (and to the tick boxes on the confirm desk,
    which write back into it), NOT to the speaker labels: the loopback track carries a
    single label for everybody at the far end, so reading names off it produced an
    "attendee" that was twenty-seven names in one line, and an "organizer" that was the
    same twenty-seven names again.
    """
    names = [n.strip() for n in re.split(r"[;,\u3001\n]+", str(meta.get("others") or ""))
             if n.strip()]
    if names:
        return names
    skip = {"you", "others", "me", (me or "").lower()}
    return [k for k in sorted(talk, key=lambda k: -talk[k]) if k.lower() not in skip]


def _names(s) -> list[str]:
    return [n.strip() for n in re.split(r"[;,\u3001\n]+", str(s or "")) if n.strip()]


def rosters(meta: dict) -> list[list[str]]:
    """Every attendee list this session has carried, newest first.

    session.json keeps the earlier value of anything edited, which is what makes it possible
    to tell a list this tool wrote from a list a person typed.
    """
    out = [_names(meta.get("others"))]
    for e in reversed(meta.get("edits") or []):
        was = (e or {}).get("was") or {}
        if isinstance(was, dict) and "others" in was:
            out.append(_names(was.get("others")))
    return [r for r in out if r]


def sync_people(p: Path, meta: dict, me: str = "") -> bool:
    """Bring the attendee list in a written set of minutes back in step with the roster.

    Who actually turned up is answered on the confirm desk, which comes AFTER the minutes
    have been drafted -- so "invited but did not come" was decided too late to reach the
    header, and a set of minutes kept naming people who were never in the room. Two fields
    are brought forward, attendees and distribution, and only while they still hold a list
    this tool wrote itself (the roster as it stood at some point). The moment a person has
    typed a name of their own in there, the header is theirs and nothing is touched.
    """
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return False
    m = FM_RE.match(text)
    fm, _ = parse_front_matter(text)
    known = rosters(meta)
    if not m or not fm or not known:
        return False

    def low(xs) -> set:
        return {str(x).strip().lower() for x in xs if str(x).strip()}

    now, mine = known[0], low([me]) if me else set()
    have = [str(x).strip() for x in (fm.get("attendees") or []) if str(x).strip()]
    others = [h for h in have if h.lower() not in mine]
    head, rest = m.group(1), text[m.end():]
    done = False
    if low(others) != low(now) and any(low(others) == low(r) for r in known):
        keep = ([me] if me and low(have) & mine else []) + now
        head = re.sub(r"(?m)^attendees:[^\n]*\n(?:[ \t]*-[^\n]*\n?)*",
                      "attendees:\n" + "".join(f"  - {n}\n" for n in keep), head, count=1)
        done = True
    dist = _names(fm.get("distribution"))
    if low(dist) != low(now) and any(low(dist) == low(r) for r in known):
        head = re.sub(r"(?m)^distribution:[^\n]*$", "distribution: " + ", ".join(now),
                      head, count=1)
        done = True
    if not done:
        return False
    p.write_text("---\n" + head.rstrip("\n") + "\n---\n" + rest, encoding="utf-8")
    return True


def scaffold(ses: Path, tr: dict, meta: dict, me: str = "") -> str:
    talk = tr.get("talk_time_s") or {}
    people = _people(meta, talk, me)
    started = str(meta.get("started_local") or "")
    date, _, clock = started.partition("T")
    dur = (meta.get("duration_s") or 0) / 60
    return TEMPLATE.format(
        title=tr.get("title") or meta.get("title") or ses.name,
        date=date or "TBD", time=clock[:5] or "TBD",
        duration=f"{dur:.0f} min" if dur else "TBD",
        # who called the meeting is not something the audio knows: in a 1:1 there is only
        # one candidate, in a group meeting a guess would be wrong more often than right
        organizer=people[0] if len(people) == 1 else "TBD",
        attendees="\n".join(f"  - {w}" for w in ([me or "You"] + people)) or "  - TBD",
        distribution=", ".join(people) or "TBD",
        subject=f"Meeting notes | {tr.get('title') or ses.name} | {date}",
        me=me)

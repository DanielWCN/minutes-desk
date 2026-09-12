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


def scaffold(ses: Path, tr: dict, meta: dict, me: str = "") -> str:
    talk = tr.get("talk_time_s") or {}
    who = [k for k, _ in sorted(talk.items(), key=lambda kv: -kv[1])]
    started = str(meta.get("started_local") or "")
    date, _, clock = started.partition("T")
    dur = (meta.get("duration_s") or 0) / 60
    return TEMPLATE.format(
        title=tr.get("title") or meta.get("title") or ses.name,
        date=date or "TBD", time=clock[:5] or "TBD",
        duration=f"{dur:.0f} min" if dur else "TBD",
        organizer=who[0] if who else "TBD",
        attendees="\n".join(f"  - {w}" for w in who) or "  - TBD",
        distribution=", ".join(w for w in who if w.lower() not in ("you", me.lower())) or "TBD",
        subject=f"Meeting notes | {tr.get('title') or ses.name} | {date}",
        me=me)

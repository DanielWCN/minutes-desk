#!/usr/bin/env python3
"""What the browser cannot be asked twice: the shape of a finished document.

Three bugs in one week came from the same blind spot. A stylesheet lives inside every
document, so a fix to the program does not reach the meetings already on disk; the
mechanism that repairs them reads a version stamp out of the top of the file; and the
title bar hid controls at a width nobody tests at. All three were found by hand, by
opening a browser, dragging a panel and measuring. That is an afternoon each.

This renders a document from a fixture and asserts the things that were wrong, in about a
second, with nothing installed. It is not a layout engine and does not pretend to be one:
it reads the file that would be served and asks whether the promises the program makes
about that file are still in it. Run it after touching report.py, app.py or ui.html.

    python pagetest.py            # one line per check, non-zero exit if any failed
    python pagetest.py --json     # the same, for a machine
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

FIXTURE_MINUTES = """# Fixture Meeting

## Decisions

- the first one
- the second one

## Actions

- somebody does something
"""
FIXTURE_TRANSCRIPT = {
    "segments": [
        {"start": 0.0, "end": 3.0, "speaker": "S1", "text": "One sentence, spoken."},
        {"start": 3.0, "end": 6.5, "speaker": "S2", "text": "And the reply to it."},
    ]
}

# A manual is the same trap one layer over: its stylesheet and its seeking, its lightbox
# and its open-folder button all live inside the file, so a change in sopreport.py is
# correct in the program and absent from every manual already written. Two steps is enough
# to catch it - one citing pictures, one citing only a sentence.
FIXTURE_SOP = """title: Fixture Walkthrough
source: mmt-pagetest
lang: en
steps: 2
shots: 2
unverified: 1

## What this manual is for

Two steps, so the shape of the page can be checked.

## Steps

### Step 1 \u00b7 Open the console
- Where: the console
- What to do: open it and sign in
- Success looks like: the landing page is shown (shots/shot_000000.png)
- Evidence: shots/shot_000000.png \u00b7 0:00:00 \u00b7 "First open the console"

### Step 2 \u00b7 Read the table
- Where: the landing page
- What to do: look at the table
- Success looks like: the table is in view (shots/shot_000003.png)
- Evidence: shots/shot_000003.png \u00b7 0:00:03 \u00b7 "And the reply to it"
- \u26a0\ufe0f Unverified: only said out loud, never shown on screen.
"""
FIXTURE_SHOTS = [
    {"i": 0, "start": 0.0, "end": 3.0, "speaker": "S1", "text": "One sentence, spoken.",
     "before": "shot_000000.png", "after": None},
    {"i": 1, "start": 3.0, "end": 6.5, "speaker": "S2", "text": "And the reply to it.",
     "before": "shot_000003.png", "after": None},
]

R: list[tuple[str, bool, str]] = []


def ck(name: str, ok: bool, detail: str = "") -> bool:
    R.append((name, bool(ok), detail))
    return bool(ok)


def render(d: Path) -> Path:
    (d / "minutes.md").write_text(FIXTURE_MINUTES, encoding="utf-8")
    (d / "transcript.json").write_text(json.dumps(FIXTURE_TRANSCRIPT), encoding="utf-8")
    p = subprocess.run([PY, "-u", str(HERE / "report.py"), str(d)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180)
    out = d / "minutes.html"
    ck("renders", out.is_file(), (p.stderr or p.stdout or "").strip()[-300:])
    return out


def check_stamp(html: bytes) -> None:
    """The stamp has to be where the code that reads it is looking.

    app.py reads the first N bytes of the file and matches a pattern. Both the N and the
    pattern are taken from app.py itself rather than repeated here, so that moving either
    one is enough to make this fail loudly instead of quietly agreeing with the old value.
    """
    sys.path.insert(0, str(HERE))
    import report                                                   # noqa: PLC0415

    src = (HERE / "app.py").read_text(encoding="utf-8", errors="replace")
    m = re.search(r"head\s*=\s*fh\.read\((\d+)\)", src)
    if not ck("app.py declares a head window", bool(m)):
        return
    win = int(m.group(1))
    pats = re.findall(r"re\.search\(r'([^']*rv[^']*)'", src)
    if not ck("app.py declares a stamp pattern", bool(pats), str(pats)):
        return
    head = html[:win].decode("utf-8", "replace")
    found = None
    for pat in pats:
        mm = re.search(pat, head)
        if mm:
            found = int(mm.group(1))
            break
    ck(f"stamp is inside the first {win} bytes app.py reads", found is not None,
       "not found; a document written now would be re-rendered on every single open")
    if found is not None:
        ck("stamp in the head equals report.RV", found == report.RV,
           f"head says {found}, report.RV is {report.RV}")
    whole = html.decode("utf-8", "replace")
    mb = re.search(r'data-rv="(\d+)"', whole)
    ck("body carries the same stamp", bool(mb) and int(mb.group(1)) == report.RV,
       f"body says {mb.group(1) if mb else None}, report.RV is {report.RV}")


def check_bar(html: str) -> None:
    """Nothing in the title bar may be removed at any width, and the bar may grow."""
    css = html.split("<style>", 1)[-1].split("</style>", 1)[0] if "<style>" in html else html
    # Every @media block with its condition, by counting braces rather than by regex: a
    # stylesheet nests, and a pattern that stops at the first closing brace reads half a
    # block. Only width-keyed ones are the subject here. @media print hides the bar on
    # purpose - paper has no jump links - and that is correct, not a regression.
    bad = []
    for m in re.finditer(r"@media([^{]*)\{", css):
        cond, i, depth = m.group(1), m.end(), 1
        while i < len(css) and depth:
            depth += 1 if css[i] == "{" else -1 if css[i] == "}" else 0
            i += 1
        body = css[m.end():i - 1]
        if "width" in cond and ".rail" in body and "display:none" in body:
            bad.append(cond.strip() + " ->" + body[:100])
    ck("no window width hides part of the title bar", not bad,
       "hidden by a width-keyed @media: " + (bad[0] if bad else ""))
    rail = re.search(r"(?<![\w.-])\.rail\{([^}]*)\}", css)
    if ck("the bar has a rule", bool(rail)):
        body = rail.group(1)
        ck("the bar is not pinned to one row's height",
           not re.search(r"(?<!min-)height:", body), body[:120])
    inner = re.search(r"\.rail\s+\.in\{([^}]*)\}", css)
    if ck("the bar's row has a rule", bool(inner)):
        ck("the bar's row is allowed to wrap", "flex-wrap:wrap" in inner.group(1),
           inner.group(1)[:120])
    ck("headings are offset by the measured bar height",
       "scroll-padding-top" in css and "--railh" in css)


def check_paper_js(html: str) -> None:
    """The measurement itself: zero is not a height, and it is re-taken on request."""
    ck("a zero bar height is refused",
       re.search(r"var h\s*=\s*r\.offsetHeight;\s*if\(!h\)return;", html) is not None,
       "measured inside a hidden panel, a zero would collapse the heading offset")
    ck("the shell can ask for a re-measure", "d.mmt==='railh'" in html)
    for name, needle in (("the three jumps are in the bar",
                          ('href="#s1"', 'href="#s2"', 'href="#s3"')),
                         ("the copy button is in the bar", ("copy",)),
                         ("the language switch is in the bar", ("data-lg",))):
        ck(name, all(n in html for n in needle))


def render_sop(d: Path) -> Path:
    """The manual, from the same two sentences plus two one-pixel pictures. The pictures
    have to be real files: the point of half these checks is that the page never cites a
    picture that is not there."""
    png = bytes.fromhex("89504e470d0a1a0a0000000d494844520000000100000001080600000"
                        "01f15c4890000000a49444154789c6300010000050001"
                        "0d0a2db40000000049454e44ae426082")
    sh = d / "shots"
    sh.mkdir(exist_ok=True)
    for n in ("shot_000000.png", "shot_000003.png"):
        (sh / n).write_bytes(png)
    (sh / "shots.json").write_text(json.dumps(FIXTURE_SHOTS), encoding="utf-8")
    (d / "sop.md").write_text(FIXTURE_SOP, encoding="utf-8")
    (d / "session.json").write_text(json.dumps(
        {"session": d.name, "title": "Fixture Walkthrough", "duration_s": 6.5,
         "origin": "import"}), encoding="utf-8")
    sys.path.insert(0, str(HERE))
    import sopreport                                                  # noqa: PLC0415

    out = d / "sop.html"
    try:
        out.write_text(sopreport.render(d), encoding="utf-8")
    except Exception as exc:                                          # noqa: BLE001
        ck("the manual renders", False, f"{type(exc).__name__}: {exc}")
        return out
    ck("the manual renders", out.is_file())
    return out


def check_sop(html: str, d: Path) -> None:
    sys.path.insert(0, str(HERE))
    import sopreport                                                  # noqa: PLC0415

    # the same stamp bug as the minutes, in the same place, read by the same kind of code
    src = (HERE / "app.py").read_text(encoding="utf-8", errors="replace")
    win = int(m.group(1)) if (m := re.search(r"head\s*=\s*fh\.read\((\d+)\)", src)) else 4096
    pat = re.search(r"re\.search\(r'([^']*mmt-sv[^']*)'", src)
    if ck("app.py declares a manual stamp pattern", bool(pat), ""):
        mm = re.search(pat.group(1), html[:win])
        ck(f"the manual's stamp is inside the first {win} bytes", bool(mm),
           "not found; every manual would be re-rendered on every open")
        if mm:
            ck("the manual's stamp equals sopreport.SV", int(mm.group(1)) == sopreport.SV,
               f"page says {mm.group(1)}, sopreport.SV is {sopreport.SV}")
    # every step in the source becomes exactly one card, and carries its evidence
    ck("both steps became cards", html.count('class="step"') == 2,
       f"{html.count('class=\"step\"')} cards")
    ck("each step cites its evidence", html.count('class="ev"') == 2,
       f"{html.count('class=\"ev\"')} evidence lines")
    ck("the unverified note survived", html.count('class="warn"') >= 1)
    # a step that names a picture shows it, and no <img> may point at a file that is absent
    srcs = re.findall(r'<img src="([^"]+)"', html)
    missing = [x for x in srcs if x and not (d / x).exists()]
    ck("no picture on the page is missing from the folder", not missing, str(missing[:3]))
    ck("the pictures are shown", len([x for x in srcs if x.startswith("shots/")]) >= 2,
       str(srcs[:4]))
    # the file paths the prose cites are turned into pictures and clocks, never left raw
    body = re.sub(r"<[^>]+>", " ", html.split("</style>", 1)[-1])
    ck("no raw shots/ path is left in the prose", "shots/" not in body,
       body[max(0, body.find("shots/") - 60):body.find("shots/") + 40])
    ck("the clock seeks the recording", "onclick=\"seek(" in html)
    ck("a picture opens full size", "onclick=\"lb(" in html and 'id="lb"' in html)
    ck("the folder can be opened from the page", "openf(" in html and str(d) in html,
       "the absolute path has to be on the page, not only in the app")


def check_shell() -> None:
    ui = (HERE / "ui.html").read_text(encoding="utf-8", errors="replace")
    ck("the shell asks the paper to re-measure", "pushRailh" in ui
       and "mmt:'railh'" in ui)
    ck("it asks on show, on resize and on load",
       ui.count("pushRailh()") >= 2 and "addEventListener('resize', pushRailh)" in ui)
    app = (HERE / "app.py").read_text(encoding="utf-8", errors="replace")
    mv = re.search(r'VERSION\s*=\s*"([\d.]+)"', app)
    mu = re.search(r"UI_VERSION\s*=\s*'([\d.]+)'", ui)
    ck("the two halves carry the same version", bool(mv) and bool(mu)
       and mv.group(1) == mu.group(1),
       f"app.py {mv.group(1) if mv else '?'}, ui.html {mu.group(1) if mu else '?'}")


def main() -> int:
    d = Path(tempfile.mkdtemp(prefix="mmt-pagetest-"))
    try:
        out = render(d)
        if out.is_file():
            raw = out.read_bytes()
            text = raw.decode("utf-8", "replace")
            check_stamp(raw)
            check_bar(text)
            check_paper_js(text)
        sp = render_sop(d)
        if sp.is_file():
            check_sop(sp.read_text(encoding="utf-8", errors="replace"), d)
        check_shell()
    finally:
        shutil.rmtree(d, ignore_errors=True)

    failed = [r for r in R if not r[1]]
    if "--json" in sys.argv:
        print(json.dumps({"total": len(R), "failed": len(failed),
                          "checks": [{"name": n, "ok": o, "detail": dt} for n, o, dt in R]},
                         ensure_ascii=False, indent=2))
    else:
        for n, o, dt in R:
            line = ("  ok   " if o else "  FAIL ") + n
            if not o and dt:
                line += "\n         " + dt.replace("\n", " ")[:200]
            print(line)
        print(f"\n{len(R) - len(failed)}/{len(R)} ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

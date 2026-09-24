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

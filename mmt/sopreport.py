"""
Render sop.html from sop.md. The manual is a separate document from the minutes: its own
file, its own page, nothing of it leaks into minutes.md.

Inputs (all inside the session folder):
    sop.md              the manual. Written by sop.py --draft, or by hand.
    shots/shots.json    which picture belongs to which sentence, from sop.py
    shots/*.png         the pictures the manual cites
    screen.mp4 / *.wav  referenced, never embedded, so the file stays small

Why this is not report.py with a flag: a set of minutes is a sheet someone pastes into
Outlook, so every element inside it carries inline styles and the layout is built for
paper. A manual is the opposite - it is read on screen, beside the recording, and its
whole value is that each step can be checked against the second it came from. Sharing
the stylesheet keeps the two looking like one tool; sharing the layout would make both
worse. So report.CSS and report.blocks_html are imported, and nothing else.

The one promise this page makes: every step shows the picture it came from and the exact
second, and clicking either jumps the recording there. A step nobody can check is a step
nobody should follow.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

import archive
import minutes as M
import report
import sop

# The behaviour inside this page - seeking, the lightbox, the open-folder button - is the
# document's own code, so a file written by an older version does not gain it just because
# the app around it is new. app.py compares this stamp and re-renders a stale manual before
# serving it. Bump it whenever this stylesheet or this JavaScript changes.
SV = 1

SHOT_RE = re.compile(r"shots/(shot_\d{6}(?:_\d+)?\.png)")
BULLET = re.compile(r"^\s*-\s+(.*)$")
EVID = re.compile(r"^(依据|Evidence)\s*[:：]\s*(.*)$", re.I | re.S)
CLOCK = re.compile(r"\b(\d{1,2}):([0-5]\d):([0-5]\d)\b")
# A label is only a label if it is short. "Step 3" split on its colon would otherwise turn
# half of a sentence into a heading, which reads like a bug and hides the sentence.
STEP_N = re.compile(r"^\s*(?:\u6b65\u9aa4|Step)\s*\d+\s*(?:[·:.\u3001\uff1a\u3002-]\s*)?",
                    re.I)
LABEL = re.compile(r"^([^:：]{1,28})\s*[:：]\s*(.+)$", re.S)

T = {
    "zh": {"kicker": "操作手册", "steps": "步骤", "evidence": "证据",
           "before": "操作前", "after": "操作后", "shot": "画面",
           "src": "来源", "open": "打开文件夹", "copied": "已复制",
           "copy": "复制全文", "copiedall": "已复制 · 粘进任何地方",
           "nshots": "张画面", "nsteps": "步", "unver": "处待确认",
           "sec": "秒录音", "min": "分钟录音", "jump": "跳到这一刻",
           "made": "这份手册是工具根据这段录音和录屏写的。每一步下面的画面就是当时的屏幕，"
                   "点一下画面或时间，录音和录屏都会跳到那一秒。",
           "noshot": "这段只有录音，没有录屏，所以手册里没有画面，只有原话。"
                     "照着做之前请自己核对一遍屏幕。",
           "unverh": "手册里有 %d 处工具没把握，已经在正文里标出来了。",
           "tl": "每句话对应的画面", "said": "原话", "edit": "改法",
           "editv": "手册的正文是同一个文件夹里的 sop.md。改完它，刷新这一页就跟着变。",
           "none": "手册还没生成。"},
    "en": {"kicker": "Operating manual", "steps": "Steps", "evidence": "Evidence",
           "before": "Before", "after": "After", "shot": "Screen",
           "src": "Source", "open": "Open folder", "copied": "Copied",
           "copy": "Copy all", "copiedall": "Copied",
           "nshots": "pictures", "nsteps": "steps", "unver": "to confirm",
           "sec": "s recorded", "min": "min recorded", "jump": "jump to this moment",
           "made": "This manual was written by the tool from the recording below. The "
                   "picture under each step is the screen at that moment; click a picture "
                   "or a timestamp and both the audio and the video jump to that second.",
           "noshot": "This session has audio only, so the manual quotes what was said and "
                     "shows no pictures. Check the screen yourself before following it.",
           "unverh": "The tool was unsure about %d things. They are marked in the text.",
           "tl": "Which picture belongs to which sentence", "said": "Said", "edit": "Editing",
           "editv": "The text of this manual is sop.md in the same folder. Edit it and "
                    "reload this page.",
           "none": "No manual has been generated yet."},
}

CSS_EXTRA = """
/* ---- the manual ------------------------------------------------------------------
   A step is a card, because a step is the unit a person acts on: it has to be possible
   to lose your place, look up, and find the same step again. */
.kicker{font:600 10.5px/1 var(--mono);letter-spacing:.14em;
 text-transform:uppercase;color:var(--acc-hi);margin:0 0 6px}
/* a bare timestamp button. Everywhere on this page a clock is clickable, so it looks the
   same everywhere on this page. */
.ev .t,.pics .t{background:none;border:none;padding:0;cursor:pointer;
 font:11px/1.4 var(--mono);color:var(--faint)}
.ev .t:hover,.pics .t:hover{color:var(--link);text-decoration:underline}
.bar video{height:110px;background:#000;border-radius:var(--r)}
.kpi{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 16px}
.kpi div{flex:1 1 110px;background:var(--pane-2);border:1px solid var(--line);
 border-left:3px solid var(--acc);border-radius:var(--r);padding:8px 11px}
.kpi b{display:block;font:600 19px/1.2 var(--sans);color:var(--ink)}
.kpi span{font-size:11px;color:var(--faint)}
.srcln{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 18px;
 font-size:12px;color:var(--dim)}
.srcln code{font:12px/1.5 var(--mono);color:var(--dim);background:var(--pane-2);
 border:1px solid var(--line);border-radius:4px;padding:2px 6px;word-break:break-all}
.step{background:var(--pane);border:1px solid var(--line);border-radius:var(--r-lg);
 padding:14px 16px 16px;margin:0 0 12px}
.step>h3{display:flex;gap:9px;align-items:baseline;margin:0 0 10px;
 font:600 15px/1.45 var(--sans);color:var(--ink)}
.step>h3 i{flex:0 0 auto;min-width:22px;height:22px;border-radius:5px;background:var(--acc);
 color:#fff;font:700 12px/22px var(--mono);text-align:center;font-style:normal}
.step dl{margin:0;display:grid;grid-template-columns:max-content 1fr;gap:4px 12px}
.step dt{font-size:12px;color:var(--faint);white-space:nowrap}
.step dd{margin:0;color:var(--dim)}
@media (max-width:520px){.step dl{grid-template-columns:1fr}
 .step dt{margin-top:6px}}
.ev{margin:11px 0 0;padding:8px 10px;background:var(--pane-2);border:1px solid var(--hair);
 border-radius:var(--r);font-size:12px;color:var(--dim)}
.ev .lbl{color:var(--faint);margin-right:6px}
.ev q{color:var(--ink);quotes:'\\201c' '\\201d'}
.warn{margin:11px 0 0;padding:8px 10px;background:var(--warnbg);
 border-left:3px solid var(--warn);border-radius:var(--r);font-size:12.5px;color:var(--ink)}
.pics{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0 0}
.pics figure{margin:0;flex:1 1 240px;min-width:0;cursor:zoom-in}
.pics img{width:100%;display:block;border:1px solid var(--line);border-radius:var(--r);
 background:var(--pane-2)}
.pics figure:hover img{border-color:var(--acc)}
.pics figcaption{display:flex;gap:7px;align-items:center;font-size:11px;
 color:var(--faint);margin-top:5px}
.pics .t{font:11px/1.5 var(--mono)}
/* full size, because the whole point of the picture is a column heading or a button
   label, and neither survives being shown 240px wide */
#lb{position:fixed;inset:0;z-index:60;background:rgba(0,0,0,.88);display:none;
 align-items:center;justify-content:center;padding:18px;cursor:zoom-out}
#lb.on{display:flex}
#lb img{max-width:100%;max-height:100%;border-radius:var(--r);box-shadow:0 8px 40px #000}
#lb .cap{position:absolute;left:0;right:0;bottom:10px;text-align:center;font-size:12px;
 color:#cfd3da}
.tlx{font:12px/1.7 var(--mono);white-space:pre-wrap;color:var(--dim);margin:0}
.editv{font-size:12px;color:var(--faint);margin:18px 0 0}
"""

JS_EXTRA = r"""
function lb(src,cap){var b=document.getElementById('lb');
 b.querySelector('img').src=src;b.querySelector('.cap').textContent=cap||'';
 b.classList.add('on');}
function lbx(){document.getElementById('lb').classList.remove('on');}
addEventListener('keydown',function(e){if(e.key==='Escape')lbx();});
/* Served by the app this reaches the app; opened as a loose file it cannot, so it says so
   instead of doing nothing. */
async function openf(btn,p){
 var o=btn.dataset.o||btn.textContent;btn.dataset.o=o;
 try{var r=await fetch('/api/open',{method:'POST',headers:{'content-type':'application/json'},
  body:JSON.stringify({path:p})});var j=await r.json();
  if(j&&j.ok)return;throw new Error((j&&j.error)||'no');}
 catch(err){
  var ta=document.createElement('textarea');ta.value=p;document.body.appendChild(ta);
  ta.select();try{document.execCommand('copy');}catch(e){}ta.remove();
  btn.textContent=btn.dataset.cp||'copied';setTimeout(function(){btn.textContent=o;},2000);}}
function copyall(btn){
 var n=document.querySelector('.wrap');if(!n)return;
 var r=document.createRange();r.selectNodeContents(n);
 var s=window.getSelection();s.removeAllRanges();s.addRange(r);
 var ok=false;try{ok=document.execCommand('copy');}catch(e){}
 s.removeAllRanges();
 var o=btn.dataset.o||btn.textContent;btn.dataset.o=o;
 btn.textContent=ok?(btn.dataset.ok||'ok'):'-';
 setTimeout(function(){btn.textContent=o;},2200);}
"""


def e(s) -> str:
    return html.escape(str(s or ""))


def _secs(clock: str) -> float | None:
    m = CLOCK.search(clock or "")
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def shot_times(ses: Path) -> dict[str, float]:
    """file -> the second it shows. A picture with no time cannot be clicked, and a
    picture that cannot be clicked is not evidence."""
    p = ses / "shots" / "shots.json"
    if not p.exists():
        return {}
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                # noqa: BLE001
        return {}
    out: dict[str, float] = {}
    for r in rows:
        for key, t in (("before", r.get("start")), ("after", r.get("end"))):
            f = r.get(key)
            if f and t is not None and f not in out:
                out[f] = float(t)
    return out


# what a caveat looks like at the head of a line, in either language the tool writes
WARN = re.compile(r"^\s*(?:\u26a0\ufe0f?|!{1,2})\s*(?:Unverified|\u672a\u6838\u5b9e|"
                  r"\u672a\u9a8c\u8bc1|\u6ce8\u610f|Note)\b\s*[:\uff1a]?", re.I)


def parse_steps(md: str) -> tuple[str, list[dict]]:
    """Split a Steps section into cards. Anything before the first `### ` is kept as
    prose, because a walkthrough sometimes opens with a sentence about all of them."""
    pre: list[str] = []
    steps: list[dict] = []
    cur: dict | None = None
    for ln in md.split("\n"):
        if ln.startswith("### "):
            cur = {"title": ln[4:].strip(), "rows": [], "ev": "", "notes": []}
            steps.append(cur)
            continue
        if cur is None:
            pre.append(ln)
            continue
        m = BULLET.match(ln)
        if m:
            txt = m.group(1).strip()
            if EVID.match(txt):
                cur["ev"] = txt
            elif WARN.match(txt):
                # a caveat is a caveat wherever it was written. The spec asks for it on its
                # own line, but an assistant that hangs it off the bullet list instead used
                # to have it rendered as one more calm labelled row, which reads as verified
                cur["notes"].append(txt)
            else:
                cur["rows"].append(txt)
        elif ln.strip():
            cur["notes"].append(ln.strip())
    return "\n".join(pre).strip(), steps


def _pics(files: list[str], times: dict[str, float], lg: str) -> str:
    if not files:
        return ""
    t = T[lg]
    figs = []
    for i, f in enumerate(files):
        sec = times.get(f)
        cap = t["before"] if (i == 0 and len(files) > 1) else (
            t["after"] if len(files) > 1 else t["shot"])
        clk = (f'<button class="t" onclick="event.stopPropagation();seek({sec:.2f})" '
               f'title="{e(t["jump"])}">{sop.clk(sec)}</button>') if sec is not None else ""
        figs.append(f'<figure onclick="lb(\'shots/{e(f)}\',\'{e(cap)}\')">'
                    f'<img src="shots/{e(f)}" loading="lazy" alt="{e(cap)}">'
                    f"<figcaption><span>{e(cap)}</span>{clk}</figcaption></figure>")
    return f'<div class="pics">{"".join(figs)}</div>'


def step_html(n: int, st: dict, times: dict[str, float], lg: str) -> str:
    t = T[lg]
    seen: list[str] = []

    def take(s: str) -> str:
        """Pull the picture references out of a sentence and remember them in order. The
        step reads as a sentence; the pictures are shown as pictures."""
        for m in SHOT_RE.finditer(s):
            if m.group(1) not in seen:
                seen.append(m.group(1))
        s = re.sub(r"\s*[（(]\s*" + SHOT_RE.pattern + r"(\s*(?:->|→|,|、)\s*"
                   + SHOT_RE.pattern + r")*\s*[)）]", "", s)
        return SHOT_RE.sub("", s).replace("  ", " ").strip(" ·-,、")

    ev = ""
    if st["ev"]:
        m = EVID.match(st["ev"])
        body = take(m.group(2) if m else st["ev"])
        sec = _secs(body)
        # the quote is whatever is left after the file and the clock: the words actually said
        said = body
        for part in re.split(r"\s*·\s*|\s{2,}", body):
            if part.strip() and _secs(part) is None:
                said = part.strip()
                break
        said = said.strip().strip('"\u201c\u201d')
        clk = (f'<button class="t" onclick="seek({sec:.2f})" title="{e(t["jump"])}">'
               f"{sop.clk(sec)}</button>") if sec is not None else ""
        ev = (f'<div class="ev"><span class="lbl">{e(t["evidence"])}</span>{clk} '
              f"<q>{e(said)}</q></div>")

    rows = []
    for r in st["rows"]:
        txt = take(r)
        if not txt:
            continue
        m = LABEL.match(txt)
        if m:
            rows.append(f"<dt>{e(m.group(1).strip())}</dt><dd>{M.inline(m.group(2).strip())}</dd>")
        else:
            rows.append(f"<dt></dt><dd>{M.inline(txt)}</dd>")
    notes = "".join(f'<div class="warn">{M.inline(take(x))}</div>' for x in st["notes"])
    # the card already shows the number in a badge, so "Step 3 - " in the heading is the
    # number twice and costs a third of the line on a narrow column
    title = STEP_N.sub("", take(st["title"])).strip()
    return (f'<article class="step" id="st{n}"><h3><i>{n}</i><span>{e(title)}</span></h3>'
            + (f"<dl>{''.join(rows)}</dl>" if rows else "")
            + ev + notes + _pics(seen, times, lg) + "</article>")


def evidence_html(ses: Path, lg: str, times: dict[str, float]) -> str:
    t = T[lg]
    p = ses / "shots" / "shots.json"
    if not p.exists():
        return ""
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                # noqa: BLE001
        return ""
    files: list[str] = []
    for r in rows:
        for k in ("before", "after"):
            if r.get(k) and r[k] not in files:
                files.append(r[k])
    figs = "".join(
        f'<figure onclick="lb(\'shots/{e(f)}\',\'{sop.clk(times.get(f) or 0)}\')" '
        f'title="{e(t["jump"])}">'
        f'<img src="shots/{e(f)}" loading="lazy">'
        f'<figcaption>{sop.clk(times.get(f) or 0)} · {e(f)}</figcaption></figure>'
        for f in files)
    lines = "\n".join(
        f'[{sop.clk(r.get("start") or 0)}] {r.get("before") or "-"} -> {r.get("after") or "-"}\n'
        f'    {r.get("speaker") or ""}: {r.get("text") or ""}' for r in rows)
    return (f'<div class="frames">{figs}</div>'
            f'<details class="sect" style="margin-top:14px"><summary><h2>{e(t["tl"])}</h2>'
            f'<span class="cnt">{len(rows)}</span></summary>'
            f'<pre class="tlx">{e(lines)}</pre></details>')


def render(ses: Path, lg_force: str = "") -> str:
    d = M.load(ses / "sop.md")
    fm, secs = d["meta"], d["sections"]
    lg = lg_force or ("zh" if str(fm.get("lang") or "").lower().startswith("zh") else "en")
    t = T[lg]
    times = shot_times(ses)
    has_video = (ses / "screen.mp4").exists()
    nshots = len(list((ses / "shots").glob("*.png"))) if (ses / "shots").is_dir() else 0
    title = str(fm.get("title") or ses.name)

    nav, body = [], []
    nstep = 0
    for i, (h, md) in enumerate(secs, 1):
        nav.append(f'<a href="#s{i}">{e(h)}</a>')
        if "### " in md:
            pre, steps = parse_steps(md)
            nstep = len(steps)
            inner = (report.blocks_html(pre, False) if pre else "") + "".join(
                step_html(n, s, times, lg) for n, s in enumerate(steps, 1))
        else:
            inner = report.blocks_html(md, False)
        body.append(f'<div class="sect" id="s{i}"><h2>{e(h)}</h2>{inner}</div>')

    unver = 0
    try:
        unver = int(fm.get("unverified") or 0)
    except Exception:                                # noqa: BLE001
        unver = 0
    dur = 0.0
    try:
        dur = float(json.loads((ses / "session.json").read_text(
            encoding="utf-8")).get("duration_s") or 0)
    except Exception:                                # noqa: BLE001
        pass

    kpi = "".join(
        f"<div><b>{v}</b><span>{e(k)}</span></div>" for v, k in
        [(nstep, t["nsteps"]), (nshots, t["nshots"])]
        + ([(unver, t["unver"])] if unver else [])
        + ([(f"{dur:.0f}", t["sec"]) if dur < 120 else (f"{dur/60:.0f}", t["min"])]
           if dur else []))

    ban = [f'<div class="pvw"><span>{e(t["made"] if nshots else t["noshot"])}</span></div>']
    if unver:
        ban.append(f'<div class="banner warn">{e(t["unverh"] % unver)}</div>')

    fold = str(ses)
    srcln = (f'<div class="srcln"><span>{e(t["src"])}</span><code>{e(fold)}</code>'
             f'<button class="btn g sm" data-cp="{e(t["copied"])}" '
             f"onclick=\"openf(this,'{e(fold).replace(chr(92), chr(92) * 2)}')\">"
             f'{e(t["open"])}</button></div>')

    au = "mic.wav" if (ses / "mic.wav").exists() else (
        "others.wav" if (ses / "others.wav").exists() else "")
    bar = ""
    if au or has_video:
        voff = sop._voff(ses)
        bar = (f'<script>window.VOFF={voff:.2f};</script><div class="bar">'
               + (f'<video id="vid" src="screen.mp4" controls preload="metadata" '
                  f'style="max-height:150px"></video>' if has_video else "")
               + (f'<audio id="au" src="{au}" controls preload="metadata"></audio>'
                  if au else "") + "</div>")

    evh = evidence_html(ses, lg, times)
    if evh:
        nav.append(f'<a href="#sev">{e(t["evidence"])}</a>')
        body.append(f'<div class="sect" id="sev"><h2>{e(t["evidence"])}</h2>{evh}</div>')

    return f"""<!doctype html><html lang="{lg}"><meta charset="utf-8">
<meta name="mmt-sv" content="{SV}">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} - {e(t["kicker"])}</title>
<style>{report.CSS}{CSS_EXTRA}</style>
<script>try{{document.documentElement.dataset.theme=localStorage.getItem('mmt.theme')||'dark';}}catch(e){{document.documentElement.dataset.theme='dark';}}</script>
<body data-sv="{SV}" data-l="{lg}">
<div class="rail"><div class="in">
 <div class="ttl">{e(t["kicker"])} · {e(title)}</div>
 <nav>{"".join(nav)}</nav>
 <button class="thsw" id="thm" onclick="flipTheme()" role="switch" title="dark / light"><svg class="ic s" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/></svg><span class="tr"><i></i></span><svg class="ic m" viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg></button>
 <button class="btn" data-ok="{e(t["copiedall"])}" onclick="copyall(this)">{e(t["copy"])}</button>
</div></div>
<div class="wrap">
<div class="sect" id="s0"><div class="kicker">{e(t["kicker"])}</div>
<h1 style="margin:2px 0 14px;font:600 22px/1.3 var(--sans)">{e(title)}</h1>
{"".join(ban)}
<div class="kpi">{kpi}</div>
{srcln}</div>
{"".join(body)}
<p class="editv">{e(t["editv"])}</p>
</div>{bar}
<div id="lb" onclick="lbx()"><img alt=""><div class="cap"></div></div>
<script>{report.JS}{JS_EXTRA}</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="render sop.html from sop.md")
    ap.add_argument("session")
    ap.add_argument("--lang", default="", choices=["", "zh", "en"])
    a = ap.parse_args()
    ses = archive.resolve(Path(a.session).resolve())
    if not (ses / "sop.md").exists():
        print(f"no sop.md in {ses}")
        return 2
    doc = render(ses, a.lang)
    out = ses / "sop.html"
    out.write_text(doc, encoding="utf-8")
    print(f"-> {out}  ({len(doc)/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

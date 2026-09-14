"""
Render minutes.html from minutes.md. The Markdown file is what a human edits; this file
only lays it out. Correct a name in minutes.md, re-run, and the page follows.

Inputs (all inside the session folder):
    minutes.md          the summary a human owns. Scaffolded if missing.
    transcript.json     verbatim lines with timestamps and speaker, from build.py
    transcript.zh.md    optional translation, matched to the original by timestamp
    transcript.en.md    optional, for a meeting held in Chinese
    session.json / transcribe_report.json    provenance
    mic.wav / others.wav    referenced, never embedded, so long meetings stay small

Design decisions, and why:

 1. One continuous page, no tabs. The previous version put the summary behind a tab and
    the summary is the entire point of the document. Tabs hide; a sheet at the top of a
    scroll cannot be missed.
 2. The pasteable region is drawn as a physical sheet with a marked boundary, so what
    lands in Outlook is exactly what was on screen. Every element inside it carries
    inline styles, because Outlook discards <style> blocks.
 3. Verbatim is verbatim. The transcript keeps filler, repetition and false starts. A
    cleaned-up transcript cannot be used as evidence, which is the only reason to keep
    one at all.
 4. Uncertainty stays visible. Anything the pipeline could not resolve is listed rather
    than silently corrected.
 5. Light and print-shaped. Minutes get pasted, forwarded and printed, so the page is
    built for paper first and screen second.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

import minutes as M

# --------------------------------------------------------------------------- constants
# Outlook throws away <style>, so anything inside the copy zone is styled inline.
_F = "font-family:'Segoe UI',Arial,'Microsoft YaHei',sans-serif"
# Inline styles for the pasteable sheet. Same greys as the page (Ant neutrals), but
# written literally: Outlook drops <style>, and no rgba() anywhere or it renders black.
S = {
    # letterhead
    "h1": f"{_F};font-size:21px;line-height:1.35;font-weight:600;color:#0f1115;"
          "letter-spacing:-.01em;margin:0 0 3px",
    "kicker": f"{_F};font-size:11px;line-height:1.4;font-weight:600;color:#5e6ad2;"
              "letter-spacing:.10em;text-transform:uppercase;margin:0 0 6px",
    # meta panel: label / value pairs, two pairs per row
    "mk": f"{_F};font-size:10.5px;line-height:1.5;font-weight:600;color:#6b7280;"
          "letter-spacing:.07em;text-transform:uppercase;padding:5px 10px 5px 0;"
          "vertical-align:top;white-space:nowrap",
    "mv": f"{_F};font-size:13px;line-height:1.55;color:#0f1115;padding:4px 28px 4px 0;"
          "vertical-align:top",
    # the numbers that carry the meeting, so nobody has to mine them out of the prose
    "fk": f"{_F};font-size:10.5px;line-height:1.4;font-weight:600;color:#6b7280;"
          "letter-spacing:.07em;text-transform:uppercase;padding:0 0 3px",
    "fv": f"{_F};font-size:19px;line-height:1.2;font-weight:600;color:#0f1115;padding:0",
    # body
    "p": f"{_F};font-size:14px;line-height:1.7;color:#0f1115;margin:0 0 12px",
    "ul": "margin:0 0 12px;padding-left:20px",
    "li": f"{_F};font-size:14px;line-height:1.7;color:#0f1115;margin:0 0 7px",
    # tables read as a report table: dark header, hairline rows, alternating bands
    "tb": f"{_F};border-collapse:collapse;width:100%;font-size:14px;margin:0 0 14px",
    "th": f"{_F};text-align:left;font-size:11px;font-weight:600;color:#ffffff;"
          "letter-spacing:.06em;text-transform:uppercase;background:#0f1115;"
          "padding:8px 12px",
    "th1": f"{_F};text-align:center;font-size:11px;font-weight:600;color:#ffffff;"
           "letter-spacing:.06em;background:#0f1115;padding:8px 10px;width:44px",
    "td": f"{_F};font-size:14px;line-height:1.6;color:#0f1115;padding:9px 12px;"
          "border-bottom:1px solid #e5e7eb;vertical-align:top",
    "td1": "font-family:'Consolas','Courier New',monospace;font-size:12px;font-weight:600;"
           "color:#5e6ad2;text-align:center;padding:9px 10px;"
           "border-bottom:1px solid #e5e7eb;vertical-align:top;white-space:nowrap",
    "hr": "border:none;border-top:1px solid #e5e7eb;margin:26px 0 16px",
}
# Front-matter keys that belong in the header block of the mail, in this order.
HEAD_KEYS = [("date", "Date", "日期"), ("time", "Time", "时间"),
             ("duration", "Duration", "时长"), ("location", "Location", "地点"),
             ("organizer", "Organizer", "组织者"), ("attendees", "Attendees", "参会人"),
             ("distribution", "Distribution", "分发"), ("recording", "Recording", "录音")]

CSS = """
/* Enterprise (dark cloud-platform panels) — the same token set as the app shell
   mmt/ui.html, because the report is a window of the same tool, not a web page.
   The exception is .sheet: the minutes themselves stay white paper, because that
   block is pasted into Outlook and has to look like what the reader will receive. */
:root{
--bg:#08090a;--pane:#0f1011;--pane-2:#141516;--pane-3:#191a1b;
--line:rgba(255,255,255,.07);--hair:rgba(255,255,255,.045);
--ink:#f7f8f8;--dim:#d0d6e0;--faint:#9096a0;
--acc:#5e6ad2;--acc-hi:#7170ff;--link:#9db4ff;
--ok:#27a644;--warn:#f2c94c;--bad:#eb5757;--info:#5ac8fa;
--okbg:rgba(39,166,68,.15);--warnbg:rgba(242,201,76,.15);
--badbg:rgba(235,87,87,.15);--soft:rgba(90,200,250,.15);
/* on white, #9ca3af measured 2.54:1 - a caption nobody could read. 4.83:1 now. */
--paper:#fff;--paper-ink:#0f1115;--paper-dim:#42474f;--paper-faint:#6b7280;
--paper-line:#e5e7eb;
--r:6px;--r-lg:10px;
--sans:"Segoe UI Variable Text","Segoe UI","Microsoft YaHei",-apple-system,sans-serif;
--mono:"IBM Plex Mono","Cascadia Mono",Consolas,ui-monospace,monospace}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:60px;-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 var(--sans);
 -webkit-font-smoothing:antialiased}
a{color:var(--link);text-decoration:none}
a:hover{color:var(--link)}
::selection{background:var(--acc);color:#fff}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:#2c2c33;border-radius:5px;border:2px solid var(--bg)}

/* title bar: identity, jumps, and the one button that matters */
.rail{position:sticky;top:0;z-index:20;height:44px;background:var(--pane-2);
 border-bottom:1px solid var(--line)}
.rail .in{max-width:1180px;margin:0 auto;padding:0 12px;height:44px;display:flex;
 gap:12px;align-items:center}
.rail .ttl{font-size:13px;font-weight:600;color:var(--ink);flex:1;min-width:0;
 overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rail nav{display:flex;gap:2px;padding:2px;background:var(--bg);
 border:1px solid var(--line);border-radius:var(--r)}
.rail nav a{font-size:12px;color:var(--dim);padding:3px 10px;border-radius:2px}
.rail nav a:hover{background:var(--pane-3);color:var(--ink)}

/* controls */
.btn{font:500 12px/1 var(--sans);height:26px;padding:0 11px;border-radius:var(--r);
 border:1px solid var(--acc);background:var(--acc);color:#fff;cursor:pointer;
 white-space:nowrap;display:inline-flex;align-items:center;gap:6px;transition:.12s}
.btn:hover{background:var(--acc-hi);border-color:var(--acc-hi)}
.btn:active{transform:translateY(1px)}
.btn.g{background:var(--pane-3);color:var(--ink);border-color:var(--line)}
.ic{width:13px;height:13px;flex:none;fill:none;stroke:currentColor;stroke-width:1.7;
  stroke-linecap:round;stroke-linejoin:round}
.thsw{display:inline-flex;align-items:center;gap:5px;height:24px;padding:0 6px;cursor:pointer;
  border:1px solid var(--line);border-radius:999px;background:var(--pane-3);color:var(--faint)}
.thsw .tr{width:24px;height:13px;flex:none;position:relative;border-radius:999px;
  background:var(--pane-2);border:1px solid var(--line)}
.thsw .tr i{position:absolute;top:1px;left:1px;width:9px;height:9px;border-radius:50%;
  background:var(--faint);transition:transform .16s ease,background .16s ease}
.thsw .tr i{transform:translateX(11px)}
.thsw.lt .tr i{transform:translateX(0);background:#f59e0b}
/* .thsw sets display, and an author rule beats the UA rule for [hidden] - which is
   why hiding this switch inside the app silently did nothing. */
[hidden]{display:none!important}
.thsw.lt .ic.s{color:#f59e0b}
.thsw:not(.lt) .ic.m{color:var(--acc)}
.btn.g:hover{background:#26262d;border-color:#3a3a44}
.btn.g.on{color:var(--link);border-color:var(--acc-hi);background:rgba(94,106,210,.22)}
.btn.sm{height:22px;padding:0 8px;font-size:11px}
/* on the white sheet the same button has to be a light-surface button */
.sheet .btn.g{background:#f3f4f6;color:var(--paper-ink);border-color:var(--paper-line)}
.sheet .btn.g:hover{background:#e5e7eb;border-color:#cbd5e1}

.wrap{max-width:1180px;margin:0 auto;padding:16px 12px 96px}
.sect{margin:0 0 20px}
.sect>h2{font-size:13px;font-weight:600;color:var(--ink);margin:0 0 3px}
.sect>.lede{font-size:12px;color:var(--faint);margin:0 0 10px;max-width:88ch;line-height:1.6}
.sect>.lede code{font-family:var(--mono)}

/* Minutes are a summary. The verbatim record is evidence kept beside the summary, not
   the document itself, so it ships folded and says how much is inside. */
details.sect>summary{list-style:none;cursor:pointer;display:flex;align-items:center;
 gap:8px;padding:0 0 3px}
details.sect>summary::-webkit-details-marker{display:none}
details.sect>summary::before{content:"\25B6";font-size:8px;color:var(--faint);
 transition:transform .18s;display:inline-block}
details.sect[open]>summary::before{transform:rotate(90deg)}
details.sect>summary>h2{margin:0}
details.sect>summary .cnt{font:11px var(--mono);color:var(--faint)}
details.sect>summary:hover>h2,details.sect>summary:hover .cnt{color:var(--link)}

/* The sheet is white in BOTH themes on purpose: it is the email body, and copyz()
   copies the live computed styles, so a dark sheet would paste dark into Outlook.
   This caption says so out loud, otherwise the white block reads as a styling bug. */
.pvw{display:flex;align-items:center;gap:7px;margin:0 2px 9px;
 font-size:12px;line-height:1.5;color:var(--faint)}
.pvw svg{width:13px;height:13px;flex:none;stroke:currentColor;stroke-width:1.7;fill:none;
 stroke-linecap:round;stroke-linejoin:round}
[data-pl]{display:none}
body[data-l=zh] [data-pl=zh],body[data-l=en] [data-pl=en]{display:inline}
/* the sheet is the copy zone, drawn as paper so WYSIWYG is literal */
.sheet{background:var(--paper);color:var(--paper-ink);border-radius:var(--r-lg);
 padding:36px 44px;max-width:860px;box-shadow:0 1px 2px rgba(0,0,0,.5),0 12px 28px rgba(0,0,0,.35)}
.subj{display:flex;gap:12px;align-items:baseline;border-bottom:1px solid var(--paper-line);
 padding-bottom:14px;margin-bottom:22px}
.subj .lbl{font:11px/1.6 var(--mono);color:var(--paper-faint);padding-top:3px;
 white-space:nowrap;text-transform:uppercase;letter-spacing:.06em}
.subj .val{font-size:15px;font-weight:600;flex:1;color:var(--paper-ink)}
.edge{max-width:860px;font:11px var(--mono);color:var(--faint);
 display:flex;align-items:center;gap:8px;margin:0 0 7px}
.edge:after{content:"";flex:1;border-top:1px dashed var(--line)}
.edge.b{margin:7px 0 0}
.edge.b:before{content:"";flex:1;border-top:1px dashed var(--line)}
.edge.b:after{content:none}

/* transcript */
.tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 10px}
.tx{background:var(--pane);border:1px solid var(--line);border-radius:var(--r-lg);
 padding:2px 18px 6px}
.turn{display:grid;grid-template-columns:74px 1fr;gap:0 14px;
 padding:11px 0;border-bottom:1px solid var(--hair)}
.turn:last-child{border-bottom:none}
.turn .m{padding-top:2px}
.turn .who{font-size:12px;font-weight:600;line-height:1.5}
.turn .t{display:block;margin-top:2px;font:11px/1.4 var(--mono);
 color:var(--faint);background:none;border:none;padding:0;cursor:pointer;text-align:left}
.turn .t:hover{color:var(--link);text-decoration:underline}
.turn .bd{border-left:2px solid var(--hair);padding-left:14px}
.turn .o,.turn .z{font-size:13.5px;line-height:1.65}
.turn .z{color:var(--dim)}
.turn .o+.z{margin-top:7px;padding-top:7px;border-top:1px dotted var(--line)}
/* One switch drives the whole document. data-orig says which language the recording
   was in, so "show Chinese" resolves to the original on a Chinese meeting and to the
   translation on an English one, without the page needing to know anything else. */
.sheet[data-l]{display:none}
body[data-l=en] .sheet[data-l=en],body[data-l=zh] .sheet[data-l=zh]{display:block}
body[data-l=en][data-orig=en] .turn .z,body[data-l=zh][data-orig=zh] .turn .z{display:none}
body[data-l=en][data-orig=zh] .turn .o,body[data-l=zh][data-orig=en] .turn .o{display:none}
body.pair[data-l][data-orig] .turn .o,
body.pair[data-l][data-orig] .turn .z{display:block}
body.pair .tx .turn{grid-template-columns:74px 1fr 1fr}
body.pair .turn .o+.z{margin:0;padding:0 0 0 14px;border:none;
 border-left:1px solid var(--hair)}
/* segmented control */
.seg{display:flex;padding:2px;gap:2px;border-radius:var(--r);background:var(--pane-2);
 border:1px solid var(--line)}
.seg button{font:500 12px/1 var(--sans);padding:4px 11px;border:none;border-radius:2px;
 background:none;color:var(--dim);cursor:pointer}
.seg button:hover{color:var(--ink)}
.seg button.on{background:var(--pane-3);color:var(--ink);box-shadow:0 0 0 1px var(--line)}
.chk{font-size:12px;color:var(--dim);display:flex;gap:7px;align-items:center;
 cursor:pointer;padding:4px}
.chk input{accent-color:var(--acc-hi)}
.s0{color:var(--info-t)}.s1{color:#c9b8ff}.s2{color:var(--ok-t)}
.s3{color:var(--warn-t)}.s4{color:#f7b0d6}.s5{color:#5eead4}
.turn.g0 .bd{border-left-color:#1e4d73}.turn.g1 .bd{border-left-color:#4c3b7a}
.turn.g2 .bd{border-left-color:#1c5a45}.turn.g3 .bd{border-left-color:#6b4d15}
.turn.g4 .bd{border-left-color:#6b2545}.turn.g5 .bd{border-left-color:#155e5e}
.turn.flag{background:var(--warnbg);margin:0 -18px;padding:11px 18px}
.mark{font-size:12px;color:var(--warn);padding:7px 0;border-bottom:1px solid var(--hair)}
.mark.bad{color:var(--bad-t)}

/* appendix panels */
details.ap{background:var(--pane);border:1px solid var(--line);border-radius:var(--r-lg);
 margin:0 0 8px;overflow:hidden}
details.ap>summary{cursor:pointer;padding:0 12px;height:34px;font-size:13px;font-weight:500;
 list-style:none;display:flex;gap:8px;align-items:center;background:var(--pane-2)}
details.ap>summary::-webkit-details-marker{display:none}
details.ap>summary:hover{background:var(--pane-3)}
details.ap>summary:before{content:"\25B6";color:var(--faint);font-size:8px;
 transition:transform .18s;display:inline-block}
details.ap[open]>summary:before{transform:rotate(90deg)}
details.ap>summary .n{margin-left:auto;font:11px var(--mono);color:var(--faint);font-weight:400}
details.ap .bdy{padding:10px 12px 14px;border-top:1px solid var(--line)}
table.d{width:100%;border-collapse:collapse;font-size:13px}
table.d th{text-align:left;color:var(--dim);font-weight:600;font-size:11px;
 letter-spacing:.06em;text-transform:uppercase;padding:6px 10px;background:var(--pane-2);
 border-bottom:1px solid var(--line)}
table.d td{padding:7px 10px;border-bottom:1px solid var(--hair);vertical-align:top;
 color:var(--dim)}
table.d tr:last-child td{border-bottom:none}
/* the provenance table puts its label in a th on every row */
table.d tr>th:first-child:not(:only-child){background:none;border-bottom:1px solid var(--hair);
 text-transform:none;letter-spacing:0;font-size:12px;color:var(--faint);white-space:nowrap;
 width:150px}
.pill{display:inline-flex;align-items:center;height:20px;font-size:11px;padding:0 7px;
 border-radius:3px;background:var(--pane-3);color:var(--dim);border:1px solid var(--line);
 white-space:nowrap}
.pill.ok{color:var(--ok-t);background:var(--okbg);border-color:rgba(39,166,68,.4)}
.pill.warn{color:var(--warn-t);background:var(--warnbg);border-color:rgba(242,201,76,.4)}
.pill.bad{color:var(--bad-t);background:var(--badbg);border-color:rgba(235,87,87,.4)}
.why{color:var(--faint);font-size:12px;line-height:1.6}
.empty{color:var(--faint);font-size:12px;line-height:1.6}
code{background:var(--pane-3);padding:1px 5px;border-radius:3px;font:12px var(--mono);
 color:var(--dim)}
.sheet code{background:#f3f4f6;color:var(--paper-ink)}
/* alerts */
.banner{border-radius:var(--r);padding:9px 11px;margin:0 0 8px;font-size:13px;
 display:flex;gap:10px;align-items:flex-start;line-height:1.6;max-width:860px;
 border:1px solid transparent}
.banner.warn{background:var(--warnbg);border-color:rgba(245,158,11,.32);color:#fde68a}
.banner.bad{background:var(--badbg);border-color:rgba(239,68,68,.32);color:#fecaca}
.banner.info{background:var(--soft);border-color:rgba(56,189,248,.28);color:#bae6fd}
/* the player is a status bar, fixed where a tool keeps it */
.bar{position:fixed;left:0;right:0;bottom:0;background:var(--pane-2);
 border-top:1px solid var(--line);padding:6px 12px;display:flex;gap:14px;
 align-items:center;z-index:19}
.bar audio{flex:1;height:30px;max-width:640px;filter:invert(.92) hue-rotate(180deg)}
.bar label{font-size:12px;color:var(--dim);display:flex;gap:6px;align-items:center;
 cursor:pointer}
.frames{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}
.frames figure{margin:0}
.frames img{width:100%;border:1px solid var(--line);border-radius:var(--r)}
.frames figcaption{font-size:12px;color:var(--faint);margin-top:6px}
.frames figure{cursor:pointer}
.frames figure:hover img{border-color:var(--acc)}
#vid{width:100%;max-height:60vh;background:#000;border:1px solid var(--line);
 border-radius:var(--r);margin-bottom:8px}
.hint{font-size:12px;color:var(--faint);margin:0 0 12px}
@media (max-width:820px){.wrap{padding:12px 10px 96px}.sheet{padding:22px 18px}
 .rail nav{display:none}.turn{grid-template-columns:1fr;gap:6px}
 .turn .bd{padding-left:12px}
 body.pair .tx .turn{grid-template-columns:1fr}
 body.pair .turn .o+.z{margin-top:7px;padding:7px 0 0;border:none;
  border-top:1px dotted var(--line)}}
/* Scrollbars, checkboxes and native popups are drawn by the browser and default to
   the light palette; this is the one declaration that tells it otherwise. */
:root{color-scheme:dark}
:root[data-theme=light]{color-scheme:light}
/* ============================================================ light theme
   The sheet is white paper in both themes; this is only the chrome around it. One
   switch in the title bar, remembered in localStorage, same key as the app shell. */
:root[data-theme=light]{
/* Same four surfaces as the app shell. White paper on a near-white canvas is one
   white field; the canvas is a real grey so the sheet reads as a sheet. */
--bg:#e8ebf0;--pane:#ffffff;--pane-2:#f5f6f9;--pane-3:#e6e9ef;
--line:rgba(16,24,40,.14);--hair:rgba(16,24,40,.08);
--ink:#101828;--dim:#475467;--faint:#667085;
--link:#0b5aa8;
--ok:#059669;--warn:#b45309;--bad:#dc2626;--info:#0284c7;
--okbg:rgba(5,150,105,.10);--warnbg:rgba(180,83,9,.10);
--badbg:rgba(220,38,38,.09);--soft:rgba(2,132,199,.09)}
[data-theme=light] a:hover{color:#083c73}
[data-theme=light] ::-webkit-scrollbar-thumb{background:#cbcbd2}
[data-theme=light] .btn.g:hover{background:#e6e6ea;border-color:#c6c6ce}
[data-theme=light] .btn.g.on{color:#0b5aa8;background:rgba(12,92,171,.09)}
[data-theme=light] .sheet{box-shadow:0 1px 3px rgba(16,24,40,.10),0 10px 30px rgba(16,24,40,.10)}
[data-theme=light] .edge:after,[data-theme=light] .edge.b:before{border-top-color:#c9c9d0}
[data-theme=light] .turn .o+.z{border-top-color:#d4d4d8}
[data-theme=light] .bar audio{filter:none}
[data-theme=light] .mark.bad{color:#b91c1c}
[data-theme=light] .pill.ok{color:#04654a;border-color:rgba(5,150,105,.3)}
[data-theme=light] .pill.warn{color:#8a4009;border-color:rgba(180,83,9,.3)}
[data-theme=light] .pill.bad{color:#a01c1c;border-color:rgba(220,38,38,.3)}
[data-theme=light] .banner.warn{color:#7c3a06;border-color:rgba(180,83,9,.28)}
[data-theme=light] .banner.bad{color:#991b1b;border-color:rgba(220,38,38,.28)}
[data-theme=light] .banner.info{color:#075985;border-color:rgba(2,132,199,.28)}
[data-theme=light] .s0{color:#0369a1}[data-theme=light] .s1{color:#6d28d9}
[data-theme=light] .s2{color:#047857}[data-theme=light] .s3{color:#a16207}
[data-theme=light] .s4{color:#be185d}[data-theme=light] .s5{color:#0f766e}
[data-theme=light] .turn.g0 .bd{border-left-color:#93c5fd}
[data-theme=light] .turn.g1 .bd{border-left-color:#8b6fe0}
[data-theme=light] .turn.g2 .bd{border-left-color:var(--ok-t)}
[data-theme=light] .turn.g3 .bd{border-left-color:var(--warn-t)}
[data-theme=light] .turn.g4 .bd{border-left-color:#c85fa0}
[data-theme=light] .turn.g5 .bd{border-left-color:#5eead4}
@media print{.rail,.bar,.tools,.edge,.btn{display:none!important}
 body{background:#fff;color:#0f1115}.wrap{padding:0;max-width:none}
 .pvw{display:none}
 .sheet{box-shadow:none;padding:0;max-width:none}
 .sect{page-break-before:always;margin:0}.sect:first-of-type{page-break-before:auto}
 details.ap{border:none}details.ap>summary{display:none}
 body.pair[data-l][data-orig] .turn .z{display:block}}
"""

JS = r"""
function seek(s){var a=document.getElementById('au');
 var v=document.getElementById('vid');
 /* the mp4 clock starts at 0 when the capture started, which can be minutes into the
    meeting; VOFF carries that gap so a picture, the video and the audio line up. */
 if(v){var o=(window.VOFF||0);var w=Math.min(Math.max(0,s-o),Math.max(0,(v.duration||1e9)-0.1));
  if(!isNaN(w))v.currentTime=w;}
 if(!a)return;a.currentTime=Math.max(0,s-1.0);a.play();}
function track(v){var a=document.getElementById('au');var t=a.currentTime;
 a.src=v;a.currentTime=t;}
/* NOT named lang(): inside an inline onclick the scope chain includes the button, and
   every HTMLElement has a .lang property, which shadowed the function and made the
   language switch a silent no-op. Any handler name here must not be an element property. */
function setTheme(t){document.documentElement.dataset.theme=t;
 var dark=t!=='light',b=document.getElementById('thm');
 if(b){b.classList.toggle('lt',!dark);b.setAttribute('aria-checked',dark?'false':'true');}
 try{localStorage.setItem('mmt.theme',t);}catch(e){}}
/* Inside the app this page is the third column, so appearance is the app's switch and
   not a second one sitting next to it. An iframe that has already painted will not
   re-read localStorage, so the shell says which palette it is in. Standalone in a
   browser tab the page keeps its own switch. */
addEventListener('message',function(e){var d=e.data||{};
 if(d.mmt==='theme'&&d.theme)setTheme(d.theme);
 /* The shell's language switch reaches the document too, but only for a language this
    document actually has - a meeting minuted only in English stays English. */
 if(d.mmt==='lang'&&d.lang&&document.querySelector('.sheet[data-l='+d.lang+']'))setLang(d.lang);});
if(window.parent!==window){addEventListener('DOMContentLoaded',function(){
 var b=document.getElementById('thm');if(b)b.hidden=true;});}
function flipTheme(){setTheme(document.documentElement.dataset.theme==='light'?'dark':'light');}
function setLang(l){document.body.dataset.l=l;
 document.querySelectorAll('[data-lg]').forEach(b=>b.classList.toggle('on',b.dataset.lg==l));
 try{localStorage.setItem('mmt.lang',l);}catch(e){}}
function pair(on){document.body.classList.toggle('pair',on);
 try{localStorage.setItem('mmt.pair',on?'1':'');}catch(e){}}
function vis(sel){return [...document.querySelectorAll(sel)].find(n=>n.offsetParent!==null);}
function copyz(btn){
 var n=vis('.cz');if(!n)return;
 var r=document.createRange();r.selectNodeContents(n);
 var s=window.getSelection();s.removeAllRanges();s.addRange(r);
 var ok=false;try{ok=document.execCommand('copy');}catch(e){}
 s.removeAllRanges();
 var o=btn.dataset.o||btn.textContent;btn.dataset.o=o;
 btn.textContent=ok?'\u5df2\u590d\u5236 \u00b7 \u7c98\u8fdb Outlook':'\u590d\u5236\u5931\u8d25';
 setTimeout(()=>btn.textContent=o,2400);}
function copytxt(btn,sel){
 var n=vis(sel);if(!n)return;
 var ta=document.createElement('textarea');ta.value=n.textContent.trim();
 document.body.appendChild(ta);ta.select();
 var ok=false;try{ok=document.execCommand('copy');}catch(e){}
 ta.remove();
 var o=btn.dataset.o||btn.textContent;btn.dataset.o=o;
 btn.textContent=ok?'\u5df2\u590d\u5236':'\u5931\u8d25';
 setTimeout(()=>btn.textContent=o,1800);}
document.addEventListener('DOMContentLoaded',()=>{
 setTheme(document.documentElement.dataset.theme||'dark');
 var d=document.body.dataset.def||'en',l=d;
 try{l=localStorage.getItem('mmt.lang')||d;}catch(e){}
 if(!document.querySelector('.sheet[data-l='+l+']'))l=d;
 setLang(l);
 var pr=false;try{pr=!!localStorage.getItem('mmt.pair');}catch(e){}
 var c=document.getElementById('pairbox');
 if(c){c.checked=pr;pair(pr);}
 /* Last, so the shell's reply lands on a page that has stopped changing its own mind. */
 if(window.parent!==window){try{parent.postMessage({mmt:'paper-ready'},'*');}catch(e){}}});
"""


# --------------------------------------------------------------------------- helpers
def hhmmss(t: float) -> str:
    t = max(0.0, float(t))
    return f"{int(t//3600):02d}:{int(t%3600//60):02d}:{int(t%60):02d}"


def e(s) -> str:
    return html.escape(str(s or ""))


def blocks_html(md: str, inline_style: bool) -> str:
    """Render minutes Markdown. inline_style=True for the copy zone, False elsewhere."""
    out = []
    for b in M.md_blocks(md):
        st = (lambda k: f' style="{S[k]}"') if inline_style else (lambda k: "")
        if b["k"] == "p":
            out.append(f'<p{st("p")}>{M.inline(b["text"])}</p>')
        elif b["k"] in ("ul", "ol"):
            tag = b["k"]
            li = "".join(f'<li{st("li")}>{M.inline(x)}</li>' for x in b["items"])
            out.append(f'<{tag}{st("ul")}>{li}</{tag}>')
        elif b["k"] == "table" and inline_style:
            # An action-items table is the part a reader acts on, so it gets a real header
            # band, banded rows and a monospaced id column. Bands are per-row inline
            # styles because Outlook has no :nth-child.
            th = "".join(f'<th style="{S["th1"] if i == 0 else S["th"]}">{M.inline(c)}</th>'
                         for i, c in enumerate(b["head"]))
            tr = ""
            for j, r in enumerate(b["rows"]):
                band = "background:#f9fafb;" if j % 2 else ""
                tr += "<tr>" + "".join(
                    f'<td style="{band}{S["td1"] if i == 0 else S["td"]}">{M.inline(c)}</td>'
                    for i, c in enumerate(r)) + "</tr>"
            out.append(f'<table style="{S["tb"]}"><tr>{th}</tr>{tr}</table>')
        elif b["k"] == "table":
            th = "".join(f"<th>{M.inline(c)}</th>" for c in b["head"])
            tr = "".join("<tr>" + "".join(f"<td>{M.inline(c)}</td>" for c in r)
                         + "</tr>" for r in b["rows"])
            out.append(f'<table class="d"><tr>{th}</tr>{tr}</table>')
    return "".join(out)


WIDE = {"attendees", "distribution", "recording"}


def _h2(n: int, text: str) -> str:
    """A numbered section heading. Four numbered blocks make the shape of the document
    visible before a word is read, which is the whole job of a minutes layout."""
    return (f'<table style="{_F};border-collapse:collapse;width:100%;margin:26px 0 11px">'
            f'<tr><td style="width:22px;padding:0 9px 7px 0;vertical-align:middle;'
            f'border-bottom:1px solid #e5e7eb">'
            f'<div style="{_F};width:20px;height:20px;background:#0f1115;color:#ffffff;'
            f'font-size:11px;font-weight:700;line-height:20px;text-align:center">{n}</div>'
            f'</td><td style="{_F};font-size:15px;font-weight:600;color:#0f1115;'
            f'padding:0 0 7px;border-bottom:1px solid #e5e7eb">{e(text)}</td></tr></table>')


def _meta_panel(fm: dict, lg: str) -> str:
    """Date, attendees, distribution: the facts of the meeting, in a panel rather than
    loose above the text, so the reader can see where the prose starts."""
    pairs = []
    for key, en, zh in HEAD_KEYS:
        v = fm.get(key)
        if not v:
            continue
        wide = key in WIDE or isinstance(v, list)
        v = "<br>".join(e(x) for x in v) if isinstance(v, list) else e(v)
        pairs.append((zh if lg == "zh" else en, v, wide))
    if not pairs:
        return ""
    rows, buf = [], []
    def flush():
        if buf:
            rows.append("<tr>" + "".join(
                f'<td style="{S["mk"]}">{k}</td><td style="{S["mv"]}">{v}</td>'
                for k, v in buf) + ("" if len(buf) == 2 else
                                    f'<td style="{S["mv"]}"></td><td style="{S["mv"]}"></td>')
                + "</tr>")
            buf.clear()
    for k, v, wide in pairs:
        if wide:
            flush()
            rows.append(f'<tr><td style="{S["mk"]}">{k}</td>'
                        f'<td colspan="3" style="{S["mv"]}">{v}</td></tr>')
        else:
            buf.append((k, v))
            if len(buf) == 2:
                flush()
    flush()
    return (f'<table style="{_F};border-collapse:collapse;width:100%;background:#f9fafb;'
            f'border:1px solid #e5e7eb;margin:0 0 22px"><tr><td style="padding:9px 14px">'
            f'<table style="{_F};border-collapse:collapse;width:100%">'
            + "".join(rows) + "</table></td></tr></table>")


def _facts_strip(facts) -> str:
    """Optional `facts:` front matter, `label | value` per line. The two or three numbers
    the meeting turned on, lifted out of the paragraph so they survive a skim."""
    items = []
    for f in (facts if isinstance(facts, list) else [facts]):
        k, _, v = str(f).partition("|")
        if v.strip():
            items.append((k.strip(), v.strip()))
    if not items:
        return ""
    w = int(100 / len(items))
    tds = "".join(
        f'<td style="width:{w}%;padding:0 8px 0 0;vertical-align:top">'
        f'<table style="{_F};border-collapse:collapse;width:100%;background:#f9fafb;'
        f'border-left:3px solid #5e6ad2"><tr><td style="padding:9px 12px">'
        f'<div style="{S["fk"]}">{e(k)}</div><div style="{S["fv"]}">{e(v)}</div>'
        f"</td></tr></table></td>" for k, v in items)
    return (f'<table style="{_F};border-collapse:collapse;width:100%;margin:0 0 20px">'
            f"<tr>{tds}</tr></table>")


def sheet_html(d: dict, lg: str, fallback_title: str) -> str:
    """One language of the pasteable minutes. Everything inside .cz is inline-styled,
    because Outlook discards <style> and a set of minutes that loses its table on paste
    is worse than no minutes."""
    fm, secs = d["meta"], d["sections"]
    title = fm.get("title") or fallback_title
    subject = fm.get("subject") or (f"会议纪要 | {title}" if lg == "zh"
                                    else f"Meeting notes | {title}")
    kicker = "会议纪要" if lg == "zh" else "Meeting notes"
    body = [f'<table style="{_F};border-collapse:collapse;width:100%;margin:0 0 16px">'
            f'<tr><td style="border-top:3px solid #5e6ad2;padding:12px 0 0">'
            f'<div style="{S["kicker"]}">{kicker}</div>'
            f'<div style="{S["h1"]}">{e(title)}</div></td></tr></table>']
    body.append(_meta_panel(fm, lg))
    for k in ("greeting", "intro"):
        if fm.get(k):
            body.append(f'<p style="{S["p"]}">{M.inline(fm[k])}</p>')
    if fm.get("facts"):
        body.append(_facts_strip(fm["facts"]))
    for n, (h, md) in enumerate(secs, 1):
        body.append(_h2(n, h))
        body.append(blocks_html(md, True))
    if fm.get("signoff"):
        body.append(f'<hr style="{S["hr"]}">')
        body.append(f'<p style="{S["p"]}">'
                    + "<br>".join(e(x) for x in str(fm["signoff"]).split("\n")) + "</p>")
    cp = "复制标题" if lg == "zh" else "Copy subject"
    return (f'<div class="sheet" data-l="{lg}">'
            f'<div class="subj"><span class="lbl">Subject</span>'
            f'<span class="val">{e(subject)}</span>'
            f'<button class="btn g sm" onclick="copytxt(this,\'.sheet .subj .val\')">'
            f"{cp}</button></div>"
            f'<div class="cz">{"".join(body)}</div></div>')


TS_RE = re.compile(r"^\*\*\[(\d+):(\d\d):(\d\d)\]\s*(.*?)\*\*\s*:\s*(.*)$")


def load_translation(p: Path) -> dict[int, str]:
    """{start_second: text} keyed by the timestamp in the heading, so a translator can
    reorder or reflow lines without breaking the pairing."""
    if not p.exists():
        return {}
    out = {}
    for ln in p.read_text(encoding="utf-8").split("\n"):
        m = TS_RE.match(ln.strip())
        if m:
            out[int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))] = m.group(5).strip()
    return out


# ------------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--me", default="", help="name used when scaffolding minutes.md")
    args = ap.parse_args()
    ses = Path(args.session)

    tr = json.loads((ses / "transcript.json").read_text(encoding="utf-8"))
    meta = json.loads((ses / "session.json").read_text(encoding="utf-8")) \
        if (ses / "session.json").exists() else {}
    rep = json.loads((ses / "transcribe_report.json").read_text(encoding="utf-8")) \
        if (ses / "transcribe_report.json").exists() else {}
    fj = ses / "frames" / "frames.json"      # frames.py writes it beside the PNGs
    fmeta = json.loads(fj.read_text(encoding="utf-8")) if fj.exists() else {}
    frames = fmeta.get("frames") or []
    voff = float(fmeta.get("offset") or 0.0)   # meeting seconds at which the capture began
    spkinfo = json.loads((ses / "speakers.json").read_text(encoding="utf-8")) \
        if (ses / "speakers.json").exists() else {}

    # minutes.md is the primary language (its front matter may say which, default en);
    # minutes.zh.md / minutes.en.md are the other one. Two files rather than one
    # bilingual file, so each is valid Markdown that can be sent as it stands.
    docs: dict[str, dict] = {}
    for fn, forced in (("minutes.md", None), ("minutes.zh.md", "zh"),
                       ("minutes.en.md", "en")):
        fp = ses / fn
        if fp.exists():
            d = M.load(fp)
            docs.setdefault(forced or (d["meta"].get("lang") or "en"), d)
    scaffolded = False
    if not docs:
        (ses / "minutes.md").write_text(M.scaffold(ses, tr, meta, args.me), encoding="utf-8")
        docs["en"] = M.load(ses / "minutes.md")
        scaffolded = True
    prime = docs.get("en") or next(iter(docs.values()))
    fm = prime["meta"]

    has_audio = (ses / "mic.wav").exists() or (ses / "others.wav").exists()
    sensitive = bool(tr.get("sensitive") or meta.get("sensitive"))
    susp = tr.get("suspend_events") or meta.get("suspend_events") or []
    dropped = tr.get("dropped_hallucinations") or []
    pranges = tr.get("private_ranges") or []
    bookmarks = tr.get("bookmarks") or []
    talk = tr.get("talk_time_s") or {}
    lines = tr.get("lines") or []

    # ---------------------------------------------------------------- banners
    ban = []
    if scaffolded:
        ban.append(("info", "&#9998;",
                    "<div><b>minutes.md 刚刚被创建为空白模板</b>：里面全是 <code>TBD</code>。"
                    "写完再重跑一次这个脚本，这一页就会跟着更新。"
                    "<br><span style=\"opacity:.75\">A blank minutes.md was scaffolded. "
                    "Fill it in and re-run.</span></div>"))
    unconf = [c for c in (spkinfo.get("clusters") or {}).values()
              if c.get("confidence") != "high"]
    if unconf:
        ban.append(("warn", "&#128100;",
                    f"<div><b>{len(unconf)} 个声音还没确认是谁</b>：写成 <code>Speaker N</code> "
                    "或带「推测」的都是猜的。跑一次 "
                    "<code>python mmt/speakers.py &lt;session&gt; --confirm</code> 即可记住。</div>"))
    if susp:
        det = ", ".join(f"{hhmmss(x.get('t') or 0)} (~{float(x.get('gap_s') or 0):.0f}s)"
                        for x in susp)
        ban.append(("bad", "&#9888;",
                    f"<div><b>录音有缺口</b>：机器被挂起 {len(susp)} 次（{e(det)}），"
                    "这几段音频根本没录到，对应时间的内容可能整段缺失。</div>"))
    if pranges:
        # A private range removes BOTH tracks, and record.py also stops encoding the
        # screen while it is on. Say so plainly: the old wording only mentioned "your
        # own speech", which was wrong and gave a false sense of what stayed in.
        by = tr.get("private_cut_by_speaker") or {}
        who = "，".join(f"{e(k)} {v} 行" for k, v in by.items() if v)
        shot = " 录屏当时也停了。" \
               if int((meta.get("video") or {}).get("dropped_private") or 0) > 0 else ""
        ban.append(("info", "&#128274;",
                    f"<div><b>有 {len(pranges)} 段 PRIVATE</b>：这几段里 "
                    f"{tr.get('private_cut_s', 0):.0f} 秒、"
                    f"{tr.get('private_cut_lines', 0)} 行话"
                    + (f"（{who}）" if who else "")
                    + "已按你当时按 <code>p</code> 的意愿从文字里删掉，"
                      "<b>你和对方两条轨都删</b>。" + shot
                    + "原始录音还在磁盘上。</div>"))
    if sensitive:
        ban.append(("info", "&#128465;",
                    "<div><b>敏感会话</b>：" + ("录音已删除，时间戳不再跳音频。"
                     if not has_audio else
                     "录音还在磁盘上。跑 <code>python mmt/purge.py &lt;session&gt;</code> "
                     "可以只留文字。") + "</div>"))
    ban_html = "".join(f'<div class="banner {c}"><span>{i}</span>{b}</div>' for c, i, b in ban)

    # ------------------------------------------------- part 1: the pasteable sheet
    title = fm.get("title") or tr.get("title") or ses.name
    sheets = "".join(sheet_html(docs[lg], lg, title) for lg in ("en", "zh") if lg in docs)
    sheet = sheets

    # -------------------------------------------------------- part 2: verbatim
    zh = load_translation(ses / "transcript.zh.md")
    en2 = load_translation(ses / "transcript.en.md")
    alt = zh or en2
    alt_label = "中文" if zh else "English"

    pal = [k for k, _ in sorted((tr.get("speakers", {}).get("by_speaker") or {}).items(),
                                key=lambda kv: -kv[1])]

    def base(w: str) -> str:
        w = str(w or "?")
        return w[:-1] if w.endswith("?") and not w.endswith("?)") else w

    for l in lines:
        if base(l.get("speaker")) not in pal:
            pal.append(base(l.get("speaker")))

    marks = [("bk", float(t)) for t in bookmarks] + \
            [("gap", float(x.get("t") or 0.0)) for x in susp]
    marks.sort(key=lambda kv: kv[1])
    turns = []

    def flush(upto: float) -> None:
        while marks and marks[0][1] <= upto:
            k, t = marks.pop(0)
            if k == "bk":
                turns.append(f'<div class="mark">&#9873; 你在这里打了书签 · bookmark '
                             f'{hhmmss(t)}</div>')
            else:
                turns.append(f'<div class="mark bad">&#9888; 机器在这里被挂起，这一段没有录音 · '
                             f'audio gap {hhmmss(t)}</div>')

    for l in lines:
        st = float(l["start"])
        flush(st)
        spk = l.get("speaker") or "?"
        idx = pal.index(base(spk)) % 6
        cls = "turn g%d" % idx + (" flag" if l.get("low_conf") else "")
        z = alt.get(int(st))
        turns.append(
            f'<div class="{cls}" id="L{round(st*10)}">'
            f'<div class="m"><div class="who s{idx}">{e(spk)}</div>'
            f'<button class="t" onclick="seek({st})">{hhmmss(st)}</button></div>'
            f'<div class="bd"><div class="o">{e(l.get("text"))}</div>'
            + (f'<div class="z">{e(z)}</div>' if z else "") + "</div></div>")
    flush(float("inf"))

    tools = ['<div class="tools">']
    if alt:
        tools.append('<label class="chk"><input type="checkbox" id="pairbox" '
                     'onchange="pair(this.checked)"> 双语对照并排 · side by side</label>')
    else:
        tools.append('<span class="why">没有 <code>transcript.zh.md</code>，'
                     '所以逐字稿只有原文一种语言。</span>')
    tools.append("</div>")

    # ------------------------------------------------------- part 3: appendix
    ap_ = []
    # phonetic.py hands over {"heard -> guess": count}. It annotates, never rewrites, so
    # every row here is a suggestion the reader is expected to overrule; on real
    # conversational English most of them are false positives.
    ph = tr.get("phonetic_guesses") or {}
    if isinstance(ph, list):
        ph = {f'{g.get("heard")} -> {g.get("guess")}': 1 for g in ph if isinstance(g, dict)}
    amb = tr.get("ambiguous_terms_present") or []
    if amb or ph:
        rows = ""
        for k, n in sorted(ph.items(), key=lambda kv: -kv[1]):
            heard, _, guess = str(k).partition(" -> ")
            rows += (f'<tr><td><code>{e(heard)}</code></td><td>{e(guess)}</td>'
                     f'<td>{n}</td></tr>')
        ap_.append(("没听准的词 · Near-misses to overrule", sum(ph.values()) + len(amb),
                    '<div class="why" style="margin-bottom:10px">逐字稿里带 <code>[?]</code> '
                    '的都在这里。词表只标注、不改写,所以每一条都可能是误报 —— '
                    '把它当提示看,不要当结论。</div>'
                    + (f'<table class="d"><tr><th>听到的</th><th>词表猜的</th><th>次数</th></tr>'
                       f"{rows}</table>" if rows else "")
                    + (f'<div class="why" style="margin-top:12px">上下文相关、未自动改写：'
                       f'{e(", ".join(amb))}</div>' if amb else "")))
    if spkinfo.get("clusters"):
        rows = ""
        for cid, c in sorted(spkinfo["clusters"].items(),
                             key=lambda kv: -(kv[1].get("talk_time_s") or 0)):
            pill = {"high": '<span class="pill ok">已确认</span>',
                    "low": '<span class="pill warn">推测</span>'}.get(
                        c.get("confidence"), '<span class="pill">未命名</span>')
            au = (f'<audio controls preload="none" src="{e(c["clip"])}"></audio>'
                  if c.get("clip") else "")
            cand = ("；".join(f'{e(x["name"])} {x["score"]:.2f}'
                             for x in (c.get("candidates") or [])[:3]))
            rows += (f'<tr><td><b>{e(c.get("speaker_full") or c.get("speaker"))}</b><br>{pill}'
                     f'</td><td>{c.get("talk_time_s", 0):.0f}s</td>'
                     f'<td class="why">{cand}</td><td>{au}</td></tr>')
        ap_.append(("说话人是怎么认出来的 · Speaker attribution",
                    len(spkinfo["clusters"]),
                    '<div class="why" style="margin-bottom:10px">声纹聚类、本机声纹库和 Outlook '
                    '受邀名单三者一致才写真名，否则保留 Speaker N。</div>'
                    f'<table class="d"><tr><th>是谁</th><th>时长</th><th>声纹比对</th>'
                    f'<th>试听</th></tr>{rows}</table>'))
    if dropped:
        rows = "".join(
            f'<tr><td>{hhmmss(d.get("start") or 0)}</td>'
            f'<td><span class="pill">{e(d.get("track") or "?")}</span></td>'
            f'<td style="text-decoration:line-through;color:#666b73">'
            f'{e((d.get("text") or "").strip())}</td>'
            f'<td class="why">{e(d.get("why"))}</td></tr>' for d in dropped)
        ap_.append(("过滤掉的幻听 · Dropped as hallucination", len(dropped),
                    '<div class="why" style="margin-bottom:10px">Whisper 拿到非语音音频时会编出字来。'
                    '这些段没有进纪要，原文留在这里给你否决。</div>'
                    f'<table class="d"><tr><th>时间</th><th>轨</th><th>原文</th><th>为什么</th></tr>'
                    f"{rows}</table>"))
    if frames:
        # Clicking a picture jumps the video AND the audio to that moment, which is the whole
        # reason the frames carry a meeting-clock timestamp.
        figs = "".join(
            f'<figure onclick="seek({float(f.get("t") or 0):.2f})" '
            f'title="跳到 {hhmmss(f.get("t") or 0)}">'
            f'<img src="frames/{e(f["file"])}" loading="lazy">'
            f'<figcaption>{hhmmss(f.get("t") or 0)} {e(f.get("caption_zh") or "")}</figcaption>'
            "</figure>" for f in frames)
        vid = (f'<script>window.VOFF={voff:.2f};</script>'
               f'<video id="vid" src="screen.mp4" controls preload="metadata"></video>'
               '<div class="hint"><span data-pl="zh">点任意一张图，录屏和录音都会跳到那一刻。'
               '</span><span data-pl="en">Click any picture and both the video and the audio '
               'jump to that moment.</span></div>') if (ses / "screen.mp4").exists() else ""
        ap_.append(("演示画面 · Screen key frames", len(frames),
                    f'{vid}<div class="frames">{figs}</div>'))

    prov = ("<table class=\"d\">"
            f'<tr><th>录音时长</th><td>{(meta.get("duration_s") or 0)/60:.1f} min</td></tr>'
            f'<tr><th>转写模型</th><td><code>{e(rep.get("model"))}</code> · '
            f'{e(rep.get("speed") or "")}</td></tr>'
            f'<tr><th>发言时长</th><td>'
            + ", ".join(f"{e(k)} {v/60:.1f} min"
                        for k, v in sorted(talk.items(), key=lambda kv: -kv[1])) + "</td></tr>"
            f'<tr><th>词表自动修正</th><td>'
            + (", ".join(f"{e(k)} x{v}" for k, v in
                         (tr.get("glossary_corrections") or {}).items()) or "-") + "</td></tr>"
            f'<tr><th>低置信度行</th><td>{tr.get("low_conf_lines", 0)} / {len(lines)}</td></tr>'
            f'<tr><th>过滤掉的幻听</th><td>{len(dropped)} 段</td></tr>'
            f'<tr><th>录音缺口</th><td>{(str(len(susp)) + " 次挂起") if susp else "无"}</td></tr>'
            f'<tr><th>会话目录</th><td><code>{e(ses.name)}</code></td></tr>'
            "</table>")
    ap_.append(("这份稿子怎么来的 · Provenance", "", prov))

    ap_html = "".join(
        f'<details class="ap"><summary>{e(t)}'
        + (f'<span class="n">{n}</span>' if n != "" else "")
        + f'</summary><div class="bdy">{b}</div></details>' for t, n, b in ap_)

    orig = "zh" if en2 else "en"
    deflang = "zh" if "zh" in docs else next(iter(docs))
    seg = ('<div class="seg">'
           '<button data-lg="zh" onclick="setLang(\'zh\')">中文</button>'
           '<button data-lg="en" onclick="setLang(\'en\')">English</button></div>'
           if len(docs) > 1 else "")

    n_turns = sum(1 for t in turns if 'class="t"' in t or "class='t'" in t) or len(turns)
    src = "mic.wav" if (ses / "mic.wav").exists() else "others.wav"
    bar = (('<div class="bar">'
            f'<audio id="au" src="{src}" controls preload="metadata"></audio>'
            '<label><input type="checkbox" '
            "onchange=\"track(this.checked?'others.wav':'mic.wav')\">听对方轨</label></div>")
           if has_audio else "")

    doc = f"""<!doctype html><html lang="zh"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} - Minutes</title><style>{CSS}</style>
<script>try{{document.documentElement.dataset.theme=localStorage.getItem('mmt.theme')||'dark';}}catch(e){{document.documentElement.dataset.theme='dark';}}</script>
<body data-l="{deflang}" data-orig="{orig}" data-def="{deflang}">
<div class="rail"><div class="in">
 <div class="ttl">{e(title)}</div>
 <nav><a href="#s1">纪要</a><a href="#s2" onclick="document.getElementById('s2').open=true">逐字稿</a><a href="#s3">附录</a></nav>
 {seg}
 <button class="thsw" id="thm" onclick="flipTheme()" role="switch" title="深色 / 浅色外观"><svg class="ic s" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/></svg><span class="tr"><i></i></span><svg class="ic m" viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg></button>
 <button class="btn" onclick="copyz(this)">复制邮件正文 · Copy body</button>
</div></div>
<div class="wrap">
{ban_html}
<div class="sect" id="s1">
<div class="pvw"><svg viewBox="0 0 24 24"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg><span data-pl="zh">下面这张白纸就是收件人在 Outlook 里看到的样子，所以它不跟着界面变深色。</span><span data-pl="en">The white sheet below is exactly what the recipient sees in Outlook, which is why it stays white in dark mode.</span></div>
{sheet}</div>
<details class="sect" id="s2"><summary><h2>逐字原文</h2>
<span class="cnt">{n_turns} 段 · 全文留档</span></summary>
{''.join(tools)}<div class="tx">{''.join(turns) or '<div class="empty">no lines</div>'}</div></details>
<div class="sect" id="s3"><h2>技术附录</h2>
{ap_html}</div>
</div>{bar}
<script>{JS}</script></body></html>"""

    out = ses / "minutes.html"
    out.write_text(doc, encoding="utf-8")
    print(f"-> {out}  ({len(doc)/1024:.0f} KB)"
          + ("  [scaffolded minutes.md]" if scaffolded else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

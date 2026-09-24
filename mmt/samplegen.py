"""A narrated fake walkthrough, so the operating-manual path can be developed.

Nobody should have to film their real desktop to test this, and the one screen recording
on this machine is eight seconds of a bug that has since been fixed. So this draws a
plausible console, narrates a script over it with the speech synthesis Windows already
ships, and leaves behind a session the ordinary pipeline can pick up unchanged:

    <staging>/zz-sop-sample/
      others.wav     the narration, 16 kHz mono - the track the transcriber reads
      screen.mp4     3 fps, ten distinct screens, each timed to its sentence
      session.json   what the recorder would have written

    python samplegen.py [--name zz-sop-sample] [--zh] [--keep-parts]

The drawn screens stay in English even with --zh. The point of --zh is the narration
language, which is what the manual is supposed to follow; the UI text on screen is the
thing a manual must quote verbatim, so it has to look like real UI either way.

Dev fixture. It writes into the staging directory like any other session, and deleting
that folder is the whole cleanup.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

W, H, FPS = 1280, 800, 3
GAP = 0.55                                 # silence between sentences, seconds

# (screen, sentence in English, sentence in Chinese)
SCRIPT = [
    ("login", "Okay, let me walk you through the weekly leakage check.",
     "好，我把每周的 leakage 检查过一遍给你看。"),
    ("login2", "First open the Utilization Console and sign in with your corporate account.",
     "先打开 Utilization Console，用公司账号登录。"),
    ("landing", "On the landing page, click Weekly Report in the left sidebar.",
     "进来以后，点左边那一栏的 Weekly Report。"),
    ("table", "That opens the table you see here, one row per agent for last week.",
     "打开的就是你现在看到的这张表，上周每个 agent 一行。"),
    ("colc", "Look at column C, Leakage Hours. Anything above forty is what we care about.",
     "看 column C，Leakage Hours。超过四十的才是我们要管的。"),
    ("rowsel", "Row four is at sixty one hours, so select that row and click Investigate.",
     "第四行是六十一个小时，所以选中那一行，点 Investigate。"),
    ("dialog", "This dialog asks for a reason code. Pick Schedule Mismatch unless it is a leave issue.",
     "这个框要你选 reason code。不是休假的问题就选 Schedule Mismatch。"),
    ("dialog2", "Then type a short note in the comment box. Keep it to one line.",
     "然后在 comment 框里写一句话，就一行，别写长。"),
    ("green", "Click Save. The row turns green, which means it is closed for this week.",
     "点 Save。那一行变绿，就说明这周结掉了。"),
    ("export", "Last step, click Export at the top right and save the file to the team folder.",
     "最后一步，点右上角 Export，把文件存到团队的文件夹里。"),
]

PS = r"""
param([string]$Spec,[string]$Out,[string]$Voice)
Add-Type -AssemblyName System.Speech
$lines = [System.IO.File]::ReadAllText($Spec, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
foreach ($v in $s.GetInstalledVoices()) {
  if ($v.VoiceInfo.Name -like "*$Voice*") { $s.SelectVoice($v.VoiceInfo.Name); break }
}
$s.Rate = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
  $p = Join-Path $Out ("part_{0:d2}.wav" -f $i)
  $s.SetOutputToWaveFile($p, $fmt)
  $s.Speak([string]$lines[$i])
}
$s.SetOutputToNull()
$s.Dispose()
Write-Output ("voice: " + $s.Voice.VoiceInfo.Name)
"""


# --------------------------------------------------------------------------- the drawing
def _font(size: int, mono: bool = False) -> ImageFont.FreeTypeFont:
    names = ("consola.ttf", "cour.ttf") if mono else ("segoeuib.ttf", "segoeui.ttf", "arial.ttf")
    for n in names:
        p = Path("C:/Windows/Fonts") / n
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:                                  # noqa: BLE001, PERF203
                pass
    return ImageFont.load_default()


F = {k: _font(s) for k, s in (("h", 26), ("b", 17), ("s", 14))}
FM = _font(17, mono=True)
ROWS = [("AGT-1041", "38.0", "12.5"), ("AGT-1042", "40.0", "6.0"),
        ("AGT-1043", "39.5", "8.0"), ("AGT-1044", "41.0", "61.0"),
        ("AGT-1045", "37.0", "4.5"), ("AGT-1046", "40.0", "19.0")]
NAV = ("Overview", "Weekly Report", "Exceptions", "Settings")


def _chrome(d: ImageDraw.ImageDraw, page: str) -> None:
    d.rectangle([0, 0, W, 44], fill=(48, 52, 60))
    d.rounded_rectangle([14, 10, 300, 36], 5, fill=(88, 94, 104))
    d.text((26, 15), "Utilization Console", font=F["s"], fill=(235, 238, 242))
    d.rectangle([0, 44, W, 92], fill=(255, 255, 255))
    d.rounded_rectangle([16, 56, W - 16, 82], 13, fill=(240, 242, 245))
    d.text((32, 61), "https://utilization.internal/" + page, font=F["s"], fill=(96, 102, 112))


def _shell(active: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    im = Image.new("RGB", (W, H), (246, 247, 249))
    d = ImageDraw.Draw(im)
    _chrome(d, active.lower().replace(" ", "-"))
    d.rectangle([0, 92, 232, H], fill=(255, 255, 255))
    d.line([232, 92, 232, H], fill=(224, 227, 232))
    for i, n in enumerate(NAV):
        y = 124 + i * 44
        if n == active:
            d.rounded_rectangle([12, y - 8, 220, y + 26], 6, fill=(226, 238, 255))
            d.rectangle([12, y - 8, 15, y + 26], fill=(34, 110, 220))
        d.text((32, y), n, font=F["b"], fill=(24, 28, 34) if n == active else (92, 98, 108))
    return im, d


def _table(d: ImageDraw.ImageDraw, *, hl_col: bool = False, sel: int = -1,
           green: int = -1, toolbar: bool = False) -> None:
    x0, y0 = 264, 124
    d.text((x0, y0), "Weekly Report", font=F["h"], fill=(20, 24, 30))
    d.text((x0, y0 + 38), "Week 39 - 6 agents", font=F["s"], fill=(110, 116, 126))
    if toolbar:
        d.rounded_rectangle([x0, y0 + 66, x0 + 124, y0 + 98], 5, fill=(34, 110, 220))
        d.text((x0 + 22, y0 + 74), "Investigate", font=F["b"], fill=(255, 255, 255))
    ty = y0 + 118
    cols = (("A  Agent", 0), ("B  Paid Hours", 300), ("C  Leakage Hours", 520))
    if hl_col:
        d.rectangle([x0 + 508, ty - 6, x0 + 720, ty + 34 + len(ROWS) * 44], fill=(255, 246, 205))
    d.rectangle([x0, ty, x0 + 860, ty + 34], fill=(242, 244, 247))
    for name, dx in cols:
        d.text((x0 + 14 + dx, ty + 8), name, font=F["s"], fill=(70, 76, 86))
    for i, (a, p, lk) in enumerate(ROWS):
        ry = ty + 34 + i * 44
        if i == sel:
            d.rectangle([x0, ry, x0 + 860, ry + 44], fill=(222, 235, 255))
        if i == green:
            d.rectangle([x0, ry, x0 + 860, ry + 44], fill=(222, 245, 226))
        d.line([x0, ry + 44, x0 + 860, ry + 44], fill=(232, 235, 240))
        d.text((x0 + 14, ry + 12), a, font=FM, fill=(30, 34, 40))
        d.text((x0 + 314, ry + 12), p, font=FM, fill=(30, 34, 40))
        d.text((x0 + 534, ry + 12), lk, font=FM,
               fill=(200, 40, 40) if float(lk) > 40 else (30, 34, 40))


def _modal(im: Image.Image, d: ImageDraw.ImageDraw, note: str = "") -> None:
    im.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), Image.new("L", (W, H), 90))
    mx, my, mw, mh = 340, 220, 600, 360
    d.rounded_rectangle([mx, my, mx + mw, my + mh], 10, fill=(255, 255, 255))
    d.text((mx + 28, my + 26), "Investigate AGT-1044", font=F["h"], fill=(20, 24, 30))
    d.text((mx + 28, my + 76), "Reason code", font=F["s"], fill=(96, 102, 112))
    d.rounded_rectangle([mx + 28, my + 100, mx + mw - 28, my + 138], 5,
                        outline=(198, 203, 210), width=1)
    d.text((mx + 42, my + 110), "Schedule Mismatch", font=F["b"], fill=(30, 34, 40))
    d.text((mx + 28, my + 158), "Comment", font=F["s"], fill=(96, 102, 112))
    d.rounded_rectangle([mx + 28, my + 182, mx + mw - 28, my + 258], 5,
                        outline=(198, 203, 210), width=1)
    if note:
        d.text((mx + 42, my + 194), note, font=F["b"], fill=(30, 34, 40))
    d.rounded_rectangle([mx + mw - 128, my + mh - 62, mx + mw - 28, my + mh - 24], 5,
                        fill=(34, 110, 220))
    d.text((mx + mw - 108, my + mh - 54), "Save", font=F["b"], fill=(255, 255, 255))


def screen(key: str) -> Image.Image:
    if key in ("login", "login2"):
        im = Image.new("RGB", (W, H), (240, 243, 247))
        d = ImageDraw.Draw(im)
        _chrome(d, "sign-in")
        d.rounded_rectangle([440, 200, 840, 560], 10, fill=(255, 255, 255))
        d.text((478, 240), "Sign in", font=F["h"], fill=(20, 24, 30))
        for i, lab in enumerate(("Corporate account", "Password")):
            y = 310 + i * 86
            d.text((478, y), lab, font=F["s"], fill=(96, 102, 112))
            d.rounded_rectangle([478, y + 22, 802, y + 60], 5, outline=(198, 203, 210), width=1)
            if key == "login2":
                d.text((492, y + 30), "agent@example.com" if i == 0 else "........", font=FM,
                       fill=(30, 34, 40))
        d.rounded_rectangle([478, 486, 802, 526], 5, fill=(34, 110, 220))
        d.text((618, 496), "Sign in", font=F["b"], fill=(255, 255, 255))
        return im
    if key == "landing":
        im, d = _shell("Overview")
        d.text((264, 124), "Overview", font=F["h"], fill=(20, 24, 30))
        d.text((264, 172), "Pick a report on the left to begin.", font=F["b"], fill=(110, 116, 126))
        for i in range(3):
            d.rounded_rectangle([264 + i * 210, 220, 264 + i * 210 + 186, 340], 8,
                                fill=(255, 255, 255), outline=(228, 231, 236), width=1)
        return im
    im, d = _shell("Weekly Report")
    if key == "table":
        _table(d)
    elif key == "colc":
        _table(d, hl_col=True)
    elif key == "rowsel":
        _table(d, hl_col=True, sel=3, toolbar=True)
    elif key == "dialog":
        _table(d, sel=3, toolbar=True)
        _modal(im, d)
    elif key == "dialog2":
        _table(d, sel=3, toolbar=True)
        _modal(im, d, "Roster published late, shift ended 21:00")
    elif key == "green":
        _table(d, green=3)
    elif key == "export":
        _table(d, green=3)
        d.rounded_rectangle([1000, 124, 1120, 158], 5, fill=(34, 110, 220))
        d.text((1024, 132), "Export", font=F["b"], fill=(255, 255, 255))
        d.rounded_rectangle([968, 166, 1160, 268], 8, fill=(255, 255, 255),
                            outline=(214, 218, 224), width=1)
        for i, o in enumerate(("Excel (.xlsx)", "CSV", "PDF")):
            d.text((988, 182 + i * 30), o, font=F["b"], fill=(40, 44, 52))
    return im


# ------------------------------------------------------------------------------ the parts
def narrate(lines: list[str], out: Path, zh: bool) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    spec = out / "spec.json"
    spec.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    ps1 = out / "say.ps1"
    ps1.write_text(PS, encoding="utf-8")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(ps1), str(spec), str(out),
                        "Huihui" if zh else "Zira"],
                       capture_output=True, text=True, timeout=600)
    got = sorted(out.glob("part_*.wav"))
    if len(got) != len(lines):
        raise SystemExit(f"speech synthesis made {len(got)} of {len(lines)} parts\n"
                         f"{r.stdout}\n{r.stderr}")
    print(" ", r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "voice: default")
    return got


def build(name: str, zh: bool, keep: bool) -> Path:
    cfg = config.load()
    ses = Path(config.staging(cfg)) / name
    if ses.exists():
        shutil.rmtree(ses)
    ses.mkdir(parents=True)
    work = Path(tempfile.mkdtemp(prefix="mdsample-"))
    try:
        print("narrating", len(SCRIPT), "lines")
        parts = narrate([s[2] if zh else s[1] for s in SCRIPT], work, zh)

        audio, timeline, t = [], [], 0.0
        for (key, en, zhs), p in zip(SCRIPT, parts):
            a, sr = sf.read(p, dtype="float32", always_2d=False)
            if sr != 16000:
                raise SystemExit(f"{p.name} came out at {sr} Hz, expected 16000")
            timeline.append({"screen": key, "start": round(t, 2),
                             "end": round(t + len(a) / sr, 2), "text": zhs if zh else en})
            audio.append(a)
            audio.append(np.zeros(int(GAP * 16000), dtype="float32"))
            t += len(a) / sr + GAP
        sf.write(ses / "others.wav", np.concatenate(audio) * 0.9, 16000, subtype="PCM_16")
        total = t
        print(f"  others.wav {total:.1f}s")

        import av
        cache = {k: np.asarray(screen(k).convert("RGB")) for k in dict.fromkeys(
            s[0] for s in SCRIPT)}
        oc = av.open(str(ses / "screen.mp4"), "w")
        st = oc.add_stream("libx264", rate=FPS)
        st.width, st.height, st.pix_fmt = W, H, "yuv420p"
        tb = Fraction(1, 1000)
        st.time_base = tb
        st.options = {"crf": "32", "preset": "veryfast", "tune": "stillimage"}
        n = 0
        for i in range(int(total * FPS)):
            ts = i / FPS
            key = timeline[0]["screen"]
            for seg in timeline:
                if ts >= seg["start"]:
                    key = seg["screen"]
            fr = av.VideoFrame.from_ndarray(cache[key], format="rgb24").reformat(format="yuv420p")
            fr.pts, fr.time_base = int(round(ts * 1000)), tb
            for pkt in st.encode(fr):
                oc.mux(pkt)
            n += 1
        for pkt in st.encode():
            oc.mux(pkt)
        oc.close()
        mb = (ses / "screen.mp4").stat().st_size / 1e6
        print(f"  screen.mp4 {n} frames, {mb:.1f} MB, {len(cache)} distinct screens")

        started = datetime.now().replace(microsecond=0)
        (ses / "session.json").write_text(json.dumps({
            "session": name, "title": "Weekly Leakage Check (sample walkthrough)",
            "others": "", "started_local": started.isoformat(timespec="seconds"),
            "duration_s": round(total, 2), "target_sr": 16000, "sensitive": False,
            "origin": "import", "sample": True,
            "video": {"file": "screen.mp4", "frames": n, "dropped_private": 0, "fps": FPS,
                      "crf": 32, "size": f"{W}x{H}",
                      "bytes": (ses / "screen.mp4").stat().st_size,
                      "started_at_s": 0.0, "error": None},
            "tracks": {"loopback": {"file": "others.wav", "seconds": round(total, 2),
                                    "device_events": [], "error": None, "warn": None,
                                    "peak_dbfs": -1.0, "mean_dbfs": -22.0, "silent": False}},
            "marks": [], "suspend_events": [], "endpoint_switches": [], "last_probe": [],
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        (ses / "sample.timeline.json").write_text(
            json.dumps(timeline, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        if keep:
            print("  parts kept in", work)
        else:
            shutil.rmtree(work, ignore_errors=True)
    return ses


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", default="zz-sop-sample")
    ap.add_argument("--zh", action="store_true", help="narrate in Chinese instead of English")
    ap.add_argument("--keep-parts", action="store_true")
    a = ap.parse_args()
    ses = build(a.name, a.zh, a.keep_parts)
    print("\nsample session:", ses)
    print("next:  python transcribe.py --session", a.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Turn screen.mp4 into a small set of pictures a human (or an AI) can actually look at.

A one-hour meeting at 3 fps is ~11,000 frames and nobody reads that. Almost all of them are
the same slide with a moving cursor. So: decode at a low resolution and keep only frames that
differ from the last KEPT frame by more than a threshold. The result is one PNG per visible
change, named with its timestamp, plus contact sheets so a whole meeting fits in a few images.

--max is a budget, not a stop sign. Measured on a real desktop: --diff 0.02 keeps about 630
frames per hour, so a 1-hour meeting would blow a 150 cap around minute 19. Stopping there
would silently lose the whole second half, so instead the cap THINS: the least-changed half of
what is held is deleted and the threshold rises to the smallest survivor. Decoding always runs
to the end of the file.

The threshold works on a 64x64 grayscale thumbnail, which is what makes a moving mouse and a
blinking caret invisible while a slide change is obvious. Numbers printed at the end are real
counts, not estimates: decoded / kept / skipped.

On --diff: the score is a mean over the WHOLE desktop, so a window in one corner changing from
white-on-black text to more white-on-black text moves it very little. Measured over 74s of a
real desktop with six full page changes: largest frame-to-frame score 0.0475, 99th percentile
0.0371. An earlier default of 0.06 was therefore unreachable and kept exactly one frame out of
224. A max-over-16-tiles score was tried instead and was far too chatty (2,200 frames/hour on
the same footage), so the whole-desktop mean stays and only the threshold moved.

    python frames.py ../sessions/<session> [--max 150] [--diff 0.02] [--width 1280] [--sheet 3]
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import av
import numpy as np


def thumb(frame, size: int = 64) -> np.ndarray:
    """A tiny grayscale version: the fingerprint we compare frames on."""
    img = frame.to_ndarray(format="gray8")
    h, w = img.shape
    ys = (np.linspace(0, h - 1, size)).astype(np.int32)
    xs = (np.linspace(0, w - 1, size)).astype(np.int32)
    return img[ys][:, xs].astype(np.float32) / 255.0


def hms(t: float) -> str:
    return f"{int(t // 3600):02d}{int(t % 3600 // 60):02d}{int(t % 60):02d}"


def clock(t: float) -> str:
    return f"{int(t // 3600):d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"


def thin(out: Path, kept: list[dict], budget: int) -> tuple[list[dict], float, int]:
    """Budget full: delete the least-changed half and raise the bar to the smallest survivor,
    so decoding can carry on to the end of the meeting instead of stopping mid-way."""
    order = sorted(kept, key=lambda k: k["change"], reverse=True)
    live = order[:max(1, budget // 2)]
    for k in order[len(live):]:
        (out / k["file"]).unlink(missing_ok=True)
    ids = {id(k) for k in live}
    return [k for k in kept if id(k) in ids], min(k["change"] for k in live), len(order) - len(live)


def extract(video: Path, out: Path, max_frames: int, diff: float, width: int,
            offset: float = 0.0) -> dict:
    """`offset` = seconds into the meeting when the screen capture started, so a recording
    switched on mid-meeting still gets frame names that match the transcript clock."""
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()

    kept: list[dict] = []
    last: np.ndarray | None = None
    decoded = 0
    floor = diff      # rises each time the budget is thinned
    thinned = 0
    with av.open(str(video)) as c:
        stream = c.streams.video[0]
        tb = float(stream.time_base or 0.0) or 1.0
        # A file written by an older build carries pts=0 on every frame; average_rate then
        # reads as nonsense too, so trust guessed_rate and fall back to the frame index.
        rate = float(stream.guessed_rate or stream.average_rate or 0) or 3.0
        if not 0.1 <= rate <= 120:
            rate = 3.0
        for frame in c.decode(stream):
            decoded += 1
            # `not frame.pts` covers both None and the all-zeros case; for a healthy file the
            # only frame with pts 0 is the first one, where both formulas give the same 0.0.
            t = offset + (float(frame.pts * tb) if frame.pts else (decoded - 1) / rate)
            small = thumb(frame)
            score = 1.0 if last is None else float(np.abs(small - last).mean())
            if score < floor:
                continue
            last = small
            img = frame.to_image()
            if img.width > width:
                img = img.resize((width, max(1, round(img.height * width / img.width))))
            name = f"_{decoded:06d}.png"      # renumbered in time order once the run is over
            img.save(out / name)
            kept.append({"file": name, "t": round(t, 2), "clock": clock(t),
                         "change": round(score, 4)})
            if len(kept) > max_frames:
                kept, floor, dropped = thin(out, kept, max_frames)
                thinned += dropped

    for i, k in enumerate(kept):
        final = f"{i:03d}_{hms(k['t'])}.png"
        (out / k["file"]).rename(out / final)
        k["file"] = final
    return {"decoded": decoded, "kept": kept, "floor": round(floor, 4), "thinned": thinned}


def sheets(out: Path, kept: list[dict], per: int, width: int = 2400) -> list[str]:
    """Contact sheets: `per`x2 grids so a whole meeting is a few images, each cell time-stamped."""
    from PIL import Image, ImageDraw

    made = []
    cols = per
    rows = 2
    step = cols * rows
    for i in range(0, len(kept), step):
        chunk = kept[i:i + step]
        thumbs = [Image.open(out / k["file"]).convert("RGB") for k in chunk]
        cw = width // cols
        ch = max(1, round(cw * thumbs[0].height / thumbs[0].width))
        sheet = Image.new("RGB", (cw * cols, (ch + 20) * rows), "#111")
        d = ImageDraw.Draw(sheet)
        for n, (im, k) in enumerate(zip(thumbs, chunk)):
            x, y = (n % cols) * cw, (n // cols) * (ch + 20)
            sheet.paste(im.resize((cw, ch)), (x, y + 20))
            d.text((x + 4, y + 5), f"{i + n:03d}  {k['clock']}", fill="#fff")
            im.close()
        name = f"sheet_{i // step + 1}.png"
        sheet.save(out / name)
        made.append(name)
    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--max", type=int, default=150, help="hard cap on kept frames")
    ap.add_argument("--diff", type=float, default=0.02,
                    help="0-1 mean pixel change needed to count as a new screen")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--sheet", type=int, default=3, help="cells per row in the contact sheets")
    args = ap.parse_args()

    ses = Path(args.session)
    video = ses / "screen.mp4"
    if not video.exists():
        print(f"!! no screen.mp4 in {ses} - this session was recorded without --video")
        return 1

    out = ses / "frames"
    offset = 0.0
    try:  # the mp4 clock restarts at 0 even when the capture began mid-meeting
        meta = json.loads((ses / "session.json").read_text(encoding="utf-8"))
        offset = float((meta.get("video") or {}).get("started_at_s") or 0.0)
    except Exception:
        pass
    res = extract(video, out, args.max, args.diff, args.width, offset)
    kept = res["kept"]
    if not kept:
        print("!! nothing kept - try a smaller --diff")
        return 1

    sh = sheets(out, kept, args.sheet) if len(kept) > 1 else []
    total = sum(p.stat().st_size for p in out.glob("*.png"))
    (out / "frames.json").write_text(json.dumps(
        {"video": video.name, "decoded": res["decoded"], "kept": len(kept),
         "diff": args.diff, "floor": res["floor"], "thinned": res["thinned"],
         "width": args.width, "offset": round(offset, 2), "sheets": sh, "frames": kept},
        ensure_ascii=False, indent=1), encoding="utf-8")

    span = kept[-1]["t"] - kept[0]["t"]
    print(f"decoded {res['decoded']} frames, kept {len(kept)} "
          f"({100 * len(kept) / max(1, res['decoded']):.0f}%), skipped "
          f"{res['decoded'] - len(kept)} near-identical")
    if res["thinned"]:
        print(f"budget  {max(1, args.max)} reached, thinned {res['thinned']} of the smallest "
              f"changes away; threshold rose {args.diff} -> {res['floor']}")
    print(f"span    {clock(kept[0]['t'])} -> {clock(kept[-1]['t'])} "
          f"({span / 60:.1f} min), one picture every {span / max(1, len(kept) - 1):.0f}s on average")
    print(f"sheets  {len(sh)} contact sheet(s), {args.sheet}x2 cells each")
    if len(kept) == 1:
        print("        only one picture kept - the screen barely changed, or --diff is too high")
    print(f"size    {total / 1e6:.1f} MB in {out}")
    print(f" -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Transcribe a recorded session.

Two modes:
  one-shot : session already finished -> transcribe both tracks end to end
  --live   : run this WHILE the meeting is going. It tails the growing WAVs and
             transcribes in ~5 minute chunks, always cutting at a silent point so
             no word is ever split. By the time you press stop, ~95% is done and
             you wait a minute or two, not twenty.

Output: <track>.segments.json  (segments + word timings + detected language)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).parent))
try:
    import lexicon
except Exception:                                            # noqa: BLE001
    lexicon = None

SR = 16000
STALE = 90.0      # no heartbeat from the recorder for this long and it is not coming back
TRACKS = ("others.wav", "mic.wav")
MIN_CHUNK_S = 300.0     # don't bother transcribing less than this while live
TAIL_SEARCH_S = 25.0    # look for a silent cut point in this much of the tail
MIN_CUT_GAP_S = 0.35    # a cut point needs at least this much quiet
POLL_S = 10.0


def _load_glossary(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"hotwords": [], "fix_after": {}}


def _frames(path: Path) -> int:
    try:
        return sf.info(str(path)).frames
    except Exception:
        return 0


def _read(path: Path, start: int, stop: int) -> np.ndarray:
    with sf.SoundFile(str(path)) as f:
        f.seek(start)
        return f.read(frames=max(0, stop - start), dtype="float32", always_2d=False)


def _find_cut(x: np.ndarray, min_keep: int) -> int:
    """
    Index (relative to x) of the quietest short window in the tail, so we cut
    between utterances rather than mid-word. Falls back to the very end.

    `x` must be at most one chunk long. Only its last TAIL_SEARCH_S are searched, so a
    longer `x` moves that window to the end of the whole track and the answer degenerates
    to len(x) -- a plausible integer, silently, rather than an error.
    """
    win = int(0.10 * SR)
    if len(x) < min_keep + win * 4:
        return len(x)
    tail_start = max(min_keep, len(x) - int(TAIL_SEARCH_S * SR))
    seg = x[tail_start:]
    n = (len(seg) // win) * win
    if n < win * 4:
        return len(x)
    rms = np.sqrt(np.mean(seg[:n].reshape(-1, win) ** 2, axis=1))
    need = max(1, int(MIN_CUT_GAP_S / 0.10))
    # rolling minimum over `need` consecutive quiet windows
    if len(rms) <= need:
        return len(x)
    roll = np.convolve(rms, np.ones(need) / need, mode="valid")
    i = int(np.argmin(roll))
    return tail_start + (i + need) * win


class Engine:
    def __init__(self, model_name: str, threads: int, glossary: dict, batch: int):
        from faster_whisper import BatchedInferencePipeline, WhisperModel

        self.model = WhisperModel(
            model_name, device="cpu", compute_type="int8", cpu_threads=threads,
            num_workers=1,
        )
        self.pipe = BatchedInferencePipeline(model=self.model)
        self.batch = batch
        hw = glossary.get("hotwords") or []
        # Isolated by experiment: hotwords are SAFE and useful. A config with neither
        # prompt nor hotwords still transcribed two of the product names from the test
        # audio, proving those were really spoken, and adding hotwords additionally
        # recovered a personal name the bare config missed. Bare nouns cannot be copied
        # out as a sentence, so they do not contaminate.
        self.hotwords = ", ".join(hw) if hw else None
        # initial_prompt is DELIBERATELY NOT USED. The reason is measured, not cautious.
        # Two versions were tried on 90s of real accented speech from a live meeting:
        #   a prompt naming meeting facts -> emitted "it is still waiting for an access
        #       approval" and "before the next review" as if a participant had said them
        #   a style-only prompt with no facts, written specifically to be safe -> emitted
        #       "Transcribe exactly what is said, according to Chinese speakers from
        #       China" as line 1 of the transcript
        # So the failure is not about prompt CONTENT. Any English sentence in the decoder
        # context can come back out as speech when the audio is hard, and accented,
        # hesitant, restarting speech is hard. A fabricated sentence in minutes is a far
        # worse defect than a missed jargon word, so the prompt goes. Measured cost of
        # removing it: EN WER 12.4% -> 13.2%, jargon recall 11/16 -> 10/16. Worth it.
        self.prompt = None

    def run(self, audio: np.ndarray, offset_s: float, speaker_hint: str) -> list[dict]:
        if audio.size == 0:
            return []
        segments, info = self.pipe.transcribe(
            audio,
            language=None,
            multilingual=True,             # mixed EN / ZH in one meeting
            vad_filter=True,               # the big speedup: skip all the silence
            vad_parameters={"min_silence_duration_ms": 400},
            word_timestamps=True,
            without_timestamps=False,
            # condition_on_previous_text only stops loops ACROSS segments. It does
            # nothing inside one segment, and a VAD segment here runs up to 29s, which is
            # room for 50 repeats. Measured on 90s of real accented speech from a live
            # meeting: without no_repeat_ngram_size the decoder emitted a two-term
            # loop "<term A>, <term B>, <term A>, <term B>..." x50 with a compression
            # ratio of 16.3, and
            # faster-whisper's own compression_ratio_threshold=2.4 did NOT reject it,
            # because BatchedInferencePipeline skips temperature fallback entirely.
            # This one parameter took gzip ratio 16.27 -> 1.79. It is not optional.
            condition_on_previous_text=False,
            no_repeat_ngram_size=3,
            beam_size=5,
            batch_size=self.batch,
            hotwords=self.hotwords,
            initial_prompt=self.prompt,
            log_progress=False,
        )
        out = []
        for s in segments:
            out.append({
                "start": round(s.start + offset_s, 3),
                "end": round(s.end + offset_s, 3),
                "text": s.text.strip(),
                "lang": getattr(s, "language", None) or info.language,
                "avg_logprob": round(float(s.avg_logprob), 3),
                "no_speech_prob": round(float(s.no_speech_prob), 3),
                # needed by the hallucination filter: a repeated loop compresses well
                "compression_ratio": round(float(getattr(s, "compression_ratio", 0.0) or 0.0), 2),
                "track": speaker_hint,
                "words": [
                    {"w": w.word, "s": round(w.start + offset_s, 3), "e": round(w.end + offset_s, 3),
                     "p": round(float(w.probability), 3)}
                    for w in (s.words or [])
                ],
            })
        return out


def _save(path: Path, segs: list[dict], done: bool, elapsed: float) -> None:
    path.write_text(json.dumps(
        {"complete": done, "processing_seconds": round(elapsed, 1),
         "segment_count": len(segs), "segments": segs},
        ensure_ascii=False, indent=1), encoding="utf-8")


def transcribe_track(eng: Engine, wav: Path, out: Path, live: bool, session_done) -> dict:
    segs: list[dict] = []
    cursor = 0
    t_proc = 0.0
    audio_s = 0.0
    while True:
        avail = _frames(wav)
        finished = session_done()
        pending = avail - cursor
        enough = pending >= int(MIN_CHUNK_S * SR)
        if not live:
            finished = True

        if enough or (finished and pending > int(0.5 * SR)):
            # One chunk at a time, never the rest of the file. _find_cut() only searches the
            # last TAIL_SEARCH_S of what it is handed, so a block longer than one chunk puts
            # the search window at the end of the *track*: the cut degenerates to len(block)
            # and the whole recording goes into a single transcribe() call. Live mode hid
            # this because a growing file never had more than a chunk available anyway; a
            # finished file does, and a 53 minute call kills CTranslate2 with a native stack
            # overflow (0xC00000FD) after burning 40 seconds.
            block = _read(wav, cursor, min(avail, cursor + int(MIN_CHUNK_S * SR)))
            # Chunk even when the file is already complete: a 30 minute track handled in one
            # go prints nothing for a quarter of an hour, and the UI cannot tell "working"
            # from "hung". Cutting at a silent point keeps quality identical.
            if enough:
                cut = _find_cut(block, int(MIN_CHUNK_S * SR * 0.6))
            else:
                cut = len(block)
            chunk = block[:cut]
            if chunk.size > int(0.5 * SR):
                t0 = time.time()
                new = eng.run(chunk, cursor / SR, wav.stem)
                t_proc += time.time() - t0
                audio_s += len(chunk) / SR
                segs.extend(new)
                cursor += cut
                _save(out, segs, finished and cursor >= avail, t_proc)
                rt = (audio_s / t_proc) if t_proc else 0.0
                print(f"  [{wav.name}] {cursor/SR:7.1f}s done, {len(segs):4d} segs, "
                      f"{rt:4.1f}x realtime", flush=True)
            else:
                cursor = avail

        # A tail under half a second is not worth a transcribe() call, but it still has to
        # end the loop: the branch above ignores it, so cursor can never reach the end and
        # one-shot mode used to spin on a fraction of a second at 100% CPU forever.
        if finished and _frames(wav) - cursor <= int(0.5 * SR):
            break
        if not live:
            continue                      # keep going through the rest of the file at once
        time.sleep(POLL_S)

    _save(out, segs, True, t_proc)
    return {"track": wav.stem, "segments": len(segs), "audio_s": round(audio_s, 1),
            "process_s": round(t_proc, 1),
            "speed_x_realtime": round(audio_s / t_proc, 2) if t_proc else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", help="session folder")
    ap.add_argument("--live", action="store_true", help="tail growing WAVs during the meeting")
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--threads", type=int, default=0, help="0 = auto (half the cores when live)")
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--only", action="append", metavar="TRACK",
                    help="transcribe just this track (mic / others); repeatable")
    args = ap.parse_args()

    ses = Path(args.session)
    if not ses.is_dir():
        print(f"no such session: {ses}")
        return 2

    want = {o.strip().removesuffix(".wav") for o in args.only} if args.only else None
    if want is not None:
        # Check before the 30 second model load, and never silently: a typo used to skip
        # both tracks, merge the old report and exit 0, which reads as success.
        known = {Path(t).stem for t in TRACKS}
        unknown = sorted(want - known)
        if unknown:
            print(f"--only: unknown track {', '.join(unknown)}; "
                  f"expected {' or '.join(sorted(known))}")
            return 2

    # Measured: 10 threads is FASTER than 14 on this hybrid CPU (2 P + 8 E + 2 LP-E);
    # oversubscribing the efficiency cores hurts. Live mode leaves room for the meeting app.
    _cpu = os.cpu_count() or 8
    threads = args.threads or (max(2, _cpu // 2) if args.live else min(10, _cpu))
    gloss = lexicon.merged() if lexicon else _load_glossary(
        Path(__file__).with_name("glossary.base.json"))

    if args.live:
        try:  # be a good citizen: don't fight Zoom for CPU
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)  # BELOW_NORMAL
        except Exception:
            pass

    print(f"model={args.model} threads={threads} batch={args.batch} live={args.live}")
    t0 = time.time()
    eng = Engine(args.model, threads, gloss, args.batch)
    print(f"model loaded in {time.time()-t0:.1f}s")

    done_marker = ses / "session.json"
    heartbeat = ses.parent / "status.json"

    def live_done() -> bool:
        """The recording is over - politely, or because the recorder is gone.

        session.json is the polite ending. But a recorder that was killed, or a server window
        that was closed, never writes one, and then this process would keep tailing a WAV that
        stopped growing and reloading the model's context forever, at a real cost in CPU. The
        recorder rewrites status.json twice a second, so a stale one means there is nothing left
        to wait for. STALE is generous on purpose: finishing early would truncate a transcript,
        which is far worse than running 90 seconds too long.
        """
        if done_marker.exists():
            return True
        try:
            if time.time() - heartbeat.stat().st_mtime > STALE:
                print(f"  录音那边 {STALE:.0f}s 没有动静了，按结束处理")
                return True
        except OSError:
            pass
        return False

    session_done = live_done if args.live else (lambda: True)

    report = []
    for name in TRACKS:
        wav = ses / name
        if not wav.exists():
            continue
        if want is not None and wav.stem not in want:
            print(f"\n-- {name}  跳过（不在 --only 里）")
            continue
        print(f"\n-- {name}")
        report.append(transcribe_track(eng, wav, ses / f"{wav.stem}.segments.json",
                                       args.live, session_done))

    # A skipped track's numbers are still true, and build.py and report.py read this file.
    # So --only merges into the old report instead of publishing one that claims the
    # session had a single track.
    rp = ses / "transcribe_report.json"
    tracks = {t["track"]: t for t in report}
    if want is not None and rp.exists():
        try:
            for t in json.loads(rp.read_text(encoding="utf-8")).get("tracks", []):
                tracks.setdefault(t.get("track"), t)
        except (OSError, ValueError):
            pass
    order = {"others": 0, "mic": 1}
    rp.write_text(
        json.dumps({"model": args.model, "threads": threads, "batch": args.batch,
                    "tracks": sorted(tracks.values(), key=lambda t: order.get(t["track"], 9))},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    report = sorted(tracks.values(), key=lambda t: order.get(t["track"], 9))
    print("\n" + json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Who spoke on the far-end track.

`others.wav` is everyone except you, mixed into one channel by Windows. Splitting it back
apart is the whole of phase 2. Two models, both offline, both small:

  segmentation  pyannote-segmentation-3.0 exported to ONNX (6 MB) - finds speech regions
                and, inside a 10 s window, who overlaps whom
  embedding     3D-Speaker CAM++ zh+en (28 MB) - a 192-d voiceprint per region.
                The zh+en model is deliberate: the meetings are mixed Chinese/English and
                the voxceleb English-only model drops accuracy on Chinese speech.

Output is `diarization.json`: turns with an anonymous cluster id, and how long each
cluster talked. No embedding is written and none is kept - a voiceprint that outlives the
meeting is biometric data, and recognising a colleague across meetings is not worth
storing one. The embeddings the clustering needs exist only inside sherpa, for one call.

This file assigns NO names. It cannot: clustering only knows "voice 1 sounds different
from voice 2". Naming happens in whois.py, from the attendee list and from how people
address each other out loud. Keeping the two apart means a wrong name is always
correctable without re-running the models.

Usage:
    diarize.py <session>                       auto speaker count
    diarize.py <session> --threshold 0.7       split more aggressively
    diarize.py <session> --speakers 5          force a count (measured worse - see below)
    diarize.py <session> --track mic.wav       diarize your own track (rarely useful)
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf

MODELS = Path.home() / ".aki" / "models" / "sherpa"
SEG_MODEL = MODELS / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMB_MODEL = MODELS / "campplus.onnx"

# MEASURED on a 105 s, 4-speaker file with known ground truth (3 Windows TTS voices +
# one real recording). sherpa's FastClustering threshold is a DISTANCE cut, so a higher
# value merges more and yields fewer clusters:
#     thr 0.40 -> 9 clusters, 69.9% of speech attributed to the right person
#     thr 0.50 -> 8 clusters, 70.4%
#     thr 0.60 -> 6 clusters, 73.9%
#     thr 0.70 -> 5 clusters, 78.9%
#     thr 0.80 -> 4 clusters, 88.6%   <- exactly right, and a plateau to 0.95
#     thr 0.95 -> 4 clusters, 88.6%
# Detection ceiling on that file is 90.3% (the segmentation model trims turn edges), so
# 88.6% is 98% of everything it detected. Two honest caveats: TTS voices are unnaturally
# consistent so a real meeting will be worse, and forcing --speakers N made it WORSE
# (n=4 collapsed to 3 clusters, 70.7%) - the threshold path is better, so do not force it
# unless auto is clearly wrong.
DEFAULT_THRESHOLD = 0.82

SR = 16000


def _load_mono16k(p: Path) -> np.ndarray:
    x, sr = sf.read(str(p), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if sr != SR:
        # linear resample is enough: both models are fed 16 kHz and the recorder
        # already writes 16 kHz, so this is a fallback, not the normal path
        n = int(round(len(x) * SR / sr))
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype("float32")
    return x


def _check_models() -> None:
    missing = [str(p) for p in (SEG_MODEL, EMB_MODEL) if not p.exists()]
    if missing:
        raise SystemExit(
            "missing model file(s):\n  " + "\n  ".join(missing) +
            "\n\nDownload once:\n"
            "  https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2\n"
            "  https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_"
            "advanced.onnx   (upstream really does spell it 'recongition')\n"
            f"and put them under {MODELS}")


def diarize(wav: Path, speakers: int = -1, threshold: float = DEFAULT_THRESHOLD,
            threads: int = 6, progress: bool = True) -> dict:
    import sherpa_onnx as so
    _check_models()

    audio = _load_mono16k(wav)
    dur = len(audio) / SR

    cfg = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(str(SEG_MODEL)),
            num_threads=threads),
        embedding=so.SpeakerEmbeddingExtractorConfig(
            model=str(EMB_MODEL), num_threads=threads),
        clustering=so.FastClusteringConfig(
            num_clusters=int(speakers) if speakers and speakers > 0 else -1,
            threshold=float(threshold)),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    sd = so.OfflineSpeakerDiarization(cfg)
    if sd.sample_rate != SR:
        raise SystemExit(f"model wants {sd.sample_rate} Hz, we fed {SR}")

    t0 = time.time()
    if progress:
        def cb(processed: int, total: int) -> int:
            print(f"\r  diarizing {processed*100//max(1,total):3d}%", end="", flush=True)
            return 0
        res = sd.process(audio, callback=cb)
        print()
    else:
        res = sd.process(audio)
    took = time.time() - t0

    turns = [{"start": round(s.start, 2), "end": round(s.end, 2),
              "cluster": int(s.speaker)}
             for s in res.sort_by_start_time()]

    by_cluster: dict[int, list[dict]] = {}
    for t in turns:
        by_cluster.setdefault(t["cluster"], []).append(t)

    talk = {cid: round(sum(t["end"] - t["start"] for t in ts), 1)
            for cid, ts in sorted(by_cluster.items())}

    return {
        "track": wav.name,
        "audio_s": round(dur, 1),
        "num_speakers": int(res.num_speakers),
        "num_turns": len(turns),
        "requested_speakers": int(speakers),
        "cluster_threshold": float(threshold),
        "proc_s": round(took, 1),
        "speed_x_realtime": round(dur / took, 2) if took > 0 else None,
        "talk_time_s": talk,
        "turns": turns,
        "note": "cluster ids are anonymous, and no voiceprint is stored. whois.py names them.",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--track", default="others.wav")
    ap.add_argument("--speakers", type=int, default=-1,
                    help="force a cluster count. MEASURED WORSE than auto - only use it "
                         "when auto is obviously wrong")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help="distance cut: higher merges more voices together "
                         f"(default {DEFAULT_THRESHOLD}, measured)")
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--out", default="diarization.json")
    args = ap.parse_args()

    ses = Path(args.session)
    wav = ses / args.track
    if not wav.exists():
        print(f"no {wav}")
        return 1

    d = diarize(wav, args.speakers, args.threshold, args.threads)
    (ses / args.out).write_text(json.dumps(d, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    print(f"speakers={d['num_speakers']}  turns={d['num_turns']}  "
          f"{d['proc_s']}s ({d['speed_x_realtime']}x realtime)")
    for cid, s in sorted(d["talk_time_s"].items(), key=lambda kv: -kv[1]):
        print(f"  cluster {cid}: {s:6.1f}s")
    print(f" -> {ses / args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Merge the transcribed tracks into one human-readable transcript, then apply the
glossary. Pure text work, no models.

Phase 1 speakers:  mic.wav -> you,  others.wav -> one label (a name in a 1:1, else "Others")
Phase 2 will replace "Others" with real names from diarization + the Outlook roster.
Anything the pipeline is unsure about is marked, never silently guessed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    import phonetic
except Exception:
    phonetic = None
try:
    import halluc
except Exception:
    halluc = None
try:
    import lexicon
except Exception:
    lexicon = None

LOW_CONF = -0.65          # avg_logprob below this -> flag the line
MERGE_GAP_S = 1.2         # same speaker, gap smaller than this -> join into one line


def hhmmss(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t//3600):02d}:{int(t%3600//60):02d}:{int(t%60):02d}"


def load_glossary(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_fixers(gloss: dict) -> list[tuple[re.Pattern, str]]:
    """Longest variants first so 'UT clinic online' wins over 'UT clinic'."""
    pairs: list[tuple[str, str]] = []
    for canon, variants in (gloss.get("fix_after") or {}).items():
        for v in variants:
            if v == canon:
                continue
            pairs.append((v, canon))
    pairs.sort(key=lambda kv: -len(kv[0]))
    out = []
    for v, canon in pairs:
        # ASCII terms get word boundaries; CJK does not have them
        if re.fullmatch(r"[\x00-\x7f ]+", v):
            pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(v) + r"(?![A-Za-z0-9])", re.I)
        else:
            pat = re.compile(re.escape(v))
        out.append((pat, canon))
    return out


def apply_glossary(text: str, fixers) -> tuple[str, list[str]]:
    hits = []
    for pat, canon in fixers:
        new, n = pat.subn(canon, text)
        if n and new != text:
            hits.append(canon)
            text = new
    return text, hits


def load_track(p: Path, label: str) -> list[dict]:
    if not p.exists():
        return []
    d = json.loads(p.read_text(encoding="utf-8"))
    for s in d.get("segments", []):
        s["speaker"] = label
        s["track"] = p.name.split(".")[0]
    return d.get("segments", [])


def apply_confirm(segs: list[dict], ses: Path, guesses: dict, fixed: dict) -> dict:
    """
    A near-miss like "pipelines -> PipeIn" is a *question*, and the person who was in the
    meeting is the only one who can answer it. confirm.json holds those answers (written by
    the app). Accepted: the word is really replaced. Rejected: the "[PipeIn?]" hint
    disappears and the text keeps what was heard. A *string* answer is the third case -
    neither form was right, so the person typed the correct one and that is what lands in
    the transcript. Nothing is guessed here.
    """
    f = ses / "confirm.json"
    if not f.exists():
        return {}
    try:
        acc = (json.loads(f.read_text(encoding="utf-8")).get("accept") or {})
    except Exception:                                          # noqa: BLE001
        return {}
    accepted: dict[str, int] = {}
    rejected: list[str] = []
    for key, ok in acc.items():
        surface, _, canon = str(key).partition(" -> ")
        surface, canon = surface.strip(), canon.strip()
        if not surface or not canon:
            continue
        # what actually replaces the heard form: the glossary's canonical spelling, or the
        # one the person typed when neither of the two offered forms was right
        want = ok.strip() if isinstance(ok, str) and ok.strip() else canon
        ann = f" [{canon}?]"
        hits = 0
        for sg in segs:
            t = sg["text"]
            if ok:
                t = t.replace(surface + ann, want)
                t2 = re.sub(r"(?<![A-Za-z0-9])" + re.escape(surface) + r"(?![A-Za-z0-9])",
                            want, t, flags=re.I)
                hits += 0 if t2 == sg["text"] else 1
                t = t2
            else:
                t = t.replace(surface + ann, surface).replace(ann, "")
            sg["text"] = t
        if ok:
            accepted[f"{surface} -> {want}"] = hits or guesses.get(key, 0)
            fixed[want] = fixed.get(want, 0) + (hits or 1)
        else:
            rejected.append(key)
        guesses.pop(key, None)
    return {"accepted": accepted, "rejected": rejected,
            "at": (json.loads(f.read_text(encoding="utf-8")).get("at") or "")}


def apply_speakers(segs: list[dict], ses: Path, fallback: str = "Others") -> dict:
    """Phase 2: replace the single "Others" label with per-person labels.

    A transcript segment is attributed to the diarization cluster it overlaps most. When
    the overlap is weak (< 60% of the segment) two people were probably talking over each
    other, so the line is marked rather than assigned - a crossfire line handed to the
    wrong person is exactly the mistake that makes minutes untrustworthy.
    """
    diar = ses / "diarization.json"
    if not diar.exists():
        return {}
    try:
        d = json.loads(diar.read_text(encoding="utf-8"))
    except Exception:
        return {}
    turns = d.get("turns") or []
    if not turns:
        return {}
    names: dict[str, dict] = {}
    spk = ses / "speakers.json"
    if spk.exists():
        try:
            names = (json.loads(spk.read_text(encoding="utf-8")).get("clusters") or {})
        except Exception:
            names = {}

    stats = {"named": 0, "unnamed": 0, "overlapped": 0, "unmatched": 0, "by_speaker": {}}
    for s2 in segs:
        if s2.get("speaker") != fallback:
            continue
        a, b = float(s2["start"]), float(s2["end"])
        span = max(1e-6, b - a)
        best, bestov = None, 0.0
        for t in turns:
            o = max(0.0, min(b, t["end"]) - max(a, t["start"]))
            if o > bestov:
                best, bestov = t["cluster"], o
        if best is None:
            stats["unmatched"] += 1
            continue
        info = names.get(str(best)) or {}
        who = info.get("speaker") or f"Speaker {best}"
        conf = info.get("confidence") or "unknown"
        disp = who
        if bestov / span < 0.6:
            stats["overlapped"] += 1
            # attribution itself is shaky, not just the name. A low-confidence label already
            # ends in "?)" - do not stack a second question mark on it.
            disp = who if who.rstrip().endswith("?)") else f"{who}?"
            conf = "overlap"
        s2["speaker"] = disp
        s2["speaker_base"] = who      # talk time is per person, never per label variant
        s2["cluster"] = best
        s2["speaker_confidence"] = conf
        stats["named" if conf == "high" else "unnamed"] += 1
        stats["by_speaker"][who] = round(stats["by_speaker"].get(who, 0.0) + span, 1)
    stats["distinct"] = len(stats["by_speaker"])
    return stats


def split_on_pauses(segs: list[dict], min_gap: float) -> tuple[list[dict], int]:
    """Break a segment where the speaker actually stopped talking.

    Whisper's VAD strips silence before the model sees the audio, so one "segment" can run
    for a minute across five separate utterances - measured here: a single 2.0 -> 53.0 line
    holding five turns with 4.4s to 6.0s of silence between them. Word timestamps survive
    that, so the pauses are recoverable exactly.

    Not cosmetic. Three things key off where a line ends:
      * the timestamp you click to jump the audio or the screen recording
      * one row of the confirm desk, which a person reads and approves
      * a private range, which deletes any line it OVERLAPS - so a minute-long line touching
        a 10-second private range used to take the whole minute with it

    No text changes: the words and their order are exactly as recognised, and each piece
    keeps its own first and last word timestamp.

    One wrinkle, measured rather than guessed. The word sitting just before a pause often has
    the silence baked into its own duration: at a real 5.5s boundary the character before it
    read 6.43 -> 7.53, a 1.10s single character where every other one in the same segment ran
    0.16 to 0.40s. Split naively and that character lands at the end of the previous line
    instead of the start of the next, which is where it was actually spoken. So a word whose
    duration per character is wildly above this segment's own median is handed to the next
    group.
    """
    out: list[dict] = []
    splits = 0
    for s in segs:
        words = s.get("words") or []
        if len(words) < 2:
            out.append(s)
            continue
        per_char = sorted((float(w.get("e") or 0.0) - float(w.get("s") or 0.0))
                          / max(1, len(str(w.get("w") or "").strip()))
                          for w in words)
        med = per_char[len(per_char) // 2] or 0.0
        groups: list[list[dict]] = [[words[0]]]
        for i in range(1, len(words)):
            prev, w = words[i - 1], words[i]
            if float(w.get("s") or 0.0) - float(prev.get("e") or 0.0) < min_gap:
                groups[-1].append(w)
                continue
            stretched = med > 0 and len(groups[-1]) > 1 and (
                (float(prev.get("e") or 0.0) - float(prev.get("s") or 0.0))
                / max(1, len(str(prev.get("w") or "").strip())) > 2.5 * med)
            if stretched:
                # The silence lives inside prev, not after it, so prev starts the next line.
                # Its own stamps have to move with it or the line would claim to begin before
                # the pause, and the merge step downstream would glue the two back together.
                groups[-1].pop()
                w_s = float(w.get("s") or 0.0)
                nchar = max(1, len(str(prev.get("w") or "").strip()))
                moved = {**prev, "s": round(max(0.0, w_s - med * nchar), 3), "e": round(w_s, 3),
                         "s_raw": prev.get("s"), "e_raw": prev.get("e")}
                groups.append([moved, w])
            else:
                groups.append([w])
        if len(groups) < 2:
            out.append(s)
            continue
        pieces = []
        for g in groups:
            txt = "".join(str(w.get("w") or "") for w in g).strip()
            if not txt:
                continue
            pieces.append({**s, "start": round(float(g[0].get("s") or 0.0), 3),
                           "end": round(float(g[-1].get("e") or 0.0), 3),
                           "text": txt, "words": g,
                           "split_from": [s.get("start"), s.get("end")]})
        if len(pieces) < 2:
            out.append(s)
            continue
        out.extend(pieces)
        splits += len(pieces) - 1
    return out, splits


def private_ranges(marks: list[dict], duration: float) -> list[tuple[float, float]]:
    """marks are (kind, t) pairs written by record.py. An unclosed range runs to the end."""
    out, open_at = [], None
    for m in sorted(marks or [], key=lambda m: m.get("t") or 0.0):
        k, t = m.get("kind"), float(m.get("t") or 0.0)
        if k == "private_start" and open_at is None:
            open_at = t
        elif k == "private_end" and open_at is not None:
            if t > open_at:
                out.append((open_at, t))
            open_at = None
    if open_at is not None:
        out.append((open_at, max(open_at, duration)))
    return out


def in_private(seg: dict, ranges: list[tuple[float, float]]) -> tuple[float, float] | None:
    """A segment counts as private if it overlaps a range at all - half a private
    sentence leaking into the minutes is worse than losing a whole line."""
    a, b = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
    for lo, hi in ranges:
        if a < hi and b > lo:
            return (lo, hi)
    return None


def track_label(s: str, default: str = "Others") -> str:
    """
    One short label for the loopback track.

    In a 1:1 the name that comes in is exact and is used as given. A meeting invite for
    twenty-seven people is a different animal: it is a roster, it names nobody in
    particular, and it was arriving here as the speaker label. Every line of the
    transcript then carried four hundred characters of other people's names, the talk-time
    header read "<the whole roster> 26.0 min", and in the confirm desk the roster filled
    the row where the sentence was supposed to be. The roster is still kept, in
    session.json, as the attendee list, which is what it actually is.
    """
    s = " ".join(str(s or "").split())
    if not s:
        return default
    if len(re.split(r"[;,\u3001]", s)) > 1 or len(s) > 24:
        return default
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--you", default="You", help="how to label the microphone track")
    ap.add_argument("--others", default="Others",
                    help="how to label the loopback track. In a 1:1 this is EXACT, not a "
                         "guess: the two tracks are captured separately, so mic=you and "
                         "loopback=them with no diarisation involved. Prefer this over the "
                         "voiceprint path whenever there are only two people.")
    ap.add_argument("--split-gap", type=float, default=1.2,
                    help="split a segment wherever the speaker paused this long (seconds); "
                         "0 keeps Whisper's own segment boundaries")
    args = ap.parse_args()
    args.others = track_label(args.others)      # a roster is not a speaker name
    ses = Path(args.session)
    if args.split_gap <= 0:
        args.split_gap = 1e9

    meta = {}
    if (ses / "session.json").exists():
        meta = json.loads((ses / "session.json").read_text(encoding="utf-8"))

    # base layer (ships) + this machine's own layer (never published). See lexicon.py.
    gloss = lexicon.merged() if lexicon else load_glossary(Path(__file__).with_name("glossary.base.json"))
    fixers = build_fixers(gloss)
    pvocab = phonetic.build_vocab(gloss) if (phonetic and phonetic.HAVE) else []

    segs = load_track(ses / "mic.segments.json", args.you) + \
           load_track(ses / "others.segments.json", args.others)
    segs = [s for s in segs if s.get("text")]
    segs.sort(key=lambda s: s["start"])
    segs, n_split = split_on_pauses(segs, args.split_gap)
    segs.sort(key=lambda s: s["start"])

    spk_stats = apply_speakers(segs, ses, fallback=args.others)

    # --- privacy: NOTHING said inside a PRIVATE range reaches the text ---
    # This used to spare the loopback track, on the reasoning that the far end is still the
    # meeting. That was wrong twice over. The key is documented as "everything inside a
    # private range is cut", and it is pressed precisely when something must not be written
    # down - often something the OTHER person is saying. Cutting half of it while reporting
    # "removed on purpose" is worse than not offering the key at all.
    pranges = private_ranges(meta.get("marks") or [], float(meta.get("duration_s") or 0.0))
    private_cut_s = 0.0
    private_cut_n = 0
    private_cut_by: dict[str, int] = {}
    if pranges:
        keep = []
        for s in segs:
            if in_private(s, pranges):
                private_cut_s += float(s.get("end", 0)) - float(s.get("start", 0))
                private_cut_n += 1
                who = str(s.get("speaker") or args.others)
                private_cut_by[who] = private_cut_by.get(who, 0) + 1
                continue
            keep.append(s)
        segs = keep

    # --- hallucination filter: drops are recorded, never silent ---
    dropped: list[dict] = []
    if halluc is not None:
        segs, dropped = halluc.filter_segments(segs)

    bookmarks = [round(float(m.get("t") or 0.0), 2)
                 for m in (meta.get("marks") or []) if m.get("kind") == "bookmark"]

    fixed_terms: dict[str, int] = {}
    guess_terms: dict[str, int] = {}
    seen_pairs: set[str] = set()
    for s in segs:
        s["text_raw"] = s["text"]
        s["text"], hits = apply_glossary(s["text"], fixers)
        for h in hits:
            fixed_terms[h] = fixed_terms.get(h, 0) + 1
        if pvocab:
            s["text"], pg = phonetic.annotate(s["text"], pvocab, seen_pairs)
            for surface, canon, mr in pg:
                k = f"{surface} -> {canon}"
                guess_terms[k] = guess_terms.get(k, 0) + 1
        s["low_conf"] = bool(s.get("avg_logprob", 0.0) < LOW_CONF)

    # --- what the human confirmed in the app: apply it, and say so ---
    decided = apply_confirm(segs, ses, guess_terms, fixed_terms)

    # merge consecutive lines from the same speaker
    lines: list[dict] = []
    for s in segs:
        if lines and lines[-1]["speaker"] == s["speaker"] \
                and s["start"] - lines[-1]["end"] <= MERGE_GAP_S \
                and lines[-1]["low_conf"] == s["low_conf"]:
            lines[-1]["end"] = s["end"]
            lines[-1]["text"] += " " + s["text"]
            lines[-1]["langs"].add(s.get("lang") or "?")
            lines[-1]["end"] = s["end"]
        else:
            lines.append({"speaker": s["speaker"], "start": s["start"], "end": s["end"],
                          "text": s["text"], "low_conf": s["low_conf"],
                          "speaker_confidence": s.get("speaker_confidence"),
                          "cluster": s.get("cluster"),
                          "langs": {s.get("lang") or "?"}})

    total = sum(l["end"] - l["start"] for l in lines)
    by_spk: dict[str, float] = {}
    for l in lines:
        # one person is one row: strip the overlap marker so "Ann" and "Ann?" do not
        # show up as two different people in the talk-time summary
        _k = l.get("speaker_base") or str(l["speaker"] or "?")
        if _k.endswith("?") and not _k.endswith("?)"):
            _k = _k[:-1]
        by_spk[_k] = by_spk.get(_k, 0.0) + (l["end"] - l["start"])
    langs: dict[str, int] = {}
    for l in lines:
        for g in l["langs"]:
            langs[g] = langs.get(g, 0) + 1

    md = []
    md.append(f"# Transcript - {meta.get('title') or ses.name}\n")
    md.append(f"- session: `{ses.name}`")
    md.append(f"- started: {meta.get('started_local','?')}")
    md.append(f"- recorded: {meta.get('duration_s',0)/60:.1f} min, speech {total/60:.1f} min")
    rep = ses / "transcribe_report.json"
    if rep.exists():
        try:
            r = json.loads(rep.read_text(encoding="utf-8"))
            sx = [t.get("speed_x_realtime") for t in r.get("tracks", []) if t.get("speed_x_realtime")]
            md.append(f"- model: `{r.get('model')}`" +
                      (f", {sum(sx)/len(sx):.1f}x realtime" if sx else ""))
        except Exception:
            pass
    md.append(f"- languages: {', '.join(f'{k} ({v} lines)' for k,v in sorted(langs.items(), key=lambda kv:-kv[1]))}")
    md.append("- talk time: " + ", ".join(f"{k} {v/60:.1f} min" for k, v in
                                          sorted(by_spk.items(), key=lambda kv: -kv[1])))
    lc = sum(1 for l in lines if l["low_conf"])
    if lc:
        md.append(f"- **{lc} of {len(lines)} lines are low confidence and marked with `[?]`**")
    if spk_stats:
        md.append("- far-end speakers: " + ", ".join(
            f"{k} {v/60:.1f} min" for k, v in
            sorted(spk_stats["by_speaker"].items(), key=lambda kv: -kv[1])))
        if spk_stats.get("overlapped"):
            md.append(f"- **{spk_stats['overlapped']} line(s) marked with a trailing `?` on "
                      "the speaker**: two people overlapped there, so who said it is a guess")
        if spk_stats.get("unnamed"):
            md.append(f"- {spk_stats['unnamed']} line(s) are from a voice that is not "
                      "confirmed yet. Run `speakers.py <session> --confirm` to name it once "
                      "and it is remembered.")
    if dropped:
        md.append(f"- **{len(dropped)} segment(s) dropped as hallucinations** "
                  "(non-speech audio that Whisper turned into text); listed at the bottom")
    if pranges:
        who = ("，".join(f"{k} {v} 行" for k, v in sorted(private_cut_by.items()))
               if private_cut_by else "没有语音落在里面")
        md.append(f"- **PRIVATE: {len(pranges)} 段隐私区间，{private_cut_s:.0f}s / "
                  f"{private_cut_n} 行已按你的意思删除，两条轨都删（{who}）**")
    if bookmarks:
        md.append("- bookmarks you dropped while recording: " +
                  ", ".join(hhmmss(t) for t in bookmarks))
    if meta.get("suspend_events"):
        md.append(f"- **WARNING: the machine was suspended {len(meta['suspend_events'])}x "
                  "during this recording - the audio has holes**")
    if meta.get("recovered"):
        md.append("- **recovered session**: " + str(meta.get("recovery_note") or
                  "the recorder did not shut down cleanly; duration is inferred from the WAV"))
    if fixed_terms:
        md.append("- glossary corrections applied: " +
                  ", ".join(f"{k} x{v}" for k, v in sorted(fixed_terms.items(), key=lambda kv: -kv[1])))
    if decided.get("accepted"):
        md.append("- corrections confirmed by hand in the app: " +
                  ", ".join(f"{k} x{v}" for k, v in sorted(decided["accepted"].items(),
                                                           key=lambda kv: -kv[1])))
    if decided.get("rejected"):
        md.append("- guesses rejected by hand in the app (text keeps what was heard): " +
                  ", ".join(sorted(decided["rejected"])))
    if guess_terms:
        md.append("- **phonetic near-misses, NOT corrected** - the text keeps what was heard and "
                  "adds `[Canon?]`; judge each from context: " +
                  ", ".join(f"{k} x{v}" for k, v in sorted(guess_terms.items(), key=lambda kv: -kv[1])))
    amb = (gloss.get("ambiguous") or {})
    seen_amb = [k for k in amb if k != "_note" and re.search(
        r"(?<![A-Za-z0-9])" + re.escape(k) + r"(?![A-Za-z0-9])",
        " ".join(l["text"] for l in lines), re.I)]
    if seen_amb:
        md.append("- context-dependent terms present (NOT auto-corrected, judge from context): " +
                  ", ".join(seen_amb))
    md.append("\n---\n")
    bmq = list(bookmarks)
    for l in lines:
        while bmq and bmq[0] <= l["start"]:
            md.append(f"> **BOOKMARK [{hhmmss(bmq.pop(0))}]**\n")
        flag = " `[?]`" if l["low_conf"] else ""
        md.append(f"**[{hhmmss(l['start'])}] {l['speaker']}**{flag}: {l['text']}\n")
    for t in bmq:
        md.append(f"> **BOOKMARK [{hhmmss(t)}]**\n")
    if dropped:
        md.append("\n---\n")
        md.append("## Dropped as hallucination\n")
        md.append("Kept here so you can overrule the filter. None of this is in the minutes.\n")
        for d in dropped:
            md.append(f"- `[{hhmmss(d['start'] or 0)}]` {d.get('track') or '?'}: "
                      f"{(d.get('text') or '').strip()}  -- _{d['why']}_")

    (ses / "transcript.md").write_text("\n".join(md), encoding="utf-8")
    for l in lines:
        l["langs"] = sorted(l["langs"])
    (ses / "transcript.json").write_text(json.dumps(
        {"session": ses.name, "title": meta.get("title"),
         "started_local": meta.get("started_local"),
         "talk_time_s": {k: round(v, 1) for k, v in by_spk.items()},
         "languages": langs, "low_conf_lines": lc,
         "glossary_corrections": fixed_terms,
         "phonetic_guesses": guess_terms,
         "ambiguous_terms_present": seen_amb,
         "confirmed": decided,
         "speakers": spk_stats,
         "dropped_hallucinations": dropped,
         "private_ranges": [[round(a, 2), round(b, 2)] for a, b in pranges],
         "private_cut_s": round(private_cut_s, 1),
         "private_cut_lines": private_cut_n,
         "private_cut_by_speaker": private_cut_by,
         "bookmarks": bookmarks,
         "suspend_events": meta.get("suspend_events") or [],
         "recovered": bool(meta.get("recovered")),
         "sensitive": bool(meta.get("sensitive")),
         "lines": lines}, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"lines={len(lines)}  split={n_split}  speech={total/60:.1f}min  low_conf={lc}  "
          f"glossary_fixes={sum(fixed_terms.values())}  "
          f"phonetic_guesses={sum(guess_terms.values())}  "
          f"dropped={len(dropped)}" +
          (f"  speakers={len(spk_stats.get('by_speaker') or {})}" if spk_stats else "") +
          (f"  private_cut={private_cut_s:.0f}s" if pranges else "") +
          (f"  bookmarks={len(bookmarks)}" if bookmarks else ""))
    print(f" -> {ses/'transcript.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

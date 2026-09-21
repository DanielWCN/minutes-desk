"""One config file for the whole tool, so a meeting costs zero setup.

Everything a run needs - where to record, where to archive, which devices, who "me" is -
lives in config.json beside the tool. Written once by the setup page, read by every other
module. Two paths, not one, on purpose:

  staging_dir   local disk. Recording and processing happen here, always.
  archive_dir   OneDrive. A finished session is MOVED here afterwards, never written into
                directly - OneDrive would try to sync a wav file while it is still growing,
                which costs the meeting's CPU and can sync a half-written file.

The file is deliberately plain JSON with flat keys: a human who has never seen this code
should be able to open it, understand it, and fix a path by hand.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def user_dir() -> Path:
    """Where this machine's own data lives - config, private glossary, sessions.

    Deliberately NOT the program directory. The tool is meant to be re-downloaded and
    replaced in place (an agent re-clones it, a user unzips a newer build over the old
    one); anything the user accumulated would be wiped if it sat next to the code.
    Override with MINUTESDESK_HOME for a portable install on a stick.
    """
    env = os.environ.get("MINUTESDESK_HOME", "").strip()
    if env:
        p = Path(env)
    else:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        p = Path(base) / "MinutesDesk"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:                                        # noqa: BLE001
        p = ROOT                                             # last resort: behave like before
    return p


USER_DIR = user_dir()
CONFIG_PATH = USER_DIR / "config.json"
LEGACY_CONFIG = ROOT / "config.json"        # where it used to live, before 2026-09-12

# Import name -> what to pip install, for the dependency check.
PACKAGES = {
    "numpy": "numpy",
    "soundcard": "soundcard",
    "soundfile": "soundfile",
    "av": "av",
    "faster_whisper": "faster-whisper",
    "jellyfish": "jellyfish",
    "rapidfuzz": "rapidfuzz",
    "wordfreq": "wordfreq",
    "PIL": "Pillow",
}
# Optional: with three or more people the loopback track is everyone mixed together, and
# splitting it back apart is what turns "Others" into names (diarize.py + whois.py). A 1:1
# needs none of it - there the two tracks already are the two people.
# uiautomation reads a meeting client's own caption panel through the Windows accessibility layer,
# which is where the far end's NAMES come from (zcap.py). Pure Python, a few hundred KB.
OPTIONAL_PACKAGES: dict[str, str] = {"sherpa_onnx": "sherpa-onnx",
                                     "uiautomation": "uiautomation"}

MODEL_CACHE_HINT = "large-v3-turbo"
MODEL_SIZE_MB = 1622


def onedrive_root() -> Path | None:
    """The locally synced OneDrive root, if the client is set up."""
    for var in ("OneDriveCommercial", "OneDrive"):
        v = os.environ.get(var)
        if v and Path(v).is_dir():
            return Path(v)
    return None


def suggested_archive() -> str:
    od = onedrive_root()
    return str(od / "Meetings" / "Recordings") if od else ""


def defaults() -> dict:
    return {
        "me": "",
        "staging_dir": str(USER_DIR / "sessions"),
        "archive_dir": suggested_archive(),
        "archive_enabled": bool(suggested_archive()),
        "model": "large-v3-turbo",
        "mic_device": "",          # "" = Windows default
        "loopback_device": "",     # "" = default output, with auto-follow
        "video": False,
        # Read the meeting client's caption panel while recording, when it happens to be
        # open - Zoom and Slack are both read the same way. It is the
        # only source in the room that KNOWS who is speaking - the names come off the meeting
        # roster instead of being worked out afterwards. Text is never taken from it.
        "captions": True,
        "video_fps": 3,
        "video_crf": 32,
        "video_width": 1280,
        "languages": ["en", "zh"],
        # Who writes the draft minutes. "assistant" sends nothing anywhere: the prompt
        # goes to the clipboard and the user pastes it into whichever AI assistant they
        # already use. "api" and "ollama"
        # are the same OpenAI-compatible POST; they are two cards only because one of
        # them needs no key and runs on this machine.
        "engine": "assistant",     # assistant | api | ollama
        "api_base": "",
        "api_model": "",
        "api_key": "",
        # Ollama is always this machine, so the card stores a model name and nothing else.
        "ol_model": "",
        # True once the user has acknowledged, for a non-local endpoint, that the
        # transcript leaves this machine. Asked once, never again.
        "api_ack": False,
        # True once the user has agreed that the local assistant CLI named in
        # assistants.json may be handed a transcript. Same shape as api_ack, asked once:
        # that program has its own model behind it, wherever that model runs.
        "cli_ack": False,
        "setup_done": False,
        # The result of the last real test-record, so opening the tool shows a result
        # instead of "not tested yet". {"at": iso, "mic": dbfs, "loopback": dbfs, "state": ...}
        "last_test": {},
    }


def _migrate_legacy() -> None:
    """One-time lift of a pre-2026-09-12 config.json out of the program directory."""
    if CONFIG_PATH.exists() or not LEGACY_CONFIG.exists() or LEGACY_CONFIG == CONFIG_PATH:
        return
    try:
        CONFIG_PATH.write_text(LEGACY_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        LEGACY_CONFIG.rename(LEGACY_CONFIG.with_suffix(".json.moved"))
    except Exception:                                        # noqa: BLE001
        pass


def load() -> dict:
    cfg = defaults()
    _migrate_legacy()
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:                                    # noqa: BLE001
            pass                                             # a broken file must not brick the app
    return cfg


def save(cfg: dict) -> dict:
    keep = defaults()
    keep.update({k: v for k, v in cfg.items() if k in keep})
    CONFIG_PATH.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
    return keep


def remember_test(peaks: dict, state: str) -> dict:
    """Keep the last test-record result on disk. A check you cannot see is not a check."""
    cfg = load()
    cfg["last_test"] = {"at": datetime.now().isoformat(timespec="seconds"),
                        "mic": peaks.get("mic"), "loopback": peaks.get("loopback"),
                        "state": state}
    return save(cfg)


def staging(cfg: dict | None = None) -> Path:
    cfg = cfg or load()
    p = Path(cfg["staging_dir"])
    p.mkdir(parents=True, exist_ok=True)
    return p

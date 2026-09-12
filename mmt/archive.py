"""Move a finished session into OneDrive, once, after the user is done with it.

Never record into a synced folder. OneDrive watches the folder and will start uploading a wav
file while it is still growing: that costs the meeting's CPU and network, and can leave a
half-written file in the cloud. So the whole session lives on local disk until the user says
it is finished, and only then does it move.

"Finished" is the user's call, not ours - the minutes usually need a hand edit first. So the
move is an explicit action in the UI, and a session that has been moved leaves a pointer
behind (archived.json) so the app can still find it.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import config

REQUIRED = ("session.json",)


def state(session: Path) -> str:
    """local | archived | recording"""
    if (session / "recording.lock").exists():
        return "recording"
    if (session / "archived.json").exists():
        return "archived"
    return "local"


def target(session: Path, cfg: dict | None = None) -> Path:
    cfg = cfg or config.load()
    return Path(cfg["archive_dir"]) / session.name


def size_mb(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6


def move(session: Path, cfg: dict | None = None, keep_audio: bool = True) -> dict:
    cfg = cfg or config.load()
    if not cfg.get("archive_enabled"):
        return {"ok": False, "error": "归档没有开启（在设置里打开 OneDrive 归档）"}
    if state(session) == "recording":
        return {"ok": False, "error": "还在录音，先停止"}
    if state(session) == "archived":
        return {"ok": False, "error": "已经归档过了"}
    for f in REQUIRED:
        if not (session / f).exists():
            return {"ok": False, "error": f"缺 {f}，这个会话看起来没有正常收尾"}

    dst = target(session, cfg)
    if dst.exists():
        return {"ok": False, "error": f"目标已存在：{dst}"}
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        mb = size_mb(session)
        shutil.copytree(session, dst)
        moved = sorted(p.name for p in dst.iterdir())
        if not keep_audio:
            for w in dst.glob("*.wav"):
                w.unlink()
        # Only delete the local copy once the destination is verifiably complete.
        if not (dst / "session.json").exists():
            return {"ok": False, "error": "复制后目标缺 session.json，本地副本已保留"}
        shutil.rmtree(session)
        session.mkdir(parents=True, exist_ok=True)
        (session / "archived.json").write_text(json.dumps(
            {"archived_at": datetime.now().isoformat(timespec="seconds"),
             "path": str(dst), "size_mb": round(mb, 1), "files": moved},
            ensure_ascii=False, indent=1), encoding="utf-8")
        return {"ok": True, "path": str(dst), "size_mb": round(mb, 1), "files": len(moved)}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def resolve(session: Path) -> Path:
    """Where the session's files actually are - here, or in OneDrive."""
    ptr = session / "archived.json"
    if ptr.exists():
        try:
            p = Path(json.loads(ptr.read_text(encoding="utf-8"))["path"])
            if p.is_dir():
                return p
        except Exception:                                      # noqa: BLE001
            pass
    return session


if __name__ == "__main__":
    import sys
    print(json.dumps(move(Path(sys.argv[1])), ensure_ascii=False, indent=1))

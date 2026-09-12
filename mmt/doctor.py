"""Seven checks that must pass before the first meeting, each one a row the user can read.

The design rule is that a check never says "failed" without saying what to do about it, and
never guesses: a device name is the real Windows name, a size is the real byte count. Checks 4
to 6 exist because of a real loss - a meeting was recorded with the laptop speakers as the
output endpoint instead of the headset, and nothing on screen said so until afterwards. The
device names now sit in front of the user before the recording starts.

Every check returns the same shape so the UI can render them without special cases:
  {"id", "name", "state": ok|warn|fail, "detail", "fix": {"action", "label"} | None}
"""
from __future__ import annotations

import importlib
import shutil
import sys
import threading
import time
from pathlib import Path

from datetime import datetime

import config


# Three of these seven checks are the only ones a user cares about before a meeting:
# which device carries my voice, which carries theirs, and did a real test record both.
# The other four are install-once facts. Tagging them here lets the UI rank them instead
# of printing seven equal rows, which is what made the page read like a debug dump.
LIVE = {"mic", "loopback", "levels"}
RANK = {"ok": 0, "todo": 1, "warn": 2, "fail": 3}

# The report is grouped, because a user handed this tool needs to see that three separate
# things were inspected - can this machine run it, can it hear both sides, is there room
# to put the result - and not a run of seven unrelated lines.
GROUPS = ("运行环境", "音频通路", "存储")
GROUP = {"python": "运行环境", "packages": "运行环境", "model": "运行环境",
         "mic": "音频通路", "loopback": "音频通路", "levels": "音频通路",
         "paths": "存储"}


def _row(cid: str, name: str, state: str, detail: str, fix: dict | None = None,
         note: str = "", table: list[list[str]] | None = None) -> dict:
    """`detail` is what was measured. `note` is what the user should do about it.

    Keeping them apart is what stops a row from turning into a run-on sentence with an
    arrow in the middle, and lets the UI render advice as advice.
    """
    return {"id": cid, "name": name, "state": state, "detail": detail,
            "note": note, "table": table or [], "fix": fix,
            "group": GROUP.get(cid, ""),
            "tier": "live" if cid in LIVE else "setup"}


def check_python() -> dict:
    v = sys.version_info
    txt = f"Python {v.major}.{v.minor}.{v.micro} · {sys.executable}"
    if v < (3, 10):
        return _row("python", "Python 运行时", "fail",
                    txt + " · 需要 3.10 或以上",
                    {"action": "open", "label": "下载 Python",
                     "url": "https://www.python.org/downloads/windows/"})
    return _row("python", "Python 运行时", "ok", txt)


def check_packages() -> dict:
    missing, present = [], []
    for mod, pkg in config.PACKAGES.items():
        try:
            importlib.import_module(mod)
            present.append(mod)
        except Exception:                                     # noqa: BLE001
            missing.append(pkg)
    opt_missing = []
    for mod, pkg in config.OPTIONAL_PACKAGES.items():
        try:
            importlib.import_module(mod)
        except Exception:                                     # noqa: BLE001
            opt_missing.append(pkg)
    if missing:
        return _row("packages", "依赖组件", "fail",
                    f"{len(present)}/{len(config.PACKAGES)} 已安装，缺少 " + ", ".join(missing),
                    {"action": "install", "label": "一键安装", "packages": missing})
    if opt_missing:
        return _row("packages", "依赖组件", "warn",
                    f"{len(present)}/{len(config.PACKAGES)} 必需组件已安装；可选的说话人分离缺少 "
                    + ", ".join(opt_missing) + "（1:1 会议不需要）",
                    {"action": "install", "label": "装上可选项", "packages": opt_missing})
    return _row("packages", "依赖组件", "ok", f"{len(present)}/{len(config.PACKAGES)} 已安装")


def _model_dirs() -> list[Path]:
    hub = Path.home() / ".cache/huggingface/hub"
    if not hub.is_dir():
        return []
    return [d for d in hub.iterdir() if d.is_dir() and config.MODEL_CACHE_HINT in d.name]


def check_model() -> dict:
    dirs = _model_dirs()
    if not dirs:
        return _row("model", "语音识别模型", "fail",
                    f"未下载。whisper {config.MODEL_CACHE_HINT}，约 {config.MODEL_SIZE_MB} MB，"
                    "下载后本地永久缓存",
                    {"action": "download_model", "label": "现在下载"})
    mb = sum(f.stat().st_size for d in dirs for f in d.rglob("*") if f.is_file()) / 1e6
    if mb < config.MODEL_SIZE_MB * 0.8:
        return _row("model", "语音识别模型", "warn",
                    f"缓存仅 {mb:.0f} MB，上次下载可能中断",
                    {"action": "download_model", "label": "重新下载"})
    return _row("model", "语音识别模型", "ok", f"{config.MODEL_CACHE_HINT} · {mb:.0f} MB 已缓存")


_co = threading.local()


def _co_init() -> None:
    """soundcard talks COM; a fresh HTTP worker thread has none (0x800401f0)."""
    if getattr(_co, "done", False):
        return
    try:
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None, 0x2)          # APARTMENTTHREADED
    except Exception:                                          # noqa: BLE001
        pass
    _co.done = True


def _devices() -> tuple[list[str], list[str], str, str]:
    _co_init()
    import soundcard as sc
    mics = [m.name for m in sc.all_microphones(include_loopback=False)]
    outs = [s.name for s in sc.all_speakers()]
    return mics, outs, sc.default_microphone().name, sc.default_speaker().name


def check_mic(cfg: dict) -> dict:
    try:
        mics, _outs, dmic, _dspk = _devices()
    except Exception as exc:                                  # noqa: BLE001
        return _row("mic", "麦克风采集（本端语音）", "fail", f"设备枚举失败：{exc}")
    chosen = cfg.get("mic_device") or dmic
    if chosen not in mics and cfg.get("mic_device"):
        return _row("mic", "麦克风采集（本端语音）", "fail",
                    f"配置指定的「{chosen}」当前不可用；系统默认为「{dmic}」",
                    {"action": "pick_mic", "label": "重新选择", "options": mics})
    return _row("mic", "麦克风采集（本端语音）", "ok", chosen,
                {"action": "pick_mic", "label": "选择设备", "options": mics})


def check_loopback(cfg: dict) -> dict:
    """The output endpoint is what records the other side of the call."""
    try:
        _mics, outs, _dmic, dspk = _devices()
    except Exception as exc:                                  # noqa: BLE001
        return _row("loopback", "系统回环采集（对端语音）", "fail", f"设备枚举失败：{exc}")
    chosen = cfg.get("loopback_device") or dspk
    pinned = bool(cfg.get("loopback_device"))
    detail = chosen + ("（已锁定）" if pinned else "（随系统默认输出）")
    return _row("loopback", "系统回环采集（对端语音）", "ok", detail,
                {"action": "pick_loopback", "label": "选择设备", "options": outs})


def _tone(seconds: float, freq: float = 440.0, gain: float = 0.15) -> None:
    """Play a quiet sine on the default output, so the loopback test proves something.

    Without this, "loopback peak -240 dBFS" only means nothing happened to be playing,
    which is not a finding. With it, silence means the capture path is genuinely broken.
    """
    _co_init()
    try:
        import numpy as np
        import soundcard as sc
        sr = 48000
        t = np.arange(int(sr * seconds), dtype=np.float32) / sr
        wave = (gain * np.sin(2 * np.pi * freq * t)).astype(np.float32)
        fade = int(sr * 0.05)
        wave[:fade] *= np.linspace(0, 1, fade)
        wave[-fade:] *= np.linspace(1, 0, fade)
        sc.default_speaker().play(wave, samplerate=sr)
    except Exception:                                          # noqa: BLE001
        pass


def check_levels(cfg: dict, seconds: float = 3.0, tone: bool = True) -> dict:
    """Listen for `seconds` and report peak dBFS on both tracks.

    With `tone`, we play our own test sound while listening, which turns the loopback
    result from "inconclusive" into "works" or "broken".
    """
    _co_init()
    try:
        import numpy as np
        import soundcard as sc
    except Exception as exc:                                  # noqa: BLE001
        return _row("levels", "双路录音实测", "fail", f"{exc}")
    sr, out = 16000, {}
    try:
        mic_name = cfg.get("mic_device") or sc.default_microphone().name
        spk_name = cfg.get("loopback_device") or sc.default_speaker().name
        for tag, dev in (("mic", sc.get_microphone(mic_name, include_loopback=False)),
                         ("loopback", sc.get_microphone(spk_name, include_loopback=True))):
            player = None
            if tone and tag == "loopback":
                player = threading.Thread(target=_tone, args=(seconds,), daemon=True)
                player.start()
                time.sleep(0.15)                               # let the stream come up
            with dev.recorder(samplerate=sr, blocksize=1024) as rec:
                data = rec.record(numframes=int(sr * seconds))
            if player is not None:
                player.join(timeout=seconds + 2)
            a = np.abs(np.asarray(data, dtype="float32")).max() if data is not None else 0.0
            out[tag] = round(float(20 * np.log10(max(a, 1e-12))), 1)
    except Exception as exc:                                  # noqa: BLE001
        return _row("levels", "双路录音实测", "fail", f"{exc}",
                    {"action": "levels", "label": "重测"})
    m, l = out.get("mic", -120.0), out.get("loopback", -120.0)
    retry = {"action": "levels", "label": "重新实测"}
    peaks = {"mic": m, "loopback": l}
    detail = (f"{seconds:.0f} 秒实测"
              + ("，含 440 Hz 测试音" if tone else "，未播放测试音"))
    def done(state: str, note: str) -> dict:
        r = _row("levels", "双路录音实测", state, detail, retry, note)
        r["peaks"] = peaks
        try:
            config.remember_test(peaks, state)
        except Exception:                                      # noqa: BLE001
            pass                       # a read-only config must not lose the measurement
        return r

    if l < -60:
        if tone:
            return done("fail",
                        "已播放测试音，对端通路仍为静音。请在「系统回环采集」中改选"
                        "当前正在输出声音的设备，然后重新实测。")
        return done("warn", "实测期间无音频输出，对端通路无结论。重新实测时将自动播放测试音。")
    if m < -60:
        return done("warn", "麦克风未采集到信号。重新实测的 3 秒内请发声；"
                            "若仍无信号，请检查系统输入是否处于静音。")
    if m > -1.0 or l > -1.0:
        return done("warn", "电平已达满刻度，录音会削波。请在 Windows 声音设置中降低输入音量。")
    return done("ok", "")


def check_paths(cfg: dict) -> dict:
    rows = []
    for key, label in (("staging_dir", "本地暂存"), ("archive_dir", "OneDrive 归档")):
        raw = cfg.get(key) or ""
        if key == "archive_dir" and not cfg.get("archive_enabled"):
            rows.append([label, "已关闭", ""])
            continue
        if not raw:
            return _row("paths", "存储位置与容量", "fail", f"{label}未设置",
                        {"action": "pick_path", "label": "设置路径", "key": key})
        p = Path(raw)
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".mmt_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except Exception as exc:                              # noqa: BLE001
            return _row("paths", "存储位置与容量", "fail", f"{label}不可写：{p} — {exc}",
                        {"action": "pick_path", "label": "改路径", "key": key})
        free = shutil.disk_usage(p).free / 1e9
        rows.append([label, str(p), f"剩余 {free:.1f} GB"])
    free_stage = shutil.disk_usage(Path(cfg["staging_dir"])).free / 1e9
    per_hour = 232 + (153 if cfg.get("video") else 0)
    hours = free_stage * 1000 / per_hour
    detail = (f"按实测 {per_hour} MB/小时"
              + ("（含录屏）" if cfg.get("video") else "（纯音频）")
              + f"，暂存盘可录约 {hours:.0f} 小时")
    if free_stage < 5:
        return _row("paths", "存储位置与容量", "warn", detail,
                    {"action": "open_dir", "label": "打开暂存目录", "key": "staging_dir"},
                    "暂存盘可用空间不足 5 GB。将已完成的会议归档至 OneDrive 可释放本地空间。",
                    rows)
    return _row("paths", "存储位置与容量", "ok", detail, None, "", rows)


def _ago(iso: str) -> str:
    try:
        t = datetime.fromisoformat(iso)
    except Exception:                                          # noqa: BLE001
        return ""
    now = datetime.now()
    if t.date() == now.date():
        return f"今天 {t:%H:%M}"
    if (now.date() - t.date()).days == 1:
        return f"昨天 {t:%H:%M}"
    return f"{t:%m-%d %H:%M}"


def _worst(rows: list[dict]) -> str:
    return max(rows, key=lambda c: RANK.get(c["state"], 0))["state"]


def _report(checks: list[dict], cfg: dict) -> list[dict]:
    """Every check, in inspection order, with its measured value.

    Nothing is collapsed and nothing is hidden: a colleague opening this tool for the first
    time has to be able to see the full list of what was inspected on *their* machine, or
    the verdict at the top is just an assertion. The two audio rows additionally carry the
    dBFS measured for that track, so "device is selected" and "device actually captured
    sound" are visibly two different facts.
    """
    by = {c["id"]: c for c in checks}
    lv = by.get("levels", {})
    pk = lv.get("peaks") or {}
    out = []
    for cid in ("python", "packages", "model", "mic", "loopback", "levels", "paths"):
        c = by.get(cid)
        if not c:
            continue
        st, db = c["state"], pk.get(cid)
        if cid in ("mic", "loopback") and st == "ok":
            if db is None:
                st = "todo"                       # selected, but never actually captured
            elif db < -60:
                st = "fail"
            elif lv.get("state") == "warn":
                st = "warn"
        out.append({"id": cid, "group": c.get("group", ""), "name": c["name"],
                    "state": st, "value": c["detail"], "db": db,
                    "fix": c.get("fix"), "table": c.get("table", []),
                    "note": (c.get("note", "") or (lv.get("note", "")
                             if cid in ("mic", "loopback") else "")) if st != "ok" else ""})
    return out


def _verdict(rows: list[dict], worst: str, cfg: dict) -> dict:
    """One line stating whether this machine is cleared to record, and on what evidence."""
    when = _ago((cfg.get("last_test") or {}).get("at") or "")
    total = len(rows)
    n_fail = sum(1 for c in rows if c["state"] == "fail")
    n_warn = sum(1 for c in rows if c["state"] == "warn")
    bad = [c for c in rows if c["state"] in ("fail", "warn")]
    if worst == "fail":
        return {"head": "检测未通过，暂不能录制",
                "sub": f"{n_fail} 项未通过，首项为「{bad[0]['name']}」。处理后重新检测。"}
    if worst == "warn":
        return {"head": "检测通过，有 %d 项提示" % n_warn,
                "sub": f"「{bad[0]['name']}」：{bad[0]['note'] or bad[0]['value']}"}
    if worst == "todo":
        return {"head": "尚未完成录音实测",
                "sub": "运行环境与音频设备已就绪。执行双路录音实测（3 秒，含测试音）"
                       "以确认两路音频均可实际采集。"}
    return {"head": "检测通过，可开始录制",
            "sub": (f"{total} 项全部通过 · 双路录音实测：{when}" if when
                    else f"{total} 项全部通过")}


def _remembered(cfg: dict) -> dict:
    """Last time's measurement, replayed. Reopening the tool should not lose the result."""
    lt = cfg.get("last_test") or {}
    if not lt.get("at"):
        return _row("levels", "双路录音实测", "todo", "尚未执行",
                    {"action": "levels", "label": "执行实测"})
    m, l = lt.get("mic"), lt.get("loopback")
    r = _row("levels", "双路录音实测", lt.get("state", "ok"),
             f"3 秒实测 · 上次执行 {_ago(lt['at'])}",
             {"action": "levels", "label": "重新实测"})
    r["peaks"] = {k: v for k, v in (("mic", m), ("loopback", l)) if v is not None}
    r["remembered"] = True
    return r


def run(cfg: dict | None = None, levels: bool = False) -> dict:
    cfg = cfg or config.load()
    checks = [check_python(), check_packages(), check_model(),
              check_mic(cfg), check_loopback(cfg)]
    checks.append(check_levels(cfg) if levels else _remembered(cfg))
    checks.append(check_paths(cfg))
    if levels:
        cfg = config.load()                        # pick up the just-saved last_test
    rows = _report(checks, cfg)
    worst = _worst(rows)
    return {"checks": checks, "rows": rows, "groups": list(GROUPS), "state": worst,
            "verdict": _verdict(rows, worst, cfg),
            "tested_at": (cfg.get("last_test") or {}).get("at", ""),
            "ok": sum(1 for c in rows if c["state"] == "ok"),
            "todo": sum(1 for c in rows if c["state"] == "todo"),
            "warn": sum(1 for c in rows if c["state"] == "warn"),
            "fail": sum(1 for c in rows if c["state"] == "fail"),
            "total": len(rows),
            "ready": all(c["state"] != "fail" for c in rows)}


if __name__ == "__main__":
    import json
    print(json.dumps(run(levels="--levels" in sys.argv), ensure_ascii=False, indent=1))

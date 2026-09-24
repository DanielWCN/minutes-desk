"""Today's meetings, read from the Outlook client that is already signed in.

No credential, no token, no dependency: the desktop client on this machine holds the
mailbox, so we ask it through COM and let PowerShell do the marshalling. If Outlook is
not the classic desktop client, or is not installed, this returns an error string the UI
shows as-is instead of pretending the calendar is empty.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta

PS = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$ol = New-Object -ComObject Outlook.Application
$ns = $ol.GetNamespace('MAPI')
$items = $ns.GetDefaultFolder(9).Items
$items.IncludeRecurrences = $true
$items.Sort('[Start]')
$s = (Get-Date).Date.AddDays(DAYS_FROM).ToString('MM/dd/yyyy HH:mm')
$e = (Get-Date).Date.AddDays(DAYS_TO).ToString('MM/dd/yyyy HH:mm')
$r = $items.Restrict("[Start] >= '$s' AND [Start] < '$e'")
$out = @()
foreach($a in $r){
  $out += [ordered]@{
    subject   = $a.Subject
    start     = $a.Start.ToString('yyyy-MM-ddTHH:mm')
    end       = $a.End.ToString('yyyy-MM-ddTHH:mm')
    organizer = $a.Organizer
    required  = $a.RequiredAttendees
    optional  = $a.OptionalAttendees
    allday    = $a.AllDayEvent
  }
}
ConvertTo-Json -InputObject @($out) -Depth 3 -Compress
"""

WHOAMI_PS = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$ol = New-Object -ComObject Outlook.Application
Write-Output $ol.GetNamespace('MAPI').CurrentUser.Name
"""

CREATE_NO_WINDOW = 0x08000000
DROP_SUBJECT = re.compile(r"^\s*(canceled|cancelled|已取消)\s*[:：]", re.I)
# a bare lowercase token is a distribution list alias, not a person
DL = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _ps(days_from: int, days_to: int) -> list[dict]:
    script = PS.replace("DAYS_FROM", str(days_from)).replace("DAYS_TO", str(days_to))
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = CREATE_NO_WINDOW
    p = subprocess.run(                                            # noqa: S603
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        capture_output=True, timeout=40, **kw)
    out = p.stdout.decode("utf-8", "replace").strip()
    if not out:
        err = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(err[0] if err else "Outlook 没有返回内容")
    return json.loads(out)


def person(raw: str, me: str = "") -> str:
    """One attendee entry to a person's name, or '' if it is not a person."""
    s = (raw or "").strip().strip('"').strip()
    if not s or "@" in s:                       # mailbox, room, chime pin, unresolved DL
        return ""
    if s.lower().endswith(("-directs", "directs")) and " " not in s:
        return ""
    if DL.match(s):                             # pars-people-managers
        return ""
    # "Smith, Alex" -> "Alex Smith"; "Chen, Wenjie (Oliver)" -> "Wenjie Chen".
    # The bracket is NOT the name to use. This used to prefer it - the comment said
    # '"Zhang, Yinuo (Helen)" -> "Helen Zhang"', on the assumption that the bracket holds
    # the English name somebody goes by. Reported from a real directory entry, it is the
    # other way round: the field before the bracket is what the person set as their name,
    # and the bracket holds the legal one. So the bracket is only used when there is no
    # given name without it, e.g. "Zhao, (Nina)" or a bare "Wu (Bella)".
    nick = ""
    m = re.search(r"\(([^)]+)\)", s)
    if m:
        nick = m.group(1).strip()
        s = re.sub(r"\s*\([^)]*\)", "", s).strip()
    if "," in s:
        last, _, first = s.partition(",")
        first = first.strip() or nick
        s = f"{first} {last.strip()}".strip()
    elif nick and " " not in s:
        s = f"{nick} {s}".strip() if s else nick
    s = re.sub(r"\s+", " ", s).strip(" .;")
    if not s:
        return ""
    if me and _same(s, me):
        return ""
    return s


def _same(a: str, b: str) -> bool:
    ka = {w.lower() for w in re.split(r"[\s,.]+", a) if len(w) > 1}
    kb = {w.lower() for w in re.split(r"[\s,.]+", b) if len(w) > 1}
    return bool(ka) and bool(kb) and (ka <= kb or kb <= ka)


def names(required: str, optional: str = "", me: str = "", cap: int = 30) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for chunk in (required or "", optional or ""):
        for part in re.split(r"[;\n]+", chunk):
            n = person(part, me)
            if n and n.lower() not in seen:
                seen.add(n.lower())
                out.append(n)
    return out[:cap]


def day(cfg: dict | None = None, iso_day: str = "") -> dict:
    """Meetings of one calendar day; empty string means today."""
    off = 0
    iso_day = (iso_day or "").strip()
    if iso_day:
        try:
            d = datetime.strptime(iso_day[:10], "%Y-%m-%d").date()
            off = (d - datetime.now().date()).days
        except Exception:                                          # noqa: BLE001
            return {"error": f"日期看不懂：{iso_day}"}
    r = today(cfg, off, off + 1)
    r["day"] = (datetime.now().date() + timedelta(days=off)).isoformat()
    return r


def today(cfg: dict | None = None, days_from: int = 0, days_to: int = 1) -> dict:
    me = (cfg or {}).get("me") or ""
    try:
        raw = _ps(days_from, days_to)
    except FileNotFoundError:
        return {"error": "找不到 PowerShell，无法读取 Outlook。"}
    except subprocess.TimeoutExpired:
        return {"error": "Outlook 没有响应（40 秒）。确认桌面版 Outlook 已启动后重试。"}
    except Exception as e:                                         # noqa: BLE001
        msg = str(e).strip().splitlines()
        return {"error": "读取 Outlook 失败：" + (msg[0] if msg else "未知错误")
                + "。此功能需要桌面版 Outlook（新版 Outlook / 网页版不支持）。"}

    now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    out = []
    for a in raw:
        subj = (a.get("subject") or "").strip()
        if not subj or DROP_SUBJECT.match(subj) or a.get("allday"):
            continue
        who = names(a.get("required") or "", a.get("optional") or "", me)
        org = person(a.get("organizer") or "", me)
        if org and org not in who:
            who.insert(0, org)
        out.append({"subject": subj, "start": a.get("start") or "", "end": a.get("end") or "",
                    "live": bool(a.get("start") and a.get("end")
                                 and a["start"] <= now <= a["end"]),
                    "names": who})
    out.sort(key=lambda m: m["start"])
    return {"ok": True, "meetings": out, "at": now}


def whoami() -> str:
    """The mailbox owner's display name, straight from the signed-in client.

    Used to fill "me" without asking: the tool needs it to leave you out of the attendee
    list and to sign the minutes. Returns "" if Outlook is not the classic desktop client.
    """
    kw = {"creationflags": CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    try:
        p = subprocess.run(                                        # noqa: S603
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
             "Bypass", "-Command", WHOAMI_PS],
            capture_output=True, timeout=40, **kw)
        return person(p.stdout.decode("utf-8", "replace").strip()) or ""
    except Exception:                                              # noqa: BLE001
        return ""


if __name__ == "__main__":
    print(json.dumps(today({"me": whoami()}), ensure_ascii=False, indent=1))

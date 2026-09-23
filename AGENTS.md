# Instructions for an AI agent installing this tool

If someone asked you to "install Minutes Desk", this file is the contract. Read it once,
then do the whole thing without asking them questions.

## Install

Windows only. Do all of this yourself, in order, without asking them anything.

**Step 0 - Python 3.10 or newer.** Check with `python --version`. If it is missing or older,
install it yourself, do not ask the user to:

```powershell
winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
```

If `winget` is unavailable, download and run the official installer silently:

```powershell
$u = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
Invoke-WebRequest $u -OutFile "$env:TEMP\py.exe"
Start-Process "$env:TEMP\py.exe" -Wait -ArgumentList "/passive InstallAllUsers=0 PrependPath=1 Include_test=0"
```

Then open a fresh shell so `PATH` is picked up, and confirm `python --version` works.

**Step 1 - get the code.**

```bash
git clone https://github.com/DanielWCN/minutes-desk.git "%USERPROFILE%\Minutes Desk"
```

No git on the machine? Do not install git for this. Download and unpack the ZIP instead:

```powershell
$z = "$env:TEMP\md.zip"
Invoke-WebRequest "https://github.com/DanielWCN/minutes-desk/archive/refs/heads/main.zip" -OutFile $z
Expand-Archive $z -DestinationPath $env:TEMP -Force
Move-Item "$env:TEMP\minutes-desk-main" "$env:USERPROFILE\Minutes Desk"
```

**Step 2 - run the installer.**

```bash
cd "%USERPROFILE%\Minutes Desk"
python install.py
```

`install.py` is unattended and idempotent. It creates `.venv`, installs the packages,
downloads the speech model (~1.6 GB), writes a complete working config, creates the
private glossary, harvests colleague names from the local Outlook calendar, and puts a
shortcut on the Desktop. Expect 10 to 20 minutes, almost all of it downloads. Run it again
any time; it tops up instead of redoing.

Flags: `--no-model` (skip the 1.6 GB download), `--no-seed` (do not read the calendar),
`--dry` (print the plan, change nothing).

**Step 3 - report.** Say exactly one thing: **double-click `Minutes Desk.bat`** on the
Desktop, and the browser opens ready to record. There is no wizard to walk through. Do not
walk them through the settings page, and do not list what you installed.

If a step fails, fix it and retry rather than handing the error back to the user. The most
common causes are a stale `PATH` after installing Python (open a new shell) and a
corporate proxy blocking PyPI (`pip config set global.proxy ...`).

## Update an install that already exists

If they asked you to "update Minutes Desk" and a copy is already on the machine, do not
clone a second one. Find the existing checkout (the Desktop shortcut `Minutes Desk.bat`
points at it; so does the `staging_dir` in `%LOCALAPPDATA%\MinutesDesk\config.json`), then:

```bash
git pull
python install.py            # tops up; add --no-model if the model is already there
.venv\Scripts\python.exe mmt\llm.py --ping
```

That last command is the point of the update on an internal machine, so run it and do not
skip it. The tool writes the minutes by driving the assistant app on this machine from a
command line, and whether that works is the one thing nobody can know from anywhere else:
the app answering in its own window proves it is signed in, but not that its build accepts
being driven this way. `--ping` feeds it a real sentence and waits for the answer. Exit 0
and a line starting `ping: ok` means the minutes will write themselves. Exit 1 prints the
reason, and the reason is worth reading out.

Close the black `Minutes Desk` console window if it is open, and tell them to double-click
`Minutes Desk.bat` again. Then say two things: the version now next to the logo in the
title bar, and whether `--ping` came back ok. `CHANGELOG.md` lists what changed.

Everything the person has accumulated lives in `%LOCALAPPDATA%\MinutesDesk\` and outside
the program directory on purpose, so an update cannot touch their config, their glossary or
their meetings. Do not "clean up" or reset any of it, do not re-run the calendar harvest,
and do not change any setting they chose.

## Then, if you have access to their internal documentation

The single biggest quality lever is the glossary: a speech model that knows a term spells
it right, and one that does not writes nonsense that then propagates into the minutes.
So after installing, seed the glossary with the vocabulary of the team this person is on.

Where things go, and this matters:

| table | what belongs there | who may write it |
|---|---|---|
| `fix_after` | everything. Canonical spelling -> the wrong forms you expect to see. Applied to finished text, so a mistake here is cosmetic and reversible. | **you** |
| `hotwords` | a short list, fed to the decoder while it listens. A long list makes recognition measurably WORSE. Hard cap 80, and it is nearly full already. | a human, in the app |
| `people` | alias -> display name | the tool, from the calendar |
| `_candidates` | anything you are unsure about; the app shows these for a human to promote | you |

Write only `fix_after` and `_candidates`. Never add to `hotwords`.

Edit this file, creating it if absent:

```
%LOCALAPPDATA%\MinutesDesk\glossary.user.json
```

```json
{
  "fix_after": {
    "CanonicalSpelling": ["canonical spelling", "cannonical speling"],
    "SomeSystem": ["some system", "sum system"]
  },
  "_candidates": {
    "TermYouAreUnsureAbout": {"n": 1, "src": "wiki"}
  }
}
```

Merge into what is already there; do not overwrite the file. `mmt/glossary.base.json` is
the shipped layer and is replaced on every update - never edit that one.

Rules for what you put in it:

- Team, product, system and metric names. Acronyms the team actually says out loud.
- **No personal names from documents.** Names come from the person's own calendar, which
  the tool already reads. Do not harvest names off a wiki, an org chart or a directory.
- Nothing that only exists behind a login the person may not intend to share. You are
  reading their documentation with their identity; keep the output to vocabulary.
- 20 to 60 terms is a good seed. Hundreds is worse than none.

## Do not do these

- Do not set `archive_enabled` to true. Copying meeting audio into someone's cloud drive
  is their decision and the app asks in place.
- Do not set `api_ack` to true. That flag records that a human understood a transcript
  would leave the machine.
- Do not set `cli_ack` to true either. Same flag, one step closer: it records that a human
  agreed the transcript may go to the assistant program named in `assistants.json`.
- Do not configure `api_base` / `api_key`. The default engine sends nothing anywhere: it
  hands the user a prompt to paste into whichever assistant they already use.
- Do not put anything in the program directory. Everything the user accumulates lives in
  `%LOCALAPPDATA%\MinutesDesk\` so that replacing the program never wipes it.

## Useful commands

```bash
cd mmt
python firstrun.py --reseed     # harvest the calendar again
python seed.py --dry            # show what a harvest would add, write nothing
python lexicon.py               # what the merged glossary currently holds
python llm.py --ping            # does the assistant really answer? exit 0 = yes
python doctor.py                # the self-checks, as JSON
```

"""Everything a first run needs, done without asking.

The old shape was a setup page with seven checks and a Save button, which is a fine thing
to have and a terrible thing to require. A colleague who was handed this tool wants to hit
record, not answer questions about staging directories. So: on a machine that has never run
it, this module writes a complete, working config, creates the private glossary, and seeds
that glossary from the calendar already signed in on this machine - all of it silently, all
of it reversible from the settings page afterwards.

Two things are deliberately NOT decided here, because they are not setup friction:

  archive_enabled   left OFF. Copying meeting audio into OneDrive is a decision about where
                    recordings of other people live, and nobody gets to make that for you.
  api_ack           left False, so the first use of a non-local model endpoint still asks.

Safe to call on every boot: every step is guarded, and the seed runs once per machine.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config                                                 # noqa: E402
import lexicon                                                # noqa: E402

SEED_MARK = ".seeded"          # in the user dir, so re-downloading the program never re-seeds


def _mark() -> Path:
    return config.user_dir() / SEED_MARK


def ensure_config(log=lambda s: None) -> tuple[dict, bool]:
    """A complete config on the first run. Returns (cfg, wrote_a_new_one)."""
    if config.CONFIG_PATH.exists():
        return config.load(), False
    cfg = config.defaults()
    try:
        import outlook                                        # noqa: PLC0415
        cfg["me"] = outlook.whoami()
    except Exception:                                         # noqa: BLE001
        cfg["me"] = ""
    cfg["archive_enabled"] = False        # your call, not ours - see the module docstring
    cfg["setup_done"] = True              # nothing left to ask; the checks still live in settings
    config.save(cfg)
    Path(cfg["staging_dir"]).mkdir(parents=True, exist_ok=True)
    log(f"config written  {config.CONFIG_PATH}")
    log(f"  me = {cfg['me'] or '(Outlook did not answer; set it in settings)'}")
    log(f"  recordings go to {cfg['staging_dir']}")
    return cfg, True


def ensure_glossary(log=lambda s: None) -> bool:
    if lexicon.user_path().exists():
        return False
    lexicon.save_user(lexicon.blank_user())
    log(f"glossary created  {lexicon.user_path()}")
    return True


def seed(log=lambda s: None, force: bool = False) -> dict:
    """Harvest colleague names from the local calendar. Once per machine unless forced."""
    if _mark().exists() and not force:
        return {"skipped": "already seeded"}
    try:
        import seed as seeder                                 # noqa: PLC0415
        r = seeder.run(log=log)
    except Exception as e:                                    # noqa: BLE001
        log(f"glossary seeding skipped: {e}")
        return {"error": str(e)}
    _mark().write_text(str(r.get("meetings", 0)), encoding="utf-8")
    return r


def ensure(do_seed: bool = True, background: bool = True, log=lambda s: None) -> dict:
    """Called on every boot. Cheap when there is nothing to do."""
    cfg, fresh = ensure_config(log)
    made = ensure_glossary(log)
    out = {"config_written": fresh, "glossary_created": made, "seed": None}
    if do_seed and not _mark().exists():
        if background:
            # a full calendar walk is a couple of PowerShell round trips; never make the
            # first window wait on it
            threading.Thread(target=seed, args=(log,), daemon=True).start()
            out["seed"] = "started in the background"
        else:
            out["seed"] = seed(log)
    return out


def main() -> int:
    import argparse                                           # noqa: PLC0415
    ap = argparse.ArgumentParser(description="Prepare this machine on first run")
    ap.add_argument("--no-seed", action="store_true", help="skip the calendar harvest")
    ap.add_argument("--reseed", action="store_true", help="harvest again even if done before")
    a = ap.parse_args()
    print(f"user data  {config.user_dir()}")
    ensure(do_seed=not a.no_seed, background=False, log=print)
    if a.reseed:
        seed(print, force=True)
    g = lexicon.merged()
    print(f"glossary   {len(g['hotwords'])} hotwords, {len(g['fix_after'])} fix rules, "
          f"{len(g['people'])} people")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

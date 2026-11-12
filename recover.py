#!/usr/bin/env python3
"""Instagram Recovery Kit: rebuild a lost or disabled Instagram account from your data download.

  python recover.py build [EXPORT ...]   read your export, build recovery/hub.html and open it
  python recover.py post [--cap N]       start the one-click post helper for the hub's Posts tab
  python recover.py followers            who doesn't follow your new account back (also: import DIR, report)
  python recover.py follow               optional paced auto-follow (also: run --dry-run, status, login)
  python recover.py setup                (re)install what the kit needs; build does this on first use

EXPORT is the zip file(s) Instagram gave you, or a folder you already unzipped. With no
EXPORT, build uses whatever is in the export/ folder next to this file.
Everything stays on your computer. Nothing is uploaded anywhere except what you post or
follow on Instagram yourself.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import webbrowser
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / "tools"
EXPORT_DIR = ROOT / "export"
UNZIPPED = EXPORT_DIR / "unzipped"
REC = ROOT / "recovery"
VENV = ROOT / ".venv"
REQS = ROOT / "requirements.txt"
EXPORT_MARKERS = ("your_instagram_activity", "connections")
SKIP_DIRS = {".venv", "recovery", "tools", ".git", "__pycache__"}


class KitError(Exception):
    pass


def say(msg=""):
    print(msg, flush=True)


# ------------------------------------------------------------------- setup
def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def setup(force=False) -> Path:
    """Create .venv and install requirements.txt (again only when it changes)."""
    if sys.version_info < (3, 10):
        raise KitError(f"Python 3.10 or newer is needed (this is {sys.version.split()[0]}). "
                       "Get it from https://www.python.org/downloads/")
    py, stamp = venv_python(), VENV / ".kit-requirements"
    want = hashlib.sha256(REQS.read_bytes()).hexdigest()
    if not force and py.exists() and stamp.exists() and stamp.read_text().strip() == want:
        return py
    if not py.exists():
        say("First run: creating a private Python environment in .venv (one time)...")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    say("Installing the packages the kit needs (Playwright, Pillow, pillow-heif). This takes a minute...")
    subprocess.check_call([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQS)])
    stamp.write_text(want)
    say("Setup done.\n")
    return py


def tool(script, *args) -> int:
    """Run tools/<script> with the kit's Python, isolated from the current folder (-I)."""
    py = setup()
    return subprocess.call([str(py), "-I", str(TOOLS / script), *map(str, args)], cwd=ROOT)


def need_hub():
    if not (REC / "data" / "people.json").exists():
        raise KitError("Build the hub from your export first: double-click START_HERE.bat, "
                       "or run: python recover.py build")


# ---------------------------------------------------------------- the export
def is_export_root(d: Path) -> bool:
    return any((d / m).is_dir() for m in EXPORT_MARKERS)


def find_export_roots(base: Path, depth=3) -> list[Path]:
    found = []

    def walk(d, n):
        if is_export_root(d):
            found.append(d)
            return
        if n <= 0:
            return
        try:
            kids = sorted(c for c in d.iterdir() if c.is_dir())
        except OSError:
            return
        for c in kids:
            if c.name not in SKIP_DIRS and not c.name.startswith("."):
                walk(c, n - 1)

    walk(base, depth)
    return found


def zip_group(z: Path) -> str:
    """Parts of one download (same account and date) are unzipped into one folder."""
    m = re.match(r"(instagram-.+?-\d{4}-\d{2}-\d{2})", z.name)
    return m.group(1) if m else "export"


def safe_unzip(z: Path, dest: Path):
    marker = dest / f".unzipped-{z.name}-{z.stat().st_size}"
    if marker.exists():
        return
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with zipfile.ZipFile(z) as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        total = sum(m.file_size for m in members) or 1
        done, shown, skipped = 0, -1, 0
        say(f"Unzipping {z.name} ({total / 1e9:.1f} GB) into export/unzipped/{dest.name}/ ...")
        for m in members:
            target = (dest / m.filename.replace("\\", "/")).resolve()
            if not target.is_relative_to(root):  # never write outside the folder (zip-slip)
                skipped += 1
                done += m.file_size
                continue
            if not (target.exists() and target.stat().st_size == m.file_size):
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(m) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
            done += m.file_size
            pct = done * 100 // total
            if pct // 10 != shown:
                shown = pct // 10
                say(f"  {pct}%")
    if skipped:
        say(f"  skipped {skipped} entries with unsafe paths")
    marker.write_text("ok")


def resolve_export(paths: list[str]) -> Path:
    inputs = [Path(p).expanduser().resolve() for p in paths] or [EXPORT_DIR]
    zips, roots = [], []
    for p in inputs:
        if not p.exists():
            raise KitError(f"Not found: {p}")
        if p.is_file():
            if p.suffix.lower() != ".zip":
                raise KitError(f"{p.name} isn't a zip file. Give the zip(s) Instagram sent you, or the unzipped folder.")
            zips.append(p)
        else:
            zips += sorted(p.glob("*.zip"))
            roots += find_export_roots(p)
    # a zip that was already unzipped by hand next to itself doesn't need unzipping again
    done_by_hand = [z for z in zips if is_export_root(z.with_suffix(""))]
    roots += [z.with_suffix("") for z in done_by_hand]
    zips = [z for z in zips if z not in done_by_hand]
    for z in zips:
        safe_unzip(z, UNZIPPED / zip_group(z))
    for g in dict.fromkeys(zip_group(z) for z in zips):
        roots += find_export_roots(UNZIPPED / g, 2)
    roots = list({r.resolve(): None for r in roots})
    if not roots:
        raise KitError(
            "No Instagram export found. Put the zip file(s) from Instagram's 'Download your information' into the "
            f"export folder ({EXPORT_DIR}), or drag them onto START_HERE.bat, then try again.")
    root = max(roots, key=lambda r: r.stat().st_mtime)
    if len(roots) > 1:
        say("Found more than one export:\n" + "\n".join(f"  {r}" for r in roots))
        say(f"Using the newest: {root}\n(To pick another, run: python recover.py build <folder>)\n")
    return root


def check_format(root: Path):
    ff = root / "connections" / "followers_and_following"
    act = root / "your_instagram_activity"
    html = any(ff.glob("*.html")) or any(act.rglob("*.html"))
    js = any(ff.glob("*.json")) or any(act.rglob("*.json"))
    if js and not html:
        raise KitError(
            "This export is in JSON format. The kit reads the HTML format. If you still can, request a new download "
            "and choose Format: HTML (and set Instagram's language to English first). See the README for steps.")
    if not html:
        raise KitError(f"{root} doesn't look like an Instagram export (no followers, following or activity files).")


def cmd_build(args):
    no_open = "--no-open" in args
    paths = [a for a in args if a != "--no-open"]
    setup()
    root = resolve_export(paths)
    say(f"Reading your export: {root}")
    check_format(root)
    REC.mkdir(exist_ok=True)
    for step, script in (("1/3 Extracting people, posts, chats, stories...", "extract.py"),
                         ("2/3 Preparing upload-ready photos and thumbnails (slow the first time)...", "prepare_media.py"),
                         ("3/3 Building the hub...", "build_hub.py")):
        say(step)
        if tool(script, root, REC) != 0:
            raise KitError(f"{script} failed (see the messages above).")
    s = json.loads((REC / "data" / "summary.json").read_text(encoding="utf-8"))
    if not (s.get("following") or s.get("followers") or s.get("posts")):
        say("\nWARNING: no followers, following or posts were recognised. The kit reads exports in English. "
            "Set Instagram's language to English (US), request the download again in HTML format, and rebuild.")
    hub = REC / "hub.html"
    say(f"\nDone. Your recovery hub: {hub}")
    if not no_open:
        say("Opening it in your browser (Chrome or Edge recommended). Bookmark it; progress is saved in that browser.")
        webbrowser.open(hub.as_uri())


# --------------------------------------------------------------------- main
def main():
    argv = sys.argv[1:]
    cmd, rest = (argv[0], argv[1:]) if argv else ("help", [])
    if cmd == "setup":
        setup(force=True)
        return 0
    if cmd == "build":
        cmd_build(rest)
        return 0
    if cmd == "post":
        need_hub()
        return tool("autopost.py", "serve", *rest)
    if cmd == "followers":
        sub = rest if rest and rest[0] in ("scan", "import", "report") else ["scan", *rest]
        return tool("followcheck.py", *sub)
    if cmd == "follow":
        need_hub()
        sub = rest if rest and rest[0] in ("run", "login", "status") else ["run", *rest]
        return tool("autofollow.py", *sub)
    say(__doc__)
    return 0 if cmd in ("help", "-h", "--help") else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KitError as e:
        say(f"\nSTOPPED: {e}")
        sys.exit(2)
    except subprocess.CalledProcessError as e:
        say(f"\nSTOPPED: setup command failed ({e}). Check your internet connection and try again.")
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)

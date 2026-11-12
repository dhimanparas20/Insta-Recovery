"""Prepare upload-ready post folders, date-sorted stories, and hub thumbnails.

Usage: .venv/Scripts/python -I tools/prepare_media.py <export_dir> <recovery_dir>

Creates (inside <recovery_dir>):
  upload_ready/posts/NN_YYYY-MM-DD_<slug>[_ARCHIVED]/01.jpg ... caption.txt info.txt
  stories_by_date/YYYY/YYYY-MM-DD_HHMM_<id>.<ext>   (hard links; HEIC/WEBP -> JPG)
  thumbs/<id>.jpg                                     (small previews for hub.html)
Safe to re-run: existing outputs are skipped.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from PIL import Image, ImageOps  # noqa: E402
import pillow_heif  # noqa: E402

pillow_heif.register_heif_opener()

EXP = Path(sys.argv[1]).resolve()
REC = Path(sys.argv[2]).resolve()
DATA = REC / "data"
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
CONVERT = {".png", ".webp", ".heic", ".heif"}

posts = json.loads((DATA / "posts.json").read_text(encoding="utf-8"))
stories = json.loads((DATA / "stories.json").read_text(encoding="utf-8"))
profile = json.loads((DATA / "profile.json").read_text(encoding="utf-8"))


def slug(text, n=40):
    s = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").split("\n")[0]).strip("-").lower()
    return (s[:n].rstrip("-")) or "untitled"


def to_jpg(src: Path, dst: Path, max_side=None, quality=95):
    if dst.exists():
        return
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        if max_side:
            im.thumbnail((max_side, max_side))
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.save(dst, "JPEG", quality=quality, optimize=True)


def link_or_copy(src: Path, dst: Path):
    if dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def place(src: Path, dst_noext: Path):
    """Image -> JPG (converted if needed); video/other -> hard link."""
    ext = src.suffix.lower()
    if ext in CONVERT:
        dst = dst_noext.with_suffix(".jpg")
        to_jpg(src, dst)
    else:
        dst = dst_noext.with_suffix(ext if ext != ".jpeg" else ".jpg")
        link_or_copy(src, dst)
    return dst


jobs = []

# ------------------------------------------------------------------- posts
post_root = REC / "upload_ready" / "posts"
for i, p in enumerate(posts, 1):
    date = (p["date"] or "unknown")[:10]
    name = f"{i:02d}_{date}_{slug(p['caption'])}" + ("_ARCHIVED" if p["archived"] else "")
    d = post_root / name
    d.mkdir(parents=True, exist_ok=True)
    p["upload_folder"] = d.relative_to(REC).as_posix()
    p["upload_files"] = []
    for j, m in enumerate(p["media"], 1):
        src = EXP / m
        p["upload_files"].append(None)

        def job(src=src, d=d, j=j, p=p):
            out = place(src, d / f"{j:02d}")
            p["upload_files"][j - 1] = out.relative_to(REC).as_posix()

        jobs.append(job)
    (d / "caption.txt").write_text(p["caption"] + "\n", encoding="utf-8")
    info = [
        f"Original date : {p['date']}",
        f"Archived      : {p['archived']}",
        f"Items         : {len(p['media'])}",
        f"Tagged users  : {', '.join(p['tagged']) or '-'}",
        f"Location      : {p['lat']}, {p['lng']}  (https://maps.google.com/?q={p['lat']},{p['lng']})" if p["lat"] else "Location      : -",
        "Source files  :",
        *[f"  {m}" for m in p["media"]],
    ]
    (d / "info.txt").write_text("\n".join(info) + "\n", encoding="utf-8")

# profile photo
pf = profile.get("profile_photo_file")
if pf and (EXP / pf).exists():
    jobs.append(lambda: place(EXP / pf, REC / "upload_ready" / "profile_photo"))

# ----------------------------------------------------------------- stories
for s in stories:
    s["files"] = []
    if not s["media"]:
        continue
    m = s["media"][0]
    src = EXP / m
    if not src.exists():
        continue
    stamp = (s["date"] or "0000-00-00T00:00").replace(":", "").replace("T", "_")[:15]
    dst_noext = REC / "stories_by_date" / stamp[:4] / f"{stamp}_{src.stem}"

    def job(src=src, dst_noext=dst_noext, s=s):
        out = place(src, dst_noext)
        s["files"].append(out.relative_to(REC).as_posix())

    jobs.append(job)

# ------------------------------------------------------------------ thumbs
thumb_root = REC / "thumbs"


def thumb_job(src: Path):
    def run():
        to_jpg(src, thumb_root / f"{src.stem}.jpg", max_side=480, quality=80)
    return run


seen = set()
for m in [m for p in posts for m in p["media"]] + [s["media"][0] for s in stories if s["media"]]:
    src = EXP / m
    if src.suffix.lower() in IMG_EXT and src.exists() and src.stem not in seen:
        seen.add(src.stem)
        jobs.append(thumb_job(src))
if pf and (EXP / pf).exists():
    jobs.append(thumb_job(EXP / pf))

errors = []


def safe(fn):
    try:
        fn()
    except Exception as e:  # keep going; report at end
        errors.append(repr(e))


with ThreadPoolExecutor(max_workers=8) as ex:
    list(ex.map(safe, jobs))

(DATA / "posts.json").write_text(json.dumps(posts, ensure_ascii=False, indent=1), encoding="utf-8")
(DATA / "stories.json").write_text(json.dumps(stories, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"jobs={len(jobs)} errors={len(errors)}")
for e in errors[:10]:
    print("  ", e)

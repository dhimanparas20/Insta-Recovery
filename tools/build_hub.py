"""Build recovery/hub.html: a local, self-contained recovery dashboard.

Usage: python -I tools/build_hub.py <export_dir> <recovery_dir>
Progress is stored in the browser's localStorage (and can be exported).
"""
import json
import os
import secrets
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
EXP = Path(sys.argv[1]).resolve()
REC = Path(sys.argv[2]).resolve()
DATA = REC / "data"
try:  # the hub links to photos and chats inside the export, relative to recovery/
    EXP_REL = os.path.relpath(EXP, REC).replace("\\", "/") + "/"
except ValueError:  # export on another drive (Windows): use an absolute file:// link instead
    EXP_REL = EXP.as_uri() + "/"


def j(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


people, posts, stories, saved, chats, profile, summary = (
    j("people.json"), j("posts.json"), j("stories.json"), j("saved.json"), j("chats.json"), j("profile.json"), j("summary.json"))

P = [{
    "u": p["username"], "t": p["tier"][0], "m": p["mutual"], "fo": bool(p.get("following")), "fl": bool(p.get("follower")),
    "cf": bool(p.get("close_friend")), "fav": bool(p.get("favorite")), "hs": bool(p.get("hide_story")),
    "bl": bool(p.get("blocked")), "rs": bool(p.get("restricted")), "ps": bool(p.get("pending_sent")),
    "dm": p["dm_messages"], "sl": p["story_likes"], "sr": p["story_replies"], "cm": p["comments_on_their_posts"],
    "lc": p["liked_their_comments"], "fs": (p.get("following_since") or "")[:10], "fls": (p.get("follower_since") or "")[:10],
} for p in people]

PO = [{
    "d": p["date"], "c": p["caption"], "a": p["archived"], "tg": p["tagged"], "lat": p["lat"], "lng": p["lng"],
    "dir": p.get("upload_folder"),
    "items": [{"src": EXP_REL + m, "stem": Path(m).stem, "vid": m.lower().endswith(".mp4")} for m in p["media"]],
} for p in posts]

ST = [{"d": s["date"], "src": EXP_REL + s["media"][0], "stem": Path(s["media"][0]).stem,
       "vid": s["media"][0].lower().endswith(".mp4"), "f": (s.get("files") or [None])[0]} for s in stories if s["media"]]

SV = {"all": [{"url": s["url"], "o": s["owner"], "c": s["caption"][:160], "d": (s["saved"] or "")[:10]} for s in saved["all_saved"]],
      "cols": [{"n": c["name"], "items": c["items"]} for c in saved["collections"]]}

CH = [{"t": c["title"], "f": EXP_REL + c["file"], "box": c["box"], "g": c["is_group"], "n": c["messages"], "me": c["mine"],
       "first": (c["first"] or "")[:10], "last": (c["last"] or "")[:10], "ph": c["photos"], "vi": c["videos"],
       "au": c["audio"], "u": c["username_guess"], "pp": c["participants"][:12]} for c in chats]

PROFILE = {k: profile.get(k) for k in ("Username", "Name", "Bio", "Website", "Private Account", "Music on profile", "Gender")}

# shared secret for the local post helper (tools/autopost.py); keeps other websites from driving it
tok_file = DATA / ".post_token"
if not tok_file.exists() or not tok_file.read_text().strip():
    tok_file.write_text(secrets.token_hex(16))
PT = tok_file.read_text().strip()

payload = json.dumps({"P": P, "PO": PO, "ST": ST, "SV": SV, "CH": CH, "PROFILE": PROFILE, "S": summary, "EXP": EXP_REL,
                      "PT": PT},
                     ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

tpl = (Path(__file__).parent / "hub_template.html").read_text(encoding="utf-8")
(REC / "hub.html").write_text(tpl.replace("/*__DATA__*/null", payload), encoding="utf-8")
print("wrote", REC / "hub.html", f"{len(payload)//1024} KB data")

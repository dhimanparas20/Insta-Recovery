"""Follow-back checker for the new Instagram account.

Reads your followers and following, saves a dated snapshot, and opens a web
report: who doesn't follow you back, who you don't follow back, mutuals,
changes since the last scan, and progress against the old account.

Usage (from the kit folder):
  python recover.py followers                 # live scan (sign in if needed) + report
  python recover.py followers import <dir>    # use a 'Download your information' export of the NEW account
  python recover.py followers report          # rebuild the report from saved scans

Live scan uses the same Chrome sign-in as the post helper and auto-follow (recovery/.browser-profile).
It only reads lists; it never follows, unfollows or posts.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parent
REC = ROOT / "recovery"
OLD_PEOPLE = REC / "data" / "people.json"
IG = "https://www.instagram.com"
APP_ID = "936619743392459"  # public web-app id sent by instagram.com itself


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class ScanError(Exception):
    pass


# ----------------------------------------------------------------- live scan
FETCH_JS = """async (url) => {
  const csrf = (document.cookie.match(/(?:^|; )csrftoken=([^;]+)/) || [])[1] || "";
  const r = await fetch(url, {credentials: "include", headers: {
    "x-ig-app-id": "%s", "x-requested-with": "XMLHttpRequest", "x-csrftoken": csrf}});
  const text = await r.text();
  let json = null; try { json = JSON.parse(text); } catch (e) {}
  return {status: r.status, json, snippet: json ? null : text.slice(0, 200)};
}""" % APP_ID


def api_get(page, path):
    r = page.evaluate(FETCH_JS, IG + path)
    j = r.get("json") or {}
    msg = (j.get("message") or "") if isinstance(j, dict) else ""
    if r["status"] == 429 or re.search(r"wait a few minutes|try again later|rate limit", msg, re.I):
        raise ScanError("Instagram asked to slow down (rate limit). Wait an hour and run again.")
    if r["status"] in (401, 403) or j.get("require_login") or msg == "checkpoint_required" or "challenge" in j:
        raise ScanError(f"Not allowed ({r['status']} {msg or ''}). Open Instagram in the Chrome window, clear any "
                        "security check, or sign in again there.")
    if r["status"] != 200 or not isinstance(j, dict):
        raise ScanError(f"Unexpected response {r['status']}: {msg or r.get('snippet')}")
    return j


def slim(u):
    return {"username": u.get("username"), "name": u.get("full_name") or "", "pk": str(u.get("pk") or u.get("pk_id") or ""),
            "private": bool(u.get("is_private")), "verified": bool(u.get("is_verified")),
            "pic": u.get("profile_pic_url") or ""}


def fetch_list(page, uid, kind):
    users, seen, max_id, pages = [], set(), None, 0
    while True:
        q = "count=50"
        if kind == "followers":
            q += "&search_surface=follow_list_page"
        if max_id:
            q += "&max_id=" + quote(str(max_id))
        j = api_get(page, f"/api/v1/friendships/{uid}/{kind}/?{q}")
        for u in j.get("users", []):
            s = slim(u)
            if s["username"] and s["username"] not in seen:
                seen.add(s["username"])
                users.append(s)
        pages += 1
        max_id = j.get("next_max_id")
        log(f"  {kind}: {len(users)}")
        if not max_id or pages > 400:
            break
        time.sleep(random.uniform(1.2, 2.8))
    return users


def scan(args):
    from playwright.sync_api import sync_playwright
    import autofollow as AF  # shared Chrome profile + sign-in helpers

    with sync_playwright() as pw:
        ctx, page = AF.open_browser(pw)
        try:
            if not AF.logged_in(page):
                log("Not signed in. Sign in to your NEW account in the Chrome window...")
                page.goto(IG + "/accounts/login/", wait_until="domcontentloaded")
                deadline = time.time() + args.login_timeout
                while time.time() < deadline and not AF.signed_in_here(page):
                    page.wait_for_timeout(3000)
                if not AF.signed_in_here(page):
                    raise ScanError("Timed out waiting for sign-in.")
                log("Signed in.")
            cookies = {c["name"]: c["value"] for c in ctx.cookies(IG)}
            uid = cookies.get("ds_user_id")
            if not uid:
                raise ScanError("Couldn't read your account id from the session. Sign out and in again in the Chrome window.")
            me = {"pk": uid}
            try:
                info = api_get(page, f"/api/v1/users/{uid}/info/").get("user", {})
                me.update(username=info.get("username"), name=info.get("full_name"),
                          followers=info.get("follower_count"), following=info.get("following_count"),
                          pic=info.get("profile_pic_url"), private=info.get("is_private"))
            except ScanError as e:
                log(f"Couldn't read profile counts ({e}); continuing.")
            log(f"Scanning @{me.get('username') or uid} "
                f"(Instagram says {me.get('followers', '?')} followers / {me.get('following', '?')} following)")
            following = fetch_list(page, uid, "following")
            time.sleep(random.uniform(2, 4))
            followers = fetch_list(page, uid, "followers")
        finally:
            ctx.close()
    return save_snapshot(args.data_dir, "live", me, followers, following)


# --------------------------------------------------------------- export import


def _user_from_item(item):
    sld = (item.get("string_list_data") or [{}])[0]
    u = item.get("title") or sld.get("value")
    if not u and sld.get("href"):
        m = re.search(r"instagram\.com/(?:_u/)?([A-Za-z0-9._]+)", sld["href"])
        u = m.group(1) if m else None
    return u


def import_export(args):
    exp = Path(args.path).resolve()
    ff = next((p for p in exp.rglob("followers_and_following") if p.is_dir()), None)
    if not ff:
        raise ScanError(f"No connections/followers_and_following folder under {exp}")
    followers, following = [], []
    jf = sorted(ff.glob("followers_*.json"))
    if jf:
        for f in jf:
            data = json.loads(f.read_text(encoding="utf-8"))
            items = data if isinstance(data, list) else next(iter(data.values()), [])
            followers += [u for u in map(_user_from_item, items) if u]
        g = ff / "following.json"
        if g.exists():
            data = json.loads(g.read_text(encoding="utf-8"))
            items = data.get("relationships_following", []) if isinstance(data, dict) else data
            following = [u for u in map(_user_from_item, items) if u]
    else:
        import igparse as P
        for f in sorted(ff.glob("followers_*.html")):
            for e in P.entries(P.load(f)):
                a = e.find("a")
                if a and a.text().strip():
                    followers.append(a.text().strip())
        g = ff / "following.html"
        if g.exists():
            following = [t for t in (P.entry_title(e) for e in P.entries(P.load(g))) if t]
    if not followers and not following:
        raise ScanError("Found the folder but no followers/following files in it.")
    owner = None
    m = re.search(r"instagram-([^-]+(?:-[^-]+)*?)-\d{4}-\d{2}-\d{2}", exp.name)
    if m:
        owner = m.group(1)
    mk = lambda u: {"username": u, "name": "", "pk": "", "private": False, "verified": False, "pic": ""}
    me = {"username": owner, "followers": len(followers), "following": len(following)}
    return save_snapshot(args.data_dir, "export", me, [mk(u) for u in dict.fromkeys(followers)],
                         [mk(u) for u in dict.fromkeys(following)])


# -------------------------------------------------------------- snapshots


def save_snapshot(data_dir, source, me, followers, following):
    snaps = Path(data_dir) / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    exp_f, exp_g = me.get("followers"), me.get("following")
    complete = all(e is None or got >= e - max(3, int(e * 0.02))
                   for got, e in ((len(followers), exp_f), (len(following), exp_g)))
    snap = {"taken": datetime.now().isoformat(timespec="seconds"), "source": source, "complete": complete,
            "me": me, "followers": followers, "following": following}
    p = snaps / f"{datetime.now():%Y-%m-%d_%H%M%S}.json"
    p.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    log(f"Saved {p.name}: {len(followers)} followers, {len(following)} following"
        + ("" if complete else "  (WARNING: fewer than Instagram's own count - some accounts may be hidden)"))
    return p


def build_report(data_dir, open_it=True):
    data_dir = Path(data_dir)
    snaps = sorted((data_dir / "snapshots").glob("*.json"))
    if not snaps:
        raise ScanError("No scans yet. Run: followcheck.py scan")
    latest = json.loads(snaps[-1].read_text(encoding="utf-8"))
    me_name = latest["me"].get("username")
    prev = None
    for p in reversed(snaps[:-1]):  # previous scan of the same account
        s = json.loads(p.read_text(encoding="utf-8"))
        if s["me"].get("username") == me_name:
            prev = s
            break
    old = {}
    if OLD_PEOPLE.exists():
        for p in json.loads(OLD_PEOPLE.read_text(encoding="utf-8")):
            old[p["username"]] = {"t": p["tier"][0], "cf": bool(p.get("close_friend")), "fav": bool(p.get("favorite")),
                                  "m": p["mutual"], "fo": bool(p.get("following")), "fl": bool(p.get("follower")),
                                  "dm": p.get("dm_messages", 0)}
    history = []
    for p in snaps[-30:]:
        s = json.loads(p.read_text(encoding="utf-8"))
        if s["me"].get("username") == me_name:
            history.append({"t": s["taken"], "fl": len(s["followers"]), "fo": len(s["following"])})
    payload = {
        "me": latest["me"], "taken": latest["taken"], "source": latest["source"], "complete": latest["complete"],
        "followers": latest["followers"], "following": latest["following"],
        "prev": None if not prev else {"taken": prev["taken"], "complete": prev["complete"],
                                        "followers": [u["username"] for u in prev["followers"]],
                                        "following": [u["username"] for u in prev["following"]]},
        "old": old, "history": history,
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    tpl = (HERE / "followcheck_template.html").read_text(encoding="utf-8")
    out = data_dir / "report.html"
    out.write_text(tpl.replace("/*__DATA__*/null", blob), encoding="utf-8")
    # keep the recovery hub in sync: everyone you now follow counts as "done"
    fol = {u["username"] for u in latest["following"]}
    hub = {"people": {u: "f" for u in fol if u in old}}
    (data_dir / "hub_progress_from_scan.json").write_text(json.dumps(hub), encoding="utf-8")
    log(f"Report: {out}")
    if open_it:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())  # the local report, in the default browser
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["scan", "import", "report"])
    ap.add_argument("path", nargs="?", help="export folder (for import)")
    ap.add_argument("--data-dir", default=str(REC / "followcheck"), help="where scans and the report are kept")
    ap.add_argument("--login-timeout", type=int, default=600, help="seconds to wait for sign-in (default 600)")
    ap.add_argument("--no-open", action="store_true", help="don't open the report in the browser")
    args = ap.parse_args()
    try:
        if args.command == "scan":
            scan(args)
        elif args.command == "import":
            if not args.path:
                ap.error("import needs the export folder path")
            import_export(args)
        build_report(args.data_dir, open_it=not args.no_open)
    except ScanError as e:
        log(f"STOPPED: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()

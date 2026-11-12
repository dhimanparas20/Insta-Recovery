"""Paced auto-follow for the new Instagram account, driven through real Chrome.

You sign in yourself, once, in the Chrome window this script opens; the
session is kept in recovery/.browser-profile (separate from your normal Chrome).
No password is stored or typed by this script.

Usage (from the kit folder):
  python recover.py follow                    # follow up to today's cap (sign in the first time)
  python recover.py follow run --dry-run      # visit 5 profiles, never click
  python recover.py follow status             # progress summary
  python recover.py follow login              # sign in only

Safety: stops immediately on any "Try again later"/restriction notice, on a
security checkpoint, if logged out, or after repeated failures. It never
retries around a block. Default cap is 25 follows per day.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
REC = ROOT / "recovery"
PEOPLE = REC / "data" / "people.json"
STATE = REC / "data" / "autofollow_state.json"
HUB_EXPORT = REC / "autofollow_progress_for_hub.json"
PROFILE_DIR = REC / ".browser-profile"
IG = "https://www.instagram.com/"

FOLLOW_RE = re.compile(r"^\s*(Follow|Follow Back)\s*$", re.I)
DONE_RE = re.compile(r"^\s*(Following|Requested)\s*$", re.I)
BLOCK_RE = re.compile(r"try again later|we restrict certain activity|limit how often|action blocked|"
                      r"suspicious activity|temporarily blocked|we suspect automated behavio", re.I)
MISSING_RE = re.compile(r"Sorry, this page isn't available|Profile isn't available|page may have been removed", re.I)


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"people": {}, "daily": {}, "events": []}


def save_state(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    # hub-importable progress (Start tab -> Import progress)
    hub_people = {u: ("f" if v["result"] in ("followed", "requested", "already") else "s")
                  for u, v in st["people"].items() if v["result"] in ("followed", "requested", "already", "missing")}
    HUB_EXPORT.write_text(json.dumps({"people": hub_people, "log": st["daily"]}, ensure_ascii=False), encoding="utf-8")


def queue(st, tiers):
    people = json.loads(PEOPLE.read_text(encoding="utf-8"))  # already sorted by tier, priority
    done = {u for u, v in st["people"].items() if v["result"] in ("followed", "requested", "already", "missing", "skipped")}
    return [p["username"] for p in people if p["tier"][0] in tiers and p["username"] not in done
            and not p.get("blocked") and not p.get("restricted")]


def open_browser(pw):
    """Real Google Chrome if installed, else Microsoft Edge, else Playwright's own Chromium."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    errors = []
    for channel in ("chrome", "msedge", None):
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR), channel=channel, headless=False, chromium_sandbox=True,
                viewport={"width": 1280, "height": 900}, locale="en-US")
            break
        except Exception as e:  # noqa: BLE001 - try the next browser
            errors.append(f"{channel or 'chromium'}: {str(e).splitlines()[0]}")
    else:
        raise RuntimeError("Couldn't start a browser. Install Google Chrome (recommended), or run: "
                           "python -m playwright install chromium\n  " + "\n  ".join(errors))
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return ctx, page


def wait_for_sign_in(page, timeout=600):
    """Open the login page and wait (up to `timeout` s) until you've signed in yourself in the window."""
    page.goto(IG + "accounts/login/", wait_until="domcontentloaded")
    log("Sign in to your NEW account in the Chrome window (and finish any 2FA / 'save login info' prompts).")
    deadline = time.time() + timeout
    while time.time() < deadline:
        page.wait_for_timeout(3000)
        if signed_in_here(page):
            page.wait_for_timeout(2000)
            log("Signed in. The session is saved for next time.")
            return True
    log("Timed out waiting for sign-in.")
    return False


SIGNED_IN_SEL = "a[href^='/direct/'], svg[aria-label='Home'], svg[aria-label='New post'], svg[aria-label='Direct']"
LOGIN_UI_RE = re.compile(r"^\s*(Log in|Sign up|Sign up for Instagram)\s*$", re.I)


def signed_in_here(page):
    """True only if signed-in navigation is present on the current page."""
    try:
        return page.locator(SIGNED_IN_SEL).count() > 0
    except Exception:
        return False


def login_ui_here(page):
    try:
        return (page.locator("input[name='username']").count() > 0
                or page.get_by_role("button", name=LOGIN_UI_RE).count() > 0
                or page.get_by_role("link", name=LOGIN_UI_RE).count() > 0)
    except Exception:
        return False


def logged_in(page):
    page.goto(IG, wait_until="domcontentloaded")
    if "/challenge" in page.url or "/accounts/suspended" in page.url or "/accounts/login" in page.url:
        return False
    for _ in range(12):  # up to ~12s for the app shell to render
        if signed_in_here(page):
            return True
        page.wait_for_timeout(1000)
    return False


def body_text(page):
    try:
        return page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


def find_buttons(page):
    """Return (follow_btn, done_btn) scoped to the profile header when possible."""
    for scope in (page.locator("header").first, page.locator("main").first):
        try:
            if not scope.count():
                continue
        except Exception:
            continue
        f = scope.get_by_role("button", name=FOLLOW_RE)
        d = scope.get_by_role("button", name=DONE_RE)
        if f.count() or d.count():
            return (f.first if f.count() else None), (d.first if d.count() else None)
    return None, None


def check_profile(page, username, dry_run):
    """Returns one of: followed, requested, already, missing, would_follow, failed, BLOCKED, LOGGED_OUT, CHECKPOINT."""
    page.goto(f"{IG}{username}/", wait_until="domcontentloaded")
    page.wait_for_timeout(random.randint(3000, 6000))
    if "/accounts/login" in page.url:
        return "LOGGED_OUT"
    if "/challenge" in page.url or "/accounts/suspended" in page.url:
        return "CHECKPOINT"
    txt = body_text(page)
    if MISSING_RE.search(txt):
        return "missing"
    if BLOCK_RE.search(txt):
        return "BLOCKED"
    follow, done = None, None
    for _ in range(8):
        follow, done = find_buttons(page)
        if follow or done:
            break
        page.wait_for_timeout(1000)
    if done and not follow:
        return "already"
    if not follow:
        if login_ui_here(page) and not signed_in_here(page):
            return "LOGGED_OUT"
        return "failed"
    if dry_run:
        return "would_follow"
    page.wait_for_timeout(random.randint(800, 2500))
    follow.click()
    page.wait_for_timeout(random.randint(3000, 5000))
    if "/challenge" in page.url:
        return "CHECKPOINT"
    if BLOCK_RE.search(body_text(page)):
        return "BLOCKED"
    _, done = find_buttons(page)
    if done:
        label = (done.inner_text() or "").strip().lower()
        return "requested" if label.startswith("requested") else "followed"
    return "failed"


def cmd_login(args):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx, page = open_browser(pw)
        if logged_in(page):
            log("Already signed in.")
        else:
            log("Waiting up to 10 minutes; the window closes by itself once you're signed in.")
            wait_for_sign_in(page)
        ctx.close()


def cmd_run(args):
    from playwright.sync_api import sync_playwright
    st = load_state()
    today = date.today().isoformat()
    done_today = st["daily"].get(today, 0)
    cap = args.cap
    if cap > 40:
        log(f"WARNING: cap {cap}/day is high for a new account; 20-30 is safer.")
    remaining = cap - done_today if not args.dry_run else args.limit
    if remaining <= 0:
        log(f"Already followed {done_today} today (cap {cap}). Run again tomorrow.")
        return
    q = queue(st, args.tiers)
    if not q:
        log("Nothing left to follow in the selected tiers.")
        return
    log(f"{len(q)} left in tiers {args.tiers}. Today so far: {done_today}/{cap}. "
        f"{'DRY RUN - no clicks.' if args.dry_run else f'Will follow up to {remaining} now.'}")

    with sync_playwright() as pw:
        ctx, page = open_browser(pw)
        if not logged_in(page) and not wait_for_sign_in(page):
            log("Not signed in (or a security check is showing). Clear it in the window and run this again.")
            ctx.close()
            sys.exit(2)
        acted, fails, since_break = 0, 0, 0
        next_break = random.randint(8, 12)
        for u in q:
            if acted >= remaining:
                break
            res = check_profile(page, u, args.dry_run)
            log(f"@{u}: {res}")
            if res in ("BLOCKED", "CHECKPOINT", "LOGGED_OUT"):
                st["events"].append({"at": datetime.now().isoformat(timespec="seconds"), "event": res, "user": u})
                save_state(st)
                log({"BLOCKED": "Instagram restricted activity. STOPPING. Wait at least 48h, then lower --cap.",
                     "CHECKPOINT": "Security check shown. STOPPING. Complete it by hand in the app, wait 24h.",
                     "LOGGED_OUT": "Session ended. STOPPING. Run 'login' again."}[res])
                ctx.close()
                sys.exit(3)
            if args.dry_run:
                acted += 1
                page.wait_for_timeout(random.randint(4000, 9000))
                continue
            if res == "failed":
                fails += 1
                if fails >= 3:
                    log("3 failures in a row - the page layout may have changed. STOPPING.")
                    break
                continue
            fails = 0
            st["people"][u] = {"result": res, "at": datetime.now().isoformat(timespec="seconds")}
            if res in ("followed", "requested"):
                acted += 1
                since_break += 1
                st["daily"][today] = st["daily"].get(today, 0) + 1
            save_state(st)
            if acted >= remaining:
                break
            if res in ("followed", "requested"):
                if since_break >= next_break:
                    pause = random.randint(300, 600)
                    log(f"Longer break: {pause // 60} min")
                    time.sleep(pause)
                    since_break, next_break = 0, random.randint(8, 12)
                else:
                    pause = random.randint(args.min_delay, args.max_delay)
                    log(f"waiting {pause}s ({st['daily'][today]}/{cap} today)")
                    time.sleep(pause)
            else:
                time.sleep(random.randint(5, 15))
        ctx.close()
    log(f"Done for now. Today: {st['daily'].get(today, 0)}/{cap}. Progress file for the hub: {HUB_EXPORT}")


def cmd_status(args):
    st = load_state()
    from collections import Counter
    c = Counter(v["result"] for v in st["people"].values())
    left = len(queue(st, args.tiers))
    print(json.dumps({"results": c, "left_in_tiers": left, "tiers": args.tiers,
                      "last_7_days": dict(sorted(st["daily"].items())[-7:]), "events": st["events"][-5:]}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["login", "run", "status"])
    ap.add_argument("--cap", type=int, default=25, help="max follows per calendar day (default 25)")
    ap.add_argument("--tiers", default="12345", help="tiers to include, e.g. 1 or 123 (default 12345)")
    ap.add_argument("--min-delay", type=int, default=45, help="min seconds between follows (default 45)")
    ap.add_argument("--max-delay", type=int, default=150, help="max seconds between follows (default 150)")
    ap.add_argument("--dry-run", action="store_true", help="visit profiles and report, never click")
    ap.add_argument("--limit", type=int, default=5, help="profiles to visit in --dry-run (default 5)")
    args = ap.parse_args()
    if args.min_delay > args.max_delay:
        ap.error("--min-delay must be <= --max-delay")
    {"login": cmd_login, "run": cmd_run, "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    main()

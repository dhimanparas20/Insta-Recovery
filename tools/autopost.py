"""One-click reposting of your old posts to the new Instagram account.

Keep this running (double-click post_helper.bat, or: python recover.py post), then use the
"Post to new account" buttons in recovery/hub.html (Posts tab). It drives instagram.com in
the same Chrome profile as the follower checker, the way you would by hand:
Create -> select all files -> Original crop -> caption -> tag people -> location -> Share.

  python recover.py post              # the helper the hub talks to
  python recover.py post --cap 4      # change the daily limit (default 6)
  python recover.py post --simulate   # UI test: no browser, nothing posted

Safety: a post that was shared (or may have been) is never posted twice unless you click
"post again" in the hub. Stops on Instagram error or restriction notices.
Only listens on 127.0.0.1 and only accepts requests carrying the hub's secret token.
"""
from __future__ import annotations

import argparse
import hmac
import json
import math
import queue
import re
import secrets
import socket
import sys
import threading
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parent
REC = ROOT / "recovery"
DATA = REC / "data"
POSTS = DATA / "posts.json"
STATE = DATA / "autopost_state.json"
LOCS = DATA / "post_locations.json"
TOKEN = DATA / ".post_token"
EXTRA = REC / "upload_ready" / "extra"   # photos added from the computer, per post
STORIES = REC / "stories_by_date"         # your old stories, addable to a post
UPLOAD_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".avif", ".mp4", ".mov"}
POST_EXT = UPLOAD_EXT - {".webp"}         # what instagram.com's file picker accepts
PORT = 8767
LOCS_VERSION = 2  # bump to make the helper redo the place-name lookup
IG = "https://www.instagram.com/"

DONE_RE = re.compile(r"has been shared|^\s*(post|reel) shared\s*$", re.I | re.M)
FAIL_RE = re.compile(r"couldn.t be shared|could not be shared|something went wrong|try again later|"
                     r"we restrict certain activity|action blocked|suspect automated", re.I)
TAG_SPOTS = [(0.5, 0.3), (0.28, 0.55), (0.72, 0.55), (0.28, 0.8), (0.72, 0.8), (0.5, 0.65)]


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class PostError(Exception):
    pass


def get_token():
    if TOKEN.exists() and TOKEN.read_text().strip():
        return TOKEN.read_text().strip()
    t = secrets.token_hex(16)
    TOKEN.write_text(t)
    return t


def added_file(rel):
    """An added photo's path (relative to recovery/) if it's a real, postable file in extra/ or stories_by_date/."""
    try:
        p = (REC / rel).resolve()
    except (OSError, ValueError):
        return None
    inside = any(p.is_relative_to(d.resolve()) for d in (EXTRA, STORIES))
    return p if inside and p.is_file() and p.suffix.lower() in POST_EXT else None


def order_text(order, n_orig):
    used = sum(isinstance(k, int) for k in order)
    notes = [f"{n_orig - used} left out"] if n_orig - used else []
    if len(order) - used:
        notes.append(f"{len(order) - used} added")
    return ("Your order: " + " ".join(str(k + 1) if isinstance(k, int) else "+" for k in order)
            + (f" ({', '.join(notes)})" if notes else ""))


def to_jpg(p):
    """HEIC/WEBP -> JPG (instagram.com's picker and the hub's thumbnails can't use them)."""
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    out = p.with_suffix(".jpg")
    with Image.open(p) as im:
        ImageOps.exif_transpose(im).convert("RGB").save(out, quality=95)
    p.unlink()
    return out


def dist_m(lat1, lng1, lat2, lng2):
    r = math.radians
    a = (math.sin(r(lat2 - lat1) / 2) ** 2
         + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lng2 - lng1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(a))


# ------------------------------------------------------------------ state
class Store:
    def __init__(self, persist=True):
        self.lock = threading.Lock()
        self.persist = persist
        self.data = {"posts": {}, "daily": {}}
        if persist and STATE.exists():
            self.data = json.loads(STATE.read_text(encoding="utf-8"))

    def _save(self):
        if self.persist:
            STATE.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")

    def set_post(self, i, **kw):
        with self.lock:
            self.data["posts"].setdefault(str(i), {}).update(kw)
            self._save()

    def clear_post(self, i):
        with self.lock:
            self.data["posts"].pop(str(i), None)
            self._save()

    def today(self):
        return self.data["daily"].get(date.today().isoformat(), 0)

    def bump(self):
        with self.lock:
            d = date.today().isoformat()
            self.data["daily"][d] = self.data["daily"].get(d, 0) + 1
            self._save()


class Job:
    def __init__(self, i, opts):
        self.i, self.opts = i, opts
        self.state, self.steps, self.error, self.url = "queued", [], None, None
        self.cancel = False

    def step(self, s):
        self.steps.append(s)
        log(f"#{self.i + 1}: {s}")

    def view(self):
        return {"i": self.i, "state": self.state, "steps": self.steps[-14:], "error": self.error, "url": self.url,
                "review": self.opts.get("review")}


class App:
    def __init__(self, args, posts):
        self.simulate = args.simulate
        self.cap = args.cap
        self.token = get_token()
        self.posts = posts
        self.store = Store(persist=not args.simulate)
        self.q = queue.Queue()
        self.job = self.last = None
        self.account = None
        self.ready = False
        self.note = "Starting Chrome..."
        self.locations = {}
        if LOCS.exists() and not args.simulate:
            self.locations = json.loads(LOCS.read_text(encoding="utf-8"))
        self.lock = threading.Lock()

    def status(self):
        j = self.job or self.last
        return {"ok": True, "ready": self.ready, "note": self.note, "account": self.account, "busy": self.job is not None,
                "job": j.view() if j else None, "posted": self.store.data["posts"], "today": self.store.today(),
                "cap": self.cap, "locations": bool(self.locations), "simulate": self.simulate}

    def submit(self, body):
        with self.lock:
            if not self.ready:
                return 409, {"error": "The helper isn't ready yet. Check its window (you may need to sign in)."}
            if self.job:
                return 409, {"error": "Already posting another one. Wait for it to finish."}
            try:
                i = int(body.get("i"))
            except (TypeError, ValueError):
                return 400, {"error": "bad post number"}
            if not 0 <= i < len(self.posts):
                return 400, {"error": "bad post number"}
            prev = self.store.data["posts"].get(str(i), {})
            if prev.get("state") in ("posted", "shared_unconfirmed") and not body.get("force"):
                return 409, {"error": "Already posted. Use 'post again' if you really want it twice."}
            if self.store.today() >= self.cap and not body.get("over_cap"):
                return 429, {"error": f"Daily limit reached ({self.cap} posts today). Post the rest tomorrow; "
                                      "bursts of posts on a new account look like spam.", "code": "cap"}
            if self.store.today() >= self.cap:
                log(f"#{i + 1}: over the daily limit ({self.store.today()}/{self.cap}) because you chose 'post anyway'")
            opts = {"review": bool(body.get("review")), "add_date": bool(body.get("add_date")),
                    "loc": str(body.get("loc") or "")[:200], "tags": bool(body.get("tags", True)),
                    "archive": bool(body.get("archive")), "order": None}
            order = body.get("order")  # chosen in the hub: original item numbers and/or added file paths
            if order is not None:
                n = len(self.posts[i]["upload_files"])
                ok = isinstance(order, list) and 1 <= len(order) <= 20
                ints = [k for k in order if isinstance(k, int) and not isinstance(k, bool)] if ok else []
                paths = [k for k in order if isinstance(k, str)] if ok else []
                ok = (ok and len(ints) + len(paths) == len(order) and len(set(ints)) == len(ints)
                      and len(set(paths)) == len(paths) and all(0 <= k < n for k in ints)
                      and all(added_file(p) for p in paths))
                if not ok:
                    return 400, {"error": "bad photo order (an added photo may have been moved or deleted)"}
                if order != list(range(n)):
                    opts["order"] = order
            self.job = Job(i, opts)
            self.q.put(self.job)
            return 200, {"ok": True}

    def cancel(self):
        if self.job:
            self.job.cancel = True


APP: App | None = None


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "autopost"

    def log_message(self, *a):
        pass

    def _host_ok(self):
        return (self.headers.get("Host") or "").lower() in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type, x-post-token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        if not self._host_ok():
            self._send(403, {"error": "bad host"})
            return False
        if not hmac.compare_digest(self.headers.get("X-Post-Token", ""), APP.token):
            self._send(401, {"error": "Token mismatch: rebuild the hub (tools/build_hub.py) and reload it."})
            return False
        return True

    def do_OPTIONS(self):
        if not self._host_ok():
            return self._send(403, {"error": "bad host"})
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._auth():
            return
        path = self.path.split("?")[0]
        if path == "/status":
            return self._send(200, APP.status())
        if path == "/locations":
            return self._send(200, APP.locations)
        self._send(404, {"error": "not found"})

    def _upload(self):
        """POST /upload?post=<i>&name=<file name>, body = the file. Saved under upload_ready/extra/NN/."""
        q = parse_qs(urlparse(self.path).query)
        try:
            i = int(q.get("post", [""])[0])
        except ValueError:
            return self._send(400, {"error": "bad post number"})
        if not 0 <= i < len(APP.posts):
            return self._send(400, {"error": "bad post number"})
        name = (q.get("name") or ["file"])[0]
        ext = Path(name).suffix.lower()
        if ext not in UPLOAD_EXT:
            return self._send(415, {"error": f"'{name}': {ext or 'this file type'} isn't supported. "
                                             "Use JPG, PNG, HEIC, WEBP, MP4 or MOV."})
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= 2 * 1024 ** 3:
            return self._send(413, {"error": f"'{name}' is empty or bigger than 2 GB"})
        folder = EXTRA / f"{i + 1:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^\w.-]+", "_", Path(name).stem)[:50] or "file"
        dest = folder / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{stem}{ext}"
        with open(dest, "wb") as f:
            left = n
            while left:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left:
            dest.unlink(missing_ok=True)
            return self._send(400, {"error": f"'{name}' upload was cut off"})
        if ext in (".heic", ".heif", ".webp"):
            try:
                dest = to_jpg(dest)
            except Exception as e:  # noqa: BLE001
                dest.unlink(missing_ok=True)
                return self._send(415, {"error": f"Couldn't convert '{name}' to JPG: {e}"})
        log(f"#{i + 1}: added {name} -> {dest.relative_to(REC).as_posix()}")
        return self._send(200, {"src": dest.relative_to(REC).as_posix(), "vid": dest.suffix.lower() in (".mp4", ".mov"),
                                "name": name})

    def do_POST(self):
        if not self._auth():
            return
        if self.path.split("?")[0] == "/upload":
            return self._upload()
        n = int(self.headers.get("Content-Length") or 0)
        if n > 20000:
            return self._send(413, {"error": "too big"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        path = self.path.split("?")[0]
        if path == "/post":
            code, obj = APP.submit(body)
            return self._send(code, obj)
        if path == "/cancel":
            APP.cancel()
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})


# ------------------------------------------------------------------ browser steps
def dialog(page):
    return page.locator("div[role=dialog]")


def dialog_text(page):
    try:
        return page.evaluate("() => [...document.querySelectorAll('div[role=dialog]')].map(d => d.innerText).join('\\n')")
    except Exception:
        return ""


HEADS_JS = ("() => [...document.querySelectorAll('div[role=dialog] h1, div[role=dialog] h2, div[role=dialog] [role=heading]')]"
            ".map(h => (h.innerText || '').trim())")


def stage(page):
    """Where the Create dialog is: compose | back (Crop/Edit/file picker) | discard? | sharing | done | closed."""
    if not dialog(page).count():
        return "closed", ""
    txt = dialog_text(page)
    if DONE_RE.search(txt):
        return "done", txt
    try:
        heads = page.evaluate(HEADS_JS)
    except Exception:
        heads = []
    if "Discard post?" in heads:
        return "discard?", txt
    if any(h in ("Crop", "Edit") for h in heads) or "Select from computer" in txt:
        return "back", txt
    if page.locator("div[role=dialog] div[contenteditable=true]").count():
        return "compose", txt
    return "sharing", txt


def dismiss_popups(page):
    for name in ("Not Now", "Not now"):
        b = page.get_by_role("button", name=name, exact=True)
        if b.count():
            try:
                b.first.click(timeout=3000)
                page.wait_for_timeout(700)
            except Exception:
                pass


def discard(page):
    for _ in range(5):
        d = page.get_by_role("button", name="Discard", exact=True)
        if d.count():
            d.first.click()
            page.wait_for_timeout(1200)
            return
        if not dialog(page).count():
            return
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)


def close_popover(page):
    """Escape closes a search popover; if it reaches the 'Discard post?' prompt, back out of it."""
    page.keyboard.press("Escape")
    page.wait_for_timeout(800)
    c = page.get_by_role("button", name="Cancel", exact=True)
    if page.get_by_text("Discard post?").count() and c.count():
        c.first.click()
        page.wait_for_timeout(600)


def click_next(page):
    dialog(page).get_by_role("button", name="Next", exact=True).first.click()


def caption_box(page):
    return page.locator("div[role=dialog] div[contenteditable=true][role=textbox]").first


def squash(s):
    """lowercase letters/digits only (any script), for comparing place names"""
    return re.sub(r"[\W_]+", "", (s or "").lower())


def clean_addr(s):
    """search results prefix the address with a distance from *you* ("0.8mi · ..."); drop it"""
    return re.sub(r"^\s*[<>]?\s*[\d.]+\s*(mi|km|m)\b\s*·?\s*", "", s or "").strip()


def norm(s):
    return re.sub(r"\s+", " ", (s or "").replace("\u200b", "")).strip()


def nice_date(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%-d %b %Y") if sys.platform != "win32" else \
            datetime.fromisoformat(iso).strftime("%d %b %Y").lstrip("0")
    except ValueError:
        return iso[:10]


def find_locations(page, posts):
    import followcheck as FC
    out = {}
    for i, p in enumerate(posts):
        if p.get("lat") in (None, "") or p.get("lng") in (None, ""):
            continue
        lat, lng = float(p["lat"]), float(p["lng"])
        orig = []
        if p.get("place"):  # the export's original location name: find that venue near the original spot
            j = FC.api_get(page, f"/api/v1/location_search/?latitude={lat}&longitude={lng}"
                                 f"&search_query={quote(p['place'])}")
            same = [v for v in j.get("venues") or [] if v.get("lat") is not None
                    and squash(v.get("name")) == squash(p["place"])]
            same.sort(key=lambda v: dist_m(lat, lng, v["lat"], v["lng"]))
            v = same[0] if same else {}
            orig = [{"name": v.get("name") or p["place"], "addr": clean_addr(v.get("address")), "orig": True,
                     "dist": round(dist_m(lat, lng, v["lat"], v["lng"])) if v else None, "rank": -1}]
            time.sleep(1.2)
        j = FC.api_get(page, f"/api/v1/location_search/?latitude={lat}&longitude={lng}")
        seen, cands = {squash(c["name"]) for c in orig}, []
        for rank, v in enumerate(j.get("venues") or []):
            if v.get("lat") is None or not v.get("name") or squash(v["name"]) in seen:
                continue
            seen.add(squash(v["name"]))
            cands.append({"name": v["name"], "addr": clean_addr(v.get("address")),
                          "dist": round(dist_m(lat, lng, v["lat"], v["lng"])), "rank": rank})
        cands.sort(key=lambda c: (c["dist"] > 3, c["dist"], c["rank"]))
        out[str(i)] = orig + cands[:10]
        time.sleep(1.2)
    out["_v"] = LOCS_VERSION
    return out


def post_one(page, app, job):
    P = app.posts[job.i]
    files = [str(REC / f) for f in P["upload_files"]]
    if job.opts.get("order"):
        files = [files[k] if isinstance(k, int) else str(added_file(k)) for k in job.opts["order"]]
        job.step(order_text(job.opts["order"], len(P["upload_files"])))
    missing = [f for f in files if not Path(f).exists()]
    if missing:
        raise PostError(f"Missing file: {missing[0]}")
    if not 1 <= len(files) <= 20:
        raise PostError(f"{len(files)} files; Instagram allows 1-20 per post.")

    job.step("Opening Instagram")
    page.bring_to_front()
    page.goto(IG, wait_until="domcontentloaded")
    for _ in range(15):
        if AF.signed_in_here(page):
            break
        page.wait_for_timeout(1000)
    else:
        raise PostError("Not signed in. Sign in to the NEW account in the helper's Chrome window, then try again.")
    dismiss_popups(page)

    job.step("Opening Create")
    page.locator("svg[aria-label='New post']").first.click()
    fi = page.locator("div[role=dialog] input[type=file]")
    for _ in range(15):
        if fi.count():
            break
        sub = page.locator("svg[aria-label='Post']")  # some layouts show a Post / Live submenu first
        if sub.count():
            sub.first.click()
        page.wait_for_timeout(1000)
    fi.first.set_input_files(files)
    job.step(f"Loading {len(files)} file{'s' if len(files) > 1 else ''}")
    page.get_by_role("heading", name="Crop", exact=True).first.wait_for(timeout=180_000)
    ok = dialog(page).get_by_role("button", name="OK", exact=True)  # "video posts are now reels" notice
    if ok.count():
        ok.first.click()
        page.wait_for_timeout(800)

    page.locator("svg[aria-label='Select crop']").first.click()
    page.wait_for_timeout(600)
    dialog(page).get_by_role("button", name=re.compile(r"^\s*Original\b")).first.click()  # name includes an icon label
    page.wait_for_timeout(600)
    job.step("Crop: Original")
    click_next(page)
    for _ in range(90):  # Crop -> Edit -> details
        page.wait_for_timeout(1000)
        if page.locator("div[role=dialog] div[contenteditable=true]").count():
            break
        if page.get_by_role("heading", name="Edit", exact=True).count():
            page.wait_for_timeout(800)
            click_next(page)
            page.wait_for_timeout(1500)
    else:
        raise PostError("The caption screen didn't appear.")
    page.wait_for_timeout(1500)

    alt = dialog(page).locator("img[alt$=\"'s profile picture\"]")
    if alt.count():
        app.account = (alt.first.get_attribute("alt") or "").replace("'s profile picture", "") or app.account

    # caption
    text = (P.get("caption") or "").replace("\r\n", "\n").strip()
    if job.opts["add_date"]:
        text = (text + "\n\n" if text else "") + "\U0001F4C5 Originally posted " + nice_date(P["date"])
    text = text[:2200]
    if text:
        box = caption_box(page)
        box.click()
        lines = text.split("\n")
        for k, line in enumerate(lines):
            if line:
                page.keyboard.insert_text(line)
            if k < len(lines) - 1:
                page.keyboard.press("Shift+Enter")
        page.wait_for_timeout(700)
        if norm(box.inner_text()) == norm(text):
            job.step("Caption \u2713")
        else:
            job.opts["review"] = True
            job.step("Caption came out different from the original; stopping before Share so you can check it")
    else:
        job.step("No caption")

    # tag people (on the first photo)
    if job.opts["tags"] and P.get("tagged"):
        media = dialog(page).locator("button[aria-label='Tag people']").first
        box = media.bounding_box()
        done = []
        for n, u in enumerate(P["tagged"][:20]):
            fx, fy = TAG_SPOTS[n % len(TAG_SPOTS)]
            page.mouse.click(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy)
            inp = page.locator("input[name='userSearchInput']").first
            try:
                inp.wait_for(state="visible", timeout=5000)
            except Exception:
                job.step(f"Couldn't open tag search for @{u}")
                continue
            inp.fill(u)
            hit = page.locator("button").filter(has=page.get_by_text(u, exact=True))
            try:
                hit.first.wait_for(state="visible", timeout=8000)
                hit.first.click()
                inp.wait_for(state="hidden", timeout=5000)
                done.append(u)
            except Exception:
                close_popover(page)
                job.step(f"@{u} not found (renamed or deleted?), not tagged")
            page.wait_for_timeout(500)
        job.step(f"Tagged {len(done)}/{len(P['tagged'])}" + (": " + ", ".join("@" + u for u in done) if done else ""))

    # location (a problem here never stops the post: it just goes up without one)
    name = job.opts["loc"].strip()
    if name:
        try:
            set_location(page, app, job, name)
        except Exception as e:  # noqa: BLE001
            job.step(f"Location '{name}' couldn't be set ({type(e).__name__}); posting without it")
            try:
                li = page.locator("input[name='creation-location-input']").first
                if not li.is_disabled(timeout=2000):
                    li.fill("", timeout=3000)
                caption_box(page).click(timeout=3000)
            except Exception:
                pass

    # share
    share_flow(page, app, job)


def set_location(page, app, job, name):
    if True:
        li = page.locator("input[name='creation-location-input']").first
        li.click()
        li.fill(name)
        want_addr = next((c.get("addr") or "" for c in app.locations.get(str(job.i), [])
                          if isinstance(c, dict) and c.get("name") == name), "")
        pick, where, prev, target = None, "", None, squash(name)
        for t in range(24):
            page.wait_for_timeout(500)
            if dialog(page).count() < 2:  # the results list is its own dialog layer
                prev = None
                continue
            btns = dialog(page).last.locator("button")
            full = btns.all_inner_texts()[:25]  # one atomic read: the list re-renders while results arrive
            texts = [f.split("\n")[0].strip() for f in full]
            subs = [(f.split("\n") + [""])[1].strip() for f in full]  # address line
            if not texts or texts != prev:  # wait until the result list stops changing
                prev = texts
                continue
            idx = None
            if want_addr:  # same name and same address = the original venue
                idx = next((k for k, s in enumerate(texts) if squash(s) == target and subs[k]
                            and (squash(subs[k]) in squash(want_addr) or squash(want_addr) in squash(subs[k]))),
                           None)
            if idx is None:
                idx = next((k for k, s in enumerate(texts) if s.lower() == name.lower()), None)
            if idx is None:
                idx = next((k for k, s in enumerate(texts) if squash(s) == target), None)
            if idx is None and t >= 6:
                # text search doesn't always list the exact venue: take the longest overlapping name
                near = [(len(squash(s)), -k) for k, s in enumerate(texts)
                        if len(squash(s)) >= 5 and (squash(s) in target or target in squash(s))]
                idx = -max(near)[1] if near else None
            if idx is None and t >= 10:
                break
            if idx is not None:
                btns.nth(idx).click(timeout=5000)
                page.wait_for_timeout(800)
                if li.is_disabled():  # Instagram locks the field once a place is really chosen
                    pick = li.input_value()
                    where = subs[idx]
                break
        caption_box(page).click()  # closes the results list
        page.wait_for_timeout(600)
        if pick:
            job.step(f"Location: {pick}" + (f" ({where})" if where else "") + " \u2713"
                     + ("" if squash(pick) == target else f" (closest match to '{name}')"))
        else:
            if not li.is_disabled():
                li.fill("")
                caption_box(page).click()
            job.step(f"Location '{name}' couldn't be selected in Instagram's search, left empty")
        page.wait_for_timeout(400)


def share_flow(page, app, job):
    now = lambda: datetime.now().isoformat(timespec="seconds")  # noqa: E731
    shared_at = None
    if job.opts["review"]:
        job.state = "review"
        job.step("Ready for you: check it in the Chrome window and press Share there (or Cancel in the hub)")
        page.bring_to_front()
    else:
        share = dialog(page).get_by_role("button", name="Share", exact=True).first
        share.wait_for(state="visible", timeout=15000)  # if this fails, nothing was shared
        app.store.set_post(job.i, state="shared_unconfirmed", at=now())
        share.click()
        shared_at = time.time()
        job.step("Sharing... (big carousels with videos can take a few minutes)")

    t0, streak = time.time(), 0
    while True:
        page.wait_for_timeout(1000)
        st, txt = stage(page)
        if st == "done":
            break
        if shared_at is None:  # review: waiting for you in Chrome
            if job.cancel:
                discard(page)
                job.state = "cancelled"
                job.step("Cancelled: draft discarded, nothing posted")
                return
            if st == "closed":  # X -> Discard closes everything at once
                job.state = "cancelled"
                job.step("Closed in Chrome: nothing posted")
                return
            streak = streak + 1 if st == "sharing" else 0  # two readings in a row, so a screen change isn't mistaken for Share
            if streak >= 2:
                shared_at = time.time()
                app.store.set_post(job.i, state="shared_unconfirmed", at=now())
                job.state = "running"
                job.step("You pressed Share. Sharing... (big carousels with videos can take a few minutes)")
            elif time.time() - t0 > 1800:
                discard(page)
                job.state = "cancelled"
                job.step("Waited 30 min; draft discarded, nothing posted")
                return
        else:
            m = FAIL_RE.search(txt)
            if m:
                raise PostError(f"Instagram said: '{m.group(0)}'. Check your profile before trying again.")
            if st == "closed":
                raise PostError("The window closed before Instagram confirmed. Check your profile before trying again.")
            if st in ("compose", "back") and job.opts["review"]:  # it wasn't Share: you went back to edit
                shared_at, streak = None, 0
                app.store.clear_post(job.i)
                job.state = "review"
                job.step("Back in the editor: press Share in Chrome when you're ready")
            elif time.time() - shared_at > 1200:
                raise PostError("No 'post shared' confirmation after 20 min. Check your profile before trying again.")
    app.store.set_post(job.i, state="posted", at=now())
    app.store.bump()
    job.step("Posted \u2713")

    # find the new post's link (best effort), then archive if asked
    try:
        close = page.locator("svg[aria-label='Close']")
        if close.count():
            close.first.click()
        else:
            page.keyboard.press("Escape")
        page.wait_for_timeout(1000)
        if app.account:
            page.goto(IG + app.account + "/", wait_until="domcontentloaded")
            link = page.locator("main a[href*='/p/'], main a[href*='/reel/']").first
            link.wait_for(timeout=15000)
            href = link.get_attribute("href") or ""
            job.url = "https://www.instagram.com" + re.sub(r"^/[^/]+(?=/(p|reel)/)", "", href)
            app.store.set_post(job.i, url=job.url)
    except Exception:
        pass
    if job.opts["archive"]:
        archived = False
        try:
            if job.url:
                page.goto(job.url, wait_until="domcontentloaded")
                page.locator("svg[aria-label='More options']").first.click(timeout=15000)
                page.wait_for_timeout(1500)
                a = dialog(page).get_by_role("button", name="Archive", exact=True)
                if a.count():
                    a.first.click()
                    page.wait_for_timeout(2000)
                    archived = True
                else:
                    page.keyboard.press("Escape")
        except Exception:
            pass
        if archived:
            app.store.set_post(job.i, archived=True)
            job.step("Archived \u2713")
        else:
            job.step("Archive it in the phone app (open the post, \u22ef, Archive): the website has no Archive option")
    job.state = "done"


def simulate_one(app, job):
    P = app.posts[job.i]
    n = len(job.opts.get("order") or P["upload_files"])
    if job.opts.get("order"):
        job.step(order_text(job.opts["order"], len(P["upload_files"])))
    for s in ("Opening Instagram", "Opening Create", f"Loading {n} file{'s' if n > 1 else ''}", "Crop: Original",
              "Caption \u2713" if P.get("caption") else "No caption"):
        time.sleep(0.4)
        job.step(s)
    if job.opts["tags"] and P.get("tagged"):
        job.step(f"Tagged {len(P['tagged'])}/{len(P['tagged'])}")
    if job.opts["loc"]:
        job.step(f"Location: {job.opts['loc']} \u2713")
    if job.opts["review"]:
        job.state = "review"
        job.step("Ready for you: check it in the Chrome window and press Share there (or Cancel in the hub)")
        for _ in range(60):
            time.sleep(0.25)
            if job.cancel:
                job.state = "cancelled"
                job.step("Cancelled: draft discarded, nothing posted")
                return
        job.state = "running"
    job.step("Sharing...")
    time.sleep(0.8)
    job.url = "https://www.instagram.com/p/SIMULATED/"
    app.store.set_post(job.i, state="posted", at=datetime.now().isoformat(timespec="seconds"), url=job.url)
    app.store.bump()
    job.step("Posted \u2713 (simulated)")
    if job.opts["archive"]:
        job.step("Archived \u2713 (simulated)")
    job.state = "done"


class Server(ThreadingHTTPServer):
    """Refuses to share the port, so a second helper says 'already running' instead of starting
    (on Windows, SO_REUSEADDR would let two helpers listen on 8767 at once)."""
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


# ------------------------------------------------------------------ main loop
AF = None


def wait_signed_in(page, app, timeout=600):
    if AF.logged_in(page):
        return
    app.note = "Sign in to your NEW Instagram account in the Chrome window this helper opened."
    log(app.note)
    page.goto(IG + "accounts/login/", wait_until="domcontentloaded")
    deadline = time.time() + timeout
    while time.time() < deadline and not AF.signed_in_here(page):
        page.wait_for_timeout(3000)
    if not AF.signed_in_here(page):
        raise PostError("Timed out waiting for sign-in. Close this window and start the post helper again.")


def run_job(app, job, fn):
    job.state = "running"
    try:
        fn(job)
        if job.state == "running":
            job.state = "done"
    except Exception as e:  # noqa: BLE001 - report every failure to the hub
        shared = app.store.data["posts"].get(str(job.i), {}).get("state") == "shared_unconfirmed"
        msg = str(e) if isinstance(e, PostError) else f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
        job.error = msg + ("" if shared else " Nothing was posted.")
        job.state = "error"
        log(f"#{job.i + 1}: ERROR {job.error}")
        return False
    finally:
        app.last, app.job = job, None
    return True


def serve(args):
    global APP, AF
    posts = json.loads(POSTS.read_text(encoding="utf-8"))
    APP = app = App(args, posts)
    srv = Server(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"Helper listening on http://127.0.0.1:{PORT} (only this computer can reach it)")

    if args.simulate:
        app.locations = {str(i): [{"name": "Simulated place", "addr": "", "dist": 0, "rank": 0},
                                  {"name": "Simulated city", "addr": "", "dist": 1500, "rank": 1}]
                         for i, p in enumerate(posts) if p.get("lat")}
        app.ready, app.account, app.note = True, "simulated", "Simulation: nothing is posted"
        log("SIMULATION: no browser, nothing is posted.")
        try:
            while True:
                try:
                    job = app.q.get(timeout=1)
                except queue.Empty:
                    continue
                run_job(app, job, lambda j: simulate_one(app, j))
        except KeyboardInterrupt:
            return

    from playwright.sync_api import sync_playwright
    import autofollow
    AF = autofollow
    with sync_playwright() as pw:
        ctx = page = None

        def ensure_browser():
            nonlocal ctx, page
            alive = False
            if page is not None and not page.is_closed():
                try:  # the Chrome window may have been closed by hand
                    page.evaluate("1")
                    alive = True
                except Exception:
                    pass
            if not alive:
                if ctx is not None:
                    try:
                        ctx.close()
                    except Exception:
                        pass
                ctx, page = AF.open_browser(pw)
                wait_signed_in(page, app)
            return page

        try:
            ensure_browser()
            if app.locations.get("_v") != LOCS_VERSION:
                app.note = "Looking up place names for your post locations..."
                log(app.note)
                try:
                    app.locations = find_locations(page, posts)
                    LOCS.write_text(json.dumps(app.locations, ensure_ascii=False, indent=1), encoding="utf-8")
                except Exception as e:  # noqa: BLE001
                    log(f"Couldn't look up locations ({e}); you can still type a place name in the hub.")
            app.ready, app.note = True, "Ready"
            log("Ready. Leave this window open and use the Post buttons in the hub (Posts tab). Ctrl+C to stop.")
            while True:
                try:
                    job = app.q.get(timeout=1)
                except queue.Empty:
                    continue
                try:
                    ensure_browser()
                except Exception as e:  # noqa: BLE001
                    job.error, job.state = f"Browser problem: {e}. Nothing was posted.", "error"
                    app.last, app.job = job, None
                    continue
                ok = run_job(app, job, lambda j: post_one(page, app, j))
                if not ok:
                    try:
                        discard(page)
                    except Exception:
                        pass
        except KeyboardInterrupt:
            log("Stopping.")
        except PostError as e:
            app.note = str(e)
            log(f"STOPPED: {e}")
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["serve"])
    ap.add_argument("--cap", type=int, default=6, help="max posts per calendar day (default 6)")
    ap.add_argument("--simulate", action="store_true", help="test the hub buttons: no browser, nothing posted")
    args = ap.parse_args()
    try:
        serve(args)
    except OSError as e:
        if getattr(e, "winerror", None) in (10048, 10013) or "address already in use" in str(e).lower():
            log(f"Port {PORT} is busy: the helper is probably already running in another window.")
            sys.exit(2)
        raise


if __name__ == "__main__":
    main()

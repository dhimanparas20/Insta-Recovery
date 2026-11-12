"""Extract everything recoverable from an Instagram HTML export into JSON/CSV.

Usage: python -I tools/extract.py <export_dir> <out_dir>
Writes <out_dir>/data/*.json and *.csv. Never modifies the export.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import igparse as P  # noqa: E402

EXP = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
DATA = OUT / "data"
DATA.mkdir(parents=True, exist_ok=True)

FF = EXP / "connections/followers_and_following"
ACT = EXP / "your_instagram_activity"

POST_URL = re.compile(r"https?://(?:www\.)?instagram\.com/(?:[^/]+/)?(p|reel|reels|tv)/([A-Za-z0-9_-]+)")
STORY_URL = re.compile(r"instagram\.com/stories/([A-Za-z0-9._]+)/")
PROFILE_URL = re.compile(r"instagram\.com/(?:_u/)?([A-Za-z0-9._]+)/?$")


def parse_dt(s):
    if not s:
        return None
    s = s.strip()
    for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y %H:%M"):
        try:
            return datetime.strptime(s, fmt).isoformat(timespec="minutes")
        except ValueError:
            pass
    return None


def load(rel):
    p = EXP / rel
    return P.load(p) if p.exists() else None


def dump(name, obj):
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def canon_post(url):
    m = POST_URL.search(url or "")
    if not m:
        return None
    kind = "reel" if m.group(1) in ("reel", "reels") else m.group(1)
    return f"https://www.instagram.com/{kind}/{m.group(2)}/"


# ------------------------------------------------------------------ profile
profile = {}
r = load("personal_information/personal_information/personal_information.html")
if r:
    profile = P.table_fields(P.main_node(r))
    pics = P.media_srcs(P.main_node(r))
    profile["profile_photo_file"] = pics[0] if pics else None
r = load("personal_information/personal_information/instagram_profile_information.html")
if r:
    profile["_account_info"] = P.table_fields(P.main_node(r))
dump("profile.json", profile)

# --------------------------------------------------------- name -> username
name_to_users: dict[str, set] = defaultdict(set)


def harvest_names(root):
    for e in P.entries(root):
        for tr_tbl in [e]:
            f = P.table_fields(tr_tbl)
            n, u = f.get("Name"), f.get("Username")
            if n and u:
                name_to_users[n.strip()].add(u.strip())


for rel in [
    "connections/followers_and_following/close_friends.html",
    "connections/followers_and_following/profiles_you've_favorited.html",
    "connections/followers_and_following/blocked_profiles.html",
    "connections/followers_and_following/hide_story_from.html",
    "connections/followers_and_following/pending_follow_requests.html",
    "connections/followers_and_following/recent_follow_requests.html",
    "connections/followers_and_following/follow_requests_you've_received.html",
    "connections/followers_and_following/recently_unfollowed_profiles.html",
    "connections/followers_and_following/restricted_profiles.html",
    "ads_information/ads_and_topics/suggested_profiles_viewed.html",
    "your_instagram_activity/story_interactions/story_likes.html",
    "your_instagram_activity/story_interactions/emoji_story_reactions.html",
    "your_instagram_activity/story_interactions/emoji_sliders.html",
    "your_instagram_activity/story_interactions/story_reaction_sticker_reactions.html",
    "your_instagram_activity/story_interactions/quizzes.html",
    "your_instagram_activity/story_interactions/questions.html",
    "your_instagram_activity/story_interactions/polls.html",
    "personal_information/personal_information/note_and_repost_interactions.html",
    "your_instagram_activity/saved/saved_posts.html",
]:
    rr = load(rel)
    if rr:
        harvest_names(rr)

# ---------------------------------------------------------------- relations


def simple_user_list(rel, title_is_user=False):
    """Return [(username, iso_time)] from a followers/following-style file."""
    rr = load(rel)
    if not rr:
        return []
    res = []
    for e in P.entries(rr):
        f = P.table_fields(e)
        u = f.get("Username")
        if not u and title_is_user:
            u = P.entry_title(e)
        if not u:
            for h in P.links(e):
                m = PROFILE_URL.search(h)
                if m and "instagram.com" in h:
                    u = m.group(1)
                    break
        if not u:
            # followers_1 layout: <a href=instagram.com/x>x</a>
            a = e.find("a")
            u = a.text().strip() if a else None
        t = parse_dt(P.entry_time(e))
        if not t:
            # timestamp lives in the last <div> of _a6-p for followers/following
            divs = [d.text().strip() for d in e.find_all("div")]
            for d in reversed(divs):
                t = parse_dt(d)
                if t:
                    break
        if u:
            res.append((u.strip(), t))
    return res


following = simple_user_list("connections/followers_and_following/following.html", title_is_user=True)
followers = []
for fp in sorted(FF.glob("followers_*.html")):
    followers += simple_user_list(fp.relative_to(EXP).as_posix())
close = simple_user_list("connections/followers_and_following/close_friends.html")
favs = simple_user_list("connections/followers_and_following/profiles_you've_favorited.html")
blocked = simple_user_list("connections/followers_and_following/blocked_profiles.html")
restricted = simple_user_list("connections/followers_and_following/restricted_profiles.html")
hide_story = simple_user_list("connections/followers_and_following/hide_story_from.html")
pending_sent = simple_user_list("connections/followers_and_following/pending_follow_requests.html")
req_received = simple_user_list("connections/followers_and_following/follow_requests_you've_received.html")
unfollowed = simple_user_list("connections/followers_and_following/recently_unfollowed_profiles.html")

# ------------------------------------------------------------ interactions
story_likes = Counter()
rr = load("your_instagram_activity/story_interactions/story_likes.html")
if rr:
    for e in P.entries(rr):
        for h in P.links(e):
            m = STORY_URL.search(h)
            if m:
                story_likes[m.group(1)] += 1
                break
        else:
            u = P.table_fields(e).get("Username")
            if u:
                story_likes[u] += 1

story_other = Counter()
for rel in ["emoji_story_reactions", "emoji_sliders", "story_reaction_sticker_reactions", "quizzes", "questions", "polls", "question_music_response"]:
    rr = load(f"your_instagram_activity/story_interactions/{rel}.html")
    if not rr:
        continue
    for e in P.entries(rr):
        u = P.table_fields(e).get("Username")
        if not u:
            for h in P.links(e):
                m = STORY_URL.search(h)
                if m:
                    u = m.group(1)
                    break
        if u:
            story_other[u] += 1

comments_on = Counter()
for fp in sorted((ACT / "comments").glob("*.html")):
    rr = P.load(fp)
    for e in P.entries(rr):
        f = P.table_fields(e)
        u = f.get("Media Owner")
        if u:
            comments_on[u.strip()] += 1

liked_comments = Counter()
rr = load("your_instagram_activity/likes/liked_comments.html")
if rr:
    for e in P.entries(rr):
        u = P.entry_title(e)
        if u:
            liked_comments[u.strip()] += 1

# -------------------------------------------------------------------- chats
my_name = (profile.get("Name") or "").strip()
chats = []
chat_index = load("your_instagram_activity/messages/chats.html")
for box in ("inbox", "message_requests"):
    base = ACT / "messages" / box
    if not base.exists():
        continue
    for d in sorted(base.iterdir()):
        files = sorted(d.glob("message_*.html"))
        if not files:
            continue
        title, msgs, mine, senders, times = None, 0, 0, Counter(), []
        for fp in files:
            rr = P.load(fp)
            h1 = rr.find("h1")
            title = title or (h1.text().strip() if h1 else d.name)
            for e in P.entries(rr):
                msgs += 1
                s = (P.entry_title(e) or "").strip()
                senders[s] += 1
                if s == my_name:
                    mine += 1
                t = parse_dt(P.entry_time(e))
                if t:
                    times.append(t)
        others = [s for s in senders if s and s != my_name]
        guess = set()
        for s in [title] + others:
            guess |= name_to_users.get(s, set())
        n_photos = len(list((d / "photos").glob("*"))) if (d / "photos").exists() else 0
        n_videos = len(list((d / "videos").glob("*"))) if (d / "videos").exists() else 0
        n_audio = len(list((d / "audio").glob("*"))) if (d / "audio").exists() else 0
        chats.append({
            "box": box,
            "folder": d.relative_to(EXP).as_posix(),
            "file": files[0].relative_to(EXP).as_posix(),
            "title": title,
            "participants": others,
            "is_group": len(others) > 1,
            "messages": msgs,
            "mine": mine,
            "first": min(times) if times else None,
            "last": max(times) if times else None,
            "photos": n_photos,
            "videos": n_videos,
            "audio": n_audio,
            "username_guess": sorted(guess),
        })
chats.sort(key=lambda c: -c["messages"])
dump("chats.json", chats)

dm_by_user = Counter()
dm_last = {}
for c in chats:
    if c["is_group"]:
        continue
    for u in c["username_guess"]:
        dm_by_user[u] += c["messages"]
        if c["last"] and (u not in dm_last or c["last"] > dm_last[u]):
            dm_last[u] = c["last"]

# -------------------------------------------------------------------- people
people: dict[str, dict] = {}


def person(u):
    if u not in people:
        people[u] = {"username": u, "url": f"https://www.instagram.com/{u}/"}
    return people[u]


for u, t in following:
    person(u).update(following=True, following_since=t)
for u, t in followers:
    person(u).update(follower=True, follower_since=t)
for lst, key in [(close, "close_friend"), (favs, "favorite"), (blocked, "blocked"), (restricted, "restricted"),
                 (hide_story, "hide_story"), (pending_sent, "pending_sent"), (req_received, "request_received"),
                 (unfollowed, "recently_unfollowed")]:
    for u, _ in lst:
        person(u)[key] = True

for p in people.values():
    u = p["username"]
    p["mutual"] = bool(p.get("following") and p.get("follower"))
    p["dm_messages"] = dm_by_user.get(u, 0)
    p["dm_last"] = dm_last.get(u)
    p["story_likes"] = story_likes.get(u, 0)
    p["story_replies"] = story_other.get(u, 0)
    p["comments_on_their_posts"] = comments_on.get(u, 0)
    p["liked_their_comments"] = liked_comments.get(u, 0)
    inter = p["dm_messages"] * 2 + p["story_likes"] + p["story_replies"] * 3 + p["comments_on_their_posts"] * 3 + p["liked_their_comments"]
    p["interaction_score"] = inter
    score = inter
    if p.get("close_friend"):
        score += 5000
    if p.get("favorite"):
        score += 3000
    if p["mutual"]:
        score += 1000
    p["priority_score"] = score
    if p.get("blocked") or p.get("restricted"):
        tier = "X - blocked/restricted (do not follow)"
    elif p.get("close_friend") or p.get("favorite"):
        tier = "1 - close friends & favourites"
    elif p["mutual"] and inter >= 20:
        tier = "2 - mutuals you interact with"
    elif p["mutual"]:
        tier = "3 - other mutuals"
    elif p.get("following") and inter >= 10:
        tier = "4 - accounts you follow & engage with"
    elif p.get("following"):
        tier = "5 - other accounts you follow"
    elif p.get("follower"):
        tier = "6 - followed you, you didn't follow back"
    elif p.get("pending_sent"):
        tier = "7 - your unaccepted follow requests"
    else:
        tier = "8 - other"
    p["tier"] = tier

people_list = sorted(people.values(), key=lambda p: (p["tier"], -p["priority_score"], p["username"]))
dump("people.json", people_list)
cols = ["tier", "username", "url", "following", "follower", "mutual", "close_friend", "favorite", "dm_messages", "dm_last",
        "story_likes", "story_replies", "comments_on_their_posts", "liked_their_comments", "priority_score",
        "following_since", "follower_since", "hide_story", "blocked", "restricted", "pending_sent"]
with (DATA / "people.csv").open("w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for p in people_list:
        w.writerow({k: p.get(k, "") for k in cols})

# --------------------------------------------------------------------- posts


def post_entries(rel):
    rr = load(rel)
    if not rr:
        return []
    out = []
    for e in P.entries(rr):
        f = P.table_fields(e)
        cap = f.get("Caption") or P.entry_title(e) or ""
        media = [m for m in P.media_srcs(e) if m.startswith("media/")]
        tagged = []
        for div in e.find_all("div", "_a6-q"):
            txt = div.text()
            if "(Tagged" in txt:
                tagged += [t.split(" (")[0].strip() for t in txt.split("),") if t.strip()]
        lat, lng = f.get("Latitude"), f.get("Longitude")
        out.append({
            "date": parse_dt(P.entry_time(e)),
            "caption": P.clean(cap),
            "media": media,
            "tagged": sorted(set(tagged)),
            "lat": None if lat in (None, "0") else lat,
            "lng": None if lng in (None, "0") else lng,
            "place": f.get("Name") or None,  # original location name (posts.html only)
        })
    return out


# posts_1.html (live) + archived_posts.html carry the true carousel order.
# posts.html adds location/tags but repeats the cover image once, so it is
# used for metadata only (and as a fallback for posts missing elsewhere).
rich = post_entries("your_instagram_activity/media/posts.html")
live = post_entries("your_instagram_activity/media/posts_1.html")
archived = post_entries("your_instagram_activity/media/archived_posts.html")
for p in live:
    p["archived"] = False
for p in archived:
    p["archived"] = True
posts = live + archived
for p in posts:
    meta = next((r for r in rich if set(r["media"]) & set(p["media"])), None)
    if meta:
        p["tagged"] = p["tagged"] or meta["tagged"]
        p["lat"], p["lng"] = p["lat"] or meta["lat"], p["lng"] or meta["lng"]
        p["place"] = p.get("place") or meta["place"]
        if len(meta["caption"]) > len(p["caption"]):
            p["caption"] = meta["caption"]
        p["date"] = p["date"] or meta["date"]
known = {m for p in posts for m in p["media"]}
for r in rich:
    if not set(r["media"]) & known:
        r["archived"] = False
        posts.append(r)
for p in posts:
    p["missing_files"] = [m for m in p["media"] if not (EXP / m).exists()]
posts.sort(key=lambda p: p["date"] or "")
dump("posts.json", posts)

other_content = post_entries("your_instagram_activity/media/other_content.html")
dump("other_content.json", other_content)

# ------------------------------------------------------------------- stories
stories = []
rr = load("your_instagram_activity/media/stories.html")
if rr:
    for e in P.entries(rr):
        srcs = [m for m in P.media_srcs(e) if m.startswith("media/")]
        f = P.table_fields(e)
        stories.append({
            "date": parse_dt(P.entry_time(e)),
            "media": srcs,
            "caption": P.clean(P.entry_title(e) or ""),
            "music": f.get("Musical genre") or None,
        })
stories.sort(key=lambda s: s["date"] or "")
dump("stories.json", stories)

# --------------------------------------------------------------------- saved


def saved_item(e):
    f = P.table_fields(e)
    url = None
    for h in P.links(e):
        url = canon_post(h)
        if url:
            break
    owner = f.get("Username")
    return {"url": url, "owner": owner, "caption": P.clean(f.get("Caption", ""))[:300], "saved": parse_dt(P.entry_time(e))}


saved = []
rr = load("your_instagram_activity/saved/saved_posts.html")
if rr:
    saved = [saved_item(e) for e in P.entries(rr)]

collections = []
rr = load("your_instagram_activity/saved/saved_collections.html")
if rr:
    for e in P.entries(rr):
        f = P.table_fields(e)
        urls = []
        for h in P.links(e):
            c = canon_post(h)
            if c and c not in urls:
                urls.append(c)
        collections.append({"name": f.get("Name"), "type": f.get("Type"), "privacy": f.get("Privacy"),
                            "updated": f.get("Update time"), "items": urls})
saved_urls = {s["url"] for s in saved}
for c in collections:
    c["not_in_all_saved"] = [u for u in c["items"] if u not in saved_urls]
dump("saved.json", {"all_saved": saved, "collections": collections})

music = []
rr = load("your_instagram_activity/saved/saved_music.html")
if rr:
    music = [P.table_fields(e) for e in P.entries(rr)]
dump("saved_music.json", music)

# ---------------------------------------------------------------- summary
summary = {
    "profile": {k: profile.get(k) for k in ("Username", "Name", "Bio", "Website", "Private Account", "Music on profile", "profile_photo_file")},
    "following": len(following),
    "followers": len(followers),
    "mutuals": sum(1 for p in people.values() if p["mutual"]),
    "close_friends": len(close),
    "favorites": len(favs),
    "blocked": len(blocked),
    "restricted": len(restricted),
    "hide_story_from": len(hide_story),
    "pending_sent": len(pending_sent),
    "tiers": dict(sorted(Counter(p["tier"] for p in people.values()).items())),
    "posts": len(posts),
    "posts_archived": sum(1 for p in posts if p["archived"]),
    "post_media_files": sum(len(p["media"]) for p in posts),
    "posts_missing_files": sum(len(p["missing_files"]) for p in posts),
    "stories": len(stories),
    "story_years": dict(sorted(Counter((s["date"] or "????")[:4] for s in stories).items())),
    "saved_posts": len(saved),
    "collections": {c["name"]: len(c["items"]) for c in collections},
    "chat_threads": len(chats),
    "chat_threads_with_username_match": sum(1 for c in chats if c["username_guess"]),
    "chat_messages_total": sum(c["messages"] for c in chats),
    "group_chats": sum(1 for c in chats if c["is_group"]),
}
dump("summary.json", summary)
print(json.dumps(summary, ensure_ascii=False, indent=1))

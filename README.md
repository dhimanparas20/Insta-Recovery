# Instagram Recovery Kit

Lost your Instagram account (disabled, banned or hacked) but have your **data download**? This kit turns that download into a step-by-step plan for rebuilding on a new account, and does the tedious parts for you:

- a **recovery hub**: one local web page with everyone you followed in priority order, all your posts, saved posts, chats, stories and lists
- **one-click reposting**: puts an old post (carousel, caption, tagged people, location) on your new account
- a **follow-back checker**: who hasn't followed your new account back yet
- optional **paced auto-follow**

Everything runs on your own computer, and your export is never uploaded anywhere. The tools that work on your new account talk only to instagram.com, through a Chrome window you sign in to yourself.

> **Not affiliated with Instagram or Meta.** Automating actions on Instagram can break its Terms of Use and get an account restricted. The kit is deliberately slow and stops at the first warning, but you use it at your own risk.

## What can come back

| | |
|---|---|
| **One click per post** | Posts: carousels up to 20 photos/videos, caption, tagged people, location, in the original order |
| **By hand, with the hub's help** | Following (priority list with a daily cap), close friends, hide-story list, blocked accounts, saved posts and collections, profile |
| **From files** | Stories and highlights (sorted by date, ready to repost) |
| **Can't** | DMs (no import exists; read them in the hub), followers (people have to follow you again), original post dates, likes and comments, music on posts (the export has no song names) |

## Before you start: appeal

A recovered account keeps everything; a rebuilt one can't. Appeal first, and use this kit while you wait.

1. **Appeal in the app.** Log in to the disabled account: the notice offers a way to disagree with the decision and says how long you have. Don't miss that window.
2. **Hacked?** Use [instagram.com/hacked](https://www.instagram.com/hacked/) with the email or phone number on the account.
3. **If the appeal fails**, check whether your country offers another route. In India: Meta's Grievance Officer, then the [Grievance Appellate Committee](https://gac.gov.in). In the EU: a certified out-of-court dispute settlement body under the Digital Services Act.

Meta's terms say that if they disable your account you agree not to create another one without their permission, so a new account can be removed too. Go slowly: no bursts of follows or posts.

## Quick start

### 1. Request your data from Instagram

In the Instagram app or on instagram.com: **Accounts Center → Your information and permissions → Download your information**. Menu names change from time to time, but pick:

- **All available information** (or at least followers and following, content, messages and saved items)
- **Download to device**
- Date range **All time**, format **HTML**, media quality **High**

Two things matter for this kit:

- **Format must be HTML**, not JSON.
- **The export must be in English.** Switch Instagram's language to English before you request it.

Instagram emails you when the download is ready (minutes to days). Download every part, and keep a backup copy: it's the only copy of your DMs and stories.

### 2. Install the requirements

- **Python 3.10 or newer**: [python.org/downloads](https://www.python.org/downloads/). On Windows, tick **Add python.exe to PATH** during setup.
- **Google Chrome**: the posting and follower tools drive a real Chrome window. Microsoft Edge works as a fallback.
- **This kit**: *Code → Download ZIP* on GitHub and unzip it, or `git clone` it.

### 3. Build your hub

1. Put the zip file(s) from Instagram into the kit's **`export`** folder. Don't unzip them; the kit does that. An already-unzipped folder works too.
2. **Windows:** double-click **`START_HERE.bat`** (or drag the zips onto it).
   **Mac/Linux:** open a terminal in the kit folder and run `python3 recover.py build`.

The first run creates a private Python environment (`.venv`) and installs three packages (Playwright, Pillow, pillow-heif). It then unzips your export, reads it, prepares upload-ready photos, and opens **`recovery/hub.html`** in your browser. Big exports take several minutes the first time.

Bookmark the hub. Your progress (who you've followed, what you've posted) is saved in that browser; use **Start → Export progress** now and then to back it up.

### 4. Work through the hub

The **Start** tab has a checklist. The other tabs:

| Tab | What it's for |
|---|---|
| **People** | Everyone you followed, in priority order. *Open next profile*, follow on Instagram, then *Followed ✓ & next* (keys **N**, **D**, **S**). It counts against a daily cap. |
| **Posts** | Every post, oldest first, with one-click reposting (below), a ready folder for phone uploads, and *Copy caption*. |
| **Saved** | Your saved posts and collections, as links to re-save. |
| **Chats** | Searchable list of every DM thread; opens the full conversation from your export. |
| **Stories** | All stories by date; star the ones you want for highlights. |
| **Profile & lists** | Old name, bio and website, plus close friends, hide-story and blocked lists to rebuild. |

## One-click reposting

1. Start the post helper and leave its window open. **Windows:** double-click **`post_helper.bat`**. **Mac/Linux:** run `python3 recover.py post`.
2. A Chrome window opens. The first time, **sign in to your new account there yourself**. The kit never sees or stores your password; the session stays in `recovery/.browser-profile/`.
3. In the hub, open **Posts**. The chip at the top turns green: *connected*.
4. On a post's card, check:
   - **Location.** Pre-picked from your original location (★) where the export has it. Change it, type your own, or choose *No location*.
   - **Tag people** and, for posts you had archived, **Archive after posting**.
   - **Photo order.** Drag photos, or use ◀ ▶. ✕ leaves one out and *reset* restores the original.
   - **Add photos.** *+ from computer* (JPG, PNG, HEIC, WEBP, MP4, MOV) or *+ from stories*. Up to 20 per post.
5. Click **Post to new account**, or **Post next (oldest first)**. The helper uploads every photo and video in order with the Original crop, types the caption, tags people on the first photo, sets the location and shares. Progress shows live on the card.

**Options and safety**

- **Stop before Share** is on until your first post goes up. With it on, the helper leaves the post ready in Chrome so you can check it and press Share yourself. You can go back (←) to edit; it waits until you really press Share.
- **Add "Originally posted <date>"** appends the old date to the caption, since Instagram can't backdate posts.
- **Daily limit: 6 posts.** Change it with `post_helper.bat --cap 4` or `python3 recover.py post --cap 4`. When you hit it, the card offers *Post anyway*; confirm once and the limit is off for the rest of that day.
- A post that was shared (or may have been) is never posted twice unless you click *post again*.
- The helper stops on any Instagram error or restriction notice.

**Not possible:** adding music (instagram.com can't add music to posts, and the export has no song names; add one in the app afterwards), original dates, likes and comments. Archiving uses the website's ⋯ menu when it offers *Archive*; otherwise the card tells you to archive in the app.

**Prefer your phone?** Every post also has a ready folder in `recovery/upload_ready/posts/` with numbered photos, `caption.txt` and `info.txt` (date, tags, location).

## Follow-back checker

**Windows:** double-click **`check_followers.bat`**. **Mac/Linux:** run `python3 recover.py followers`.

It reads your new account's followers and following (read-only), saves a dated scan, and opens `recovery/followcheck/report.html` with these views:

- don't follow you back, you don't follow back, and mutuals (with search, sort, *Ignore* and CSV export)
- changes since the last scan: new followers, unfollows
- **Old account:** people you re-followed who haven't followed back yet (worth a DM), and old follows you haven't re-followed
- scan history

To mark everyone you already follow as done in the hub, use **Start → Import progress** with `recovery/followcheck/hub_progress_from_scan.json`.

If the live scan stops working, request a "Download your information" export of the **new** account (followers and following only) and run `python3 recover.py followers import <path-to-unzipped-export>`.

## Auto-follow (optional)

Following by hand from the **People** tab is the safer route. If you want it automated anyway:

1. Check that it can read profiles: `python3 recover.py follow run --dry-run` visits 5 profiles without clicking. Each should say `would_follow` or `already`.
2. **Windows:** double-click **`auto_follow.bat`**. **Mac/Linux:** run `python3 recover.py follow`. Run it once a day.

It follows up to 25 people a day in priority order, 45–150 s apart, with a 5–10 minute break every 8–12 follows. It stops by itself on "Try again later" or restriction notices, security checks, sign-outs, or 3 failures in a row. After a block, wait at least 48 hours and lower the cap.

Options:

- `--cap 15`: the daily cap
- `--tiers 1`: close friends only
- `--min-delay` / `--max-delay`: seconds between follows
- `python3 recover.py follow status`: progress so far

Import `recovery/autofollow_progress_for_hub.json` in the hub to see the progress there.

### Priority tiers

1. Close friends and favourites
2. Mutuals you actually interact with: DMs, story likes, comments
3. Other mutuals
4. Accounts you follow and engage with
5. Other accounts you follow
6. Followed you, you didn't follow back (optional)
7. Your follow requests that were never accepted (optional)

Blocked and restricted accounts are never suggested.

## Privacy and security

- **Nothing is uploaded.** The kit reads your export and writes only into `recovery/`. There are no servers and no analytics. Network traffic is limited to pip installing packages at setup, and to Chrome talking to instagram.com while you post, check followers or auto-follow. (The post helper also asks Instagram for place names near your old posts' coordinates, so it can pre-pick locations.)
- **Your password stays with you.** You sign in yourself in the Chrome window. Treat `recovery/.browser-profile/` like a password: it holds the signed-in session.
- **The post helper only listens on 127.0.0.1:8767** (your own computer). It only accepts requests carrying a random token that's built into your hub (`recovery/data/.post_token`), so other websites can't drive it.
- **`export/` and `recovery/` are your personal data.** `.gitignore` keeps them out of git. If you fork or share this folder, never upload those two.
- **The export is treated as untrusted input.** Tools run Python in isolated mode (`-I`), and zips are unzipped safely: nothing is written outside the `export` folder.

## Troubleshooting

| Message or problem | What to do |
|---|---|
| *This export is in JSON format* | Request the download again with format **HTML**, if you still can. |
| *No followers, following or posts were recognised* | The export isn't in English. Switch Instagram's language to English (US), request it again, and rebuild. |
| *No Instagram export found* | Put the zip(s) in `export/`, or run `python3 recover.py build <path-to-zip-or-folder>`. |
| Hub says *helper not running* | Start `post_helper.bat` (or `python3 recover.py post`) and keep its window open. Use Chrome or Edge for the hub. |
| *Port 8767 is busy* | The helper is already running in another window. |
| *Couldn't start a browser* | Install Google Chrome, or run `.venv/bin/python -m playwright install chromium` (on Windows, `.venv\Scripts\python`). |
| Photos missing in the hub | The export folder moved after building. Put it back, or build again. |
| A tool stops with a page or layout error | Instagram changed its website. The tools stop rather than guess. Open an issue with the message (no personal data). |

**Rebuilding is safe.** Run `START_HERE.bat` again whenever you like: existing photos are skipped, your hub progress stays in the browser, and your posting history stays in `recovery/data/autopost_state.json`.

Tested on Windows 11 with Chrome. Mac and Linux run the same Python code but have had less testing.

## Folder layout

```
START_HERE.bat        build the hub from your export (Windows)
post_helper.bat       one-click posting helper
check_followers.bat   follow-back checker
auto_follow.bat       optional auto-follow
recover.py            the same commands on any OS: build, post, followers, follow, setup
requirements.txt
tools/                the scripts and page templates
export/               your Instagram zip(s)            (personal, git-ignored)
recovery/             created by the build             (personal, git-ignored)
  hub.html              the recovery hub
  data/                 everything extracted, as JSON; people.csv opens in Excel
  upload_ready/posts/   one folder per post: numbered photos, caption.txt, info.txt
  stories_by_date/      all stories, renamed by date
  followcheck/          follow-back scans and report
```

## Contributing

Contributions are welcome, especially:

- support for JSON exports
- exports in other languages
- fixes when Instagram changes its pages

Never put real export data, usernames or screenshots of other people's accounts in issues or pull requests.

## License

[MIT](LICENSE)

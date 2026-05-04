# YouTube auto-upload

`scripts/upload.py` ships your rendered `main.mp4` to YouTube via the Data API v3 with title, description, tags, privacy, optional scheduled publish, and optional thumbnail set in one shot. After the one-time OAuth setup, every subsequent run is fully unattended.

## One-time setup (~10 minutes)

You need a Google Cloud project with the YouTube Data API enabled and an OAuth 2.0 **Desktop** client. Free for personal use under the standard quota (1600 units/day; one upload costs ~1600 units, so the practical limit is ~6 uploads/day per project unless you request more).

### 1. Create a Google Cloud project

1. Go to https://console.cloud.google.com/projectcreate
2. Name the project (e.g. `vedit-uploader`)
3. Click **Create**

### 2. Enable the YouTube Data API v3

1. From the project, go to https://console.cloud.google.com/apis/library/youtube.googleapis.com
2. Click **Enable**

### 3. Configure the OAuth consent screen

1. https://console.cloud.google.com/apis/credentials/consent
2. User type: **External** (unless you have a Workspace account, then Internal)
3. Fill in app name, support email, developer email (everything else can be skipped)
4. Add the scope `https://www.googleapis.com/auth/youtube.upload` and `https://www.googleapis.com/auth/youtube`
5. Add yourself as a **test user** — this lets you upload while the app is in "testing" status without going through Google's verification process
6. Save

While the app is in testing, the access token expires every 7 days. To make it permanent, submit for verification (free, takes a few days). For personal use, just re-auth weekly when the token dies.

### 4. Create OAuth credentials

1. https://console.cloud.google.com/apis/credentials
2. **Create Credentials** → **OAuth client ID**
3. Application type: **Desktop app**
4. Name it whatever
5. **Download JSON** → save the downloaded file as:

   ```
   ~/.cache/vedit-youtube/client_secrets.json
   ```

   The script auto-creates the directory the first time you run it.

### 5. First run — authorize in browser

```bash
uv run --quiet .claude/skills/youtube-edit/scripts/upload.py <workdir>
```

The first run opens your default browser to a Google consent page. Sign in, click through (you'll see "Google hasn't verified this app" — click Continue / Advanced → Go to project name (unsafe) since you're the developer), grant the YouTube scope.

The script captures the auth code on a localhost callback, exchanges it for a refresh token, and saves it to `~/.cache/vedit-youtube/token.json` (chmod 600). Every subsequent run uses the cached token.

If the token expires (7-day testing limit), delete `token.json` and re-run.

## Per-upload metadata

In your workdir, write `upload.json`:

```json
{
  "title":       "Best of Episode 42 — chess wipeouts",
  "description": "Compilation of the best wipeouts from Episode 42 of the show.\n\nFull episode: https://youtube.com/...\n\nTimestamps:\n00:00 Intro\n00:42 The bet\n01:30 The wipeout\n",
  "tags":        ["chess", "compilation", "best of", "shorts"],
  "category":    "20",
  "privacy":     "unlisted",
  "file":        "out/main.mp4",
  "thumbnail":   "out/thumbnails/01_whos_ass_at_this_game.jpg",
  "made_for_kids": false
}
```

Field reference:

| Key | Required | Notes |
|---|---|---|
| `title` | yes | Up to 100 chars; trimmed by the script. |
| `description` | no | Up to 5000 chars. Newlines as `\n`. Timestamps in the description auto-link if the video has chapters. |
| `tags` | no | List of strings. |
| `category` | no | Default `22` (People & Blogs). Common: `20` Gaming, `23` Comedy, `24` Entertainment, `27` Education, `26` Howto, `10` Music. |
| `privacy` | no | `private` (default) / `unlisted` / `public`. |
| `file` | no | Path relative to workdir; defaults to `out/main.mp4`. |
| `thumbnail` | no | Path to a JPEG thumbnail. The script calls `youtube.thumbnails().set()` after upload. Custom thumbnails require a verified channel. |
| `made_for_kids` | no | Defaults to `false`. |
| `publish_at` | no | ISO 8601 string for scheduled publish, e.g. `"2026-05-04T18:00:00Z"`. Forces `privacy: private` until the time arrives. |

## Run

```bash
uv run --quiet .claude/skills/youtube-edit/scripts/upload.py <workdir>
```

Reads `<workdir>/upload.json`, uploads `<workdir>/out/main.mp4` (or whatever `file` points at) with progress prints, sets the custom thumbnail if present, writes `<workdir>/upload.result.json` with the resulting URL.

## Combine with watch.sh

For a fully autonomous pipeline — drop a URL into a queue file, get a YouTube link back — run `watch.sh` with `AUTO_RENDER=1` AND extend it to call `upload.py` after assemble. Or simpler: write a wrapper `pipeline.sh URL` that does:

```bash
quickstart.sh "$URL"
mv "$WORKDIR/auto_edl.json" "$WORKDIR/edl.json"
assemble.py "$WORKDIR"
upload.py "$WORKDIR"   # if upload.json exists
```

## Quota and rate limits

Each upload costs ~1600 units (out of the default 10,000-units/day quota). If you regularly upload more than 6 videos/day, request a quota increase via the Cloud Console (free, takes a few days).

Bulk uploads beyond ~50/day will run into other limits even at higher quotas — for a daily-shorts pipeline, batch into the same playlist and use `category: "22"` to avoid triggering category-spam heuristics.

## Privacy first

The default `privacy: private` is intentional. **Always upload private first**, review on YouTube Studio, then flip to `unlisted` or `public` once you're sure the title / thumbnail / description / video are right. The script does not include a "force public" override.

## Troubleshooting

**`Missing OAuth client secrets at ~/.cache/vedit-youtube/client_secrets.json`** — Step 4 above wasn't completed. Download the JSON from the Cloud Console and save it at that path.

**`Token has been expired or revoked`** — re-auth: `rm ~/.cache/vedit-youtube/token.json` then re-run. In testing mode tokens expire every 7 days.

**`The user is not authorized to access this resource`** — your test-user list (Step 3) doesn't include the Google account you're signing in with. Add it.

**`quotaExceeded`** — you hit the 10K-units/day limit. Wait until UTC midnight or request more quota.

**`Custom thumbnails require a verified channel`** — phone-verify your channel at https://www.youtube.com/verify. The upload itself still succeeds; only the thumbnail fails.

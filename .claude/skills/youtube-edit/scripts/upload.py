#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "google-api-python-client>=2.0",
#     "google-auth-oauthlib>=1.2",
#     "google-auth-httplib2>=0.2",
# ]
# ///
"""Upload <workdir>/out/main.mp4 (or another file) to YouTube via the
Data API v3. OAuth installed-app flow — opens a browser the first
time, caches the refresh token under ~/.cache/vedit-youtube/.

See UPLOAD.md for the OAuth client setup (one-time, free).

Reads <workdir>/upload.json:

    {
      "title":         "Best of Episode 42",
      "description":   "Long-form description with timestamps...",
      "tags":          ["podcast", "interview", "comedy"],
      "category":      "22",            # see categoryId list
      "privacy":       "private",        # private | unlisted | public
      "file":          "out/main.mp4",   # path relative to workdir
      "thumbnail":     "out/thumbnails/01_foo.jpg",  # optional
      "made_for_kids": false,
      "publish_at":    "2026-05-04T18:00:00Z"  # optional ISO 8601 — schedules
    }

Useful YouTube category IDs:
    1=Film, 2=Autos, 10=Music, 15=Pets, 17=Sports, 19=Travel, 20=Gaming,
    22=People & Blogs, 23=Comedy, 24=Entertainment, 25=News, 26=Howto,
    27=Education, 28=Science, 29=Nonprofits.

Usage:
    uv run --quiet upload.py <workdir>

The first run prompts you to authorize in a browser; subsequent runs
use the cached refresh token.
"""
import json
import pathlib
import pickle
import sys

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",  # for thumbnail set
]
CACHE_DIR = pathlib.Path.home() / ".cache" / "vedit-youtube"
TOKEN_FILE = CACHE_DIR / "token.json"
SECRETS_FILE = CACHE_DIR / "client_secrets.json"


def get_credentials():
    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(
                str(TOKEN_FILE), SCOPES
            )
        except Exception as e:
            print("[upload] cached token unreadable: {}".format(e))
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            print("[upload] refresh failed ({}). Reauthenticating.".format(e))
            creds = None

    if not creds or not creds.valid:
        if not SECRETS_FILE.exists():
            sys.exit(
                "Missing OAuth client secrets at {}.\n"
                "See UPLOAD.md for the one-time setup at console.cloud.google.com.\n"
                "Drop the downloaded JSON there as 'client_secrets.json'.".format(
                    SECRETS_FILE
                )
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(SECRETS_FILE), SCOPES
        )
        # run_local_server opens the system browser to the consent page,
        # then captures the redirect on a localhost callback.
        creds = flow.run_local_server(port=0, open_browser=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(creds.to_json())
        # Token files contain refresh tokens; restrict permissions.
        try:
            TOKEN_FILE.chmod(0o600)
        except Exception:
            pass

    return creds


def upload(workdir):
    spec_path = workdir / "upload.json"
    if not spec_path.exists():
        sys.exit("missing " + str(spec_path))
    spec = json.loads(spec_path.read_text())

    file_rel = spec.get("file", "out/main.mp4")
    file_path = workdir / file_rel
    if not file_path.exists():
        sys.exit("video file not found: " + str(file_path))

    if "title" not in spec:
        sys.exit("upload.json: 'title' is required")

    privacy = spec.get("privacy", "private")
    if privacy not in ("private", "unlisted", "public"):
        sys.exit("upload.json: privacy must be private | unlisted | public")

    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": spec["title"][:100],  # YT max title length
            "description": spec.get("description", "")[:5000],
            "tags": spec.get("tags") or [],
            "categoryId": str(spec.get("category", "22")),
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": bool(spec.get("made_for_kids", False)),
            "embeddable": True,
        },
    }
    publish_at = spec.get("publish_at")
    if publish_at:
        # Scheduled publish requires privacyStatus="private" until time arrives.
        body["status"]["publishAt"] = publish_at
        body["status"]["privacyStatus"] = "private"

    media = MediaFileUpload(
        str(file_path),
        chunksize=8 * 1024 * 1024,  # 8MB chunks
        resumable=True,
        mimetype="video/mp4",
    )

    print("[upload] starting upload: {} ({:.1f} MB)".format(
        file_path.name, file_path.stat().st_size / 1_000_000,
    ))
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    last_pct = -1
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                if pct != last_pct:
                    print("[upload]   {}%".format(pct))
                    last_pct = pct
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504):
                # YouTube transient errors — the resumable client will retry
                print("[upload] transient error {} — retrying".format(
                    e.resp.status
                ))
                continue
            sys.exit("[upload] HTTP {}: {}".format(e.resp.status, e))

    video_id = response["id"]
    url = "https://www.youtube.com/watch?v={}".format(video_id)
    print("[upload] DONE → {}".format(url))

    # Optional: set a custom thumbnail.
    thumbnail = spec.get("thumbnail")
    if thumbnail:
        thumb_path = workdir / thumbnail
        if not thumb_path.exists():
            print("[upload] thumbnail not found at {} — skipping".format(thumb_path))
        else:
            try:
                youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(
                        str(thumb_path), mimetype="image/jpeg"
                    ),
                ).execute()
                print("[upload] thumbnail set: {}".format(thumb_path.name))
            except HttpError as e:
                print("[upload] thumbnail set failed: {}".format(e))

    # Stamp a sidecar with the resulting URL so re-runs / cron can detect.
    (workdir / "upload.result.json").write_text(
        json.dumps(
            {"video_id": video_id, "url": url, "title": spec["title"]},
            indent=2,
        )
    )
    return url


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: upload.py <workdir>")
    workdir = pathlib.Path(sys.argv[1])
    upload(workdir)


if __name__ == "__main__":
    main()

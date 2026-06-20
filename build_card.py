"""
Run this once to create the MYO trivia card.
This will add the streaming track URLs to the card. 
"""
import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

YOTO_ACCESS_TOKEN = os.getenv("YOTO_ACCESS_TOKEN")
SERVER_URL        = os.getenv("SERVER_URL")
CARD_TITLE        = "True or False Trivia!"
CONTENT_API       = "https://api.yotoplay.com/content"

COVER_FILE    = "cover.png"
UPLOAD_URL    = "https://api.yotoplay.com/media/coverImage/user/me/upload?autoconvert=true"

ICON_WELCOME  = "yoto:#XrJzTakzh3TnCyTHxdzwe57iNjQNjpNKmjQXoVShTYQ"  # Headphone frog
ICON_QUESTION = "yoto:#gTMbacpoeSMYqc9fNLJnxPjylraNG6jIrYEWevyzYbA"  # Book
ICON_TRUE     = "yoto:#juZwjkVNrvSWs30zvV4Wpbg4YCZIXLV5cmms-tgx_Fs"  # Pencil + paper
ICON_FALSE    = "yoto:#j6YrT0YDwgvFv9WoTe3DlNiq9ubSjWHNT566LRaEUIA"  # Pencil
ICON_SCORE    = "yoto:#Iy-nPpe_apDwYTUX1UzG1rTS8pM8Kgp0_tX4z7jrZM8"  # Trophy


def upload_cover():
    if not os.path.exists(COVER_FILE):
        print(f"Warning: {COVER_FILE} not found, skipping cover image.")
        return None
    print(f"Uploading cover image...")
    with open(COVER_FILE, "rb") as f:
        image_data = f.read()
    resp = requests.post(
        UPLOAD_URL,
        headers={"Authorization": f"Bearer {YOTO_ACCESS_TOKEN}", "Content-Type": "image/png"},
        data=image_data,
    )
    if not resp.ok:
        print(f"Warning: cover upload failed: {resp.status_code} {resp.text}")
        return None
    media_url = resp.json()["coverImage"]["mediaUrl"]
    print(f"Cover uploaded!")
    return media_url


def headers():
    return {
        "Authorization": f"Bearer {YOTO_ACCESS_TOKEN}",
        "Content-Type":  "application/json",
    }


def stream_track(key, title, endpoint, label, icon):
    base = SERVER_URL.rstrip("/")
    return {
        "key":          key,
        "title":        title,
        "trackUrl":     f"{base}/{endpoint}",
        "type":         "stream",
        "format":       "mp3",
        "overlayLabel": label,
        "display":      {"icon16x16": icon},
    }


def build_structure(cover_url=None):
    meta = {
        "title":       CARD_TITLE,
        "description": "A live true-or-false trivia game with random questions every time!",
    }
    if cover_url:
        meta["cover"] = {"imageL": cover_url}

    return {
        "title": CARD_TITLE,
        "content": {
            "chapters": [
                {
                    "key":   "01",
                    "title": "Welcome",
                    "display": {"icon16x16": ICON_WELCOME},
                    "tracks": [stream_track("01", "Welcome", "welcome", "W", ICON_WELCOME)],
                },
                {
                    "key":   "02",
                    "title": "Game",
                    "display": {"icon16x16": ICON_QUESTION},
                    "tracks": [
                        stream_track("01", "False",    "false",    "F", ICON_FALSE),
                        stream_track("02", "Question", "question", "?", ICON_QUESTION),
                        stream_track("03", "True",     "true",     "T", ICON_TRUE),
                    ],
                },
                {
                    "key":   "03",
                    "title": "Score",
                    "display": {"icon16x16": ICON_SCORE},
                    "tracks": [stream_track("01", "Final Score", "score", "S", ICON_SCORE)],
                },
            ]
        },
        "metadata": meta,
    }


def main():
    print("Yoto Trivia - Card Builder")
    print(f"Server: {SERVER_URL}")
    print("=" * 40)

    cover_url = upload_cover()
    body = build_structure(cover_url)
    card_id = sys.argv[1] if len(sys.argv) > 1 else None
    if card_id:
        body["cardId"] = card_id
        print(f"Updating existing card: {card_id}")
    else:
        print("Creating new card...")

    resp = requests.post(CONTENT_API, headers=headers(), json=body)

    if not resp.ok:
        print(f"Error: {resp.status_code} {resp.text}")
        sys.exit(1)

    result = resp.json()
    card = result.get("card", {})
    print(f"Done! Card ID: {card.get('cardId')}")
    print(f"Find it in the Yoto app under Playlists.")
    print(f"Link it to a blank MYO card and you're done.")


if __name__ == "__main__":
    main()

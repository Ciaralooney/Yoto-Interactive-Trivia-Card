"""
This needs to be run once to create the MYO card.
This will add the streaming track URLs to the card. 
"""

import sys
import time
import requests
from dotenv import load_dotenv
import os

load_dotenv()

# Loading API Keys
YOTO_ACCESS_TOKEN = os.getenv("YOTO_ACCESS_TOKEN")
SERVER_URL        = os.getenv("SERVER_URL")
LABS_API          = "https://labs.api.yotoplay.com/content/job"
VOICE_ID          = os.getenv("YOTO_VOICE_ID")
CARD_TITLE        = "True or False Trivia!"

ICONS_BASE = "https://raw.githubusercontent.com/Ciaralooney/Yoto-Interactive-Trivia-Card/main/images"
ICON_WELCOME  = f"{ICONS_BASE}/hello.png"
ICON_QUESTION = f"{ICONS_BASE}/question.png"
ICON_TRUE     = f"{ICONS_BASE}/true.png"
ICON_FALSE    = f"{ICONS_BASE}/false.png"
ICON_SCORE    = f"{ICONS_BASE}/score.png"


def headers():
    return {
        "Authorization": f"Bearer {YOTO_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def build_card_structure():
    """
    Structure:
      Chapter 01 — Welcome (plays on insert)
      Chapter 02 — Game Loop
        Track 01: Question
        Track 02: True answer
        Track 03: False answer
      Chapter 03 — Final Score
    """
    base = SERVER_URL.rstrip("/")

    chapters = [
        {
            "key":   "01",
            "title": "Welcome",
            "display": {"icon16x16": ICON_WELCOME},
            "tracks": [
                {
                    "key":          "01",
                    "title":        "Welcome",
                    "trackUrl":     f"{base}/welcome",
                    "type":         "stream",
                    "overlayLabel": "▶",
                    "display":      {"icon16x16": ICON_WELCOME},
                    "format":       "mp3",
                    "duration":     20,
                    "fileSize":     0,
                    "channels":     "stereo",
                }
            ],
        },
        {
            "key":   "02",
            "title": "Game",
            "display": {"icon16x16": ICON_QUESTION},
            "tracks": [
                {
                    "key":          "01",
                    "title":        "Question",
                    "trackUrl":     f"{base}/question",
                    "type":         "stream",
                    "overlayLabel": "?",
                    "display":      {"icon16x16": ICON_QUESTION},
                    "format":       "mp3",
                    "duration":     15,
                    "fileSize":     0,
                    "channels":     "stereo",
                },
                {
                    "key":          "02",
                    "title":        "True",
                    "trackUrl":     f"{base}/true",
                    "type":         "stream",
                    "overlayLabel": "T",
                    "display":      {"icon16x16": ICON_TRUE},
                    "format":       "mp3",
                    "duration":     15,
                    "fileSize":     0,
                    "channels":     "stereo",
                },
                {
                    "key":          "03",
                    "title":        "False",
                    "trackUrl":     f"{base}/false",
                    "type":         "stream",
                    "overlayLabel": "F",
                    "display":      {"icon16x16": ICON_FALSE},
                    "format":       "mp3",
                    "duration":     15,
                    "fileSize":     0,
                    "channels":     "stereo",
                },
            ],
        },
        {
            "key":   "03",
            "title": "Score",
            "display": {"icon16x16": ICON_SCORE},
            "tracks": [
                {
                    "key":          "01",
                    "title":        "Final Score",
                    "trackUrl":     f"{base}/score",
                    "type":         "stream",
                    "overlayLabel": "★",
                    "display":      {"icon16x16": ICON_SCORE},
                    "format":       "mp3",
                    "duration":     20,
                    "fileSize":     0,
                    "channels":     "stereo",
                }
            ],
        },
    ]

    return {
        "title": CARD_TITLE,
        "content": {"chapters": chapters},
        "metadata": {
            "title":       CARD_TITLE,
            "description": "A live true-or-false trivia game with random questions every time!",
        },
    }


def upload_card(card_id=None):
    content = build_card_structure()
    if card_id:
        content["cardId"] = card_id
        print(f"Updating existing card: {card_id}")
    else:
        print("Creating new card...")

    resp = requests.post(
        f"{LABS_API}?voiceId={VOICE_ID}",
        headers=headers(),
        json=content,
    )

    if not resp.ok:
        print(f"Error: {resp.status_code} {resp.text}")
        sys.exit(1)

    job = resp.json().get("job", {})
    print(f"Submitted! Job ID: {job.get('jobId')}")
    return job


def main():
    print("Yoto Trivia — Card Builder")
    print(f"   Server: {SERVER_URL}")
    print("=" * 40)

    card_id = sys.argv[1] if len(sys.argv) > 1 else None
    job = upload_card(card_id)

    print(f"\n Done! Your card is in your Yoto library.")
    print(f"   Open the Yoto app → My Cards → link to a blank MYO card.")
    print(f"\n The card URLs point permanently to: {SERVER_URL}")
    print(f"   As long as your server is running, the game works.")


if __name__ == "__main__":
    main()
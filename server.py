"""
  GET /welcome          - intro audio, starts a new game session
  GET /question         - reads the current question (and any pending feedback)
  GET /true             - silent-ish placeholder, landing spot for "True"
  GET /false            - silent-ish placeholder, landing spot for "False"
  GET /score            - final score reveal

How branching works:
  1. Yoto card plays Question -> player presses the right button (True)
     or the left button (False) -> native nav lands them on the
     True or False track.
  2. MQTT listener sees track_key become "02" (True) or "03"
     (False) in the live event stream.
  3. Answer is scored server-side, store the feedback text for
     the NEXT /question call, and immediately call play_card() to
     jump the player back to the Question track. Which now serves
     the next question with spoken feedback on the previous answer.
"""

import os
import random
import time
import logging
import asyncio
import requests
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
from dotenv import load_dotenv

from yoto_api import YotoClient

from questions import QUESTIONS

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# API Setup
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID           = os.getenv("YOTO_VOICE_ID")
QUESTIONS_PER_GAME = 10
ELEVENLABS_URL     = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/stream"

YOTO_CLIENT_ID     = os.getenv("YOTO_CLIENT_ID")
YOTO_REFRESH_TOKEN = os.getenv("YOTO_REFRESH_TOKEN")
YOTO_DEVICE_ID     = os.getenv("YOTO_DEVICE_ID")

# Chapter/track keys, match build_card.py
CHAPTER_GAME  = "02"
TRACK_QUESTION = "01"
TRACK_TRUE     = "02"
TRACK_FALSE    = "03"

# Session setup
sessions: dict[str, dict] = {}

yoto_client: YotoClient | None = None
YOTO_CARD_ID = os.getenv("YOTO_CARD_ID")

# Tracks the last (chapter_key, track_key)
_last_seen_track: dict[str, tuple[str, str]] = {}


def get_session(player_id: str) -> dict:
    return sessions.get(player_id)


def new_session(player_id: str) -> dict:
    picked = random.sample(QUESTIONS, QUESTIONS_PER_GAME)
    session = {
        "questions":      picked,
        "current":        0,       # index of current question
        "score":          0,
        "started":        time.time(),
        "pending_feedback": None,  # text to prepend to the next question read
    }
    sessions[player_id] = session
    log.info(f"New session for player {player_id}")
    return session


def current_question(session: dict):
    idx = session["current"]
    if idx >= len(session["questions"]):
        return None
    return session["questions"][idx]


# TTS Stream
def tts_stream(text: str):
    """Call ElevenLabs and stream the MP3 back."""
    log.info(f"TTS: {text[:80]}...")
    resp = requests.post(
        ELEVENLABS_URL,
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "text": text,
            "model_id": "eleven_turbo_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            },
        },
        stream=True,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.iter_content(chunk_size=4096)


def audio_response(text: str) -> StreamingResponse:
    return StreamingResponse(
        tts_stream(text),
        media_type="audio/mpeg",
    )


def player_id_from_request(request: Request) -> str:
    """
    Use the Yoto player ID from headers if available,
    otherwise fall back to client IP.
    """
    return (
        request.headers.get("x-yoto-device-id")
        or request.headers.get("x-device-id")
        or request.client.host
        or "default"
    )


# Branching / scoring logic
def _score_answer(session: dict, answered_true: bool) -> str:
    """
    Score the current question against the given answer, advance
    session state, and return the feedback text to read out before
    the next question (or before the final score).
    """
    q = current_question(session)
    if not q:
        return "No question found."

    statement, is_true, fun_fact = q
    correct = (answered_true == is_true)

    if correct:
        session["score"] += 1
        score = session["score"]
        result = f"Yes, that's correct! {fun_fact} You've got {score} right so far."
    else:
        right_answer = "true" if is_true else "false"
        result = f"Ooh, not quite - that one was {right_answer}! {fun_fact}"

    session["current"] += 1
    return result


# MQTT event handling
async def _on_player_update(player) -> None:
    """
    Called by yoto_api whenever a player's live state changes. 
    """
    event = player.last_event
    if event is None:
        return

    device_id = event.player_id
    chapter_key = event.chapter_key
    track_key = event.track_key

    if chapter_key is None or track_key is None:
        return

    key = (chapter_key, track_key)
    if _last_seen_track.get(device_id) == key:
        return  # already handled this exact landing
    _last_seen_track[device_id] = key

    if chapter_key != CHAPTER_GAME:
        return  # not in the question loop, nothing to react to

    if track_key not in (TRACK_TRUE, TRACK_FALSE):
        return  # landed on the Question track itself, no action needed

    pid = device_id  # use the Yoto device id as the session key
    session = get_session(pid)
    if not session:
        log.warning(f"Landing event for {pid} with no active session, ignoring")
        return

    answered_true = (track_key == TRACK_TRUE)
    log.info(f"Player {pid} answered {'True' if answered_true else 'False'}")

    feedback = _score_answer(session, answered_true)
    session["pending_feedback"] = feedback

    remaining = QUESTIONS_PER_GAME - session["current"]

    try:
        if remaining > 0:
            # Jump back to /question so it will read the
            # pending feedback, then the next question.
            await yoto_client.play_card(
                device_id,
                YOTO_CARD_ID,
                chapter_key=CHAPTER_GAME,
                track_key=TRACK_QUESTION,
            )
        else:
            # Game's finished so jump to the Score chapter.
            await yoto_client.play_card(
                device_id,
                YOTO_CARD_ID,
                chapter_key="03",
                track_key="01",
            )
    except Exception:
        log.exception(f"Failed to redirect player {pid} after answering")


async def _start_mqtt():
    global yoto_client

    if not (YOTO_CLIENT_ID and YOTO_REFRESH_TOKEN and YOTO_DEVICE_ID):
        log.warning(
            "YOTO_CLIENT_ID / YOTO_REFRESH_TOKEN / YOTO_DEVICE_ID not all set - "
            "MQTT branching is disabled. The server will still stream audio, "
            "but answers won't be detected."
        )
        return

    if not YOTO_CARD_ID:
        log.warning(
            "YOTO_CARD_ID not set - MQTT branching is disabled until it's configured, "
            "since we need it to know which card to redirect playback on."
        )
        return

    client = YotoClient(client_id=YOTO_CLIENT_ID)
    client.set_refresh_token(YOTO_REFRESH_TOKEN)
    await client.check_and_refresh_token()

    yoto_client = client
    await yoto_client.connect_events([YOTO_DEVICE_ID], on_update=_on_player_update)
    log.info(f"MQTT connected for device {YOTO_DEVICE_ID}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _start_mqtt()
    yield
    if yoto_client is not None:
        try:
            await yoto_client.disconnect_events()
        except Exception:
            log.exception("Error disconnecting MQTT on shutdown")


app = FastAPI(title="Yoto Trivia Server", lifespan=lifespan)


# Routes

@app.get("/welcome")
async def welcome(request: Request):
    """
    Played when the card is first inserted.
    Starts a fresh game session and reads the intro.
    """
    pid = player_id_from_request(request)
    session = new_session(pid)
    _last_seen_track.pop(pid, None)

    q_count = len(session["questions"])
    text = (
        f"Welcome to True or False Trivia! "
        f"I'll read you {q_count} statements. "
        f"Here's how to play: when I ask a question, "
        f"press the right button if you think it's true, "
        f"or press the left button if you think it's false. "
        f"Press the right button now to hear your first question!"
    )
    return audio_response(text)


@app.get("/question")
async def question(request: Request):
    """
    Reads any pending feedback from the previous answer, then the
    current question.
    """
    pid     = player_id_from_request(request)
    session = get_session(pid)

    if not session:
        session = new_session(pid)

    q = current_question(session)
    if not q:
        return audio_response(
            "You've answered all the questions! Press right to hear your final score."
        )

    idx = session["current"]
    num = idx + 1
    statement, _, _ = q

    feedback = session.pop("pending_feedback", None)
    prefix = f"{feedback} " if feedback else ""

    text = (
        f"{prefix}"
        f"Question {num}. {statement} "
        f"Press the right button for true, or the left button for false."
    )
    return audio_response(text)


@app.get("/true")
async def true_placeholder(request: Request):
    """
    Landing spot for a True answer. The MQTT listener detects this
    landing and redirects playback. This response is a short filler
    in case the redirect doesn't beat native playback.
    """
    return audio_response(" ")


@app.get("/false")
async def false_placeholder(request: Request):
    """
    Landing spot for a False answer. Same as /true.
    """
    return audio_response(" ")


@app.get("/score")
async def score(request: Request):
    """
    Final score reveal, end of the game.
    """
    pid     = player_id_from_request(request)
    session = get_session(pid)

    if not session:
        return audio_response("I couldn't find your score - sorry about that! Insert the card again to play.")

    s     = session["score"]
    total = QUESTIONS_PER_GAME

    if s == total:
        verdict = "Absolutely perfect! A flawless ten out of ten - you're a trivia genius!"
    elif s >= 8:
        verdict = f"Amazing! {s} out of {total} - you really know your stuff!"
    elif s >= 6:
        verdict = f"Great effort! {s} out of {total} - well done!"
    elif s >= 4:
        verdict = f"Not bad! {s} out of {total} - keep practising and you'll smash it next time!"
    else:
        verdict = f"You got {s} out of {total}. Keep going - every game you'll learn something new!"

    text = (
        f"Game over! {verdict} "
        f"Insert the card again whenever you want to play a brand new game. See you next time!"
    )

    sessions.pop(pid, None)
    _last_seen_track.pop(pid, None)

    return audio_response(text)


# Health Check
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "questions_in_bank": len(QUESTIONS),
        "mqtt_connected": yoto_client is not None,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)

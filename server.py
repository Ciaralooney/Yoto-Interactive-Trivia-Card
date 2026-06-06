"""
FastAPI app that serves all game audio as streaming tracks.

Endpoints:
  GET /welcome          — intro audio, starts a new game session
  GET /question         — reads the current question
  GET /true             — kid navigated here (answered True)
  GET /false            — kid navigated here (answered False)
  GET /score            — final score reveal

Session state is stored in memory keyed by player_id.
Deploy to Railway / Render / Fly.io for always-on access.

"""

import os
import random
import time
import logging
import requests
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv

from questions import QUESTIONS

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Yoto Trivia Server")

# API Setup
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID           = os.getenv("YOTO_VOICE_ID")
QUESTIONS_PER_GAME = 10
ELEVENLABS_URL     = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/stream"

# Session setup
sessions: dict[str, dict] = {}


def get_session(player_id: str) -> dict:
    return sessions.get(player_id)


def new_session(player_id: str) -> dict:
    picked = random.sample(QUESTIONS, QUESTIONS_PER_GAME)
    session = {
        "questions": picked,
        "current":   0,       # index of current question
        "score":     0,
        "started":   time.time(),
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
    Yoto sends device info in headers — adjust header name
    once you've inspected real traffic from your player.
    """
    return (
        request.headers.get("x-yoto-device-id")
        or request.headers.get("x-device-id")
        or request.client.host
        or "default"
    )


def ordinal(n: int) -> str:
    suffixes = {1: "st", 2: "nd", 3: "rd"}
    return f"{n}{suffixes.get(n if n < 20 else n % 10, 'th')}"


# Routes

@app.get("/welcome")
async def welcome(request: Request):
    """
    Played when the card is first inserted.
    Starts a fresh game session and reads the intro.
    """
    pid = player_id_from_request(request)
    session = new_session(pid)

    q_count = len(session["questions"])
    text = (
        f"Welcome to True or False Trivia! "
        f"I'll read you {q_count} statements. "
        f"Here's how to play: when I ask a question, "
        f"turn the right knob forward if you think it's true, "
        f"or turn it back if you think it's false. "
        f"Turn the right knob forward now to hear your first question!"
    )
    return audio_response(text)


@app.get("/question")
async def question(request: Request):
    """
    Reads the current question aloud.
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

    idx      = session["current"]
    num      = idx + 1
    statement, _, _ = q

    text = (
        f"Question {num}. {statement} "
        f"Turn the right knob forward for true, or turn it back for false."
    )
    return audio_response(text)


@app.get("/true")
async def answered_true(request: Request):
    """
    If user navigated here, they answered True.
    """
    return _check_answer(request, answered_true=True)


@app.get("/false")
async def answered_false(request: Request):
    """
    If user navigated here, they answered False.
    """
    return _check_answer(request, answered_true=False)


def _check_answer(request: Request, answered_true: bool) -> StreamingResponse:
    pid     = player_id_from_request(request)
    session = get_session(pid)

    if not session:
        return audio_response("Hmm, I lost track of your game! Turn the right knob forward to start over.")

    q = current_question(session)
    if not q:
        return audio_response("No question found — turn the right knob forward to go to your score.")

    statement, is_true, fun_fact = q
    correct = (answered_true == is_true)

    if correct:
        session["score"] += 1
        score = session["score"]
        result = f"Yes, that's correct! {fun_fact} You've got {score} right so far."
    else:
        right_answer = "true" if is_true else "false"
        result = f"Ooh, not quite — that one was {right_answer}! {fun_fact}"

    # Advance to next question
    session["current"] += 1
    remaining = QUESTIONS_PER_GAME - session["current"]

    if remaining > 0:
        next_prompt = f"Turn the right knob forward for question {session['current'] + 1}."
    else:
        next_prompt = "Turn the right knob forward to hear your final score!"

    text = f"{result} {next_prompt}"
    return audio_response(text)


@app.get("/score")
async def score(request: Request):
    """
    Final score reveal, end of the game.
    """
    pid     = player_id_from_request(request)
    session = get_session(pid)

    if not session:
        return audio_response("I couldn't find your score — sorry about that! Insert the card again to play.")

    s     = session["score"]
    total = QUESTIONS_PER_GAME

    if s == total:
        verdict = "Absolutely perfect! A flawless ten out of ten — you're a trivia genius!"
    elif s >= 8:
        verdict = f"Amazing! {s} out of {total} — you really know your stuff!"
    elif s >= 6:
        verdict = f"Great effort! {s} out of {total} — well done!"
    elif s >= 4:
        verdict = f"Not bad! {s} out of {total} — keep practising and you'll smash it next time!"
    else:
        verdict = f"You got {s} out of {total}. Keep going — every game you'll learn something new!"

    text = (
        f"Game over! {verdict} "
        f"Insert the card again whenever you want to play a brand new game. See you next time!"
    )

    # Clear the session so the card resets cleanly on next insert
    sessions.pop(pid, None)

    return audio_response(text)


# Health Check
@app.get("/health")
async def health():
    return {"status": "ok", "questions_in_bank": len(QUESTIONS)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)

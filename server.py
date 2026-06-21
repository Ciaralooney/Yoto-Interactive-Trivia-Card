import os
import random
import time
import datetime
import logging
import asyncio
import requests
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from yoto_api import YotoClient, Token
from questions import QUESTIONS

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
import yoto_api.auth as _yoto_auth_module
from yoto_api.exceptions import YotoAPIError as _YotoAPIError

_original_build_token = _yoto_auth_module._build_token


def _patched_build_token(body, scope, prev_refresh=None):
    if "refresh_token" not in body and prev_refresh is not None:
        body = {**body, "refresh_token": prev_refresh}
    return _original_build_token(body, scope)


def _patched_refresh(self_auth, token):

    async def _inner():
        import aiohttp

        data = {
            "client_id": self_auth.client_id,
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "audience": "https://api.yotoplay.com",
        }
        try:
            async with self_auth._session.post(
                "https://login.yotoplay.com/oauth/token",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as response:
                resp_body = await response.json(content_type=None)
        except Exception as err:
            raise _YotoAPIError(f"Refresh token request failed: {err}") from err
        if resp_body.get("error"):
            from yoto_api.exceptions import AuthenticationError

            raise AuthenticationError("Refresh token invalid")
        return _patched_build_token(
            resp_body, scope=token.scope, prev_refresh=token.refresh_token
        )

    return _inner()


_yoto_auth_module.Auth.refresh = _patched_refresh
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID = os.getenv("YOTO_VOICE_ID")
QUESTIONS_PER_GAME = 10
ELEVENLABS_URL = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/stream"
YOTO_CLIENT_ID = os.getenv("YOTO_CLIENT_ID")
YOTO_REFRESH_TOKEN = os.getenv("YOTO_REFRESH_TOKEN")
YOTO_ACCESS_TOKEN = os.getenv("YOTO_ACCESS_TOKEN")
YOTO_DEVICE_ID = os.getenv("YOTO_DEVICE_ID")
CHAPTER_GAME = "02"
TRACK_ENTRY = "01"
TRACK_FALSE = "01"
TRACK_QUESTION = "02"
TRACK_TRUE = "03"
sessions: dict[str, dict] = {}
yoto_client: YotoClient | None = None
YOTO_CARD_ID = os.getenv("YOTO_CARD_ID")
SILENCE_URL = os.getenv("SILENCE_URL", "https://raw.githubusercontent.com/Ciaralooney/Yoto-Interactive-Trivia-Card/main/silence.mp3")
_last_seen_track: dict[str, tuple[int, bool, str, str]] = {}


def get_session(player_id: str) -> dict:
    return sessions.get(player_id)


def new_session(player_id: str) -> dict:
    picked = random.sample(QUESTIONS, QUESTIONS_PER_GAME)
    session = {
        "questions": picked,
        "current": 0,
        "score": 0,
        "started": time.time(),
        "pending_feedback": None,
        "question_asked": False,
    }
    sessions[player_id] = session
    log.info(f"New session for player {player_id}")
    return session


def current_question(session: dict):
    idx = session["current"]
    if idx >= len(session["questions"]):
        return None
    return session["questions"][idx]


def tts_stream(text: str):
    log.info(f"TTS: {text[:80]}...")
    resp = requests.post(
        ELEVENLABS_URL,
        headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": "eleven_turbo_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        },
        stream=True,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.iter_content(chunk_size=4096)


def audio_response(text: str) -> StreamingResponse:
    return StreamingResponse(tts_stream(text), media_type="audio/mpeg")


def player_id_from_request(request: Request) -> str:
    return (
        request.headers.get("x-yoto-device-id")
        or request.headers.get("x-device-id")
        or YOTO_DEVICE_ID
        or request.client.host
        or "default"
    )


def _score_answer(session: dict, answered_true: bool) -> str:
    q = current_question(session)
    if not q:
        return "No question found."
    statement, is_true, fun_fact = q
    correct = answered_true == is_true
    if correct:
        session["score"] += 1
        score = session["score"]
        result = f"Yes, that's correct! {fun_fact} You've got {score} right so far."
    else:
        right_answer = "true" if is_true else "false"
        result = f"Ooh, not quite, that one was {right_answer}! {fun_fact}"
    session["current"] += 1
    session["question_asked"] = False
    return result


async def _on_player_update(player) -> None:
    event = player.last_event
    if event is None:
        return
    device_id = event.player_id
    chapter_key = event.chapter_key
    track_key = event.track_key
    log.info(
        f"[MQTT EVENT] device={device_id} chapter_key={chapter_key!r} track_key={track_key!r} playback_status={event.playback_status!r} track_title={event.track_title!r}"
    )
    if chapter_key is None or track_key is None:
        return
    if chapter_key != CHAPTER_GAME:
        return
    if track_key not in (TRACK_TRUE, TRACK_FALSE):
        return
    pid = device_id
    session = get_session(pid)
    if not session:
        log.warning(f"Landing event for {pid} with no active session, ignoring")
        return
    dedup_signal = (chapter_key, track_key, event.event_utc, event.request_id)
    if _last_seen_track.get(device_id) == dedup_signal:
        return
    _last_seen_track[device_id] = dedup_signal
    if track_key == TRACK_ENTRY and (not session.get("question_asked")):
        log.info(f"Player {pid} arrived in Game chapter, redirecting to Question")
        try:
            await yoto_client.play_card(
                device_id,
                YOTO_CARD_ID,
                chapter_key=CHAPTER_GAME,
                track_key=TRACK_QUESTION,
            )
        except Exception:
            log.exception(f"Failed to redirect player {pid} to Question on arrival")
        return
    answered_true = track_key == TRACK_TRUE
    log.info(f"Player {pid} answered {('True' if answered_true else 'False')}")
    feedback = _score_answer(session, answered_true)
    session["pending_feedback"] = feedback
    remaining = QUESTIONS_PER_GAME - session["current"]
    try:
        if remaining > 0:
            await yoto_client.play_card(
                device_id,
                YOTO_CARD_ID,
                chapter_key=CHAPTER_GAME,
                track_key=TRACK_QUESTION,
            )
        else:
            await yoto_client.play_card(
                device_id, YOTO_CARD_ID, chapter_key="03", track_key="01"
            )
    except Exception:
        log.exception(f"Failed to redirect player {pid} after answering")


async def _start_mqtt():
    global yoto_client
    if not (YOTO_CLIENT_ID and YOTO_REFRESH_TOKEN and YOTO_DEVICE_ID):
        log.warning(
            "YOTO_CLIENT_ID / YOTO_REFRESH_TOKEN / YOTO_DEVICE_ID not all set, MQTT branching is disabled. The server will still stream audio, but answers won't be detected."
        )
        return
    if not YOTO_CARD_ID:
        log.warning(
            "YOTO_CARD_ID not set, MQTT branching is disabled until it's configured, since we need it to know which card to redirect playback on."
        )
        return
    try:
        client = YotoClient(client_id=YOTO_CLIENT_ID)
        client.set_refresh_token(YOTO_REFRESH_TOKEN)
        await client.check_and_refresh_token()
        await client.update_player_list()
        if YOTO_DEVICE_ID not in client.players:
            log.warning(
                f"YOTO_DEVICE_ID {YOTO_DEVICE_ID} not found in account's device list ({list(client.players)}) Check the ID is correct."
            )
        yoto_client = client
        await yoto_client.connect_events([YOTO_DEVICE_ID], on_update=_on_player_update)
        log.info(f"MQTT connected for device {YOTO_DEVICE_ID}")
    except Exception:
        log.exception(
            "Failed to start MQTT  branching will be disabled, but audio streaming will still work."
        )
        try:
            await client.close()
        except Exception:
            pass
        yoto_client = None


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


@app.get("/welcome")
async def welcome(request: Request):
    pid = player_id_from_request(request)
    session = new_session(pid)
    _last_seen_track.pop(pid, None)
    q_count = len(session["questions"])
    text = f"Welcome to True or False Trivia! I'll read you {q_count} statements. Here's how to play: when I ask a question, press the right button if you think it's true, or press the left button if you think it's false. Press the right button now to hear your first question!"
    return audio_response(text)


@app.get("/question")
async def question(request: Request):
    pid = player_id_from_request(request)
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
    text = f"{prefix}Question {num}. {statement} Press the right button for true, or the left button for false."
    session["question_asked"] = True
    return audio_response(text)


@app.get("/true")
async def true_placeholder(request: Request):
    resp = requests.get(SILENCE_URL, stream=True, timeout=10)
    resp.raise_for_status()
    return StreamingResponse(resp.iter_content(chunk_size=4096), media_type="audio/mpeg")


@app.get("/false")
async def false_placeholder(request: Request):
    resp = requests.get(SILENCE_URL, stream=True, timeout=10)
    resp.raise_for_status()
    return StreamingResponse(resp.iter_content(chunk_size=4096), media_type="audio/mpeg")


@app.get("/score")
async def score(request: Request):
    pid = player_id_from_request(request)
    session = get_session(pid)
    if not session:
        return audio_response(
            "I couldn't find your score. sorry about that! Insert the card again to play."
        )
    s = session["score"]
    total = QUESTIONS_PER_GAME
    if s == total:
        verdict = (
            "Absolutely perfect! A flawless ten out of ten. you're a trivia genius!"
        )
    elif s >= 8:
        verdict = f"Amazing! {s} out of {total}. you really know your stuff!"
    elif s >= 6:
        verdict = f"Great effort! {s} out of {total}. well done!"
    elif s >= 4:
        verdict = f"Not bad! {s} out of {total}. Keep practising and you'll smash it next time!"
    else:
        verdict = f"You got {s} out of {total}. Keep going every game you'll learn something new!"
    text = f"Game over! {verdict} Insert the card again whenever you want to play a brand new game. See you next time!"
    sessions.pop(pid, None)
    _last_seen_track.pop(pid, None)
    return audio_response(text)


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
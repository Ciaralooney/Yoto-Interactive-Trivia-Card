# Yoto True/False Trivia with Live Server

A fully interactive true/false trivia game for Yoto players.
Random questions every game, live scoring, instant feedback.


## How it works

Kid inserts card
  → Yoto player hits your server URL
  → Server picks 10 random questions, starts a session
  → Streams audio back to the player in real time
  → Kid navigates tracks to answer True or False
  → Server scores the answer, advances the game
  → Final score read out at the end

## Setup

### 1. Download this repo

### 2. Install dependencies
 - Search for command prompt
 - Right click on it and choose run as admin
 - CD to your project folder
 - Run:
```bash
pip install -r requirements.txt
```

### 3. Get the API keys

**ElevenLabs** (for text-to-speech):
- Sign up at [elevenlabs.io](https://elevenlabs.io) 
- Copy your API key from [here](https://elevenlabs.io/app/developers/api-keys)

**Yoto**:
- Go to [dashboard.yoto.dev](https://dashboard.yoto.dev) and create an app

- Follow the [Headless/CLI auth guide](https://yoto.dev/authentication/headless-cli-auth/) to get an access token OR go to https://github.com/Ciaralooney/Yoto-Access-Token-Generator for a simpler way to do this

### 4. Create your .env file
Make an .env file and fill in `ELEVENLABS_API_KEY` and `YOTO_ACCESS_TOKEN`.

## Deploy the server
You need this running somewhere permanently. What I suggest:

### Render
1. Push your project version to GitHub
2. Go to [render.com](https://render.com) → New Web Service
3. Set start command: `uvicorn server:app --host 0.0.0.0 --port $PORT`
4. Add env vars in the Render dashboard

## Build the card
Once your server is deployed:

1. Add `SERVER_URL=example.com` to your `.env` You will find your project URL on the Render dashboard.
2. Run:
```bash
python build_card.py
```
3. Open the Yoto app → My Cards → find "True or False Trivia!" → link to a blank MYO card

That's it. The card is permanent — you don't need to rebuild it unless you change your server URL.

## Game flow for the player

```
Insert card
  → Welcome audio plays, explains the rules
  → Press right → Question 1 plays
  → Left for TRUE, right for FALSE
  → Feedback + fun fact plays, score update
  → Press right → Question 2...
  → ...after Question 10 → press right for final score
```

## Updating the card

If you ever change your server URL, rebuild the card:
```bash
python build_card.py <your-existing-card-id>
```
Card ID is visible in the Yoto app URL when viewing the card.

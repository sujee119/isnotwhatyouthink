"""Central configuration for @isnotwhatyouthink. Everything is overridable
via environment variables, so the workflow can tune it with repo variables
and no redeploy.

Note the `or` fallbacks rather than os.getenv defaults: an unset repo
variable referenced as ${{ vars.X }} arrives as an EMPTY env var, not an
absent one, so getenv's default never fires. That bug cost a day on the
reels pipeline twice.
"""
import os
from pathlib import Path

ROOT = Path(__file__).parent
STATE_DIR = ROOT / "state"
OUT_DIR = ROOT / "out"
ASSETS_DIR = ROOT / "assets"
SFX_DIR = ASSETS_DIR / "sfx"

for d in (STATE_DIR, OUT_DIR, ASSETS_DIR, SFX_DIR):
    d.mkdir(exist_ok=True)

# ------------------------------------------------------------------ channel

CHANNEL = "isnotwhatyouthink"

# The one fixed line. {subject} is filled in; {wh} becomes who for a person
# and what for everything else -- "TENZING NORGAY IS NOT WHO YOU THINK".
HOOK_TEMPLATE = os.getenv("HOOK_TEMPLATE") or "{subject} is not {wh} you think"

# One video per topic. Shorts per day is random between these, seeded from
# the date so every cron run that day agrees without shared state -- a
# fixed count looks mechanical.
DAILY_MIN = int(os.getenv("DAILY_MIN") or 4)
DAILY_MAX = int(os.getenv("DAILY_MAX") or 8)
# Cron slots available per day. Must be >= DAILY_MAX, and today's posting
# slots are sampled from these so the times move day to day.
SLOTS = int(os.getenv("SLOTS") or 8)

# Kept at 1: one video per topic. The source-subset machinery in
# core.topic still works if a rich topic is ever worth doubling up on.
VARIATIONS = int(os.getenv("VARIATIONS") or 1)

# -------------------------------------------------------------------- length

# 30-40s finished. Narration target plus the end card lands in that band.
TARGET_SECONDS = int(os.getenv("TARGET_SECONDS") or 30)
MAX_SECONDS = int(os.getenv("MAX_SECONDS") or 33)
WORDS_PER_SECOND = float(os.getenv("WORDS_PER_SECOND") or 3.0)

# ---------------------------------------------------------------- the pool

# Wikipedia's most-viewed list, how deep to read. The media and place
# filters remove most of the top 80, so this is the lever when the
# shortlist comes back thin -- never the risk filter.
WIKI_DEPTH = int(os.getenv("WIKI_DEPTH") or 200)

# Google Trends geos, used only to boost topics that are also being
# searched. Trends does not decide what gets made: its daily list is
# names and tragedies, which is the wrong shape for this format.
TRENDS_GEOS = (os.getenv("TRENDS_GEOS") or "US,GB,CA,AU").split(",")

# A death inside this many years is never "historical". Three recently
# dead people topped the pool before this existed.
HISTORICAL_AFTER_YEARS = int(os.getenv("HISTORICAL_AFTER_YEARS") or 20)

# Topics already used, so the channel never repeats itself.
USED_FILE = STATE_DIR / "used.json"

# ---------------------------------------------------------------- the gate

# Every shortlisted topic passes the LLM gate before anything is built.
# The regex layer ranks and hard-drops; it does not clear anything.
GATE_ENABLED = (os.getenv("GATE_ENABLED") or "1") not in ("0", "false", "False")

# How many candidates reach the gate, by source. The gate rejects nearly
# every trend by design -- trending subjects are people, deaths, sport
# and politics -- so the NON-trend slots are what actually fill the day.
# Eight of them yielded one approval out of twenty on 19 Oct; that one
# failed on sources and the channel produced nothing. The gate is a
# single batched call, so asking about more subjects costs one larger
# call, not more calls.
GATE_TREND_SLOTS = int(os.getenv("GATE_TREND_SLOTS") or 12)
GATE_OTHER_SLOTS = int(os.getenv("GATE_OTHER_SLOTS") or 32)

SCRIPT_PROVIDER = os.getenv("SCRIPT_PROVIDER") or "gemini"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-flash-latest"
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# ---------------------------------------------------------------- narration

TTS_VOICE = os.getenv("TTS_VOICE") or "en-US-AndrewMultilingualNeural"
TTS_RATE = os.getenv("TTS_RATE") or "+40%"
VOICE_DEEPEN = float(os.getenv("VOICE_DEEPEN") or 0.88)
TTS_PROVIDERS = os.getenv("TTS_PROVIDERS") or "gemini,azure,edge,gtts,espeak"

GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL") or "gemini-3.8-flash-tts"
GEMINI_TTS_VOICE = os.getenv("GEMINI_TTS_VOICE") or "Charon"
GEMINI_TTS_STYLE = os.getenv("GEMINI_TTS_STYLE") or (
    "a confident male narrator, brisk and slightly conspiratorial, "
    "like someone letting you in on something")

AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION") or "koreacentral"

# --------------------------------------------------------------------- video

WIDTH, HEIGHT, FPS = 1080, 1920, 30
VIDEO_PRESET = os.getenv("VIDEO_PRESET") or "veryfast"
KENBURNS_ZOOM = 1.08
SHOT_TRANSITION = 0.40
MAX_SHOT_SECONDS = 4.0
MIN_SHOT_SECONDS = 1.6
SHOT_DIM = 0.22

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")

# ------------------------------------------------------------------- captions

CAPTION_FONT = os.getenv("CAPTION_FONT") or "DejaVu Sans"
CAPTION_SIZE = int(os.getenv("CAPTION_SIZE") or 74)
CAPTION_ACTIVE = os.getenv("CAPTION_ACTIVE") or "&H0000F0FF"
CAPTION_IDLE = os.getenv("CAPTION_IDLE") or "&H00FFFFFF"
CAPTION_OUTLINE = int(os.getenv("CAPTION_OUTLINE") or 6)
CAPTION_MARGIN_V = int(os.getenv("CAPTION_MARGIN_V") or 420)

# ---------------------------------------------------------------- the cover

# The hook burned over the opening. This channel's whole identity is that
# line, so unlike the reels pipeline it is never optional.
COVER_ENABLED = True
COVER_SECONDS = float(os.getenv("COVER_SECONDS") or 3.4)

# Which frame becomes the channel-grid thumbnail, in milliseconds. A Short
# plays straight away in the feed, so the first frame is what most viewers
# see -- and the cover is already fully opaque from t=0. But the grid,
# search results and the Shorts shelf show a still, and YouTube picks that
# frame itself unless one is set. This must land inside COVER_SECONDS or
# the thumbnail will not have the catchphrase on it.
COVER_THUMB_MS = int(os.getenv("COVER_THUMB_MS") or 900)
COVER_FADE = 0.35
COVER_MAX_LINES = 3
COVER_SIZES = (104, 92, 78)
COVER_TOP = int(os.getenv("COVER_TOP") or 330)
COVER_ALIGN = os.getenv("COVER_ALIGN") or "middle"

# No end card. A Short LOOPS: whatever sits at the end replays on every
# pass, so a static card is dead screen in each loop and costs both of
# the things Shorts ranks on -- view duration as a fraction of length,
# and loop rate. YouTube already overlays the handle and the subscribe
# button, and the hook burned over the opening is the channel name, so a
# "follow for more" card duplicates what is on screen anyway.
#
# Set CTA_CARD to switch one back on and A/B it, e.g.
#   gh variable set CTA_CARD --body "FOLLOW FOR MORE\nTHINGS YOU DON'T KNOW"
# A reverse countdown in the top corner. Small and out of the way: it is
# a progress cue, not a second caption track.
COUNTDOWN_ENABLED = (os.getenv("COUNTDOWN_ENABLED") or "1") not in (
    "0", "false", "False")
COUNTDOWN_SIZE = int(os.getenv("COUNTDOWN_SIZE") or 78)
# Run the countdown as an A/B: exactly half of each week's posts get it,
# chosen at random but balanced, so the two arms are comparable. Set to 0
# to put the countdown on everything instead.
COUNTDOWN_AB = (os.getenv("COUNTDOWN_AB") or "1") not in ("0", "false", "False")
COUNTDOWN_TOP = int(os.getenv("COUNTDOWN_TOP") or 96)
COUNTDOWN_MARGIN = int(os.getenv("COUNTDOWN_MARGIN") or 60)

CTA_CARD = os.getenv("CTA_CARD", "")
CTA_CARD_SECONDS = float(os.getenv("CTA_CARD_SECONDS") or 2.2)

# Set SPOKEN_CTA=1 to have the narration itself end on a follow line.
# Off by default: it costs about two seconds of a thirty-second video and
# delays the payoff. Worth testing against retention, not assuming.
SPOKEN_CTA = (os.getenv("SPOKEN_CTA") or "0") not in ("0", "false", "False")

# --------------------------------------------------------------------- audio

MUSIC_DIR = ASSETS_DIR / "music"
MUSIC_ENABLED = (os.getenv("MUSIC_ENABLED") or "1") not in ("0", "false")
MUSIC_GAIN = float(os.getenv("MUSIC_GAIN") or 0.16)
MUSIC_DUCK_RATIO = 8
MUSIC_FADE = 2.0

# The reveal that lands on the hook. Generated with ffmpeg rather than
# downloaded: nothing to licence, nothing to attribute, nothing that can
# be claimed by a rights holder later.
#   whoosh  noise sweep into a low boom
#   riser   upward chirp into a bright bell
#   drop    falling tape-stop into a muted thud
SFX_STYLE = os.getenv("SFX_STYLE") or "whoosh"
SFX_ENABLED = (os.getenv("SFX_ENABLED") or "1") not in ("0", "false", "False")
SFX_GAIN = float(os.getenv("SFX_GAIN") or 0.7)
# Seconds into the reel. The hook is the first line, so the reveal lands
# just after it starts rather than at zero.
SFX_AT = float(os.getenv("SFX_AT") or 0.55)

# ------------------------------------------------------------------- youtube

YT_ENABLED = (os.getenv("YT_ENABLED") or "0") not in ("0", "false", "False")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN", "")
YT_PRIVACY = os.getenv("YT_PRIVACY") or "public"
# 27 = Education. 28 is Science & Technology, 24 Entertainment.
YT_CATEGORY_ID = os.getenv("YT_CATEGORY_ID") or "27"

# ------------------------------------------------------------------- alerts

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# feed.py is shared with the reels pipeline and expects these.
FEEDS: list[str] = []
MAX_STORY_AGE_DAYS = 7

"""Lens test server: receives audio chunks, screen frames (or on-device OCR text), heartbeats and logs from
the Lens iOS app (Footnote). It transcribes each chunk and reads each frame with the Hostinger AI Router, keeps
one "now playing" state per session, names manipulation techniques with detect.py, and gives the results back.

Standard library only, except the optional APNs push path (httpx with HTTP/2 and cryptography).
State is in memory, per session, and freed after 10 minutes without a request. The last WAV chunks are kept
on disk. No screen image is written to disk, and none is kept in memory unless the frame request has the
debug flag (X-Lens-Debug: 1 or debug=1). A frame of a private screen is discarded at once.

Technique detection (detect.py, prompts in techniques.json): each new transcript sentence, with the video's
on-screen text and caption as context, and each new burned-in text of a video. Backend `jev`
(jev-latest on the Hostinger AI Router's System One API), with a fallback chain and hedged requests (detect.py). A note needs the top technique at
or above the note threshold, the war gate true and the quote gate false; a middle band gives `unsure`.

The server also computes the Live Activity content state (`state` in /api/latest), so the app, the
broadcast extension and the APNs push path all show the same thing.

Endpoints
  POST /api/audio?session=S&seq=N&rate=16000[&encoding=mulaw]
                                               16-bit mono PCM (little endian), 8-bit mu-law, or a WAV file
  POST /api/frame?session=S&seq=N&w=W&h=H[&debug=1]
                                               a JPEG of the screen (path S1)
  POST /api/screentext                         JSON {"session", "seq", "lines": [{"text", "x", "y", "w", "h"}]} (path S2, on-device OCR)
  POST /api/heartbeat                          JSON from the broadcast extension or the app
  POST /api/log                                JSON {"session", "source", "lines": [...]}
  POST /api/config?session=S                   JSON settings from the app; {"start": true} starts a new run
  POST /api/fake?session=S                     JSON {"text": "..."}: a fake transcript (keyword rules, no router, no credits);
                                               with "detect": true or "backend": "decision"|"router" it runs the real detector
  POST /api/text                               JSON {"session", "source": "speech"|"screen", "text", "lang", "t_start", "t_end",
                                               "final"}: text transcribed or read on the phone
  POST /api/now                                JSON {"session"}: "Footnote now", the best candidate of the last 15 s, forced
  POST /api/detector                           JSON {"backend", "fallback", "note", "unsure", "speechCheck", "speechMin",
                                               "tiktokGate", "apps"}: detector settings
  GET  /api/info                               JSON: commit, detector backend, model and thresholds
  GET  /api/latest?session=S&since=N           JSON: results with id > N, the newest result, state and config
  GET  /api/events?session=S                   Server-Sent Events, one "result" event per transcript
  GET  /debug                                  HTML evidence page (refreshes each 2 s)
  GET  /debug.json                             the same data as JSON
  GET  /wav/<file>                             a saved chunk
  GET  /frame/<id>.jpg                         one of the last 5 debug frames (memory only)
"""

import base64
import collections
import concurrent.futures
import difflib
import hashlib
import html
import io
import json
import os
import queue
import re
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import detect

PORT = int(os.environ.get("PORT", "3000"))
ROUTER_URL = os.environ.get("AI_ROUTER_BASE_URL", "https://router.hostinger.com/v1").rstrip("/")
ROUTER_KEY = os.environ.get("AI_ROUTER_API_KEY", "")
# On the VPS the key is not in the repository: it is in a file mounted through .hostinger-volumes.
KEY_FILE = os.environ.get("LENS_KEY_FILE", "/run/lens/router.env")
if not ROUTER_KEY and os.path.isfile(KEY_FILE):
    for _line in open(KEY_FILE):
        if _line.startswith("AI_ROUTER_API_KEY="):
            ROUTER_KEY = _line.split("=", 1)[1].strip().strip('"')
ROUTER_KEY = ROUTER_KEY or detect.router_key() or ""  # a local run reads infra/hostinger/secrets.env
# gemini-3.1-flash-lite: a lower word error rate than gemini-3.5-flash-lite in English, Russian and Ukrainian on 36 real
# chunks, on the Hostinger router and on OpenRouter (PERF.md).
MODEL = os.environ.get("LENS_MODEL", "gemini-3.1-flash-lite")
WAV_DIR = os.environ.get("LENS_WAV_DIR", "/tmp/lens-wav")
KEEP_WAVS = 20
MAX_BODY = 4 * 1024 * 1024

# APNs (Live Activity push). The key file is mounted from the VPS, never in the repository.
APNS_KEY_FILE = os.environ.get("LENS_APNS_KEY_FILE", "/run/lens/apns.p8")
# Key "Footnote APNs": sandbox, limited to the topic no.henrikkvamme.lens. (The first key here was limited to another
# app's topic: APNs answered HTTP 400 TopicDisallowed.)
APNS_KEY_ID = os.environ.get("LENS_APNS_KEY_ID", "")
APNS_TEAM = os.environ.get("LENS_APNS_TEAM", "")
APNS_TOPIC = os.environ.get("LENS_APNS_TOPIC", "no.henrikkvamme.lens.push-type.liveactivity")

# The keyword rules of the first prototype. They run only as backend "keywords": for `/api/fake` (the app's
# "Simulate technique" button) or when the detector setting says so. The real path is detect.py.
KEYWORD_RULES = [
    {"name": "urgency", "label": "Urgency", "short": "Urgency", "why": "Pushes you to act fast.",
     "explain": "It asks you to act before you have time to think it over.",
     "phrases": ["act now", "right now", "hurry", "tonight", "ends today", "last chance", "limited time", "before it's gone", "before it is gone"]},
    {"name": "scarcity", "label": "Scarcity", "short": "Scarcity", "why": "Makes it look rare.",
     "explain": "It makes something look rare, so it feels more valuable than it is.",
     "phrases": ["only", "spots left", "left in stock", "sold out", "exclusive"]},
    {"name": "social proof", "label": "Social proof", "short": "Crowd", "why": "Says everyone does it.",
     "explain": "It says that everyone does or thinks this, so you should too.",
     "phrases": ["everyone", "millions", "viral", "trending", "all my friends"]},
    {"name": "authority", "label": "Appeal to authority", "short": "Authority", "why": "Experts, no evidence.",
     "explain": "It leans on experts or titles instead of showing the evidence.",
     "phrases": ["doctors", "experts", "scientists", "studies show", "proven"]},
    {"name": "fear", "label": "Fear appeal", "short": "Fear", "why": "Fear before thought.",
     "explain": "It uses fear to make you react before you reflect.",
     "phrases": ["dangerous", "warning", "they don't want you to know", "secret", "scam"]},
]

# The technique detector (detect.py, prompts in techniques.json). Runtime settings: POST /api/detector.
DETECT = {
    "backend": os.environ.get("LENS_DETECT_BACKEND") or detect.default_backend(),  # decision, router or keywords
    "note": float(os.environ.get("LENS_NOTE_THRESHOLD", "0.7")),
    "unsure": float(os.environ.get("LENS_UNSURE_THRESHOLD", "0.5")),
    "speechCheck": os.environ.get("LENS_SPEECH_CHECK", "0") == "1",  # off: no model call before Jev. On: decision noul "intelligible speech"
    # Real TikTok speech over music scores 0.2 to 0.6, noise and music about 0.13: below speechMin is no speech, and a
    # transcript of 1 to 3 words below speechShort is no speech (noise words such as "Vete" or "Da, amor").
    "speechMin": float(os.environ.get("LENS_SPEECH_MIN", "0.15")),
    "speechShort": float(os.environ.get("LENS_SPEECH_SHORT", "0.5")),
    "tiktokGate": os.environ.get("LENS_TIKTOK_GATE", "1") == "1",  # decision noul "is media (or TikTok) in front" before the vision read
    # "any": public media in any app or site is analysed. "tiktok": only TikTok (the first version). A session's own
    # config ("apps" in POST /api/config) wins over this default.
    "apps": os.environ.get("LENS_APPS", "any"),
    # The claim lane: a background fact check of a specific, checkable claim (kill switch: false here for every session,
    # or "factCheck": false in a session's POST /api/config).
    "factCheck": os.environ.get("LENS_FACT_CHECK", "1") == "1",
    # The fact checker of the automatic checks and of "deliver now": "agent" (our footnote-agent service: GPT-6 Luna with
    # web search through the Hostinger AI Router) or "single" (one OpenRouter call with web search). The other one
    # answers only when the first fails.
    "autoChecker": os.environ.get("LENS_AUTO_CHECKER", "agent"),
    "pressChecker": os.environ.get("LENS_PRESS_CHECKER", "agent"),
    # The 10 technique questions and technique notes (the first product). Off: each call asks only the claim gate.
    "techniques": detect.TECHNIQUES_ON,
}
SPEECH_Q = {"type": "noul", "instructions": "Does this audio clip contain intelligible human speech: words a listener can understand?",
            "criteria": {"true": "A person speaks clear words in any language, also over music.",
                         "false": "Only music, singing without clear words, noise, sound effects, or mumbling too unclear to understand."}}
TIKTOK_Q = ("Is the app in front on this phone screen TikTok (the TikTok app, or tiktok.com in a browser)?",
            "The screen shows TikTok: a TikTok video feed, profile, comments sheet, search or another TikTok screen.",
            "Any other app or site: Instagram, YouTube, a news site, a search page, messages, settings, the home screen, "
            "the lock screen, or another app such as Footnote.")
# The claim lane, the product since 15 November. The decision call on each sentence and each on-screen text asks the
# claim gate (techniques.json: war, quote, claim; the 10 technique questions only with the setting "techniques"). Jev
# answers it, hedged to Gemini Decision after 1.5 s; the item says which one answered. A claim at or above CLAIM_P (and
# not quoted to debunk it) publishes a pending check item at once and goes to the fact checker: footnote-agent, our
# TypeScript agent on the VPS (GPT-6 Luna with web search through the Hostinger AI Router), else one OpenRouter call
# with its web plugin. Jev is the first gate: before it only the transcription runs. No screen frame is read and no
# other model is called until Jev passes a claim (watching). A result whose sources contradict the claim (stance "differ") is published with notify true
# when the check is done (SETTLE_SECONDS after the claim at the earliest); other stances are published silently (notify false).
# 0.7: the reactum demo claim scores 0.74 on Gemini Decision; 17 of 78 test lines pass, opinions and questions score
# below 0.2 (PERF.md).
CLAIM_P = float(os.environ.get("LENS_CLAIM_P", "0.7"))
# The quote gate rises for a neutral report ("the ministry said ...") and for a debunk. Footnote alerts only when the
# speaker asserts the claim, so a report counts as quoted too. 0.7 with techniques.json 2026-11-15.2: precision 1.00,
# recall 0.96 on the dev split of evals/gate (0.86 and 0.99 at 0.9 with the old wording). It misses a false claim that
# names an authority as proof ("the ECB said so", quote 0.79 to 0.82).
CLAIM_QUOTE_MAX = 0.7
# The Jev same-video score: another video scores at most 0.10 and the same video at least 0.17 on evals/boundary
# (185 pairs). At 0.15 no cut inside a video is read as a new video (0.088 of the same pairs at 0.5).
SAME_T = float(os.environ.get("FOOTNOTE_SAME_T", "0.15"))
PUBLIC_MIN = 0.5  # the public affairs gate: football scores and recipe facts are claims too, but not ours
NOW_CLAIM_P = 0.5  # "deliver now" with no check yet: the most checkable claim of the last NOW_CLAIM_SECONDS at or above this
NOW_CLAIM_SECONDS = 30
CLAIMS_PER_VIDEO = 2
CHECKS_PER_HOUR = int(os.environ.get("LENS_CHECKS_PER_HOUR", "30"))  # for the whole server
CHECK_DEADLINE = 90
# Before 15 November a held "differ" result waited for the next video. Now SETTLE_SECONDS (below) delivers it when the
# check is done. What stays: nothing goes out while the broadcast is off or listening is stopped (then "deliver now"
# still returns it); the next video (another account) or LEAVE_SECONDS of no media deliver it before SETTLE_SECONDS.
DELIVER_SECONDS = 180
# Henrik, 15 November: a result shows when its check is done, also while the claim's video still plays. It waits only
# until SETTLE_SECONDS after the claim, so that the last speaker question sees the sentences that follow the claim (a
# debunk states the claim first and refutes it next). The next video or a left video delivers it before that.
SETTLE_SECONDS = float(os.environ.get("FOOTNOTE_SETTLE_SECONDS", "8"))
LEAVE_SECONDS = 8
CHECK_CACHE_SECONDS = 6 * 3600  # a claim checked before (same normalised text) answers from the store
CHECK_MODEL = os.environ.get("LENS_CHECK_MODEL", "google/gemini-3.1-flash-lite")
CHECK_RESULTS = 3  # web results per single call
# Exa gives the article URLs as annotations (about 0.008 USD a job); the native Google search gave only redirect links
# or bare domains (about 0.028 USD), but newer reports.
CHECK_ENGINE = os.environ.get("LENS_CHECK_ENGINE", "exa")
AGENT_URL = os.environ.get("LENS_AGENT_URL", "https://footnote-agent.srv2159196.hstgr.cloud/api/check")
AGENT_KEY_FILE = os.environ.get("LENS_AGENT_KEY_FILE", "/run/lens/agent.env")
AGENT_TIMEOUT = int(os.environ.get("LENS_AGENT_TIMEOUT", "30"))
ASSERT_MIN = 0.5  # a "differ" reaches the Island only when the speaker makes the claim (the speaker question)
STANCES = ("differ", "support", "mixed", "none")
VERDICT_WORDS = re.compile(r"\b(true|false|untrue|fake\w*|lies?|lying|debunk\w*|fabricat\w*|hoax\w*)\b", re.I)
# Social media and video sites are never a source; nor is the account of the video itself.
SOCIAL = re.compile(r"(^|\.)(tiktok|instagram|facebook|fb|x|twitter|youtube|youtu|reddit|t|telegram|vk|ok|threads|snapchat|pinterest|"
                    r"linkedin|rumble|bitchute|odysee|gab|truthsocial|quora|dzen|rutube)\.[a-z.]+$")
CHECK_PROMPT = (
    "A short video says this: \"%s\"\n%s"
    "Check this one claim against established sources on the web: news agencies, major newspapers, fact-checkers, governments, "
    "courts, international organisations, research. Search at most 3 times. Never use social media or the video's own account "
    "as a source. Then reply with JSON only:\n"
    '{"claim": the claim as one short, complete statement of at most 60 characters, such as "Zelenskyy owns a $20 million '
    'mansion in Florida",\n "stance": "differ" when at least 2 independent established sources (fact-checkers count) say this specific claim is '
    'incorrect, rate it false or misleading, or report facts that contradict it; "support" when the sources report the same '
    'thing; "mixed" when the sources disagree with each other, or you are in doubt; "none" when you found nothing about it,\n'
    ' "line": at most 90 characters on what the sources report, such as "Reuters and BBC report 12, not 40." or '
    '"A 100-year partnership was signed in January 2025." or "No source found." Do not start with "Sources". '
    'Never use the words true, false, fake, lie or debunk,\n'
    ' "sources": the pages you used, at most 3, each with the article\'s own URL (not a search or redirect link): '
    '[{"title", "url", "date" as YYYY-MM-DD}]}\n'
    "Today is %s."
)
TEXT_DEDUPE_SECONDS = 30  # a final sentence of /api/text that repeats within this long (same video) is the same one
PLAY_SECONDS = 90  # checks of the same video within this long belong to one play: a later play checks the claim again
FLAP_SECONDS = 20  # the same claim within this long is the same play also when the video identity changed meanwhile
NOW_KEEP_SECONDS = 30 * 60  # "deliver now" returns the newest finished check of the session for this long (only a reset clears it)
CHECKS_KEEP_SECONDS = 3600  # a session's checks are kept this long, also when the session is idle
PAUSE_EVIDENCE_SECONDS = 5  # reads without media pause the session only after this long in a row (a private screen pauses at once)
UNPAUSE_READS = 2  # media reads in a row that end a pause
MIN_WORDS = 4  # a shorter sentence is shown as a line but not judged (too little to name a technique)
NOW_SECONDS = 15  # "Footnote now" judges the sentences of this many seconds and the screen text
NO_NOTE = "No note. Nothing here looks like a technique."
PRIVATE_TEXT_Q = {"type": "noul", "instructions": "Is this text from a private or sensitive phone screen rather than public media?",
                  "criteria": {"true": "Messages or chats, mail, banking or payments, passwords or sign-in forms, health data, "
                                       "settings, a home screen, or a person typing a private text.",
                               "false": "A public video, reel, social post, news article, livestream, or its captions and comments."}}
MEDIA_Q = ("Does this phone screen show public media content: a video, a reel, a public social post, a news article or a livestream?",
           "A video or reel plays (also paused), or a public post, an article or a livestream fills the screen, in any app or browser.",
           "A private or sensitive screen (messages, chats, mail, banking, payments, passwords, sign-in forms, health, photos, "
           "settings, the home screen, the lock screen, a keyboard typing text), a menu or search page, or the Footnote app "
           "(a fact-check page with a claim, an asterisk and sources, or its home page).")
SUBTITLE_WAIT = 8  # on-screen text waits this long for the transcript, so burned-in subtitles of the speech are not judged twice
SUBTITLE_OVERLAP = 0.6  # share of the on-screen words that the video's recent speech contains, to count as a subtitle
# The sentence buffer: chunk transcripts join in seq order, and the detector sees complete sentences only.
FLUSH_SECONDS = 8  # an open sentence is judged as it is when no chunk continued it for this long
SKIP_SECONDS = 3  # a seq that never arrived is skipped when later chunks wait this long
STUCK_SECONDS = 45  # a chunk that arrived but has no transcript after this long is skipped
HOLD_SECONDS = 10  # audio that arrives while the screen shows no media (a private screen) waits this long for a media read
TRANSCRIBE_DEADLINE = 20  # total seconds for one transcription (the socket timeout alone does not limit a slow answer)
VISION_DEADLINE = 10  # total seconds for one screen read; a later answer is dropped
# Hedged requests: a second identical request after this many seconds without an answer, the first answer wins (0: off).
TRANSCRIBE_HEDGE = float(os.environ.get("LENS_TRANSCRIBE_HEDGE", "2.5"))
VISION_HEDGE = float(os.environ.get("LENS_VISION_HEDGE", "4"))
DUPLICATE_SECONDS = 90  # a note on the same sentence of the same video within this time is a duplicate
IDLE_SECONDS = int(os.environ.get("LENS_IDLE_SECONDS", "600"))  # per-session state is freed after this long without a request
END = ".!?\u2026"

# Tested on 42 real chunks (stoa replay of an English and a Russian TikTok): "Never guess words" made real speech come
# back as [no speech] (2 of 31 English chunks); without a language hint, short Russian lines came back as Portuguese
# or Spanish ("Dá, amor."). The hint names the language of the video's earlier speech.
PROMPT = (
    "Transcribe the speech in this audio verbatim, in its original language, with punctuation. Do not translate. "
    "Reply with the transcript only. Speech can be quiet, fast or cut off at the start or end of the clip: transcribe it. "
    "Reply with exactly [no speech] only when nobody speaks (only music, noise or sound effects)."
)
HINT = " Earlier speech in this video was in %s."
STRICT_HINT = " The speech is in %s: write it in that language and its own alphabet."

# The one language model read of a screen (account, caption): only for the frame of a claim that Jev passed and for a
# new video that Jev saw while that claim is under check. Jev answers every other frame question.
VISION_MODEL = os.environ.get("LENS_VISION_MODEL", "gpt-6-luna")
# The hedged second request goes to the same model on OpenRouter: the Hostinger router's latency rose to 15 to 30 s
# for any call under load, while OpenRouter answered in about 1 s (PERF.md). Used only while OpenRouter has credits.
OPENROUTER_PREFIX = "openrouter:"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
HEDGE_MODEL = os.environ.get("LENS_HEDGE_MODEL", OPENROUTER_PREFIX + "google/" + MODEL)
VISION_HEDGE_MODEL = os.environ.get("LENS_VISION_HEDGE_MODEL", OPENROUTER_PREFIX + "openai/" + VISION_MODEL)
# Any app: the read names the app and the kind of screen. Public media (a video, a post, an article, a livestream) is
# analysed; a private screen is dropped at once, and the read itself returns no text of it.
MEDIA_KINDS = ("video", "post", "article", "live")
SCREEN_FIELDS = (
    '{"app": the app or website in front, lowercase, such as "tiktok", "instagram", "youtube", "x", "telegram", "facebook", '
    '"reddit", a news site name, "safari", "messages", "mail", "settings", "home screen", "footnote", or "unknown",\n'
    ' "kind": one of "video" (a video or reel plays, also a paused one), "post" (a public social post with text or images), '
    '"article" (a news article or a blog page), "live" (a livestream), "private" (private or sensitive: messages and chats, '
    'mail, banking and payments, passwords and sign-in forms, health, the photo library, settings, the home screen or lock '
    'screen, a keyboard typing a private text), "other" (anything else, such as a menu or a search page),\n'
    ' "author": the creator, channel, account or publisher of the content in front, as the app shows it, or "". Never a '
    'watermark, logo or other text inside the video itself,\n'
    ' "title_or_description": the title, caption or headline of the content in front, without the hashtags, or "",\n'
    ' "hashtags": a list of the hashtags in the caption, without "#",\n'
    ' "on_screen_text": the text of the content itself: text burned into the video, the text of the post, or the first '
    'paragraphs of the article (not the app buttons, not the caption), or "",\n'
    ' "likes": the like count as shown, such as "43.4M", or "",\n'
    ' "comments": the comment count as shown, or "",\n'
    ' "shares": the share count as shown, or "",\n'
    ' "comments_visible": true if a comment list or comment sheet is open,\n'
    ' "visible_comments": a list of the comment texts you can read when comments are open, else []}\n'
    'Copy text exactly as shown, in its original language. If kind is "private" or "other", leave every other field empty: '
    'never copy text of a private screen. The Footnote app (a fact-check page: a claim with an asterisk, "Sources contradict '
    'this", a source list) is app "footnote", kind "other". Footnote\'s Dynamic Island or banner at the top of another app '
    'is not part of the content: never copy its text.'
)
# The model's key -> the key of the screen state (the state keeps the names of the TikTok version).
SCREEN_MAP = {"author": "handle", "title_or_description": "description", "hashtags": "hashtags", "on_screen_text": "onScreenText",
              "likes": "likes", "comments": "comments", "shares": "shares", "comments_visible": "commentsOpen",
              "visible_comments": "visibleComments"}
FRAME_PROMPT = "This is one frame of a phone screen. Read it and reply with JSON only, no prose, with exactly these keys:\n" + SCREEN_FIELDS
OCR_PROMPT = (
    "These are the text lines that on-device OCR read from one frame of a phone screen. Each line has a box (x, y, w, h) "
    "from 0 to 1, with the origin at the top left. Apps differ: TikTok and Reels show the creator and the caption at the "
    "bottom left and the counts in a column at the right; YouTube shows the title under the video; a news site shows a "
    "headline at the top. Reply with JSON only, no prose, with exactly these keys:\n" + SCREEN_FIELDS + "\n\nOCR lines:\n"
)
SCREEN_KEYS = ("handle", "description", "hashtags", "onScreenText", "likes", "comments", "shares", "commentsOpen", "visibleComments")

FOUND_SECONDS = 10  # how long the Island shows "found" after a technique
PAUSE_SECONDS = 15  # a "not TikTok" screen result younger than this pauses the session
SCREEN_FRESH_SECONDS = 20  # the handle shows in the Island while the last TikTok frame is younger than this
VISION_GAP = 1.0  # do not read a new frame for a session less than this after the last answer
KEEP_FRAMES = 5
LIVE_SECONDS = 8  # a broadcast heartbeat younger than this means "listening"
APPLE_EPOCH = 978307200  # Swift's default Date coding counts seconds from 2001-01-01

os.makedirs(WAV_DIR, exist_ok=True)

lock = threading.Lock()
cond = threading.Condition(lock)
started_at = time.time()
heartbeats = collections.deque(maxlen=60)
logs = collections.deque(maxlen=300)
results = collections.deque(maxlen=1000)
chunks = collections.deque(maxlen=KEEP_WAVS)
counters = collections.Counter()
configs: "dict[str, dict]" = {}
push_status = {"sent": 0, "errors": 0, "last_error": "", "last_sent": "", "available": False}
push_sessions = {}  # session -> {"ok", "sent", "errors", "lastAt", "lastMode", "lastError"}: the APNs path per session
ISLAND_HOLD = 12  # s a delivered result stays on the Island
TURN_SECONDS = 4  # while a check runs, the lit asterisk turns one step this often
TURN_STEPS = 8  # at most this many turn steps per lit period, then it glows steady
island_turns = {}  # session -> {"since": when the logo lit or None, "base": turn steps before that}
STANCE_WORDS = {"differ": ("Sources contradict this", "Contradicted"), "support": ("Sources support this", "Sources agree"),
                "mixed": ("Sources are mixed", "Sources mixed"), "none": ("No sources found", "No sources")}
next_result_id = 0
next_arrival = 0
jobs: "queue.Queue[dict]" = queue.Queue(maxsize=40)
# A retried upload can arrive twice (the first try reached us after the phone gave up). Keys of recent chunks.
recent_chunks = collections.deque(maxlen=200)
# Screen reading. `frames` holds the last TikTok frames (JPEG in memory) and the last OCR texts, for /debug.
frames = collections.deque(maxlen=KEEP_FRAMES)
screen_log = collections.deque(maxlen=40)  # every read: parsed JSON, latency, credits (no image)
screens: "dict[str, dict]" = {}  # session -> now playing
vision_busy: "set[str]" = set()
vision_done: "dict[str, float]" = {}
frame_hash: "dict[str, str]" = {}
next_frame_id = 0
vision_jobs: "queue.Queue[dict]" = queue.Queue(maxsize=16)
# Router cost: (ts, session, kind, credits). kind is "audio" or "screen".
usage = collections.deque(maxlen=5000)
# Detector state.
asm: "dict[str, dict]" = {}  # session -> sentence buffer (asm_of): chunk transcripts in seq order, the open sentence
held: "dict[str, list]" = {}  # session -> audio jobs that arrived while paused, waiting for a TikTok screen read
pending_frames: "dict[str, dict]" = {}  # session -> the newest frame that came while a read was busy
last_frames: "dict[str, dict]" = {}  # session -> the newest frame that came while no claim was under check (unread, memory only)
ref_frames: "dict[str, bytes]" = {}  # session -> the frame of the video in front at its last language model read: Jev compares new frames with it
check_frames: "dict[str, bytes]" = {}  # check id -> the JPEG in front when Jev passed the claim, for the agent
spoken: "dict[str, collections.deque]" = {}  # session -> (ts, videoId, text) of raw chunk transcripts, for subtitles
recent: "dict[str, collections.deque]" = {}  # session -> (ts, videoId, sentence, before) of judged speech, for /api/now
texts: "dict[str, dict]" = {}
checks: "dict[str, list]" = {}  # session -> fact checks (the claim lane), in order
check_starts = collections.deque(maxlen=1000)  # start times of fact check jobs, for the hourly cap
check_pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="check")
check_pool_inner = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="checker")  # checkers and speaker questions
# The checkers' HTTP calls have their own threads: in router_pool they could wait behind slow transcription calls.
check_http = concurrent.futures.ThreadPoolExecutor(max_workers=12, thread_name_prefix="checkhttp")
seen_videos: "dict[str, list]" = {}  # session -> the identities of the run's last videos: a loop or a later claim of one is the same video
video_counter: "dict[str, int]" = {}  # session -> the last video number, so a reset or a new run never reuses one
next_check_id = 0
check_cache: "dict[str, dict]" = {}  # claim_key -> the finished check of that claim (any session)
check_stats = {"sentToChecker": 0, "cacheHits": 0, "delivered": 0, "stances": collections.Counter(),
               "agent": {"calls": 0, "ok": 0, "errors": 0, "lastError": "", "ms": collections.deque(maxlen=50)},
               "single": {"calls": 0, "ok": 0, "errors": 0, "lastError": "", "ms": collections.deque(maxlen=50)}}
langs: "dict[str, dict]" = {}  # session -> {"videoId", "recent": [languages of recent speech]}: the transcription hint
router_pool = concurrent.futures.ThreadPoolExecutor(max_workers=32, thread_name_prefix="router")
last_seen: "dict[str, float]" = {}  # session -> time of its last request; idle sessions are freed
detect_log = collections.deque(maxlen=150)  # every detection: the sentence, top 3, gates, backend, latency
detect_usage = collections.deque(maxlen=20000)  # (ts, session, kind, usd): decision calls (detect, speech, tiktok gate)
detect_pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="detect")
try:  # written by deploy.sh next to server.py; the deploy ships it, git does not track it
    COMMIT = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "BUILD")).read().strip()
except OSError:
    COMMIT = "unknown"


def now_iso(t=None):
    return time.strftime("%H:%M:%S", time.gmtime(t or time.time())) + "Z"


def rms_of(pcm: bytes) -> float:
    n = len(pcm) // 2
    if n == 0:
        return 0.0
    total = 0
    for (s,) in struct.iter_unpack("<h", pcm[: n * 2]):
        total += s * s
    return (total / n) ** 0.5 / 32768.0


def _ulaw_table():
    table = []
    for i in range(256):
        u = ~i & 0xFF
        exp = (u >> 4) & 7
        mag = (((u & 0x0F) << 3) + 0x84) << exp
        mag -= 0x84
        table.append(-mag if u & 0x80 else mag)
    return table


ULAW = _ulaw_table()


def classify(text: str):
    """Returns a list of hits: the rule plus the exact part of the text that matched."""
    low = text.lower()
    hits = []
    for rule in KEYWORD_RULES:
        found = [(low.find(p), p) for p in rule["phrases"] if low.find(p) >= 0]
        if found:
            i, phrase = min(found)  # the first place in the text where this technique shows
            hits.append({k: rule[k] for k in ("name", "label", "short", "why", "explain")} | {"mark": text[i : i + len(phrase)], "at": i})
    hits.sort(key=lambda h: h["at"])
    return hits


def tail(text: str, limit: int) -> str:
    """The end of the text, cut at a word, so the newest words stay visible."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    space = cut.find(" ")
    return "…" + (cut[space + 1 :] if 0 <= space < 30 else cut)


def sentence_of(text: str, mark: str, limit: int) -> str:
    """The sentence that holds the marked words, so the Island marks one sentence, like the page.
    Falls back to a window around the mark when the sentence is longer than `limit`."""
    flat = " ".join(text.split())
    i = flat.lower().find(mark.lower()) if mark else -1
    if i < 0:
        return around(flat, mark or "", limit)
    start = max(flat.rfind(c, 0, i) for c in ".!?\u2026") + 1
    ends = [j for j in (flat.find(c, i + len(mark)) for c in ".!?\u2026") if j >= 0]
    end = min(ends) + 1 if ends else len(flat)
    sentence = flat[start:end].strip()
    return sentence if len(sentence) <= limit else around(sentence, mark, limit)


def around(text: str, mark: str, limit: int) -> str:
    """A window of the text around the marked part, so the mark is always visible."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    i = max(0, text.lower().find(mark.lower()))
    start = max(0, min(i - (limit - len(mark)) // 3, len(text) - limit))
    end = min(len(text), start + limit)
    if start > 0:
        space = text.find(" ", start, i if i > start else start + 1)
        start = space + 1 if space >= 0 else start
    if end < len(text):
        space = text.rfind(" ", i + len(mark), end)
        end = space if space > 0 else end
    return ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")


def to_pcm(body: bytes, rate: int, encoding: str):
    """Return (pcm16 mono little endian, sample rate). Accepts raw PCM, mu-law or a WAV file."""
    if body[:4] == b"RIFF":
        with wave.open(io.BytesIO(body)) as w:
            if w.getsampwidth() != 2:
                raise ValueError("WAV must be 16-bit")
            frames = w.readframes(w.getnframes())
            ch = w.getnchannels()
            if ch > 1:
                samples = struct.unpack("<%dh" % (len(frames) // 2), frames)
                mono = [sum(samples[i : i + ch]) // ch for i in range(0, len(samples), ch)]
                frames = struct.pack("<%dh" % len(mono), *mono)
            return frames, w.getframerate()
    if encoding == "mulaw":
        return struct.pack("<%dh" % len(body), *[ULAW[b] for b in body]), rate
    return body[: len(body) // 2 * 2], rate


def wav_bytes(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


hedge_streak: "collections.Counter[str]" = collections.Counter()  # kind -> hedge wins in a row
HEDGE_STREAK = 3
FAST_HEDGE = 0.3


def router_chat(model, content, json_mode=False, timeout=30, hedge=None, kind="chat"):
    """One chat completion within `timeout` seconds in total. Returns ({"text", "credits", "model"}, None) or (None, error).
    urllib's timeout limits each socket read, not the whole answer (a screen read took 24 s with timeout=20), so the
    call runs in a pool thread and a late answer is dropped. `hedge` is (seconds, model): when the first call has no
    answer after that long, the same request goes to that model too (the router's latency per model varies from 1 s to
    over 20 s under load), and the first good answer wins."""
    t0 = time.time()
    deadline = t0 + timeout
    futures = {router_pool.submit(_router_chat, model, content, json_mode, timeout): model}
    # While the hedge won the last HEDGE_STREAK calls (the first model is slow now), the second request starts at once.
    hedge_at = t0 + (FAST_HEDGE if hedge_streak[kind] >= HEDGE_STREAK else hedge[0]) if hedge else None
    second, err = None, None
    while True:
        now = time.time()
        if hedge_at is not None and (now >= hedge_at or not futures):
            hedge_at = None  # also at once when the first call failed fast
            second = router_pool.submit(_router_chat, hedge[1], content, json_mode, max(1.0, deadline - now))
            futures[second] = hedge[1]
            with lock:
                counters[kind + "_hedged"] += 1
        if not futures or now >= deadline:
            break
        until = min(deadline, hedge_at) if hedge_at is not None else deadline
        done, _ = concurrent.futures.wait(list(futures), timeout=max(0.0, until - now), return_when=concurrent.futures.FIRST_COMPLETED)
        for f in done:
            m = futures.pop(f)
            out, e = f.result()
            if not e:
                with lock:
                    if f is second:
                        counters[kind + "_hedge_won"] += 1
                        hedge_streak[kind] += 1
                    else:
                        hedge_streak[kind] = 0
                return out | {"model": m, "hedged": second is not None}, None
            err = e
    with lock:
        counters["router_deadline" if not err or futures else "router_failed"] += 1
    return None, ("router deadline: no answer within %d s" % timeout) if futures or not err else err


def _router_chat(model, content, json_mode, timeout):
    """One chat completion on the Hostinger AI Router, or on OpenRouter for a model named "openrouter:<model>"."""
    url, key = ROUTER_URL + "/chat/completions", ROUTER_KEY
    if model.startswith(OPENROUTER_PREFIX):
        model, url, key = model[len(OPENROUTER_PREFIX):], OPENROUTER_CHAT_URL, detect.openrouter_key()
        if not key or not detect.decision_ok():
            return None, "openrouter: no key or no credits (decision down)"
    if not key:
        return None, "AI_ROUTER_API_KEY is not set"
    payload = {"model": model, "messages": [{"role": "user", "content": content}]}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        # Cloudflare in front of the router rejects the default Python-urllib User-Agent (error 1010).
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": "lens-test-server/1", "X-Title": "Footnote"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = detect._read_json(r)  # the router ends the stream seconds after the answer: do not wait for it
        text = (data["choices"][0]["message"]["content"] or "").strip()
        usage = data.get("usage") or {}
        if url == OPENROUTER_CHAT_URL and usage.get("cost"):
            with lock:
                detect_usage.append((time.time(), "", "openrouter_chat", float(usage["cost"])))
        return {"text": text, "credits": usage.get("credits_charged")}, None
    except urllib.error.HTTPError as e:
        return None, "router HTTP %s: %s" % (e.code, e.read()[:300].decode(errors="replace"))
    except Exception as e:  # noqa: BLE001 - report every failure on /debug
        return None, "router error: %r" % (e,)


def transcribe(wav: bytes, hint=""):
    return router_chat(MODEL, [
        {"type": "text", "text": PROMPT + hint},
        {"type": "input_audio", "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"}},
    ], timeout=TRANSCRIBE_DEADLINE, hedge=(TRANSCRIBE_HEDGE, HEDGE_MODEL) if TRANSCRIBE_HEDGE else None, kind="transcribe")


ENGLISH = set("the and that this is are was were of to in it you they we he she not but what why with for have has on be".split())


def lang_of(text):
    """Russian, Ukrainian, English or None, from the alphabet and common words. Enough for a hint, not more."""
    letters = [c for c in str(text).lower() if c.isalpha()]
    if len(letters) < 4:
        return None
    cyr = sum("\u0400" <= c <= "\u04ff" for c in letters) / len(letters)
    if cyr > 0.6:
        if any(c in "\u0456\u0457\u0454\u0491" for c in letters):  # і ї є ґ: only Ukrainian
            return "Ukrainian"
        if any(c in "\u044b\u044d\u044a\u0451" for c in letters):  # ы э ъ ё: only Russian
            return "Russian"
        return "Ukrainian or Russian"
    if cyr < 0.1:
        words = re.findall(r"[a-z']+", str(text).lower())
        if words and sum(w in ENGLISH for w in words) / len(words) >= 0.15:
            return "English"
        return "Latin"
    return None


def hint_lang(job):
    """The language of the video's recent speech, else of its caption and on-screen text. Lock held."""
    entry = langs.get(job["session"])
    if entry and entry["videoId"] == job.get("videoId") and entry["recent"]:
        recent = entry["recent"][-4:]
        top = max(set(recent), key=recent.count)
        return top if top != "Latin" else None
    sc = screens.get(job["session"])
    if sc and sc["videoId"] == job.get("videoId"):
        lang = lang_of(sc["description"] + " " + sc["onScreenText"])
        return lang if lang != "Latin" else None
    return None


def remember_lang(job, text):
    """Lock held."""
    lang = lang_of(text)
    if not lang:
        return
    entry = langs.get(job["session"])
    if not entry or entry["videoId"] != job.get("videoId"):
        entry = langs[job["session"]] = {"videoId": job.get("videoId"), "recent": []}
    entry["recent"] = (entry["recent"] + [lang])[-6:]


def parse_json(text: str):
    """The model's JSON, also when it is in a ```json fence."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the answer")
    return json.loads(text[start : end + 1])


def read_screen(jpeg=None, lines=None):
    """Reads one screen: a JPEG (path S1) or OCR lines (path S2). Returns (parsed, credits, error)."""
    if jpeg is not None:
        content = [{"type": "text", "text": FRAME_PROMPT},
                   {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}]
    else:
        listing = "\n".join("[x %.2f y %.2f w %.2f h %.2f] %s" % (l.get("x", 0), l.get("y", 0), l.get("w", 0), l.get("h", 0), l.get("text", ""))
                            for l in lines[:120])
        content = [{"type": "text", "text": OCR_PROMPT + (listing or "(no text)")}]
    out, err = router_chat(VISION_MODEL, content, json_mode=True, timeout=VISION_DEADLINE, hedge=(VISION_HEDGE, VISION_HEDGE_MODEL) if VISION_HEDGE else None,
                           kind="vision")
    if err:
        return None, None, err
    try:
        parsed = parse_json(out["text"])
    except Exception as e:  # noqa: BLE001
        return None, out["credits"], "bad screen JSON: %r in %r" % (e, out["text"][:200])
    return clean_screen(parsed), out["credits"], None


def clean_screen(parsed):
    """The model's screen JSON as the screen state's fields, plus `app`, `kind` and `media`. Accepts the old TikTok keys."""
    app = str(parsed.get("app", "") or "unknown").lower().strip()[:40]
    kind = str(parsed.get("kind", "") or "").lower().strip()
    if kind not in MEDIA_KINDS + ("private", "other"):
        kind = "video" if app == "tiktok" else "other"
    clean = {"app": app, "kind": kind, "media": kind in MEDIA_KINDS}
    for src, k in SCREEN_MAP.items():
        v = parsed.get(src, parsed.get(k))
        if k in ("hashtags", "visibleComments"):
            clean[k] = [str(x)[:200] for x in v][:20] if isinstance(v, list) else []
        elif k == "commentsOpen":
            clean[k] = bool(v)
        else:
            clean[k] = str(v or "")[:400]
    if not clean["media"]:
        clean = {"app": app, "kind": kind, "media": False} | {k: [] if k in ("hashtags", "visibleComments") else False if k == "commentsOpen" else ""
                                                              for k in SCREEN_KEYS}
    return clean


def add_usage(session, kind, credits):
    """Records router credits. Call with the lock held."""
    if credits:
        usage.append((time.time(), session, kind, float(credits)))
        counters["credits_" + kind] = round(counters.get("credits_" + kind, 0) + float(credits), 4)


def cost_summary(session=None, window=60):
    """Credits in the last `window` seconds and in total, per kind. Lock held."""
    now = time.time()
    out = {"window_s": window, "audio_last_min": 0.0, "screen_last_min": 0.0, "audio_total": 0.0, "screen_total": 0.0,
           "screen_reads_last_min": 0, "audio_reads_last_min": 0}
    for ts, s, kind, c in usage:
        if session and s != session:
            continue
        out[kind + "_total"] = out.get(kind + "_total", 0.0) + c  # kinds: audio, screen, detect (router backend)
        if now - ts < window:
            out[kind + "_last_min"] = out.get(kind + "_last_min", 0.0) + c
            out[kind + "_reads_last_min"] = out.get(kind + "_reads_last_min", 0) + 1
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}


def video_of(session):
    """The video in front now: its id and creator, for grouping results by video in the app. Lock held."""
    sc = screens.get(session)
    if not sc or not sc["videoId"]:
        return {}
    return {"videoId": sc["videoId"], "handle": sc["handle"][:60], "author": sc["handle"][:60], "app": sc.get("mediaApp") or sc["app"]}


def add_result(job, text, latency, credits, fake=False, source="audio", det=None, speech=None, extra=None):
    """Appends a transcript (source "audio"), or a technique found in the on-screen text (source "screen").
    `det` is the detector summary (detect_text). Without it, the keyword rules run for a fake result or the
    "keywords" backend. Call with the lock held."""
    global next_result_id
    if speech is None:
        speech = source == "audio" and bool(text) and text != "[no speech]"
    keywords = det is None and (fake or DETECT["backend"] == "keywords") and (speech or source == "screen")
    hits = classify(text) if keywords else []
    first = hits[0] if hits else None
    note = bool(det and det["note"])
    unsure = bool(det and det["unsure"])
    # The video the audio came from (stamped when it arrived), or the one in front now.
    video = {k: job[k] for k in ("videoId", "handle", "author", "app") if job.get(k) is not None} or video_of(job["session"])
    sc_now = screens.get(job["session"])
    if sc_now and sc_now["videoId"] and video.get("videoId") not in (None, sc_now["videoId"]) and job.get("arrival", 0) > sc_now["videoArrival"]:
        video = video_of(job["session"])  # it arrived after the first read of the video in front
    kind = "speech" if source == "audio" else "screen"
    duplicate = find_duplicate(job["session"], video.get("videoId"), det["sentence"]) if (note or unsure) and not fake else None
    if duplicate:
        # The same sentence of the same video already has a note (burned-in subtitles and speech): keep one note
        # and record both sources on it. A screen duplicate adds nothing; a speech duplicate stays as a plain line.
        duplicate["sources"] = sorted(set(duplicate.get("sources") or ["speech" if duplicate["source"] == "audio" else "screen"]) | {kind})
        counters["duplicate_notes"] += 1
        logs.append({"t": now_iso(), "source": "server", "session": job["session"],
                     "line": "duplicate %s note of result %d: %s" % (kind, duplicate["id"], det["sentence"][:80])})
        if source == "screen":
            return None
        note = unsure = False
    info = detect.BY_ID.get(det["top"]) if note else None
    next_result_id += 1
    result = {
        "id": next_result_id,
        "t": now_iso(),
        "ts": time.time(),
        "arrival": job["arrival"],
        "session": job["session"],
        "seq": job["seq"],
        "chunk": job.get("name", ""),
        "seconds": job.get("seconds", 0),
        "rms": job.get("rms", 0),
        "text": text,
        "speech": speech,
        "source": source,
        "techniques": [h["name"] for h in hits],
        "technique": first["name"] if first else None,
        "label": first["label"] if first else None,
        "short": first["short"] if first else None,
        "why": first["why"] if first else None,
        "explain": first["explain"] if first else None,
        "mark": first["mark"] if first else None,
        "latency": round(latency, 2),
        "credits": credits,
        "fake": fake,
        "received": job.get("received"),
        "unsure": unsure,
        "backend": "keywords" if keywords else (det["backend"] if det else None),
    }
    if det:
        result |= {
            "probs": det["probs"], "confidence": det["confidence"], "gates": det["gates"], "detect_ms": det["detect_ms"],
            "model": det["model"], "sentences": det["sentences"], "specVersion": detect.SPEC["version"],
        }
        if note or unsure:
            # The exact sentence. A sentence that began in the chunk before is marked by its part in this chunk.
            result["mark"] = det["mark"]
            result["sentence"] = det["sentence"]
        if note:
            result |= {"technique": det["top"], "techniques": det["techniques"], "label": info["label"], "short": info["short"],
                       "why": info["why"], "explain": info["explain"]}
        elif unsure:
            result["candidate"] = det["top"]  # not shown as a technique; the app draws a dotted line
        if note or unsure:
            result["sources"] = [kind]
        if duplicate:
            result["duplicateOf"] = duplicate["id"]
        if det.get("error"):
            result["detect_error"] = det["error"]
        if det.get("fallback"):
            result["detectFallback"] = det["fallback"]
    result |= extra or {}
    result |= video
    sc = screens.get(job["session"])
    if (first or note or unsure) and sc and video.get("videoId") == sc["videoId"]:
        # What was on the screen, for the note's detail view in the app (TikTok only; other apps are never kept).
        result |= {"onScreen": sc["onScreenText"][:240], "description": sc["description"][:240]}
    results.append(result)
    if det and not duplicate and not fake:
        maybe_check(result, det)
    counters["transcripts" if source == "audio" else "screen_flags"] += 1
    if note:
        counters["notes"] += 1
    if unsure:
        counters["unsure"] += 1
    cond.notify_all()
    return result


# MARK: Technique detection


def split_sentences(text):
    """The sentences of a transcript. A piece of 1 or 2 words joins the sentence before it (or after it)."""
    flat = " ".join(str(text).split())
    out = []
    for part in re.split(r"(?<=[.!?…])[\"'»”)]?\s+", flat):
        part = part.strip()
        if not part:
            continue
        if out and len(out[-1].split()) < 3:
            out[-1] += " " + part
        elif out and len(part.split()) < 3 and part[-1] in END:
            out[-1] += " " + part
        else:
            out.append(part)
    return out


def complete(sentence):
    """True when the sentence ends with an end mark, so it was not cut at the end of the chunk."""
    s = sentence.rstrip("\"'»”) ")
    return bool(s) and s[-1] in END


def record_detection(session, source, items, preds, cfg):
    """Logs each sentence's detection and its cost for /debug. Lock held."""
    for it, p in zip(items, preds):
        top3 = sorted(p["probs"].items(), key=lambda kv: -kv[1])[:3]
        detect_log.append({"t": now_iso(), "session": session, "source": source, "text": it["text"][:300], "top3": top3,
                           "gates": p["gates"], "note": p["note"], "unsure": p["unsure"], "backend": p["backend"],
                           "ms": p["latency_ms"], "error": p.get("error", "")})
        counters["detect_calls_" + p["backend"]] += 1
        if p.get("error"):
            counters["detect_errors"] += 1
            logs.append({"t": now_iso(), "source": "server", "session": session, "line": "detect error: " + p["error"][:300]})
        if p.get("cost_usd"):
            detect_usage.append((time.time(), session, "detect", float(p["cost_usd"])))
        if p.get("credits"):
            add_usage(session, "detect", p["credits"])


def summarize(items, preds, cfg, t0):
    """One detector summary for a result: the sentence with the strongest note, else the strongest unsure one,
    else the strongest top probability (kept for /debug)."""
    def p_top(p):
        return p["probs"].get(p["top"], 0.0) if p["top"] else 0.0
    order = sorted(range(len(preds)), key=lambda i: (preds[i]["note"], preds[i]["unsure"], p_top(preds[i])), reverse=True)
    i = order[0]
    best = preds[i]
    errors = [p["error"] for p in preds if p.get("error")]
    return {
        "note": best["note"], "unsure": best["unsure"], "top": best["top"], "probs": best["probs"], "gates": best["gates"],
        "confidence": best["confidence"], "backend": best["backend"], "model": best["model"],
        "techniques": [t for t, v in sorted(best["probs"].items(), key=lambda kv: -kv[1]) if v >= cfg["note"]],
        "sentence": items[i]["text"], "mark": items[i].get("mark", items[i]["text"]), "index": i, "fallback": best.get("fallback"),
        "detect_ms": round((time.time() - t0) * 1000),
        "sentences": [{"text": it["text"][:240], "top": p["top"], "p": round(p_top(p), 3), "gates": p["gates"], "note": p["note"],
                       "unsure": p["unsure"], "ms": p["latency_ms"]} for it, p in zip(items, preds)],
        "error": errors[0] if errors else None,
    }


def detect_text(job, text, backend=None):
    """Runs the detector on each sentence of one text as it is (the /api/fake test path). Returns a summary or None."""
    t0 = time.time()
    sentences = split_sentences(text)
    if not sentences:
        return None
    with lock:
        cfg = dict(DETECT) | ({"backend": backend} if backend else {})
        sc = screens.get(job["session"])
    items = [{"id": n, "text": s, "mark": s, "source": "speech", "screen_text": sc["onScreenText"] if sc else "",
              "description": sc["description"] if sc else "", "before": tail(" ".join(sentences[:n]), 400)} for n, s in enumerate(sentences)]
    preds = detect.detect(items, backend=cfg["backend"], threshold=cfg["note"], unsure=cfg["unsure"])
    with lock:
        record_detection(job["session"], "speech", items, preds, cfg)
    return summarize(items, preds, cfg, t0)


# MARK: Sentence buffer
#
# Chunks of 4 s cut sentences, and two workers can finish chunks out of order. Each session keeps its chunk
# transcripts in seq order and judges complete sentences only. The open end of the newest chunk waits for the next
# chunk; it is judged as it is after FLUSH_SECONDS, at a chunk without speech, at a pause and at a new video.
# Each judged sentence becomes one result, so its `text` and `mark` are the whole sentence.


def asm_of(session):
    """Lock held."""
    return asm.setdefault(session, {"next": None, "ready": {}, "arrived": {}, "tail": None, "history": [], "historyVideo": None,
                                    "out": [], "emit": threading.Lock()})


def expect_chunk(session, seq):
    """An audio chunk arrived: the buffer waits for its transcript (or its gap). Lock held."""
    a = asm_of(session)
    if a["next"] is None:
        a["next"] = seq
    elif seq + 3 < a["next"]:
        # The phone started a new broadcast and its seq starts again: judge the open sentence, then start over.
        flush_tail(a)
        a["ready"].clear()
        a["arrived"].clear()
        a["next"] = seq
    a["arrived"][seq] = time.time()


def emit_sentence(a, text, jobs, extra):
    video = jobs[-1].get("videoId")
    if video != a["historyVideo"]:
        a["history"], a["historyVideo"] = [], video
    a["out"].append({"kind": "speech", "text": text, "jobs": jobs, "before": " ".join(a["history"][-2:]), "extra": extra})
    a["history"] = (a["history"] + [text])[-3:]


def flush_tail(a):
    """Judges the open sentence as it is. Lock held."""
    t = a["tail"]
    if t:
        a["tail"] = None
        counters["sentences_flushed"] += 1
        emit_sentence(a, t["text"], t["jobs"], t["extra"])


def consume(a, entry):
    """Adds the next chunk in seq order to the buffer. Lock held."""
    job = entry["job"]
    if not entry["speech"]:
        flush_tail(a)  # a chunk without speech is a pause in the speech
        if entry.get("result"):
            a["out"].append({"kind": "chunk", "job": job, "text": entry["text"], "extra": entry["extra"]})
        return
    t = a["tail"]
    if t and t["videoId"] is not None and job.get("videoId") is not None and t["videoId"] != job.get("videoId"):
        flush_tail(a)
        t = None
    # The model often ends a chunk with a period also inside a sentence ("...which they claimed was unlawfully."). So the
    # last sentence of a chunk always waits for the next chunk: when that one starts in lower case, it continues the
    # sentence and the period goes; else the waiting sentence is complete as it is.
    first = next((c for c in entry["text"] if c.isalpha()), "")
    if t and complete(t["text"]) and not first.islower():
        flush_tail(a)
        t = None
    head = t["text"].rstrip(END) if t else ""
    text = (head + " " + entry["text"]) if t else entry["text"]
    jobs = (t["jobs"] if t else []) + [job]
    sentences = split_sentences(text)
    cut = None
    if sentences and len(sentences[-1]) < 300:
        cut = sentences.pop()
    for i, sentence in enumerate(sentences):
        emit_sentence(a, sentence, jobs if i == 0 else [job], entry["extra"])
    a["tail"] = None
    if cut:
        a["tail"] = {"text": cut, "jobs": [job] if sentences else jobs, "ts": time.time(), "extra": entry["extra"],
                     "videoId": job.get("videoId", t["videoId"] if t else None)}


def drain(a, now):
    """Consumes the chunks that are next in seq order, and skips a seq that never comes. Lock held."""
    while a["next"] is not None:
        n = a["next"]
        if n in a["ready"]:
            consume(a, a["ready"].pop(n))
            a["arrived"].pop(n, None)
            a["next"] = n + 1
            continue
        if not a["ready"]:
            return
        came = a["arrived"].get(n)
        oldest = min(e["ts"] for e in a["ready"].values())
        if (came is None and now - oldest > SKIP_SECONDS) or (came is not None and now - came > STUCK_SECONDS):
            counters["seq_skipped"] += 1
            a["arrived"].pop(n, None)
            flush_tail(a)
            a["next"] = n + 1
            continue
        return


def deliver_locked(job, entry):
    """A chunk's transcript (entry["speech"]) or its gap (no speech, silence, dropped). Lock held."""
    a = asm_of(job["session"])
    job = {k: v for k, v in job.items() if k != "wav"}
    entry = {"text": "", "extra": {}, "result": False} | entry | {"job": job, "ts": time.time()}
    seq = job["seq"]
    if a["next"] is not None and seq < a["next"]:
        # Too late for its place (the buffer skipped it): judge what it holds on its own.
        counters["late_chunks"] += 1
        if entry["speech"]:
            for sentence in split_sentences(entry["text"]):
                emit_sentence(a, sentence, [job], entry["extra"])
        elif entry.get("result"):
            a["out"].append({"kind": "chunk", "job": job, "text": entry["text"], "extra": entry["extra"]})
        return
    sc = screens.get(job["session"])
    if sc and job.get("videoId") is not None and sc["videoId"] > job["videoId"] and job["received"] - job.get("seconds", 4) / 2 > sc["videoStartedAt"] - 1:
        # Stamped with the old video when it arrived, but most of it came after the next video was on screen.
        job |= video_of(job["session"])
    if a["next"] is None:
        a["next"] = seq
    a["ready"][seq] = entry
    drain(a, time.time())


def gap(job):
    """A chunk that will never have a transcript. Lock held."""
    deliver_locked(job, {"speech": False})


def pump(session):
    """Judges the sentences the buffer let out and adds their results, in order. One pump per session at a time: a
    second pump returns at once (it must never hold a pool thread), and the running one takes the new sentences."""
    with lock:
        a = asm.get(session)
        if not a or not a["out"]:
            return
        emit = a["emit"]
    if not emit.acquire(blocking=False):
        return
    try:
        while True:
            with lock:
                units, a["out"] = a["out"], []
                cfg = dict(DETECT)
                sc = dict(screens[session]) if session in screens else None
            if not units:
                return
            speech = [u for u in units if u["kind"] == "speech"]
            dets = {}
            if speech and cfg["backend"] != "keywords":
                t0 = time.time()
                items, judged = [], []
                for n, u in enumerate(speech):
                    if len(u["text"].split()) < MIN_WORDS:
                        continue
                    judged.append(u)
                    video = u["jobs"][-1].get("videoId")
                    same = bool(sc) and (video is None or video == sc["videoId"])
                    items.append({"id": n, "text": u["text"], "mark": u["text"], "source": "speech", "before": tail(u["before"], 400),
                                  "screen_text": sc["onScreenText"] if same else "", "description": sc["description"] if same else ""})
                preds = detect_known(items, cfg)
                with lock:
                    record_detection(session, "speech", items, preds, cfg)
                    for u, it in zip(judged, items):
                        remember_sentence(session, video_for(session, u["jobs"][-1]), it["text"], it["before"])
                for u, it, p in zip(judged, items, preds):
                    dets[id(u)] = summarize([it], [p], cfg, t0)
            with cond:
                for u in units:
                    if u["kind"] == "chunk":
                        job = u["job"]
                        add_result(job, u["text"], job.get("latency", 0), job.get("credits"), speech=False, extra=u["extra"])
                        continue
                    jobs = u["jobs"]
                    job = dict(jobs[-1])
                    if job.get("videoId") is None:
                        job |= video_of(session)
                    extra = dict(u["extra"]) | {"seqs": [j["seq"] for j in jobs], "waitS": round(time.time() - jobs[-1]["received"], 2)}
                    add_result(job, u["text"], job.get("latency", 0), job.get("credits"), det=dets.get(id(u)), speech=True, extra=extra)
    finally:
        emit.release()


# MARK: The claim lane (fact checks)


def claim_key(text):
    """The cache key of a claim: lower case, no punctuation, single spaces."""
    return " ".join(re.sub(r"[^\w ]+", " ", norm(text)).split())


def claim_texts(c):
    """Each text a check or a stored check is known by: the claim with its context, the sentence alone, the short claim
    and the sentence variants of other takes."""
    out = [c.get("sentence"), c.get("sentenceOnly"), c.get("short")] + list(c.get("variants") or [])
    return [t for t in dict.fromkeys(out) if t]


def own_texts(c):
    """The texts that say the claim itself: the sentence alone, the short claim and the variants (not the context)."""
    return [t for t in dict.fromkeys([c.get("sentenceOnly"), c.get("short")] + list(c.get("variants") or [])) if t]


def stance_rank(c):
    """How well a result is sourced, for one result per claim: "differ" with 2 source domains beats "support", which
    beats "mixed", which beats "none"; on a tie, more sources win."""
    domains = {domain_of(s.get("url", "")) for s in c.get("sources") or []}
    stance = c.get("stance")
    base = {"differ": 3 if len(domains) >= 2 else 1, "support": 2, "mixed": 1}.get(stance, 0) if c.get("status") == "found" else 0
    return (base, len(domains))


def matches_claim(c, texts, loose=False):
    """True when one of `texts` says the claim of check or stored check `c`. Strict (the store): a text matches the
    sentence alone, the short claim or a variant, and the claim with its context only when it says most of it, so one
    sentence of the context (a debunk video's "The invoice contained multiple errors") is not the claim. Loose (the
    checks of one play): any sentence of the context matches."""
    texts = [t for t in texts if t]
    own = [c.get("sentenceOnly"), c.get("short")] + list(c.get("variants") or [])
    if any(claim_key(a) == claim_key(b) or claim_match(a, b) for a in own if a for b in texts):
        return True
    ctx = c.get("sentence")
    if not ctx:
        return False
    if loose:
        return any(claim_match(ctx, b) for b in texts)
    wc = set(words_of(ctx))
    return any(claim_key(ctx) == claim_key(b) or (wc and len(wc & set(words_of(b))) / len(wc) >= 0.75) for b in texts)


def known_claim(*texts):
    """The stored check of this claim (a transcript of another take differs a little), or None. When more than one
    stored check matches, the best sourced one: the same claim must give the same result in every take."""
    texts = [t for t in texts if t and len(claim_key(t).split()) >= MIN_WORDS]
    if not texts:
        return None
    now = time.time()
    found = [c for c in check_cache.values() if now - c["doneTs"] <= CHECK_CACHE_SECONDS and matches_claim(c, texts)]
    return max(found, key=lambda c: (stance_rank(c), c["doneTs"]), default=None)


def claim_match(a, b):
    """Stricter than similar(): a match starts a stored check, and a wrong "differ" is the worst error. At least 4
    content words of the shorter text, and 75 % of them in the other, or nearly the same string."""
    wa, wb = set(words_of(a)), set(words_of(b))
    short = min(len(wa), len(wb))
    if short < 4:
        return False
    return len(wa & wb) / short >= 0.75 or difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() >= 0.85


def claim_with_context(session, sentence):
    """A short sentence carries the sentences just before it ("This is the house of ... . The mansion is worth $20
    million."), so the checker and the Island see the whole claim. Lock held."""
    if len(sentence.split()) >= 14:
        return sentence
    before = next((b for _, _, t, b in reversed(recent.get(session, ())) if t == sentence), "")
    lead = split_sentences(before)[-2:] if before else []
    return (" ".join(lead + [sentence]))[-400:]


def same_play(c, video_id, now, texts=()):
    """A check of the same play: the same video within PLAY_SECONDS, or the same claim within FLAP_SECONDS (a misread
    of the screen can change the video identity in the middle of a play)."""
    # seenTs: the claim said again in this play (a looped video): the play window starts again there, so the third loop
    # of a 64 s video neither starts a second check nor notifies twice.
    last = max(c["claimTs"], c.get("seenTs", 0))
    if c["videoId"] == video_id and now - last < PLAY_SECONDS:
        return True
    return bool(texts) and now - last < FLAP_SECONDS and matches_claim(c, texts, loose=True)


def add_variant(c, text):
    """Lock held: a sentence of the same play that says the same claim; the stored check learns it, so a later take
    that starts with this sentence finds the claim too."""
    if not text or text in claim_texts(c) or len(set(words_of(text))) < 4:
        return
    c.setdefault("variants", []).append(text[:400])
    c["variants"] = c["variants"][-12:]
    stored = check_cache.get(c.get("cacheKey") or "")
    if stored is not None and text not in claim_texts(stored):
        stored.setdefault("variants", []).append(text[:400])
        stored["variants"] = stored["variants"][-12:]
        save_cache()


def maybe_check(result, det, gate_by=None):
    """Starts a fact check when the gate says the sentence makes a specific, checkable claim, or when the sentence
    matches a claim checked before (gate_by "known claim"). Lock held."""
    session = result["session"]
    gates = det.get("gates") or {}
    sentence = det.get("sentence") or result["text"]
    context = claim_with_context(session, sentence)
    known = known_claim(sentence)
    if not known and (gates.get("claim", 0.0) < CLAIM_P or gates.get("quote", 1.0) >= CLAIM_QUOTE_MAX or gates.get("public", 1.0) < PUBLIC_MIN):
        return
    if len(sentence.split()) < MIN_WORDS:
        return
    if not DETECT["factCheck"] or config_of(session).get("factCheck") is False:
        counters["check_off"] += 1
        return
    now = time.time()
    texts = [sentence, context]
    started = config_of(session).get("startedAt", 0)
    shown = next((c for c in checks.get(session, []) if (c.get("notified") or c.get("secondOf")) and c["claimTs"] >= started and (
        (known and c.get("cacheKey") == known.get("cacheKey")) or matches_claim(c, texts, loose=True))), None)
    if shown:
        # The person saw this claim's result in this run (a looped video, or the claim said again): no second check,
        # and the asterisk stays dark.
        shown["seenTs"] = now
        counters["check_shown_before"] += 1
        return
    mine = [c for c in checks.get(session, []) if same_play(c, result.get("videoId"), now, texts)]
    same = next((c for c in mine if similar(c.get("sentenceOnly") or c["sentence"], sentence) or similar(c["sentence"], sentence)
                 or matches_claim(c, texts, loose=True) or (known and c.get("cacheKey") == known.get("cacheKey"))), None)
    if same:
        same["seenTs"] = now
        if not known and gates.get("claim", 0.0) >= CLAIM_P:
            add_variant(same, sentence)  # the claim spread over this and the sentences before, or in speech and on screen
        counters["check_same_claim"] += 1
        return
    if len(mine) >= CLAIMS_PER_VIDEO:
        counters["check_video_cap"] += 1
        return
    if not known and sum(1 for t in check_starts if now - t < 3600) >= CHECKS_PER_HOUR:
        counters["check_hour_cap"] += 1
        return
    by = gate_by or ("known claim" if known and gates.get("claim", 0.0) < CLAIM_P else det.get("backend"))
    start_check(result, sentence, gates.get("claim", 0.0), by, press=False)


def known_pred(text):
    """Lock held. A sentence that says a stored claim needs no detector call: its result (and the pending check item)
    is published at once. The gates of the stored check stand in for the detector's answer."""
    old = known_claim(text)
    if not old:
        return None
    return {"note": False, "unsure": False, "top": None, "probs": {}, "confidence": None, "backend": "known claim", "model": None,
            "gates": {"war": 1.0, "quote": 0.0, "claim": old.get("claimP") or CLAIM_P, "public": 1.0}, "latency_ms": 0, "techniques": []}


def detect_known(items, cfg):
    """The detector's answers for `items`, with the sentences that say a stored claim answered at once (known_pred)
    and only the others sent to the detector. Not under the lock."""
    with lock:
        known = {it["id"]: known_pred(it["text"]) for it in items}
    todo = [it for it in items if not known[it["id"]]]
    preds = detect.detect(todo, backend=cfg["backend"], threshold=cfg["note"], unsure=cfg["unsure"]) if todo else []
    got = {it["id"]: p for it, p in zip(todo, preds)}
    return [known[it["id"]] or got[it["id"]] for it in items]


def result_for_of(session, video_id):
    """Lock held: the result a check without its own sentence result points to (`of`): the newest sentence of the
    same video, else the newest result of the session."""
    rows = [r for r in reversed(results) if r["session"] == session and r.get("kind") != "check"]
    r = next((r for r in rows if r.get("speech") and r.get("videoId") == video_id), None) or next(iter(rows), None)
    return r["id"] if r else None


def start_check(result, sentence, claim_p, gate_by, press, fresh=False):
    """A new check: the pending item at once, then the checker in the background (or the stored result of the same
    claim). Lock held. Returns the check."""
    global next_check_id
    session = result["session"]
    now = time.time()
    next_check_id += 1
    video = video_of(session)
    video_id = result.get("videoId", video.get("videoId"))
    of = result.get("id")
    if of is None:
        of = result_for_of(session, video_id)
    if of is None:  # nothing heard yet in this session (screen text first): the claim text is its own result
        r = add_result({"session": session, "seq": 0, "arrival": next_arrival_id(), "received": now} | video, sentence, 0, None,
                       source="screen", speech=False, extra={"claimOnly": True})
        of = r["id"] if r else None
    c = {"kind": "check", "id": "c%d" % next_check_id, "resultId": of, "session": session,
         "videoId": video_id, "author": result.get("author") or video.get("author"),
         "app": result.get("app") or video.get("app"), "handle": result.get("handle") or video.get("handle"),
         "sentence": claim_with_context(session, sentence)[:400], "sentenceOnly": sentence[:400], "claimP": round(claim_p, 3), "gateBy": gate_by, "status": "pending", "stance": None, "line": "",
         "sources": [], "checker": None, "press": press, "held": False, "delivered": False, "claimTs": now, "createdAt": now_iso(now),
         "updatedAt": now_iso(now), "ms": None}
    checks.setdefault(session, []).append(c)
    start_watching(session, c)
    if result.get("id") is not None:
        result["checkId"] = c["id"]
    counters["check_jobs"] += 1
    check_stats["sentToChecker"] += 1
    add_check_item(c, notify=press, forced=press)
    old = None if fresh else known_claim(sentence)
    if old:
        use_stored(c, old)
        settle(c)
        return c
    check_starts.append(now)
    sc = screens.get(session)
    context = ("The video's title or caption: \"%s\"\n" % sc["description"][:200]) if sc and sc.get("description") else ""
    check_pool.submit(run_check, c, context)
    return c


def use_stored(c, old):
    """Lock held: the check takes the stored result of its claim."""
    check_stats["cacheHits"] += 1
    c.update({k: old[k] for k in ("status", "stance", "line", "sources") if k in old}, short=old.get("short"), checker="cache", cacheOf=old["id"],
             cacheKey=old.get("cacheKey"), ms=0, checkerMs=old.get("ms"), cachedChecker=old.get("checker"))
    add_variant(c, c["sentenceOnly"])


CACHE_FILE = os.path.join(os.environ.get("LENS_CACHE_DIR", "/data/cache"), "checks.json")


def load_cache():
    """The stored checks survive a deploy: a JSON file on a volume (/srv/junction-data/lens-cache on the VPS). Entries
    of the same claim from before the merge rule are merged into one."""
    try:
        with open(CACHE_FILE) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return
    with lock:
        for key, c in sorted(raw.items(), key=lambda kv: kv[1].get("doneTs", 0)):
            done = c.get("doneTs", 0)
            stored = store_check(c, key if key not in check_cache else None)
            if stored is c or stored.get("id") == c.get("id"):
                stored["doneTs"] = done
        save_cache()


def save_cache():
    """Lock held. Small: one entry per checked claim."""
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(check_cache, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except OSError as e:
        counters["cache_save_errors"] += 1
        logs.append({"t": now_iso(), "source": "server", "session": "", "line": "check cache not saved: %s" % e})


def check_prompt(c, context):
    return CHECK_PROMPT % (c["sentence"], context, time.strftime("%d %B %Y", time.gmtime()))


def agent_key():
    key = os.environ.get("LENS_AGENT_KEY", "")
    if not key and os.path.isfile(AGENT_KEY_FILE):
        for line in open(AGENT_KEY_FILE):
            if line.startswith("FOOTNOTE_AGENT_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    return key


def agent_check(c, context):
    """footnote-agent (our TypeScript harness on the VPS: GPT-6 Luna with web search through the Hostinger AI Router)
    checks the claim. It gets the claim, the transcript around it, the account, the caption and the frame in front.
    Returns ({"stance", "line", "sources", "claim"}, None) or (None, error)."""
    key = agent_key()
    if not key:
        return None, "no agent key"
    with lock:
        heard = " ".join(t for _, _, t in list(spoken.get(c["session"], ()))[-8:])[-1200:]
        jpeg = check_frames.pop(c["id"], None)
        sc = screens.get(c["session"]) or {}
        payload = {"claim": c["sentence"], "context": heard, "handle": c.get("handle") or "", "caption": (sc.get("description") or "")[:300]}
    if jpeg:
        payload["frames"] = [base64.b64encode(jpeg).decode()]
    future = check_http.submit(detect._post, AGENT_URL, payload, key, AGENT_TIMEOUT)
    try:
        data, err = future.result(timeout=AGENT_TIMEOUT)
    except concurrent.futures.TimeoutError:
        return None, "agent: no answer within %d s" % AGENT_TIMEOUT
    if err:
        return None, "agent: " + err
    if not isinstance(data, dict) or "stance" not in data:
        return None, "agent: no stance in the answer"
    return {"stance": data.get("stance"), "line": data.get("line"), "sources": data.get("sources") or [], "claim": data.get("claim"),
            "model": data.get("model"), "searches": data.get("searches")}, None


def single_check(c, context):
    """One OpenRouter chat call with its web plugin (exa). Returns ({"stance", "line", "sources", "cost"}, None) or (None, error)."""
    if not detect.openrouter_key() or not detect.decision_ok():
        return None, "OpenRouter: no key or no credits"
    payload = {"model": CHECK_MODEL, "plugins": [{"id": "web", "engine": CHECK_ENGINE, "max_results": CHECK_RESULTS}], "max_tokens": 500,
               "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": check_prompt(c, context)}]}
    future = check_http.submit(detect._post, OPENROUTER_CHAT_URL, payload, detect.openrouter_key(), 40)
    try:
        data, err = future.result(timeout=40)
    except concurrent.futures.TimeoutError:
        return None, "OpenRouter: no answer within 40 s"
    if err:
        return None, "OpenRouter: " + err
    try:
        message = data["choices"][0]["message"]
        parsed = parse_json(message["content"] or "")
    except Exception as e:  # noqa: BLE001
        return None, "OpenRouter: bad JSON: %r" % (e,)
    named = [x for x in (parsed.get("sources") or []) if isinstance(x, dict)]
    # The search results (annotations) are the pages the model read; the named sources that are among them come first.
    found = [a.get("url_citation") or {} for a in message.get("annotations") or [] if a.get("type") == "url_citation"]
    read = {str(x.get("url", "")).rstrip("/") for x in found}
    dates = {str(x.get("url", "")).rstrip("/"): x.get("date") for x in named}
    ordered = [x for x in named if str(x.get("url", "")).rstrip("/") in read] + found + ([] if found else named)
    sources = [dict(x, date=x.get("date") or dates.get(str(x.get("url", "")).rstrip("/"))) for x in ordered]
    return {"stance": parsed.get("stance"), "line": parsed.get("line"), "sources": sources, "claim": parsed.get("claim"),
            "cost": (data.get("usage") or {}).get("cost")}, None


CHECKERS = {"agent": agent_check, "single": single_check}


def checker_order(press):
    first = DETECT["pressChecker" if press else "autoChecker"]
    return [first] + [b for b in CHECKERS if b != first]


def run_check(c, context):
    """One fact check job (check pool thread, never in a request). The agent answers; the other checker runs only when
    the first one fails."""
    t0 = time.time()
    for name in checker_order(c["press"]):
        out, err, ms = timed_checker(name, c, context)
        with lock:
            st = check_stats[name]
            st["calls"] += 1
            if err:
                st["errors"] += 1
                st["lastError"] = err[:200]
                c.setdefault("errors", []).append(err[:200])
            else:
                st["ok"] += 1
                st["ms"].append(ms)
        if err:
            continue
        answer = finish_answer(out, c) | {"checker": name, "checkerMs": ms}
        with cond:
            c.update(answer, ms=round((time.time() - t0) * 1000))
            if out.get("cost"):
                detect_usage.append((time.time(), c["session"], "check", float(out["cost"])))
                c["costUsd"] = round(float(out["cost"]), 5)
            remember_check(c)
            settle(c)
        return
    with cond:  # no checker answered
        c.update(status="nothing", stance="none", line="Could not check now.", ms=round((time.time() - t0) * 1000))
        counters["check_errors"] += 1
        settle(c)


def timed_checker(name, c, context):
    t = time.time()
    out, err = CHECKERS[name](c, context)
    return out, err, round((time.time() - t) * 1000)


def finish_answer(out, c):
    """Sources cleaned, the stance rules applied, the line made safe. Not under the lock (it may resolve redirects)."""
    sources = clean_sources(out.get("sources") or [], c)
    stance = str(out.get("stance") or "none").lower()
    stance = stance if stance in STANCES else "mixed"
    note = None
    if not sources:
        stance = "none"
    elif stance == "differ" and len({domain_of(s["url"]) for s in sources}) < 2:
        stance, note = "mixed", "differ with one source domain became mixed"  # a wrong "differ" is the worst error
    short = " ".join(str(out.get("claim") or "").split()).rstrip(".")
    if VERDICT_WORDS.search(short) or len(short) < 8:
        short = ""
    return {"status": "found" if sources else "nothing", "stance": stance, "sources": sources, "line": clean_line(out.get("line"), sources),
            "stanceNote": note} | ({"short": shorten(short, 60)} if short else {})


STORE_SKIP = ("errors", "alsoChecked", "replaced", "assertP", "assertBy", "assertLines", "held", "delivered", "delivering", "deliveredAt",
              "deliveredBecause", "notNotified", "press", "session")


def store_check(entry, key=None):
    """Lock held: one stored check per claim. A new result joins the stored check of the same claim (any of its texts
    match) and replaces its result only when it is better sourced (stance_rank); the texts of both are kept, so each
    sentence variant of a take finds the one entry. Returns the stored entry."""
    entry = {k: v for k, v in entry.items() if k not in STORE_SKIP}
    now = time.time()
    group = [(k, c) for k, c in check_cache.items() if k != key and now - c["doneTs"] <= CHECK_CACHE_SECONDS and matches_claim(c, own_texts(entry))]
    if key and key in check_cache:
        group.insert(0, (key, check_cache[key]))
    if not group:
        key = key or claim_key(entry["sentence"])
        stored = check_cache[key] = entry | {"cacheKey": key, "doneTs": now}
        return stored
    key, stored = group[0]
    texts = []
    for _, c in group:
        texts += own_texts(c)
    texts += own_texts(entry)
    best = max([stored, entry] + [c for _, c in group[1:]], key=lambda c: (stance_rank(c), c.get("doneTs", now)))
    for k, _ in group[1:]:
        check_cache.pop(k, None)  # one entry per claim
    merged = dict(best) | {"cacheKey": key, "doneTs": now if best is entry else best["doneTs"]}
    merged["short"] = merged.get("short") or next((c.get("short") for c in [entry, stored] + [c for _, c in group[1:]] if c.get("short")), None)
    merged["variants"] = [t for t in dict.fromkeys(texts) if t not in (merged.get("sentence"), merged.get("sentenceOnly"), merged.get("short"))][-12:]
    check_cache[key] = merged
    return merged


def remember_check(c):
    """Lock held: the stored check of this claim (on disk). When the store already has a better sourced result for
    the claim (an earlier take), the check takes that one: the same claim gives the same result in every take."""
    if c.get("checker") and c["checker"] != "cache":
        stored = store_check(c, c.get("cacheKey"))
        c["cacheKey"] = stored["cacheKey"]
        if stored.get("id") != c["id"] and stance_rank(stored) > stance_rank(c):
            c["checkedAs"] = {k: c.get(k) for k in ("checker", "stance", "line")}
            use_stored(c, stored)
        save_cache()
    counters["check_" + c["status"]] += 1
    logs.append({"t": now_iso(), "source": "server", "session": c["session"],
                 "line": "check %s by %s: %s %s: %s" % (c["id"], c.get("checker"), c["status"], c["stance"], c["line"])})


ASSERT_Q = {"type": "noul", "instructions": "Does this video spread the claim: state it or show it as fact, rather than check, refute, question or "
            "warn about it? Read the account, the caption and all that the video says.",
            "criteria": {"true": "The video presents the claim as fact or as real, in its words, its caption or its on-screen text, also with a "
                                 "vague source ('people say').",
                         "false": "The video is a fact check or a debunk, says the claim is false or unproven, asks it only as a question, mocks "
                                  "it, or reports it neutrally as someone else's claim. Also false for satire or parody: a satirical outlet "
                                  "or account (such as The Onion or The Babylon Bee), a comedy sketch, or a fake news report made as a joke."}}


def after_context(c):
    """Lock held: the claim and the whole video around it (account, caption, every sentence of the same video so far,
    the screen text), as decision state lines. A question about the claim and 3 sentences could not tell the demo
    video (0.35 to 0.58) from a debunk video (0.05 to 0.46); about the whole video it gives 0.97 to 0.99 and 0.01 to 0.03."""
    sc = screens.get(c["session"])
    if sc and sc["videoId"] == c["videoId"]:
        c["caption"] = sc.get("description") or c.get("caption", "")
        c["screenText"] = sc.get("onScreenText") or c.get("screenText", "")
    # The same account within the play counts as the same video: a caption that changes in the middle of a video
    # (a question first, then "Fact Check: ...") splits it in two for the screen reader.
    who = handle_key(c.get("handle"))
    said = " ".join(r["text"] for r in results if r["session"] == c["session"] and r.get("speech") and r.get("kind") != "check"
                    and abs(r["ts"] - c["claimTs"]) < PLAY_SECONDS
                    and (r.get("videoId") == c["videoId"] or (who and handle_key(r.get("handle")) == who)))
    lines = ["The claim: " + (c.get("short") or c["sentence"])]
    if c.get("short") and c["sentence"] != c["short"]:
        lines.append("The video's words for it: " + c["sentence"][:300])
    if c.get("handle"):
        lines.append("The video's account: " + c["handle"][:60])
    if c.get("caption"):
        lines.append("The video's caption: " + c["caption"][:300])
    lines.append("What the video says: " + (tail(said, 900) if said else c["sentence"]))
    if c.get("screenText"):
        lines.append("Text on the screen: " + c["screenText"][:300])
    return lines


def submit_assert(c):
    """Lock held: asks in the background whether the speaker makes the claim (a debunk video reports it)."""
    lines = after_context(c)
    c["assertLines"] = len(lines)

    def job():
        p, backend, err = detect.ask_text(lines, ASSERT_Q)
        with lock:
            c["assertP"] = None if err else round(p, 3)
            c["assertBy"] = backend
    check_pool_inner.submit(job)


def play_gap(o, c):
    """Seconds between the claim of check `c` and the last time check `o`'s claim was said (its first or a loop's)."""
    return min(abs(c["claimTs"] - o["claimTs"]), abs(c["claimTs"] - max(o["claimTs"], o.get("seenTs", 0))))


def duplicate_of(c):
    """Lock held: an earlier check of the same play whose result is the same stored claim (a transcript can split one
    claim so that the gate sees it twice), or None."""
    key = c.get("cacheKey")
    return next((o for o in checks.get(c["session"], []) if o is not c and key and o.get("cacheKey") == key and o["videoId"] == c["videoId"]
                 and c["claimTs"] >= o["claimTs"] and play_gap(o, c) < PLAY_SECONDS and not o.get("duplicateOf")), None)


def settle(c):
    """A finished check: a press shows any stance at once; "differ" is held for delivery (and the speaker question
    starts); the rest goes to the stream silently. A second check of the same claim in one play goes out silently.
    Lock held."""
    c["updatedAt"] = now_iso()
    check_stats["stances"][c["stance"]] += 1
    dup = None if c["press"] else duplicate_of(c)
    if dup:
        c["duplicateOf"] = dup["id"]
        counters["check_duplicate"] += 1
        add_check_item(c, notify=False)
        return
    if c["press"]:
        deliver(c, forced=True)
    elif c["stance"] == "differ":
        c["held"] = True
        submit_assert(c)
    else:
        add_check_item(c, notify=False)


def deliver(c, forced=False):
    """Publishes a finished check with notify true (the Island). Lock held."""
    c.update(held=False, delivered=True, deliveredAt=now_iso())
    check_stats["delivered"] += 1
    return add_check_item(c, notify=True, forced=forced)


def broadcast_live(session, now):
    """Lock held: the broadcast of the session runs (a fresh heartbeat) and listening is not stopped."""
    if config_of(session).get("running") is False:
        return False
    beat = next((h for h in reversed(heartbeats) if h.get("source") == "broadcast" and h.get("session") == session), None)
    return bool(beat) and beat.get("state") == "running" and now - beat.get("ts", 0) < LIVE_SECONDS


def due_because(c, sc, now):
    """Lock held: why held check `c` goes out now, or None to keep it held (the hold rule above DELIVER_SECONDS)."""
    if not sc or not broadcast_live(c["session"], now):
        return None  # the broadcast ended or listening stopped: keep it held; "deliver now" returns it
    if now - c["claimTs"] >= SETTLE_SECONDS:
        return "check done"
    # The next video is another account: the same account's next "video" is often the same video read again.
    if c["videoId"] and sc["videoId"] and sc["videoId"] != c["videoId"] and handle_key(sc.get("handle")) != handle_key(c.get("handle")):
        return "next video"
    if paused(c["session"], now):
        # A private screen, another app or Footnote itself: the person left the video.
        if now - max(sc.get("pausedSince", 0), c["claimTs"]) >= LEAVE_SECONDS:
            return "no media for %d s" % LEAVE_SECONDS
        return None
    if sc["videoId"] == c["videoId"]:
        return None  # the claim's video is in front (also paused, looped or restarted)
    if now - c["claimTs"] >= DELIVER_SECONDS:
        return "%d s after the claim, another video in front" % DELIVER_SECONDS
    return None


def deliver_due():
    """Held "differ" results go out by the hold rule (due_because), after one more speaker question with the sentences
    that came after the claim. Lock held."""
    now = time.time()
    groups = {}
    for mine in checks.values():
        for c in sorted(mine, key=lambda c: c["claimTs"]):
            if not c["held"] or c.get("delivering"):
                continue
            because = due_because(c, screens.get(c["session"]), now)
            if because:
                c["delivering"] = True
                c["deliveredBecause"] = because
                groups.setdefault((c["session"], c["videoId"]), []).append((c, after_context(c)))
    for todo in groups.values():
        # One job per video, in claim order: the first claim of a video that passes the speaker question is the one
        # that notifies (one notify per play), not the one whose question happened to answer first.
        check_pool_inner.submit(lambda todo=todo: [finish_delivery(c, lines) for c, lines in todo])


def finish_delivery(c, lines):
    p, backend, err = detect.ask_text(lines, ASSERT_Q)
    with cond:
        c["delivering"] = False
        if err:
            p = c.get("assertP")  # the first answer, or none: deliver (the gate's quote question already passed)
        else:
            c.update(assertP=round(p, 3), assertBy=backend)
        if not c["held"]:
            return  # a press delivered it meanwhile
        if p is not None and p < ASSERT_MIN:
            c["held"] = False
            c["notNotified"] = "the speaker reports, questions or refutes the claim"
            counters["check_not_asserted"] += 1
            add_check_item(c, notify=False)
            return
        other = next((o for o in checks.get(c["session"], []) if o is not c and o.get("notified") and play_gap(o, c) < PLAY_SECONDS
                      and (o["videoId"] == c["videoId"] or (c.get("cacheKey") and o.get("cacheKey") == c.get("cacheKey")))), None)
        if other:
            # One automatic notify per video play (the speech and the caption can say the same claim in other words).
            c["held"] = False
            c["notNotified"] = "check %s of this video was delivered" % other["id"]
            c["secondOf"] = other["id"]
            counters["check_second_of_video"] += 1
            add_check_item(c, notify=False)
            return
        c["notified"] = True
        deliver(c)


def deliver_loop():
    while True:
        time.sleep(1)
        with cond:
            deliver_due()


def domain_of(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    parts = host.split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "gov", "ac", "net"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def clean_sources(raw, c):
    """At most 3 sources with an http URL, no social media, not the video's own account, dates as YYYY-MM-DD."""
    own = handle_key(c.get("handle") or "")
    out = []
    for x in raw:
        if not isinstance(x, dict):
            continue
        url = str(x.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        url = final_url(url)
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if REDIRECTS.search(url) or SOCIAL.search(host) or (own and len(own) >= 4 and own in handle_key(url)):
            continue
        if url in [y["url"] for y in out]:
            continue
        title = " ".join(str(x.get("title") or domain_of(url)).split())[:120]
        out.append({"title": title, "url": url[:600], "date": iso_date(x.get("date")), "site": site_of(title, url)})
        if len(out) == 3:
            break
    return out


def shorten(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 3].rsplit(" ", 1)[0].rstrip(",;:") + "..."


def site_of(title, url):
    """The publisher's name for the Island ("PolitiFact", "Reuters"): the title's last part after a separator, else the
    domain. A title can hold a fact-checker's rating ("No, that's False"); the name never does."""
    parts = re.split(r"\s[|\u2013\u2014-]\s", title)
    name = parts[-1].strip() if len(parts) > 1 else ""
    # A title can end in a section name ("... | Fact check"): keep the name only when the domain holds its first word.
    first = re.sub(r"[^a-z0-9]", "", (name.split() or [""])[0].lower())
    if not name or len(name) > 30 or VERDICT_WORDS.search(name) or len(first) < 3 or first not in domain_of(url).replace("-", ""):
        name = domain_of(url)
    return name


def clean_line(line, sources):
    """At most 90 characters on what the sources report: never the words true or false, no "Sources differ" start (the
    app adds "Sources contradict this.")."""
    line = " ".join(str(line or "").split())
    line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)  # a markdown link: its text
    line = re.sub(r"\(?https?://\S+\)?", "", line).strip()
    line = re.sub(r"^(sources (differ|disagree|contradict( this)?)|sources)\s*[:.,-]\s*", "", line, flags=re.I).strip()
    if line and line[0].islower():
        line = line[0].upper() + line[1:]
    if not sources:
        return "No source found."
    if not line or VERDICT_WORDS.search(line):
        names = list(dict.fromkeys(domain_of(s["url"]) for s in sources))
        line = "Reports from %s." % " and ".join(names[:2])
    if len(line) > 90:
        line = line[:87].rsplit(" ", 1)[0] + "..."
    return line


def add_check_item(c, notify, forced=False):
    """Puts the state of a check in the results stream as its own item (kind "check", a new id each time: the pending
    item and the result are separate items), the contract the app decodes. Lock held."""
    global next_result_id
    next_result_id += 1
    r = next((x for x in reversed(results) if x["id"] == c["resultId"]), None) or {}
    item = {"id": next_result_id, "kind": "check", "t": now_iso(), "ts": time.time(), "session": c["session"], "of": c["resultId"],
            "checkId": c["id"], "claim": c.get("short") or shorten(c["sentence"], 60), "claimText": c["sentence"], "status": c["status"], "stance": c["stance"], "notify": notify, "forced": forced,
            "line": c["line"] or None, "sources": c["sources"], "checker": c["checker"], "gateBy": c["gateBy"],
            "arrival": r.get("arrival", 0), "seq": r.get("seq", 0), "videoId": c["videoId"], "app": c["app"], "author": c["author"],
            "handle": c["handle"], "claimP": c["claimP"], "ms": c["ms"],
            # Fields every result has, so the code that reads results needs no case for a check.
            "text": c["sentence"], "technique": None, "unsure": False, "speech": False, "source": "check", "fake": False,
            "chunk": "", "rms": 0, "latency": round((c["ms"] or 0) / 1000, 2), "credits": None, "label": None, "mark": None}
    for k in ("costUsd", "cacheOf", "stanceNote", "deliveredBecause", "errors", "assertP", "notNotified", "replaced", "checkerMs", "cachedChecker",
              "duplicateOf", "checkedAs"):
        if c.get(k):
            item[k] = c[k]
    results.append(item)
    cond.notify_all()
    return item


def iso_date(text):
    """A source date as YYYY-MM-DD, or "" when it cannot be read."""
    text = str(text or "").strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return m.group(0)
    for fmt in ("%B %d, %Y", "%B %d %Y", "%d %B %Y", "%b %d, %Y", "%d %b %Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return time.strftime("%Y-%m-%d", time.strptime(text, fmt))
        except ValueError:
            continue
    return ""


REDIRECTS = re.compile(r"grounding-api-redirect|^https?://(www\.)?google\.[a-z.]+/(goto|url)\b")


def final_url(url):
    """The page behind a search redirect (Google grounding and goto links), within 3 s; else the link as it is."""
    if not REDIRECTS.search(url):
        return url
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    for k in ("url", "q"):
        if q.get(k) and q[k][0].startswith("http"):
            return q[k][0]
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 (footnote)"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.geturl()
    except urllib.error.HTTPError as e:
        return e.geturl() or url
    except Exception:  # noqa: BLE001
        return url


def video_for(session, job):
    """Lock held: the video id of a job: the one stamped at its arrival, or the video in front when the job arrived
    after that video's first read (see restamp)."""
    v = job.get("videoId")
    sc = screens.get(session)
    if sc and sc["videoId"] and v not in (None, sc["videoId"]) and job.get("arrival", 0) > sc["videoArrival"]:
        return sc["videoId"]
    return v


def remember_sentence(session, video, text, before):
    """A judged sentence, for "Footnote now". Lock held."""
    recent.setdefault(session, collections.deque(maxlen=20)).append((time.time(), video, text, before))


# MARK: Text from the phone (on-device transcription and OCR) and "Footnote now"


def text_in(data):
    """POST /api/text: speech or screen text that the phone already transcribed or read. Speech goes straight to the
    detector (no audio, no transcription); a non-final text only warms the detector's cache, and a note shows on a final
    sentence only. Screen text passes a text privacy gate first. Returns the reply."""
    session = str(data.get("session") or "unknown")[:64]
    source = data.get("source", "speech")
    text = " ".join(str(data.get("text") or "").split())[:2000]
    final = bool(data.get("final", True))
    if source not in ("speech", "screen"):
        return 400, {"error": 'source must be "speech" or "screen"'}
    with lock:
        counters["text_" + source] += 1
        if config_of(session).get("running") is False:
            return 200, {"ok": True, "skipped": "not listening"}
        if paused(session):
            # Privacy guard: text that arrives during a private screen is never judged or kept.
            counters["text_discarded_paused"] += 1
            return 200, {"ok": True, "discarded": screens[session].get("pauseReason") or "Paused"}
        cfg = dict(DETECT)
        sc = dict(screens[session]) if session in screens else None
        video = video_of(session)
    if not text:
        return 200, {"ok": True, "results": []}
    if source == "screen":
        return screen_text_in(session, text, data, cfg)
    sentences = split_sentences(text)
    if not final and sentences and not complete(sentences[-1]):
        sentences = sentences[:-1]  # the open end of a partial text can still change
    with lock:
        st = texts.setdefault(session, {"done": collections.deque(maxlen=50), "history": []})
        now_t, vid = time.time(), video.get("videoId")
        todo = [x for x in sentences if not any(similar(x, d) for t, v, d in st["done"] if v == vid and now_t - t < TEXT_DEDUPE_SECONDS)]
        if final:
            st["done"].extend((now_t, vid, x) for x in todo)
        before = list(st["history"])
    items = []
    for x in todo:
        items.append({"id": len(items), "text": x, "mark": x, "source": "speech", "lang": data.get("lang"), "before": tail(" ".join(before[-2:]), 400),
                      "screen_text": sc["onScreenText"] if sc else "", "description": sc["description"] if sc else ""})
        before.append(x)
    judge = [it for it in items if len(it["text"].split()) >= MIN_WORDS]
    if not final:
        if judge and cfg["backend"] != "keywords":
            detect_pool.submit(detect.detect, judge, cfg["backend"], cfg["note"], cfg["unsure"])  # warms the cache
        return 200, {"ok": True, "final": False, "warming": len(judge)}
    t0 = time.time()
    preds = detect_known(judge, cfg) if judge and cfg["backend"] != "keywords" else []
    dets = {it["id"]: summarize([it], [p], cfg, t0) for it, p in zip(judge, preds)}
    out = []
    with cond:
        st["history"] = (st["history"] + [it["text"] for it in items])[-3:]
        record_detection(session, "speech", judge, preds, cfg)
        for it in items:
            if it["id"] in dets:
                remember_sentence(session, video.get("videoId"), it["text"], it["before"])
            spoken.setdefault(session, collections.deque(maxlen=30)).append((time.time(), video.get("videoId"), it["text"]))
            job = {"session": session, "seq": int(data.get("seq", 0) or 0), "arrival": next_arrival_id(), "received": time.time()} | video
            r = add_result(job, it["text"], time.time() - t0, None, det=dets.get(it["id"]), speech=True,
                           extra={"textPath": True, "tStart": data.get("t_start"), "tEnd": data.get("t_end")})
            if r:
                out.append(public(r))
        cond.notify_all()
    return 200, {"ok": True, "final": True, "results": out, "detectMs": round((time.time() - t0) * 1000)}


def screen_text_in(session, text, data, cfg):
    """Screen text from on-device OCR: a decision question on the text says private screen or public media, so no image
    leaves the phone. Private text pauses the session and is dropped; media text is judged once per distinct text."""
    t0 = time.time()
    with lock:
        mine = strip_own(text, own_texts_of(session))
    if len(norm(mine)) < 8:
        # Footnote's own page or Island (its phrases, or a claim, line or source of this session's checks): never judged.
        with lock:
            counters["screen_own_skipped"] += 1
        return 200, {"ok": True, "private": False, "judged": False, "own": True}
    text = mine
    p, backend, err = detect.ask_text([text[:1500]], PRIVATE_TEXT_Q)
    with cond:
        sc = screen_of(session)
        now = time.time()
        if err:
            counters["text_gate_errors"] += 1
            return 200, {"ok": False, "error": "text gate: " + err[:200]}
        if p >= 0.5:
            if not paused(session, now):
                sc["pausedSince"] = now
            sc["pausedAt"] = now
            sc["kind"] = "private"
            sc["pauseReason"] = "Paused: private screen"
            counters["screen_discarded_private"] += 1
            drop_held(session)
            screen_log.append({"t": now_iso(), "session": session, "seq": data.get("seq", 0), "kind": "text", "latency": round(now - t0, 2),
                               "parsed": {"kind": "private"}, "discarded": True, "gate": {"p": round(p, 3), "backend": backend}})
            cond.notify_all()
            return 200, {"ok": True, "private": True, "pauseReason": sc["pauseReason"], "gateMs": round((now - t0) * 1000)}
        sc["lastTikTokAt"] = now
        sc["pauseReason"] = ""
        release_held(session, now)
        key = norm(text)
        if len(key) < 8 or any(difflib.SequenceMatcher(None, key, old).ratio() > 0.85 for old in sc["checked"]):
            return 200, {"ok": True, "private": False, "judged": False, "gateMs": round((now - t0) * 1000)}
        sc["checked"].append(key)
        sc["onScreenText"] = text[:400]
        job = {"session": session, "seq": int(data.get("seq", 0) or 0), "arrival": next_arrival_id(), "received": now, "text": text[:400],
               "description": sc["description"]} | video_of(session)
    detect_pool.submit(detect_screen, job)
    return 200, {"ok": True, "private": False, "judged": True, "gateMs": round((time.time() - t0) * 1000)}


def forget_session(session, keep_checks=False):
    """POST /api/reset: the session's checks, held results, dedupe and video identity go; the stored checks (the cache)
    stay, so a new take answers from them. A new run (keep_checks) forgets the video identity and the dedupe but keeps
    the checks, so "deliver now" still returns the last result. Video numbers go on counting. Lock held."""
    sc = screens.get(session)
    if sc:
        video_counter[session] = max(video_counter.get(session, 0), sc["videoId"])
    stores = (screens, recent, texts, spoken, langs, held, frame_hash, pending_frames, last_frames, ref_frames, seen_videos, asm) + (() if keep_checks else (checks,))
    for store in stores:
        store.pop(session, None)
    # A new take may send the same audio again (a replay): the retry dedupe of its chunks starts over.
    for key in [k for k in recent_chunks if k[0] == session]:
        recent_chunks.remove(key)
    counters["runs_started" if keep_checks else "sessions_reset"] += 1


def deliver_now(session):
    """POST /api/now, "deliver now" (the Action button). It returns, as a new item with notify true each time:
    the newest finished "differ" check of the session in the last NOW_KEEP_SECONDS (held or delivered before, on any
    video; a debunk video's claim is skipped); else, while a newer check runs, {pending: true} and its result the moment
    it arrives; else the newest finished check; and with no check, a new check of the most checkable claim of the
    last NOW_CLAIM_SECONDS. Only POST /api/reset clears the session's checks. Returns the response body."""
    t0 = time.time()
    with cond:
        counters["deliver_now"] += 1
        mine = [c for c in checks.get(session, []) if t0 - c["claimTs"] < NOW_KEEP_SECONDS and not c.get("duplicateOf")]
        finished = [c for c in mine if c["status"] != "pending"]
        asserted = [c for c in finished if c.get("assertP") is None or c["assertP"] >= ASSERT_MIN]
        # The check the Island showed comes first, then the newest other "differ".
        differ = next((c for c in reversed(asserted) if c["stance"] == "differ" and c.get("notified")), None) or \
            next((c for c in reversed(asserted) if c["stance"] == "differ"), None)
        if differ:
            again = differ["delivered"]
            return public(deliver(differ, forced=True)) | ({"again": True} if again else {"delivered": True}) | {"nowMs": round((time.time() - t0) * 1000)}
        if paused(session):
            # A held result of an earlier video goes out above; nothing of the screen in front is judged.
            return {"ok": True, "forced": True, "text": screens[session].get("pauseReason") or "Paused", "note": False}
        running = next((c for c in reversed(mine) if c["status"] == "pending"), None)
        if running and (not finished or running["claimTs"] >= finished[-1]["claimTs"]):
            running["press"] = True  # its result goes out with notify true, whatever the stance
            return {"ok": True, "pending": True, "kind": "check", "checkId": running["id"], "claim": running.get("short") or shorten(running["sentence"], 60),
                    "of": running["resultId"], "status": "pending", "notify": False, "forced": True, "nowMs": round((time.time() - t0) * 1000)}
        done = (asserted or finished)[-1] if finished else None
        if done:
            return public(deliver(done, forced=True)) | {"again": True, "nowMs": round((time.time() - t0) * 1000)}
        sc = dict(screens[session]) if session in screens else None
        cut = time.time() - NOW_CLAIM_SECONDS
        rows = [r for r in recent.get(session, ()) if r[0] >= cut] or list(recent.get(session, ()))[-3:]
    items = [{"id": n, "text": text, "mark": text, "source": "speech", "before": tail(before, 400),
              "screen_text": sc["onScreenText"] if sc else "", "description": sc["description"] if sc else ""} for n, (_, _, text, before) in enumerate(rows)]
    if sc and len(sc["onScreenText"]) >= 8:
        items.append({"id": len(items), "text": sc["onScreenText"], "mark": sc["onScreenText"], "source": "screen", "description": sc["description"]})
    with lock:
        cfg = dict(DETECT)
    preds = detect_known(items, cfg) if items and cfg["backend"] != "keywords" else []
    claims = [i for i, p in enumerate(preds) if p["gates"].get("claim", 0) >= NOW_CLAIM_P and p["gates"].get("quote", 1) < CLAIM_QUOTE_MAX
              and len(items[i]["text"].split()) >= MIN_WORDS]
    if not claims:
        if cfg["techniques"]:
            return footnote_now(session)
        return {"ok": True, "forced": True, "noClaim": True, "text": "No claim to check in the last %d s." % NOW_CLAIM_SECONDS,
                "nowSentences": len(items), "nowMs": round((time.time() - t0) * 1000)}
    # The highest claim score; speech before the screen text, the newest on a tie.
    best = max(claims, key=lambda i: (round(preds[i]["gates"]["claim"], 2), items[i]["source"] == "speech", i))
    it, p = items[best], preds[best]
    with cond:
        r = next((x for x in reversed(results) if x["session"] == session and x.get("kind") != "check"
                  and similar(x.get("sentence") or x["text"], it["text"])), None)
        c = start_check(r or {"id": None, "session": session}, it["text"], p["gates"]["claim"], p.get("backend"), press=True)
        item = next((x for x in reversed(results) if x.get("checkId") == c["id"]), None)
        return public(item) | {"pending": c["status"] == "pending", "nowSentences": len(items), "nowMs": round((time.time() - t0) * 1000)}


def footnote_now(session):
    """POST /api/now: judges the last NOW_SECONDS of sentences and the screen text at once and publishes the single best
    candidate as a result with `forced: true`, also in the unsure band. Below the unsure threshold the result says so:
    a technique is never invented. Returns the result."""
    t0 = time.time()
    with lock:
        cfg = dict(DETECT)
        if paused(session):
            return {"ok": True, "forced": True, "text": screens[session].get("pauseReason") or "Paused", "note": False}
        sc = dict(screens[session]) if session in screens else None
        video = video_of(session)
        cut = time.time() - NOW_SECONDS
        rows = [r for r in recent.get(session, ()) if r[0] >= cut] or list(recent.get(session, ()))[-2:]
    items = [{"id": n, "text": text, "mark": text, "source": "speech", "before": tail(before, 400),
              "screen_text": sc["onScreenText"] if sc else "", "description": sc["description"] if sc else ""} for n, (_, _, text, before) in enumerate(rows)]
    if sc and len(sc["onScreenText"]) >= 8:
        items.append({"id": len(items), "text": sc["onScreenText"], "mark": sc["onScreenText"], "source": "screen", "description": sc["description"]})
    preds = detect.detect(items, backend=cfg["backend"], threshold=cfg["note"], unsure=cfg["unsure"]) if items and cfg["backend"] != "keywords" else []

    def p_top(p):
        return p["probs"].get(p["top"], 0.0) if p["top"] else 0.0
    with cond:
        # A candidate that already has a note (an earlier press, or the note during playback) ranks below a new one, and
        # speech ranks above the screen text (a caption stays on screen for the whole video). So a second press judges
        # the newest speech instead of returning the first note again. Among notes already given, the newest wins.
        dups = [find_duplicate(session, video.get("videoId"), it["text"]) for it in items]
        best = max(range(len(preds)), key=lambda i: (preds[i]["note"] or preds[i]["unsure"], dups[i] is None, items[i]["source"] == "speech",
                                                     p_top(preds[i]) if dups[i] is None else i), default=None)
        job = {"session": session, "seq": 0, "arrival": next_arrival_id(), "received": t0} | video
        extra = {"forced": True, "nowSentences": len(items), "nowMs": round((time.time() - t0) * 1000)}
        if best is not None and (preds[best]["note"] or preds[best]["unsure"]):
            it, p = items[best], preds[best]
            det = summarize([it], [p], cfg, t0)
            old = dups[best]
            if old:
                return public(old) | {"forced": True, "alreadyNoted": True} | {"checks": check_items(session, lambda c: c["resultId"] == old["id"])}
            info = detect.BY_ID[p["top"]]
            if p["unsure"]:
                # The best candidate in the unsure band: shown on request, marked unsure.
                extra |= {"label": info["label"], "short": info["short"], "why": info["why"], "explain": info["explain"]}
            r = add_result(job, it["text"], time.time() - t0, None, source="audio" if it["source"] == "speech" else "screen", det=det,
                           speech=it["source"] == "speech", extra=extra)
        else:
            r = add_result(job, NO_NOTE, time.time() - t0, None, speech=False, extra=extra | {"noNote": True,
                           "best": {"top": preds[best]["top"], "p": round(p_top(preds[best]), 3)} if best is not None else None})
        cond.notify_all()
        counters["footnote_now"] += 1
        out = public(r) if r else {"ok": True, "forced": True, "text": NO_NOTE}
        judged = {it["text"] for it in items}
        # The newest check item of each sentence that "Footnote now" judged, also when the answer is "No note".
        return out | {"checks": check_items(session, lambda c: c["sentence"] in judged or (r and c["resultId"] == r["id"]))}


def check_items(session, want):
    """The newest results item of each check of the session that `want(check)` accepts. Lock held."""
    ids = {c["id"] for c in checks.get(session, []) if want(c)}
    newest = {}
    for r in results:
        if r.get("kind") == "check" and r["session"] == session and r["checkId"] in ids:
            newest[r["checkId"]] = public(r)
    return list(newest.values())


def next_arrival_id():
    """Lock held."""
    global next_arrival
    next_arrival += 1
    return next_arrival


def public(r):
    return {k: v for k, v in r.items() if k != "ts"} | {"ok": True}


def words_of(text):
    return [w for w in re.findall(r"\w+", text.lower()) if len(w) > 2]


def similar(a, b):
    """True when two texts say the same sentence: close as strings, or most words of the shorter one in the other."""
    wa, wb = set(words_of(a)), set(words_of(b))
    if not wa or not wb:
        return False
    if difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() >= 0.8:
        return True
    short = min(len(wa), len(wb))
    return short >= 3 and len(wa & wb) / short >= 0.7


def find_duplicate(session, video, text):
    """An earlier note or unsure result of the same video on the same sentence. Lock held."""
    now = time.time()
    for r in reversed(results):
        if now - r["ts"] > DUPLICATE_SECONDS:
            return None
        if r["session"] != session or r.get("videoId") != video or not (r["technique"] or r.get("unsure")):
            continue
        if similar(r.get("sentence") or r["text"], text):
            return r
    return None


def is_subtitle(job):
    """True when the video's recent speech already holds most words of the on-screen text: burned-in subtitles.
    The speech path judges those sentences. Raw chunk transcripts count, so open sentences count too. Lock held."""
    words = words_of(job["text"])
    if len(words) < 2:
        return False
    heard = set()
    for ts, video, text in spoken.get(job["session"], ()):
        if video == job.get("videoId") and abs(ts - job["received"]) < 40:
            heard.update(words_of(text))
    return sum(w in heard for w in words) / len(words) >= SUBTITLE_OVERLAP


def detect_screen(job):
    """Runs the detector on new burned-in text of a video (pool thread) and adds a result for a note or unsure."""
    t0 = time.time()
    with lock:
        cfg = dict(DETECT)
        text = strip_own(job["text"], own_texts_of(job["session"]))  # a check that finished since the read
        if len(norm(text)) < 8:
            counters["screen_own_skipped"] += 1
            return
        job = job | {"text": text}
        if is_subtitle(job):
            counters["screen_subtitles_skipped"] += 1
            return
    item = {"id": 0, "text": job["text"], "mark": job["text"], "source": "screen", "description": job.get("description", "")}
    preds = detect_known([item], cfg)
    det = summarize([item], preds, cfg, t0)
    with cond:
        record_detection(job["session"], "screen", [item], preds, cfg)
        if det["note"] or det["unsure"]:
            add_result(job, job["text"], time.time() - job["received"], 0, source="screen", det=det)
        elif not det.get("error"):
            # No note (the technique questions are off): the claim gate alone decides on a check of the screen text.
            maybe_check({k: job.get(k) for k in ("session", "videoId", "author", "app", "handle")} | {"id": None, "text": job["text"], "source": "screen"}, det)


def speech_check(wav):
    """The decision model's answer to "does this audio contain intelligible speech". (p, ms, usd, error)."""
    return detect.ask_media({"type": "input_audio", "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"}}, SPEECH_Q)


def worker():
    while True:
        job = jobs.get()
        try:
            transcribe_job(job)
        except Exception as e:  # noqa: BLE001 - a bad chunk must never stop a worker
            with lock:
                counters["worker_errors"] += 1
                logs.append({"t": now_iso(), "source": "server", "session": job["session"], "line": "worker error: %r" % (e,)})
                gap(job)


def transcribe_job(job):
    t0 = time.time()
    # The speech check runs next to the transcription, so it adds no time.
    check = None
    if DETECT["speechCheck"] and detect.openrouter_key() and detect.decision_ok():
        check = detect_pool.submit(speech_check, job["wav"])
    with lock:
        lang = hint_lang(job)
    out, err = transcribe(job["wav"], HINT % lang if lang else "")
    if err:
        with lock:
            counters["transcribe_errors"] += 1
            logs.append({"t": now_iso(), "source": "server", "session": job["session"], "line": err})
            gap(job)
        detect_pool.submit(pump, job["session"])
        return
    text, credits = out["text"].strip(), float(out["credits"] or 0)
    p_speech, sp_ms, sp_usd, sp_err = check.result() if check else (None, 0, None, None)
    # One more try when the answer looks wrong: [no speech] while the speech check hears speech, or a short line in
    # another alphabet than the video's language (real Russian lines came back as Portuguese).
    retry = None
    if text == "[no speech]" and p_speech is not None and p_speech >= 0.7:
        retry = ("no speech, speech check %.2f" % p_speech, HINT % lang if lang else "")
    elif lang in ("Russian", "Ukrainian", "Ukrainian or Russian") and lang_of(text) in ("Latin", "English") and len(text.split()) < 10:
        retry = ("alphabet of %s, expected %s" % (lang_of(text), lang), STRICT_HINT % lang)
    if retry:
        again, err2 = transcribe(job["wav"], retry[1])
        with lock:
            counters["transcribe_retries"] += 1
            logs.append({"t": now_iso(), "source": "server", "session": job["session"],
                         "line": "transcribe again (%s): %r -> %r" % (retry[0], text[:60], (again or {}).get("text", err2)[:60])})
        if again:
            text, credits = again["text"].strip(), credits + float(again["credits"] or 0)
    latency = time.time() - t0
    words = len(text.split())
    speech = bool(text) and text != "[no speech]" and words >= 2
    reason = ""
    if speech and lang in ("Russian", "Ukrainian", "Ukrainian or Russian") and lang_of(text) == "Latin" and words < 10:
        speech, reason = False, "language %s, expected %s" % (lang_of(text), lang)
    elif speech and p_speech is not None and p_speech < DETECT["speechMin"]:
        speech, reason = False, "speech check %.2f" % p_speech
    elif speech and words < 4 and p_speech is not None and p_speech < DETECT["speechShort"]:
        speech, reason = False, "short, speech check %.2f" % p_speech
    elif speech and words < 3 and p_speech is None:
        speech, reason = False, "short, no speech check"
    elif text and text != "[no speech]" and not speech:
        reason = "one word"
    with lock:
        add_usage(job["session"], "audio", credits)
        if sp_usd:
            detect_usage.append((time.time(), job["session"], "speech", float(sp_usd)))
        if sp_err:
            counters["speech_check_errors"] += 1
        if reason:
            counters["no_speech_dropped"] += 1
        if speech:
            remember_lang(job, text)
            spoken.setdefault(job["session"], collections.deque(maxlen=30)).append((job["received"], job.get("videoId"), text))
        for c in chunks:
            if c["name"] == job.get("name"):
                c |= {"text": text[:300], "speech": speech, "transcribeS": round(latency, 2)}
        extra = {"speechP": round(p_speech, 3) if p_speech is not None else None, "speechMs": sp_ms, "hint": lang}
        if reason:
            extra["noSpeech"] = reason
        deliver_locked(job | {"latency": latency, "credits": credits}, {"speech": speech, "text": text, "extra": extra, "result": not speech})
    detect_pool.submit(pump, job["session"])


def session_results(session, since=0):
    return [r for r in results if (not session or r["session"] == session) and r["id"] > since]


def config_of(session):
    return configs.setdefault(session, {"updatePath": "auto", "chunkSeconds": 4, "alerts": True, "alertSound": False, "startId": 0, "startedAt": 0,
                                        "screenPath": "jpeg", "fps": 1})


# MARK: Screen reading and "now playing"


def screen_of(session):
    return screens.setdefault(session, {"videoId": 0, "videoStartedAt": 0, "videoArrival": 0, "app": "", "lastTikTokAt": 0,
                                        "pausedAt": 0, "pausedSince": 0, "handle": "", "description": "", "hashtags": [], "onScreenText": "",
                                        "likes": "", "comments": "", "shares": "", "commentsOpen": False, "visibleComments": [],
                                        "flagged": [], "screenText": "", "checked": [], "reads": 0, "candidate": None, "kind": "",
                                        "pauseReason": ""})


def norm(text):
    return " ".join(str(text or "").lower().split())


def handle_key(handle):
    """"@ZVEZDANEWS" and "Zvezdanews" are the same creator."""
    return "".join(c for c in str(handle or "").lower() if c.isalnum())


def caption_of(x):
    """The title or caption. A post or an article without one is known by the start of its text."""
    cap = norm(x["description"] + " " + " ".join(x["hashtags"]))
    if not cap and x.get("kind") in ("post", "article"):
        cap = norm(x.get("onScreenText", ""))[:160]
    return cap


def video_change(cur, parsed):
    """How a read differs from the video in front: "first" (nothing known yet), "both" (handle and caption changed),
    "one" (only one changed: a misread or a watermark read as the handle) or "" (the same video)."""
    handle, desc = handle_key(parsed["handle"]), caption_of(parsed)
    if not handle and not desc:
        # Nothing to compare (the video is loading, or a bare video without an author or a title). Outside TikTok the
        # first media read still starts a video, so its results group under it.
        return "first" if not cur["videoId"] and parsed.get("app") != "tiktok" else ""
    old_handle, old_desc = handle_key(cur["handle"]), caption_of(cur)
    if not old_handle and not old_desc:
        return "first"
    if parsed.get("appChanged"):
        return "both"
    handle_changed = bool(handle and old_handle and handle != old_handle)
    desc_changed = bool(desc and old_desc and difflib.SequenceMatcher(None, desc, old_desc).ratio() < 0.6)
    if handle_changed and desc and old_desc and not desc_changed and len(desc) >= 20:
        return ""  # the same caption with another "handle": a watermark or a sticker read as the handle, not a new video
    if handle_changed and desc_changed:
        return "both"
    return "one" if handle_changed or desc_changed else ""


def same_read(a, b):
    """Two reads show the same handle and caption."""
    return handle_key(a["handle"]) == handle_key(b["handle"]) and difflib.SequenceMatcher(None, caption_of(a), caption_of(b)).ratio() >= 0.6


def apps_of(session):
    """ "any" or "tiktok": the session's own setting, else the server default. Lock held."""
    return config_of(session).get("apps") or DETECT["apps"]


def pause_reason(session, parsed):
    """Why a screen read pauses the session ("" when it shows media to analyse). Lock held."""
    if parsed.get("gateReason"):
        return parsed["gateReason"]
    if parsed.get("kind") == "private":
        return "Paused: private screen"
    if not parsed.get("media"):
        return "Paused: no media on screen"
    if apps_of(session) == "tiktok" and parsed.get("app") != "tiktok":
        return "Paused: not TikTok"
    return ""


def paused(session, now=None):
    """True while the newest screen read shows no media to analyse (a private screen, or another app). Lock held."""
    sc = screens.get(session)
    now = now or time.time()
    return bool(sc) and sc["pausedAt"] > sc["lastTikTokAt"] and now - sc["pausedAt"] < PAUSE_SECONDS


def release_held(session, now):
    """A TikTok read: the audio held during the pause goes to transcription, stamped with the video in front. Lock held."""
    for ts, job in held.pop(session, []):
        if now - ts >= HOLD_SECONDS:
            counters["audio_discarded_not_tiktok"] += 1
            gap(job)
            continue
        try:
            jobs.put_nowait(job | video_of(session))
            counters["audio_released"] += 1
        except queue.Full:
            counters["dropped_queue_full"] += 1
            gap(job)


def drop_held(session):
    """A read of another app: the held audio is dropped unheard (privacy guard). Lock held."""
    for _, job in held.pop(session, []):
        counters["audio_discarded_not_tiktok"] += 1
        gap(job)


# Footnote's own text on the screen (its result page, its home page, its Dynamic Island over another app) must never
# become a claim, a sentence, a new video or a check: the screen read of Footnote showing a result was checked again.
OWN_TEXT = re.compile(r"\bfootnote|sources? (contradict|support|agree|differ|mixed|are mixed)|no sources? (found|says)|earlier video\s*:|"
                      r"checking claim", re.I)


def own_texts_of(session):
    """Lock held: the texts Footnote itself shows for this session: each check's claim, line and source titles."""
    out = []
    for c in checks.get(session, []):
        out += claim_texts(c) + [c.get("line") or ""] + [s.get("title", "") for s in c.get("sources") or []]
    return [t for t in dict.fromkeys(out) if len(norm(t)) >= 12]


def is_own(piece, texts):
    """True when a piece of screen text is Footnote's own: one of its phrases, or a claim, line or source of a check."""
    if OWN_TEXT.search(piece):
        return True
    p = norm(piece).strip(" .*\u2026\"'\u201c\u201d")
    if len(p) < 8:
        return False
    return any((len(p) >= 16 and p in norm(t)) or norm(t) in p or claim_match(piece, t) for t in texts)


def strip_own(text, texts):
    """The screen text without Footnote's own pieces (split at sentence ends and line breaks). The sentence just before
    an asterisk is Footnote's marked claim ("... mansion in Florida*"), also when no check of this session says it."""
    parts = re.split(r"(\*+)(?=\s|$)", text or "")  # not "f*ck"
    keep = []
    for i in range(0, len(parts), 2):
        pieces = [x for x in re.split(r"(?<=[.!?\u2026])\s+|\n+", parts[i]) if x.strip()]
        if i + 1 < len(parts) and pieces:
            pieces = pieces[:-1]
        keep += [x for x in pieces if not is_own(x, texts)]
    return " ".join(keep).strip()


def own_screen(session, parsed):
    """Lock held: a media read without Footnote's own text. The Footnote app itself (its name as the app or the account,
    or its phrases with no other account or caption left, outside TikTok) is no media: the read pauses as "no media on
    screen". Footnote's Island over a video is stripped from the read; the video goes on."""
    if not parsed.get("media"):
        return parsed
    blank = clean_screen({"app": parsed.get("app", "unknown"), "kind": "other"})
    if "footnote" in parsed.get("app", "") or handle_key(parsed.get("handle")).startswith("footnote"):
        counters["screen_own_app"] += 1
        return blank
    texts = own_texts_of(session)
    fields = [parsed.get("handle", ""), parsed.get("description", ""), parsed.get("onScreenText", "")] + list(parsed.get("visibleComments") or [])
    hit = any(OWN_TEXT.search(f or "") for f in fields)
    out = dict(parsed)
    out["onScreenText"] = strip_own(parsed.get("onScreenText", ""), texts)
    out["visibleComments"] = [x for x in parsed.get("visibleComments") or [] if not is_own(x, texts)]
    if hit:
        out["description"] = strip_own(parsed.get("description", ""), texts)
        if OWN_TEXT.search(parsed.get("handle", "")):
            out["handle"] = ""
        if parsed.get("app") != "tiktok" and not handle_key(out["handle"]) and not out["description"]:
            counters["screen_own_app"] += 1
            return blank
    if out != parsed:
        counters["screen_own_stripped"] += 1
        out["ownStripped"] = True
    return out


def restamp(session, old_video, since):
    """Lock held: results (and their checks and remembered sentences) that arrived after the first read of a new video
    but before the second read confirmed it belong to the new video, not to the one before (a debunk video's first
    sentence must not count as the demo video's speech)."""
    moved = set()
    video = video_of(session)
    for r in results:
        if r["session"] == session and r.get("videoId") == old_video and r.get("arrival", 0) > since and r.get("kind") != "check":
            r |= video
            moved.add(r["id"])
    if not moved:
        return
    texts = {r["text"] for r in results if r["id"] in moved}
    rows = recent.get(session)
    if rows:
        recent[session] = collections.deque(((t, video["videoId"] if v == old_video and x in texts else v, x, b) for t, v, x, b in rows), maxlen=rows.maxlen)
    for c in checks.get(session, []):
        if c["resultId"] in moved and c["videoId"] == old_video:
            c.update(videoId=video["videoId"], handle=video.get("handle"), author=video.get("author"), app=video.get("app"))
    counters["results_restamped"] += len(moved)


def apply_screen(job, parsed, latency, credits):
    """Merges one screen read into the session's "now playing" state. Lock held."""
    global next_frame_id
    session = job["session"]
    sc = screen_of(session)
    now = time.time()
    sc["reads"] += 1
    entry = {"t": now_iso(), "session": session, "seq": job["seq"], "kind": job["kind"], "latency": round(latency, 2), "credits": credits,
             "bytes": job.get("bytes", 0), "clientMs": job.get("clientMs"), "parsed": parsed, "newVideo": False, "gate": job.get("gate")}
    parsed = own_screen(session, parsed)
    reason = pause_reason(session, parsed)
    if reason:
        sc["mediaStreak"] = 0
        if parsed.get("kind") != "private" and not paused(session, now):
            # A screen without media pauses only after PAUSE_EVIDENCE_SECONDS of such reads in a row: one odd frame
            # (a swipe, a loading video) must not flip the Island. Nothing of the read is kept meanwhile.
            sc["noMediaSince"] = sc.get("noMediaSince") or now
            if now - sc["noMediaSince"] < PAUSE_EVIDENCE_SECONDS:
                counters["screen_pause_waiting"] += 1
                screen_log.append(entry | {"parsed": {"app": parsed.get("app", "other"), "kind": parsed.get("kind", "other")}, "discarded": True,
                                           "pauseCandidate": reason})
                return
        # Privacy guard: the broadcast sees every app. Keep nothing of a private screen or a screen without media.
        if not paused(session, now):
            sc["pausedSince"] = now
        sc["pausedAt"] = now
        sc["app"] = parsed.get("app", "other")
        sc["kind"] = parsed.get("kind", "other")
        sc["pauseReason"] = reason
        counters["screen_discarded_private" if parsed.get("kind") == "private" else "screen_discarded_not_media"] += 1
        drop_held(session)
        entry["parsed"] = {"app": sc["app"], "kind": sc["kind"]}
        entry["discarded"] = True
        entry["pauseReason"] = reason
        screen_log.append(entry)
        return
    sc["noMediaSince"] = 0
    sc["blind"] = False
    if paused(session, now):
        # A pause ends after UNPAUSE_READS media reads in a row (one misread must not flip the Island either).
        sc["mediaStreak"] = sc.get("mediaStreak", 0) + 1
        if sc["mediaStreak"] < UNPAUSE_READS:
            counters["screen_unpause_waiting"] += 1
            screen_log.append(entry | {"unpauseCandidate": True})
            return
    sc["mediaStreak"] = 0
    # A change of app counts as a change of the video's identity.
    known = sc.get("mediaApp", "") not in ("", "unknown") and parsed["app"] != "unknown"
    if known and parsed["app"] != sc["mediaApp"] and sc["handle"] + sc["description"]:
        parsed = parsed | {"appChanged": True}
    if parsed["app"] != "unknown":
        sc["mediaApp"] = parsed["app"]
    sc["app"] = parsed["app"]
    sc["kind"] = parsed["kind"]
    sc["pauseReason"] = ""
    sc["lastTikTokAt"] = now  # the last read with media (the name is from the TikTok version)
    # A new video needs a new handle and a new caption, or the same change in two reads in a row: one misread (or a
    # watermark read as the handle) must not split a video.
    # A new video needs the same new identity in two reads in a row (one frame of a swipe or a misread must not start a
    # video); only the first video of a run starts on one read.
    change = video_change(sc, parsed)
    cand = sc.get("candidate")
    new = change == "first" or bool(cand and change in ("one", "both") and (
        same_read(cand, parsed) or (change == "both" and handle_key(parsed["handle"]) and handle_key(parsed["handle"]) == handle_key(cand["handle"]))))
    if change in ("one", "both") and not new:
        # The candidate keeps the arrival of the read that first showed it: a confirmed video starts there.
        keep = cand and same_read(cand, parsed)
        sc["candidate"] = {k: parsed.get(k, "") for k in ("handle", "description", "hashtags", "kind", "onScreenText")} | {
            "arrival": cand["arrival"] if keep else job["arrival"], "received": cand["received"] if keep else job["received"]}
    else:
        sc["candidate"] = None
    if new:
        # The new video's identity replaces the old one, also with empty fields (an article without an author).
        for k in ("handle", "description", "hashtags", "onScreenText", "likes", "comments", "shares"):
            sc[k] = [] if k == "hashtags" else ""
        first_video = not sc["videoId"]
        old_video = sc["videoId"]
        # A video of this run again (its loop, or its next claim after the frames stopped): the same number.
        again = next((v for v in reversed(seen_videos.get(session, [])) if v["videoId"] != old_video and same_read(v, parsed)
                      and (handle_key(parsed["handle"]) or len(caption_of(parsed)) >= 20)), None)
        if again:
            sc["videoId"] = again["videoId"]
            counters["videos_again"] += 1
        else:
            sc["videoId"] = max(sc["videoId"], video_counter.get(session, 0)) + 1
            video_counter[session] = sc["videoId"]
        # The video started at the first read that showed it (the second read confirms it).
        start = cand if cand and change in ("one", "both") else {"arrival": job["arrival"], "received": job["received"]}
        sc["videoStartedAt"] = start["received"]
        sc["videoArrival"] = start["arrival"]
        sc["flagged"] = []
        sc["screenText"] = ""
        sc["checked"] = []
        entry["newVideo"] = not again
        counters["videos"] += 0 if again else 1
        logs.append({"t": now_iso(), "source": "server", "session": session,
                     "line": "%s %d (%s %s): %s %s" % ("video again" if again else "new video", sc["videoId"], parsed["app"], parsed["kind"], parsed["handle"], parsed["description"][:60])})
    for k in SCREEN_KEYS:
        if sc["candidate"] and k in ("handle", "description", "hashtags"):
            continue  # an unconfirmed change does not replace the video's identity
        if parsed[k] or k in ("commentsOpen", "visibleComments"):
            sc[k] = parsed[k]
    if new:
        seen = [v for v in seen_videos.get(session, []) if v["videoId"] != sc["videoId"]]
        seen_videos[session] = (seen + [{k: sc[k] for k in ("videoId", "handle", "description", "hashtags", "kind", "onScreenText")}])[-8:]
    entry["videoId"] = sc["videoId"]
    entry["candidate"] = bool(sc["candidate"])
    if new and not first_video and sc["videoArrival"] < job["arrival"]:
        restamp(session, old_video, sc["videoArrival"])
    if new and first_video:
        # Results that came before the first screen read belong to this first video.
        for r in results:
            if r["session"] == session and r.get("videoId") is None and now - r["ts"] < 120:
                r |= video_of(session)
        for c in checks.get(session, []):
            if c["videoId"] is None and now - c["claimTs"] < 120:
                c.update(video_of(session))
    release_held(session, now)
    screen_log.append(entry)
    next_frame_id += 1
    # The image stays in memory only for a session that asked for debug frames (X-Lens-Debug: 1). The text stays.
    keep = bool(job.get("debug"))
    frames.append({"id": next_frame_id, "t": entry["t"], "session": session, "seq": job["seq"], "kind": job["kind"],
                   "jpeg": job.get("jpeg") if keep else None, "lines": job.get("lines"), "w": job.get("w"), "h": job.get("h"),
                   "bytes": job.get("bytes", 0), "latency": entry["latency"], "credits": credits, "parsed": parsed, "videoId": sc["videoId"]})
    if DETECT["backend"] == "keywords":
        # The prototype's keyword rules over the caption and the text in the video. Each technique once per video.
        text = " ".join(x for x in (parsed["onScreenText"], parsed["description"], " ".join("#" + h for h in parsed["hashtags"])) if x)
        if text and norm(text) != norm(sc["screenText"]):
            sc["screenText"] = text
            hits = [h for h in classify(text) if h["name"] not in sc["flagged"]]
            if hits:
                sc["flagged"].append(hits[0]["name"])
                add_result({"session": session, "seq": job["seq"], "arrival": job["arrival"], "received": job["received"]}, text, latency, 0, source="screen")
        return None
    # The detector runs once per distinct burned-in text of a video (near repeats of a read count as the same text).
    text = " ".join(parsed["onScreenText"].split())
    key = norm(text)
    if len(key) < 8 or any(difflib.SequenceMatcher(None, key, old).ratio() > 0.85 for old in sc["checked"]):
        return None
    sc["checked"].append(key)
    sc["screenText"] = text
    return {"session": session, "seq": job["seq"], "arrival": job["arrival"], "received": job["received"], "text": text[:400],
            "description": sc["description"]} | video_of(session)


PRIVATE_FRAME_Q = {"type": "noul", "instructions": "Does this phone screen show private or sensitive content?",
                   "criteria": {"true": "Messages or chats, mail, banking or payments, passwords or sign-in forms, health data, the photo "
                                        "library, settings, the home screen or lock screen, or a keyboard typing a text.",
                                "false": "A public video, reel, social post, news article or livestream, or an app menu or search page."}}


SAME_Q = ("The state has two phone screens: the first is a reference, the second is the screen now. Do both screens show the same video "
          "or post: the same account name and the same caption text?",
          "The account name and the caption are the same in both screens. The picture of the video itself may differ: a video changes "
          "from second to second.",
          "The account name or the caption differs, or the second screen shows another app, a menu, or no video.")


def same_video(jpeg, ref):
    """Jev's answer to "does this frame show the same video as the reference frame": a probability, or None on an error."""
    p, _, usd, err = detect.ask_frame(jpeg, *SAME_Q, timeout=5, ref=ref)
    return p, usd


def tiktok_gate(jpeg, apps="any"):
    """The decision model's answer to "is media (or TikTok) in front" for a frame, and in the same call "is it private":
    (p, ms, usd, error, p_private)."""
    more = {"private": PRIVATE_FRAME_Q}
    p, ms, usd, err = detect.ask_frame(jpeg, *(TIKTOK_Q if apps == "tiktok" else MEDIA_Q), timeout=5, more=more)
    return p, ms, usd, err, more.get("private")


def vision_worker():
    while True:
        job = vision_jobs.get()
        try:
            read_job(job)
        except Exception as e:  # noqa: BLE001 - a bad frame must never stop a worker
            with lock:
                finish_read(job["session"])
                counters["vision_worker_errors"] += 1
                logs.append({"t": now_iso(), "source": "server", "session": job["session"], "line": "vision worker error: %r" % (e,)})


def read_job(job):
    t0 = time.time()
    gate = None
    if job.get("jpeg") and DETECT["tiktokGate"] and detect.openrouter_key() and detect.decision_ok():
        # A fast yes or no first (about 0.3 s): a frame of another app is dropped without the slower read.
        with lock:
            apps = apps_of(job["session"])
            ref = ref_frames.get(job["session"])
        same = detect_pool.submit(same_video, job["jpeg"], ref) if ref else None
        p, ms, usd, err, p_private = tiktok_gate(job["jpeg"], apps)
        gate = {"p": round(p, 3) if p is not None else None, "private": round(p_private, 3) if p_private is not None else None, "ms": ms, "error": err}
        with lock:
            if usd:
                detect_usage.append((time.time(), job["session"], "tiktok", float(usd)))
            counters["tiktok_gate_calls"] += 1
            if err:
                counters["tiktok_gate_errors"] += 1
        if p is not None and p < 0.5:
            with cond:
                finish_read(job["session"])
                counters["screen_gated_not_tiktok"] += 1
                private = p_private is not None and p_private >= 0.5
                reason = "Paused: private screen" if private else ("Paused: not TikTok" if apps == "tiktok" else "Paused: no media on screen")
                apply_screen(job | {"gate": gate}, clean_screen({"app": "unknown", "kind": "private" if private else "other"}) | {
                    "gateReason": reason}, time.time() - t0, 0)
                cond.notify_all()
            return
        p_same, same_usd = same.result() if same else (None, None)
        if p_same is not None and p_same >= SAME_T:
            # Jev: the same video as at the last read. No language model reads this frame.
            with cond:
                finish_read(job["session"])
                counters["screen_same_video"] += 1
                if same_usd:
                    detect_usage.append((time.time(), job["session"], "tiktok", float(same_usd)))
                sc = screen_of(job["session"])
                sc.update(lastTikTokAt=time.time(), noMediaSince=0, candidate=None, mediaStreak=0)
                release_held(job["session"], time.time())
                cond.notify_all()
            return
    parsed, credits, err = read_screen(jpeg=job.get("jpeg"), lines=job.get("lines"))
    latency = time.time() - t0
    with cond:
        finish_read(job["session"])
        add_usage(job["session"], "screen", credits)
        if err:
            counters["screen_errors"] += 1
            logs.append({"t": now_iso(), "source": "server", "session": job["session"], "line": err})
            return
        counters["screen_reads"] += 1
        t_apply = time.time()
        todo = apply_screen(job | {"gate": gate}, parsed, latency, credits)
        sc = screens.get(job["session"]) or {}
        if job.get("jpeg") and not sc.get("candidate") and sc.get("lastTikTokAt", 0) >= t_apply:
            ref_frames[job["session"]] = job["jpeg"]  # the confirmed video in front: Jev compares the next frames with it
        cond.notify_all()
    if todo:
        # Wait for the transcript of the same moment first: then a burned-in subtitle can be told from other text.
        timer = threading.Timer(SUBTITLE_WAIT, lambda: detect_pool.submit(detect_screen, todo))
        timer.daemon = True
        timer.start()


def finish_read(session):
    """A read ended: the newest frame that waited for it is read next, after VISION_GAP. Lock held."""
    vision_done[session] = time.time()
    if session in pending_frames:
        start_pending_later(session, VISION_GAP)
    else:
        vision_busy.discard(session)


def start_pending_later(session, delay):
    """The session stays busy until its pending frame starts. Lock held."""
    vision_busy.add(session)
    timer = threading.Timer(max(0.0, delay), start_pending, (session,))
    timer.daemon = True
    timer.start()


def start_pending(session):
    with lock:
        job = pending_frames.pop(session, None)
        if not job:
            vision_busy.discard(session)
            return
        try:
            vision_jobs.put_nowait(job)
            counters["frames_pending_read"] += 1
        except queue.Full:
            vision_busy.discard(session)
            counters["frames_skipped_queue_full"] += 1


def watching(session, now=None):
    """Lock held: screen frames are read only while a claim that Jev passed is under check or its result is held. Before
    that no model sees a frame: Jev is the first gate."""
    now = now or time.time()
    return any((c["status"] == "pending" and now - c["claimTs"] < CHECK_DEADLINE) or c["held"] or c.get("delivering")
               for c in checks.get(session, []))


def stop_watching(session):
    """Lock held: no claim is under check, so the video identity is unknown from here (no frame is read). The next
    claim's first read starts a video, and the sentences before it belong to that video."""
    sc = screens.get(session)
    if sc and not sc.get("blind"):
        video_counter[session] = max(video_counter.get(session, 0), sc["videoId"])
        sc.update(blind=True, videoId=0, handle="", description="", hashtags=[], onScreenText="", candidate=None, checked=[],
                  screenText="", pausedAt=0, mediaApp="", noMediaSince=0)
        for _, job in held.pop(session, []):
            gap(job)
    ref_frames.pop(session, None)


def start_watching(session, c):
    """Lock held: Jev passed a claim. The newest frame (kept unread, in memory) is read now, and the agent gets it."""
    job = last_frames.pop(session, None)
    if not job or time.time() - job["received"] > 6:
        return
    if job.get("jpeg"):
        check_frames[c["id"]] = job["jpeg"]
    if session in vision_busy:
        return
    try:
        vision_jobs.put_nowait(job)
        vision_busy.add(session)
        frame_hash[session] = job["digest"]
    except queue.Full:
        counters["frames_skipped_queue_full"] += 1


def accept_screen(session, digest):
    """Decides if a frame goes to the router. Returns a reason to skip, or None. Lock held."""
    if digest == frame_hash.get(session):
        counters["frames_skipped_same"] += 1
        # The same TikTok screen (a paused video): it is still TikTok, so the handle stays fresh.
        sc = screens.get(session)
        if sc and sc["lastTikTokAt"] > sc.get("pausedAt", 0):
            sc["lastTikTokAt"] = time.time()
        return "same frame"
    if config_of(session).get("running") is False:
        counters["frames_skipped_stopped"] += 1
        return "not listening"
    if session in vision_busy or time.time() - vision_done.get(session, 0) < VISION_GAP:
        return "busy"
    return None


def spoken_line(session, speech):
    """The newest speech for the Island: the last judged sentence and the open sentence after it. Lock held."""
    last = speech[-1]["text"] if speech else ""
    a = asm.get(session)
    t = a and a["tail"]
    return (last + " " + t["text"]).strip() if t else last


def live_state(session, now=None):
    """The Live Activity content state for a session, the same JSON as Swift's ContentState. Lock held."""
    now = now or time.time()
    cfg = config_of(session)
    sc = screens.get(session)
    run = [r for r in results if r["session"] == session and r["id"] > cfg.get("startId", 0)]
    # Order by arrival of the audio, not by the end of the transcription: two workers can finish out of order.
    run.sort(key=lambda r: (r["arrival"], r["id"]))
    flagged = [r for r in run if r["technique"]]
    # The line and the shown flag belong to the video in front: only results that arrived after it started.
    since = sc["videoArrival"] if sc else 0
    video = [r for r in run if r["arrival"] > since]
    speech = [r for r in video if r["speech"]]
    video_flags = [r for r in video if r["technique"]]
    beat = next((h for h in reversed(heartbeats) if h.get("source") == "broadcast" and h.get("session") == session), None)
    live = bool(beat) and beat.get("state") == "running" and now - beat.get("ts", 0) < LIVE_SECONDS
    # A fake result (the app's "Simulate" buttons, or a test) counts as listening for a while.
    live = live or bool(run and run[-1].get("fake") and now - run[-1]["ts"] < 30)
    live = live or bool(sc and cfg.get("running") and (now - sc["lastTikTokAt"] < 10 or paused(session, now)))
    last_flag = video_flags[-1] if video_flags else (flagged[-1] if flagged and not sc else None)
    mode = "listening" if live else "idle"
    if live and paused(session, now):
        mode = "paused"
    elif last_flag and now - last_flag["ts"] < FOUND_SECONDS:
        mode = "found"
    updated = max([cfg.get("startedAt", 0)] + [r["ts"] for r in run] + ([sc["pausedSince"], sc["videoStartedAt"]] if sc else []))
    state = {
        "mode": mode,
        "line": tail(spoken_line(session, speech), 80) if mode != "paused" else "",
        "count": len(flagged),
        "chunks": len([r for r in run if r["source"] == "audio"]),
        # Never "now": a state with nothing new must compare equal to the last one, or every poll sends an update.
        "updatedAt": round((updated or started_at) - APPLE_EPOCH, 3),
    }
    if sc and sc["reads"]:
        state["videoNotes"] = len(video_flags)
    if mode == "paused" and sc:
        state["pauseReason"] = sc.get("pauseReason") or "Paused"
    found_check = next((r for r in reversed(run) if r.get("kind") == "check" and r.get("notify") and r["status"] != "pending"), None)
    if found_check and mode != "paused" and now - found_check["ts"] < FOUND_SECONDS and (not last_flag or found_check["ts"] > last_flag["ts"]):
        # A delivered check (sources contradict the claim, or "deliver now"): the Island shows the claim and what the
        # sources report. The app adds "Sources contradict this." for stance "differ".
        mode = state["mode"] = "checked"
        same = not sc or found_check.get("videoId") in (None, sc["videoId"])  # no screen read yet: the video in front
        state |= {"flagText": found_check["claim"][:120], "checkLine": (found_check["line"] or "")[:90], "checkStance": found_check.get("stance"),
                  "checkSource": (found_check["sources"][0].get("site") or found_check["sources"][0]["title"])[:80] if found_check["sources"] else None,
                  "checkOf": "This video" if same else ("Earlier video: %s" % found_check["author"] if found_check.get("author") else "Earlier video")}
        state["updatedAt"] = round(max(found_check["ts"], updated or started_at) - APPLE_EPOCH, 3)
    newest_check = next((r for r in reversed(run) if r.get("kind") == "check"), None)
    if newest_check and newest_check["status"] == "pending" and now - newest_check["ts"] < CHECK_DEADLINE and mode != "paused":
        state["checkPending"] = True  # the app lights the logo
    if sc and sc["handle"] and mode != "paused" and now - sc["lastTikTokAt"] < SCREEN_FRESH_SECONDS:
        state["handle"] = sc["handle"][:40]
    if last_flag and mode not in ("paused", "checked"):
        state |= {
            "flagText": sentence_of(last_flag["text"], last_flag["mark"], 90),
            "flagMark": last_flag["mark"],
            "flagLabel": last_flag["label"],
            "flagShort": last_flag["short"],
            "flagWhy": last_flag["why"],
        }
    return state


def island_state(session, now=None):
    """What the Dynamic Island shows, as Swift's ContentState: the one state the server pushes through APNs. It has no
    transcript line and no counts, so it changes only when the Island must change: dim asterisk (listening), paused,
    waiting for the broadcast, the lit asterisk while a claim is under check or its result is held (`checking`, with
    `turn` counted up each TURN_SECONDS), and a delivered result for ISLAND_HOLD seconds (`checked`). Lock held."""
    now = now or time.time()
    cfg = config_of(session)
    sc = screens.get(session)
    beat = next((h for h in reversed(heartbeats) if h.get("source") == "broadcast" and h.get("session") == session), None)
    live = bool(beat) and beat.get("state") == "running" and now - beat.get("ts", 0) < LIVE_SECONDS
    live = live or bool(sc and cfg.get("running") and (now - sc["lastTikTokAt"] < 10 or paused(session, now)))
    is_paused = live and paused(session, now)
    mode = "paused" if is_paused else "listening" if live else "waiting" if cfg.get("running") else "idle"
    state = {"mode": mode, "line": "", "count": 0, "chunks": 0, "updatedAt": round((cfg.get("startedAt") or started_at) - APPLE_EPOCH)}
    if is_paused and sc:
        state["pauseReason"] = sc.get("pauseReason") or "Paused"
    turns = island_turns.setdefault(session, {"since": None, "base": 0})
    mine = [c for c in checks.get(session, []) if not c.get("duplicateOf")]
    lit = mode == "listening" and any((c["status"] == "pending" and now - c["claimTs"] < CHECK_DEADLINE) or c["held"] for c in mine)
    steps = min(TURN_STEPS, int((now - turns["since"]) / TURN_SECONDS)) if turns["since"] else 0
    if lit and not turns["since"]:
        turns["since"] = now
    elif not lit and turns["since"]:
        turns.update(since=None, base=turns["base"] + steps)
        steps = 0
    found = next((r for r in reversed(results) if r["session"] == session and r.get("kind") == "check" and r.get("notify")
                  and r["status"] != "pending" and r["id"] > cfg.get("startId", 0)), None)
    if found and not is_paused and now - found["ts"] < ISLAND_HOLD:
        label, short = STANCE_WORDS.get("none" if found["status"] == "nothing" else found.get("stance"), ("Checked", "Checked"))
        same = not sc or found.get("videoId") in (None, sc["videoId"])
        names = [(x.get("site") or x.get("title") or "")[:40] for x in found["sources"][:2]]
        who = found.get("handle") or found.get("author")
        state |= {"mode": "checked", "flagText": found["claim"][:120], "flagLabel": label, "flagShort": short,
                  "checkLine": (found["line"] or "")[:90], "checkStance": found.get("stance"), "noteId": found["of"] or found["id"],
                  "updatedAt": round(found["ts"] - APPLE_EPOCH)}
        if any(names):
            state["checkSource"] = ", ".join(n for n in names if n)
        if not same:
            state["checkOf"] = "Earlier video: %s" % who[:40] if who else "Earlier video"
    elif lit:
        state |= {"mode": "checking", "turn": turns["base"] + steps}
    return state


# MARK: Sessions and settings


def touch(session):
    """Marks a session as active. Lock held."""
    if session:
        last_seen[session] = time.time()


def sweeper():
    """Each second: skips a seq that never comes, judges an open sentence that waited FLUSH_SECONDS (or whose video
    ended, or that a pause cut), and drops audio held longer than HOLD_SECONDS."""
    while True:
        time.sleep(1)
        now = time.time()
        todo = []
        try:
            with lock:
                for session, a in list(asm.items()):
                    drain(a, now)
                    t = a["tail"]
                    sc = screens.get(session)
                    if t and (now - t["ts"] > FLUSH_SECONDS or paused(session, now)
                              or (sc and t["videoId"] is not None and sc["videoId"] != t["videoId"])):
                        flush_tail(a)
                    if a["out"]:
                        todo.append(session)
                for session in list(held):
                    old = [x for x in held[session] if now - x[0] >= HOLD_SECONDS]
                    held[session] = [x for x in held[session] if now - x[0] < HOLD_SECONDS]
                    for _, job in old:
                        counters["audio_discarded_not_tiktok"] += 1
                        gap(job)
                    if not held[session]:
                        del held[session]
        except Exception as e:  # noqa: BLE001 - the sweeper must keep running
            with lock:
                counters["sweeper_errors"] += 1
                logs.append({"t": now_iso(), "source": "server", "session": "", "line": "sweeper error: %r" % (e,)})
        for session in todo:
            detect_pool.submit(pump, session)


def janitor():
    """Frees the state of each session that sent nothing for IDLE_SECONDS (replay runs make many sessions)."""
    while True:
        time.sleep(min(60, max(1, IDLE_SECONDS // 4)))
        now = time.time()
        with lock:
            for session in list(checks):
                # Checks outlive an idle session ("deliver now" returns the last result for NOW_KEEP_SECONDS).
                checks[session] = [c for c in checks[session] if now - c["claimTs"] < CHECKS_KEEP_SECONDS]
                if not checks[session]:
                    del checks[session]
                    video_counter.pop(session, None)
            for session in [s for s, t in last_seen.items() if now - t > IDLE_SECONDS]:
                if session in screens:
                    video_counter[session] = max(video_counter.get(session, 0), screens[session]["videoId"])
                for table in (last_seen, configs, screens, vision_done, frame_hash, asm, held, pending_frames, last_frames, ref_frames, spoken, langs, recent, texts,
                              island_turns, push_sessions):
                    table.pop(session, None)
                vision_busy.discard(session)
                for f in [f for f in frames if f["session"] == session]:
                    frames.remove(f)
                if pusher:
                    pusher.reset(session)
                counters["sessions_freed"] += 1
                logs.append({"t": now_iso(), "source": "server", "session": session, "line": "session idle for %d s: state freed" % IDLE_SECONDS})


def detector_info():
    """The live detector settings, for /api/info, /debug.json and each benchmark or replay run. Lock held."""
    model = detect.MODELS.get(DETECT["backend"], DETECT["backend"])
    health = detect.status()
    primary = health.get(DETECT["backend"], {})
    media_ok = bool(detect.openrouter_key()) and detect.decision_ok()
    return dict(DETECT) | {"model": model, "specVersion": detect.SPEC["version"], "decisionKeySet": bool(detect.openrouter_key()),
                           "decisionDown": None if detect.decision_ok() else health.get("decision", {}).get("reason"),
                           "fallbackOrder": [DETECT["backend"]] + [b for b in detect.FALLBACK if b != DETECT["backend"]],
                           "primaryUp": primary.get("up", True), "primaryLastError": primary.get("lastError", ""),
                           "retryPrimaryEveryS": detect.DOWN_SECONDS, "backends": health,
                           "routerKeySet": bool(ROUTER_KEY), "speechModel": detect.DECISION_MODEL if DETECT["speechCheck"] else None,
                           "tiktokGateModel": detect.DECISION_MODEL if DETECT["tiktokGate"] else None,
                           # The speech check and the frame gate need audio and image input: only Gemini Decision on
                           # OpenRouter has it (the Hostinger decision models read text only). While it is down both
                           # are skipped: the transcription's [no speech] and the screen read decide alone.
                           "speechCheckActive": DETECT["speechCheck"] and media_ok, "frameGateActive": DETECT["tiktokGate"] and media_ok,
                           "hedgeSeconds": detect.HEDGE_SECONDS, "hedgeTo": detect.HEDGE_TO, "transcribeHedge": TRANSCRIBE_HEDGE, "transcribeHedgeModel": HEDGE_MODEL,
                           "visionHedge": VISION_HEDGE,
                           "claimThreshold": CLAIM_P, "checksPerHour": CHECKS_PER_HOUR,
                           "checksLastHour": sum(1 for t in check_starts if time.time() - t < 3600), "hedge": dict(detect.hedge_stats),
                           "cache": dict(detect.cache_stats), "checker": checker_info(), "router": router_info(), "openrouter": openrouter_balance()}


def ms_stats(ms):
    ms = sorted(ms)
    return {"medianMs": ms[len(ms) // 2] if ms else None, "maxMs": ms[-1] if ms else None}


def checker_info():
    """The claim lane for /api/info: what answered, how fast, how often. Lock held."""
    st = check_stats
    return {"gate": "the claim gate of each detector call (%s, hedged to %s after %.1f s)" % (DETECT["backend"], detect.HEDGE_TO, detect.HEDGE_SECONDS),
            "autoChecker": DETECT["autoChecker"], "pressChecker": DETECT["pressChecker"], "sentToChecker": st["sentToChecker"],
            "cacheHits": st["cacheHits"], "delivered": st["delivered"], "stances": dict(st["stances"]),
            "held": sum(1 for mine in checks.values() for c in mine if c["held"]),
            "running": sum(1 for mine in checks.values() for c in mine if c["status"] == "pending"),
            "deliverSeconds": DELIVER_SECONDS, "leaveSeconds": LEAVE_SECONDS, "claimsPerVideo": CLAIMS_PER_VIDEO, "factCheck": DETECT["factCheck"],
            "storedClaims": len(check_cache), "nowKeepSeconds": NOW_KEEP_SECONDS, "pauseEvidenceSeconds": PAUSE_EVIDENCE_SECONDS,
            "firstGate": "Jev: before it only the transcription runs; frames are read only while a claim is under check",
            "contract": {"public": ">= %.2f" % PUBLIC_MIN, "claim": ">= %.2f" % CLAIM_P, "quote": "< %.2f" % CLAIM_QUOTE_MAX},
            "agent": {"url": AGENT_URL, "keySet": bool(agent_key()), "timeoutS": AGENT_TIMEOUT,
                      "model": "GPT-6 Luna with web search, Hostinger AI Router",
                      **{k: v for k, v in st["agent"].items() if k != "ms"}, **ms_stats(st["agent"]["ms"])},
            "single": {"model": CHECK_MODEL, "search": "OpenRouter web plugin, " + CHECK_ENGINE,
                       **{k: v for k, v in st["single"].items() if k != "ms"}, **ms_stats(st["single"]["ms"])}}


def router_info():
    """The Hostinger AI Router for transcription and screen reads: the hedge to OpenRouter answers when it is slow. Lock held."""
    out = {}
    for kind, hedge_s in (("transcribe", TRANSCRIBE_HEDGE), ("vision", VISION_HEDGE)):
        streak = hedge_streak[kind]
        out[kind] = {"hedgeAfterS": FAST_HEDGE if streak >= HEDGE_STREAK else hedge_s, "openrouterWinsInARow": streak,
                     "hedged": counters[kind + "_hedged"], "openrouterWon": counters[kind + "_hedge_won"],
                     "state": "slow: OpenRouter answers" if streak >= HEDGE_STREAK else "ok"}
    return out


balance = {"at": 0.0, "value": None}


def openrouter_balance():
    """The OpenRouter key's remaining limit in USD, at most once a minute (in a pool thread, never blocking a request)."""
    if time.time() - balance["at"] > 60:
        balance["at"] = time.time()

        def fetch():
            req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": "Bearer " + detect.openrouter_key(),
                                                                                      "User-Agent": detect.USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    d = json.loads(r.read()).get("data") or {}
                balance["value"] = {"limitRemainingUsd": d.get("limit_remaining"), "usageUsd": d.get("usage"), "at": now_iso()}
            except Exception as e:  # noqa: BLE001
                balance["value"] = {"error": str(e)[:120], "at": now_iso()}
        if detect.openrouter_key():
            router_pool.submit(fetch)
    return balance["value"]


def info():
    with lock:
        return {"service": "footnote-lens", "commit": COMMIT, "started": now_iso(started_at), "uptime_s": round(time.time() - started_at),
                "detector": detector_info(), "transcribeModel": MODEL, "visionModel": VISION_MODEL, "activeSessions": len(last_seen),
                "keepsFrames": "only for requests with X-Lens-Debug: 1 or debug=1"}


def set_detector(data):
    """Changes detector settings at run time (until the next deploy). Returns (settings, error)."""
    with lock:
        new = dict(DETECT)
        try:
            if "backend" in data:
                if data["backend"] not in tuple(detect.BACKENDS) + ("keywords",):
                    return None, "backend must be one of %s or keywords" % ", ".join(detect.BACKENDS)
                new["backend"] = data["backend"]
            if "fallback" in data:
                order = [b for b in data["fallback"] if b in detect.BACKENDS] if isinstance(data["fallback"], list) else None
                if order is None:
                    return None, "fallback must be a list of backends: %s" % ", ".join(detect.BACKENDS)
                detect.FALLBACK[:] = order
            if "hedgeSeconds" in data:
                detect.HEDGE_SECONDS = max(0.0, float(data["hedgeSeconds"]))  # 0: no hedged detector call
            global TRANSCRIBE_HEDGE, VISION_HEDGE
            if "transcribeHedge" in data:
                TRANSCRIBE_HEDGE = max(0.0, float(data["transcribeHedge"]))  # 0: transcription on the Hostinger router only
            if "visionHedge" in data:
                VISION_HEDGE = max(0.0, float(data["visionHedge"]))
            if "hedgeTo" in data:
                if data["hedgeTo"] not in detect.BACKENDS:
                    return None, "hedgeTo must be one of %s" % ", ".join(detect.BACKENDS)
                detect.HEDGE_TO = data["hedgeTo"]
            for k in ("note", "unsure", "speechMin", "speechShort"):
                if k in data:
                    new[k] = min(1.0, max(0.0, float(data[k])))
            if "apps" in data:
                if data["apps"] not in ("any", "tiktok"):
                    return None, 'apps must be "any" or "tiktok"'
                new["apps"] = data["apps"]
            for k in ("speechCheck", "tiktokGate", "factCheck", "techniques"):
                if k in data:
                    new[k] = bool(data[k])
            for k in ("autoChecker", "pressChecker"):
                if k in data:
                    if data[k] not in CHECKERS:
                        return None, "%s must be agent or single" % k
                    new[k] = data[k]
        except (TypeError, ValueError) as e:
            return None, "bad value: %s" % e
        if new["unsure"] > new["note"]:
            return None, "unsure must not be above note"
        DETECT.update(new)
        detect.TECHNIQUES_ON = DETECT["techniques"]
        logs.append({"t": now_iso(), "source": "server", "session": "", "line": "detector settings: %s" % json.dumps(DETECT)})
        return detector_info(), None


# MARK: APNs Live Activity push


class Pusher:
    """Sends a liveactivity push when the Island state of a session changes."""

    def __init__(self):
        self.key = None
        self.jwt = ("", 0.0)
        self.client = None
        self.last = {}  # session -> last pushed state
        self.retry_at = {}  # session -> no push before this time (after an error)
        self.logged = {}  # session -> the mode of the last logged push
        self.stamp = 0
        try:
            import httpx  # noqa: F401
            from cryptography.hazmat.primitives import serialization

            if os.path.isfile(APNS_KEY_FILE):
                with open(APNS_KEY_FILE, "rb") as f:
                    self.key = serialization.load_pem_private_key(f.read(), password=None)
                push_status["available"] = True
            else:
                push_status["last_error"] = "no APNs key file at %s" % APNS_KEY_FILE
        except Exception as e:  # noqa: BLE001
            push_status["last_error"] = "push disabled: %r" % (e,)

    def token(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

        value, made = self.jwt
        if value and time.time() - made < 40 * 60:
            return value
        b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
        head = b64(json.dumps({"alg": "ES256", "kid": APNS_KEY_ID}).encode())
        claims = b64(json.dumps({"iss": APNS_TEAM, "iat": int(time.time())}).encode())
        r, s = decode_dss_signature(self.key.sign(("%s.%s" % (head, claims)).encode(), ec.ECDSA(hashes.SHA256())))
        value = "%s.%s.%s" % (head, claims, b64(r.to_bytes(32, "big") + s.to_bytes(32, "big")))
        self.jwt = (value, time.time())
        return value

    def send(self, cfg, state, alert, priority):
        import httpx

        if self.client is None:
            self.client = httpx.Client(http2=True, timeout=10)
        host = "api.push.apple.com" if cfg.get("apsEnvironment") == "production" else "api.sandbox.push.apple.com"
        # APNs keeps the update with the newest timestamp: two pushes in one second must not carry the same one.
        self.stamp = max(int(time.time()), self.stamp + 1)
        aps = {"timestamp": self.stamp, "event": "update", "content-state": state}
        if alert:
            # An alert expands the Island. No sound.
            aps["alert"] = {"title": state.get("flagLabel") or "Footnote", "body": state.get("flagText", "")}
        r = self.client.post(
            "https://%s/3/device/%s" % (host, cfg["pushToken"]),
            json={"aps": aps},
            headers={"authorization": "bearer " + self.token(), "apns-topic": APNS_TOPIC, "apns-push-type": "liveactivity", "apns-priority": str(priority)},
        )
        return r.status_code, r.text[:200]

    def loop(self):
        """Pushes `island_state` of each running session with a push token when it changed. One owner of the Island:
        while these pushes work (`push.ok` on /api/latest), the app sends no update of its own."""
        while True:
            time.sleep(0.3)
            if not self.key:
                continue
            now = time.time()
            with lock:
                todo = []
                for session, cfg in configs.items():
                    if not cfg.get("pushToken") or not cfg.get("running") or now < self.retry_at.get(session, 0):
                        continue
                    state = island_state(session, now)
                    before = self.last.get(session)
                    if state != before:
                        # Only a turn step of the lit asterisk is a low-priority push; a result has an alert.
                        step = bool(before) and state["mode"] == "checking" and before.get("mode") == "checking"
                        alert = state["mode"] == "checked" and (not before or before.get("noteId") != state.get("noteId")
                                                               or before.get("updatedAt") != state.get("updatedAt"))
                        todo.append((session, dict(cfg), state, alert, 5 if step else 10))
            for session, cfg, state, alert, priority in todo:
                try:
                    code, text = self.send(cfg, state, alert, priority)
                except Exception as e:  # noqa: BLE001
                    code, text = 0, repr(e)
                    self.client = None
                with lock:
                    st = push_sessions.setdefault(session, {"ok": False, "sent": 0, "errors": 0, "lastAt": "", "lastMode": "", "lastError": ""})
                    was_ok = st["ok"]
                    if code == 200:
                        self.last[session] = state
                        st.update(ok=True, sent=st["sent"] + 1, lastAt=now_iso(), lastMode="%s%s%s" % (
                            state["mode"], " turn %d" % state["turn"] if "turn" in state else "", " with alert" if alert else ""))
                        push_status["sent"] += 1
                        push_status["last_sent"] = "%s %s %s" % (now_iso(), session, st["lastMode"])
                        if not was_ok or state["mode"] == "checked" or (self.logged.get(session) != state["mode"]):
                            logs.append({"t": now_iso(), "source": "server", "session": session, "line": "APNs push: %s" % st["lastMode"]})
                        self.logged[session] = state["mode"]
                    else:
                        st.update(ok=False, errors=st["errors"] + 1, lastError="%s HTTP %s %s" % (now_iso(), code, text))
                        push_status["errors"] += 1
                        push_status["last_error"] = st["lastError"]
                        self.retry_at[session] = time.time() + 5
                        if was_ok or st["errors"] == 1:
                            logs.append({"t": now_iso(), "source": "server", "session": session, "line": "APNs push failed: HTTP %s %s" % (code, text)})
                        # A dead token (the activity ended): stop until the app sends a new one. Any other error keeps
                        # the token and tries again in 5 s; the app updates the Island itself meanwhile.
                        if code == 410 or (code == 400 and "BadDeviceToken" in text):
                            if session in configs and configs[session].get("pushToken") == cfg["pushToken"]:
                                configs[session]["pushToken"] = ""

    def reset(self, session):
        self.last.pop(session, None)
        self.retry_at.pop(session, None)


pusher = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "lens/1"

    def log_message(self, fmt, *args):  # keep the container log short
        pass

    def send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("body too large")
        return self.rfile.read(n) if n else b""

    def do_OPTIONS(self):
        self.send(204, b"", extra={"Access-Control-Allow-Methods": "GET, POST", "Access-Control-Allow-Headers": "Content-Type"})

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        if url.path in ("/", "/debug"):
            return self.send(200, debug_html(), "text/html; charset=utf-8")
        if url.path == "/debug.json":
            return self.send(200, snapshot())
        if url.path == "/health":
            return self.send(200, {"ok": True})
        if url.path == "/api/info":
            return self.send(200, info())
        if url.path == "/api/latest":
            session = q.get("session", "")
            since = int(q.get("since", "0") or 0)
            with lock:
                touch(session)
                new = session_results(session, since)
                allr = session_results(session)
                counters["polls"] += 1
                counters["polls_" + q.get("source", "app")] += 1
                hb = next((h for h in reversed(heartbeats) if h.get("source") == "broadcast" and (not session or h.get("session") == session)), None)
                cfg = {k: v for k, v in config_of(session).items() if k != "pushToken"}
                state = live_state(session)
                screen = now_playing(session)
                push = {"ok": bool(push_sessions.get(session, {}).get("ok")) and bool(config_of(session).get("pushToken")),
                        "tokenSet": bool(config_of(session).get("pushToken"))}
            # Checks are items of kind "check" in `results` (each state change is a new item with a new id).
            return self.send(200, {"results": new, "screen": screen, "latest": allr[-1] if allr else None, "count": len(allr),
                                   "techniques": sum(1 for r in allr if r["technique"]), "broadcast": hb, "server_time": time.time(),
                                   "state": state, "config": cfg, "push": push})
        if url.path == "/api/events":
            return self.events(q.get("session", ""), int(q.get("since", "0") or 0))
        if url.path.startswith("/frame/"):
            try:
                fid = int(os.path.basename(url.path).split(".")[0])
            except ValueError:
                fid = -1
            with lock:
                f = next((f for f in frames if f["id"] == fid and f.get("jpeg")), None)
            if f:
                return self.send(200, f["jpeg"], "image/jpeg")
            return self.send(404, {"error": "no such frame (only the last %d TikTok frames are kept, in memory)" % KEEP_FRAMES})
        if url.path.startswith("/wav/"):
            name = os.path.basename(url.path[5:])
            path = os.path.join(WAV_DIR, name)
            if name.endswith(".wav") and os.path.isfile(path):
                with open(path, "rb") as f:
                    return self.send(200, f.read(), "audio/wav")
            return self.send(404, {"error": "no such chunk"})
        return self.send(404, {"error": "not found"})

    def events(self, session, since):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last = since
        try:
            while True:
                with cond:
                    cond.wait_for(lambda: bool(session_results(session, last)), timeout=15)
                    new = session_results(session, last)
                if not new:
                    self.wfile.write(b": ping\n\n")
                for r in new:
                    self.wfile.write(("id: %d\nevent: result\ndata: %s\n\n" % (r["id"], json.dumps(r))).encode())
                    last = r["id"]
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return

    def do_POST(self):
        global next_arrival
        url = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        try:
            body = self.body()
        except ValueError as e:
            return self.send(413, {"error": str(e)})
        if q.get("session"):
            with lock:
                touch(q["session"][:64])
        if url.path == "/api/audio":
            return self.audio(body, q)
        if url.path == "/api/frame":
            return self.frame(body, q)
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return self.send(400, {"error": "invalid JSON"})
        if isinstance(data, dict) and isinstance(data.get("session"), str):
            with lock:
                touch(data["session"][:64])
        if url.path == "/api/text":
            code, out = text_in(data if isinstance(data, dict) else {})
            return self.send(code, out)
        if url.path == "/api/cache":
            # {"forget": "text"}: drops the stored checks whose claim matches (to check a claim again after a fix).
            # {"recheck": "text"}: checks the claim again; the result joins the stored check and replaces it only when
            # it is better sourced (it adds a missing short claim, and never turns a "differ" into a "mixed").
            if data.get("recheck"):
                with cond:
                    c = start_check({"id": None, "session": "cache-recheck"}, str(data["recheck"])[:400], 1.0, "recheck", press=False, fresh=True)
                return self.send(200, {"ok": True, "checkId": c["id"], "storedClaims": len(check_cache)})
            text = str(data.get("forget") or "")
            with cond:
                gone = [k for k, c in check_cache.items() if text and (matches_claim(c, [text]) or any(norm(text) in norm(t) for t in claim_texts(c)))]
                for k in gone:
                    check_cache.pop(k)
                save_cache()
                return self.send(200, {"ok": True, "forgot": gone, "storedClaims": len(check_cache)})
        if url.path == "/api/reset":
            session = (q.get("session") or data.get("session") or "")[:64]
            if not session:
                return self.send(400, {"error": "session is required"})
            with cond:
                forget_session(session)
                logs.append({"t": now_iso(), "source": "server", "session": session, "line": "session reset (the check cache stays)"})
            return self.send(200, {"ok": True, "session": session, "storedClaims": len(check_cache)})
        if url.path == "/api/now":
            session = (q.get("session") or (data.get("session") if isinstance(data, dict) else "") or "unknown")[:64]
            return self.send(200, deliver_now(session))
        if url.path == "/api/detector":
            out, err = set_detector(data if isinstance(data, dict) else {})
            return self.send(400 if err else 200, {"error": err} if err else out)
        if url.path in ("/api/heartbeat", "/api/log"):
            with lock:
                if url.path == "/api/heartbeat":
                    data["received"] = now_iso()
                    data["ts"] = time.time()
                    heartbeats.append(data)
                    counters["heartbeats"] += 1
                else:
                    for line in data.get("lines", [])[:100]:
                        logs.append({"t": now_iso(), "source": data.get("source", "?"), "session": data.get("session", ""), "line": str(line)[:500]})
                    counters["log_lines"] += len(data.get("lines", []))
            return self.send(200, {"ok": True})
        if url.path == "/api/config":
            session = (q.get("session") or data.get("session") or "unknown")[:64]
            allowed = {"updatePath", "chunkSeconds", "alerts", "alertSound", "pushToken", "apsEnvironment", "running", "keepAlive", "screenPath", "fps", "apps", "factCheck", "speechPath"}
            with lock:
                cfg = config_of(session)
                if isinstance(data.get("nowPress"), dict):
                    # The app reports each "Footnote this" press: its time and outcome, for /debug.
                    cfg["lastNowPress"] = {"ms": data["nowPress"].get("ms"), "outcome": str(data["nowPress"].get("outcome", ""))[:200], "at": now_iso()}
                    counters["now_presses_reported"] += 1
                    logs.append({"t": now_iso(), "source": "app", "session": session, "line": "footnote this: %s" % cfg["lastNowPress"]["outcome"]})
                if "apps" in data and data["apps"] not in ("any", "tiktok", None):
                    return self.send(400, {"error": 'apps must be "any" or "tiktok"'})
                if data.get("pushToken") and data["pushToken"] != cfg.get("pushToken"):
                    # A new Live Activity: it gets the whole Island state with the first push.
                    push_sessions.pop(session, None)
                    if pusher:
                        pusher.reset(session)
                cfg.update({k: v for k, v in data.items() if k in allowed})
                if data.get("start"):
                    cfg["startId"] = next_result_id
                    cfg["startedAt"] = time.time()
                    cfg["running"] = True
                    forget_session(session, keep_checks=True)
                    frame_hash.pop(session, None)
                    if pusher:
                        pusher.reset(session)
                    logs.append({"t": now_iso(), "source": "server", "session": session, "line": "new run from result %d, path %s" % (next_result_id, cfg["updatePath"])})
                out = {k: v for k, v in cfg.items() if k != "pushToken"} | {"pushTokenSet": bool(cfg.get("pushToken"))}
            return self.send(200, out)
        if url.path == "/api/screentext":
            return self.screentext(data, q)
        if url.path == "/api/fake":
            session = (q.get("session") or data.get("session") or "unknown")[:64]
            if data.get("backend") or data.get("detect"):
                # A fake transcript through the real detector (tests without audio). Off the request thread.
                with lock:
                    next_arrival += 1
                    job = {"session": session, "seq": int(data.get("seq", 0) or 0), "arrival": next_arrival, "received": time.time()} | video_of(session)
                text = str(data.get("text", ""))[:2000]
                backend = data.get("backend") if data.get("backend") in detect.BACKENDS else None

                def run():
                    det = detect_text(job, text, backend)
                    with cond:
                        add_result(job, text, time.time() - job["received"], 0, fake=True, det=det)
                        counters["fake_results"] += 1
                threading.Thread(target=run, daemon=True).start()
                return self.send(200, {"ok": True, "detecting": True})
            with cond:
                next_arrival += 1
                r = add_result({"session": session, "seq": 0, "arrival": next_arrival}, str(data.get("text", ""))[:2000], 0, 0, fake=True)
                counters["fake_results"] += 1
            return self.send(200, r)
        return self.send(404, {"error": "not found"})

    def audio(self, body, q):
        global next_arrival
        session = q.get("session", "unknown")[:64]
        seq = int(q.get("seq", "0") or 0)
        key = (session, seq, len(body), zlib.crc32(body))
        with lock:
            duplicate = key in recent_chunks
            if not duplicate:
                recent_chunks.append(key)
            else:
                counters["duplicate_chunks"] += 1
        if duplicate:
            return self.send(200, {"ok": True, "transcribing": False, "reason": "duplicate"})
        try:
            pcm, rate = to_pcm(body, int(q.get("rate", "16000") or 16000), q.get("encoding", "pcm16"))
        except Exception as e:  # noqa: BLE001
            return self.send(400, {"error": "bad audio: %r" % (e,)})
        if not pcm:
            return self.send(400, {"error": "empty audio"})
        seconds = len(pcm) / 2 / rate
        rms = rms_of(pcm)
        wav = wav_bytes(pcm, rate)
        name = "%s-%s-%05d.wav" % (time.strftime("%H%M%S", time.gmtime()), "".join(c for c in session if c.isalnum())[:12], seq)
        with open(os.path.join(WAV_DIR, name), "wb") as f:
            f.write(wav)
        with lock:
            next_arrival += 1
            arrival = next_arrival
            if len(chunks) == chunks.maxlen:
                old = chunks[0]["name"]
                try:
                    os.remove(os.path.join(WAV_DIR, old))
                except OSError:
                    pass
            chunks.append({"name": name, "t": now_iso(), "session": session, "seq": seq, "seconds": round(seconds, 2), "rms": round(rms, 4),
                           "bytes": len(body), "encoding": q.get("encoding", "pcm16"), "attempt": q.get("attempt", "1")})
            counters["chunks"] += 1
            counters["audio_bytes"] += len(body)
        silent = rms < float(q.get("silence", "0.003"))
        with lock:
            away = paused(session)
            hold = away and not silent
            video = video_of(session)
            job = {"session": session, "seq": seq, "arrival": arrival, "wav": wav, "name": name, "seconds": round(seconds, 2),
                   "rms": round(rms, 4), "received": time.time()} | video
            expect_chunk(session, seq)
        if hold:
            # Privacy guard: the newest read says the front app is not TikTok. The WAV leaves the disk at once, and the
            # audio waits in memory for HOLD_SECONDS: a TikTok read (the user came back) sends it to transcription,
            # a read of another app or the time limit drops it unheard.
            try:
                os.remove(os.path.join(WAV_DIR, name))
            except OSError:
                pass
            with lock:
                held.setdefault(session, []).append((time.time(), job))
                counters["audio_held"] += 1
            return self.send(200, {"ok": True, "transcribing": False, "held": True, "reason": "held: waiting for a TikTok screen read"})
        if silent:
            if away:
                try:
                    os.remove(os.path.join(WAV_DIR, name))
                except OSError:
                    pass
            with lock:
                counters["silent_chunks_skipped"] += 1
                gap(job)
            detect_pool.submit(pump, session)
            return self.send(200, {"ok": True, "chunk": name, "seconds": seconds, "rms": rms, "transcribing": False, "reason": "silence"})
        try:
            jobs.put_nowait(job)
        except queue.Full:
            with lock:
                counters["dropped_queue_full"] += 1
                gap(job)
            return self.send(200, {"ok": True, "chunk": name, "transcribing": False, "reason": "queue full"})
        return self.send(200, {"ok": True, "chunk": name, "seconds": seconds, "rms": rms, "transcribing": True})

    def queue_screen(self, session, seq, digest, job):
        global next_arrival
        with lock:
            counters["frames_in"] += 1
            if not watching(session):
                # Jev is the first gate: with no claim under check the frame is kept (the newest only, in memory) and
                # no model reads it.
                counters["frames_kept_unread"] += 1
                last_frames[session] = job | {"session": session, "seq": seq, "arrival": next_arrival, "received": time.time(), "digest": digest}
                stop_watching(session)
                reason = "kept unread: no claim under check"
            else:
                reason = accept_screen(session, digest)
            if reason in (None, "busy"):
                frame_hash[session] = digest
                job |= {"session": session, "seq": seq, "arrival": next_arrival, "received": time.time()}
            if reason == "busy":
                # A read is busy: keep the newest frame and read it when the read ends (an older pending frame goes).
                if session in pending_frames:
                    counters["frames_replaced_pending"] += 1
                pending_frames[session] = job
                counters["frames_pending"] += 1
                if session not in vision_busy:  # only the gap after the last answer: start it when the gap ends
                    start_pending_later(session, VISION_GAP - (time.time() - vision_done.get(session, 0)))
            if reason is None:
                vision_busy.add(session)
        if reason == "busy":
            return self.send(200, {"ok": True, "reading": True, "pending": True})
        if reason:
            return self.send(200, {"ok": True, "reading": False, "reason": reason})
        try:
            vision_jobs.put_nowait(job)
        except queue.Full:
            with lock:
                vision_busy.discard(session)
                counters["frames_skipped_queue_full"] += 1
            return self.send(200, {"ok": True, "reading": False, "reason": "queue full"})
        return self.send(200, {"ok": True, "reading": True})

    def frame(self, body, q):
        session = q.get("session", "unknown")[:64]
        if body[:2] != b"\xff\xd8":
            return self.send(400, {"error": "not a JPEG"})
        with lock:
            counters["frame_bytes"] += len(body)
        job = {"kind": "jpeg", "jpeg": body, "bytes": len(body), "w": int(q.get("w", "0") or 0), "h": int(q.get("h", "0") or 0),
               "clientMs": q.get("ms"), "debug": self.headers.get("X-Lens-Debug") == "1" or q.get("debug") == "1"}
        return self.queue_screen(session, int(q.get("seq", "0") or 0), hashlib.sha1(body).hexdigest(), job)

    def screentext(self, data, q):
        session = (q.get("session") or data.get("session") or "unknown")[:64]
        lines = [l for l in data.get("lines", []) if isinstance(l, dict) and l.get("text")][:150]
        digest = hashlib.sha1(json.dumps([l["text"] for l in lines]).encode()).hexdigest()
        job = {"kind": "ocr", "lines": lines, "bytes": len(json.dumps(lines)), "clientMs": data.get("ocrMs")}
        return self.queue_screen(session, int(data.get("seq", 0) or 0), digest, job)


def now_playing(session):
    """The "now playing" state without images. Lock held."""
    sc = screens.get(session)
    if not sc:
        return None
    out = {k: v for k, v in sc.items()}
    out["paused"] = paused(session)
    out["apps"] = apps_of(session)
    out["age"] = round(time.time() - sc["lastTikTokAt"], 1) if sc["lastTikTokAt"] else None
    return out


def detect_cost(window=60):
    """Decision model spend in USD (OpenRouter), per kind: the last `window` seconds and in total. Lock held."""
    now = time.time()
    out = {"window_s": window, "usd_last_min": 0.0, "usd_total": 0.0, "calls_last_min": 0}
    for ts, _session, kind, usd in detect_usage:
        out["usd_total"] += usd
        out.setdefault(kind + "_usd_total", 0.0)
        out[kind + "_usd_total"] += usd
        if now - ts < window:
            out["usd_last_min"] += usd
            out["calls_last_min"] += 1
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in out.items()}


def snapshot():
    with lock:
        return {
            "server_started": now_iso(started_at),
            "now": now_iso(),
            "commit": COMMIT,
            "detector": detector_info(),
            "detect_cost": detect_cost(),
            "detections": list(detect_log)[::-1][:80],
            "active_sessions": {s: round(time.time() - t) for s, t in last_seen.items()},
            "buffers": {s: {"next": a["next"], "waiting": sorted(a["ready"]), "inFlight": len(a["arrived"]),
                            "open": a["tail"]["text"] if a["tail"] else "", "held": len(held.get(s, [])), "pendingFrame": s in pending_frames}
                        for s, a in asm.items()},
            "model": MODEL,
            "router_key_set": bool(ROUTER_KEY),
            "counters": dict(counters),
            "push": dict(push_status) | {"keyId": APNS_KEY_ID, "sessions": {k: dict(v) for k, v in push_sessions.items()}},
            "island": {s: island_state(s) for s, c in configs.items() if c.get("running")},
            "configs": {s: {k: v for k, v in c.items() if k != "pushToken"} | {"pushTokenSet": bool(c.get("pushToken"))} for s, c in configs.items()},
            "states": {s: live_state(s) for s in configs},
            "vision_model": VISION_MODEL,
            "now_playing": {s: now_playing(s) for s in screens},
            "cost": {"all": cost_summary()} | {s: cost_summary(s) for s in configs},
            "screen_reads": list(screen_log)[::-1],
            "frames": [{k: v for k, v in f.items() if k != "jpeg"} | {"image": bool(f.get("jpeg"))} for f in list(frames)[::-1]],
            "heartbeats": list(heartbeats)[::-1],
            "chunks": list(chunks)[::-1],
            "results": list(results)[::-1][:60],
            "logs": list(logs)[::-1][:200],
        }


def debug_html():
    s = snapshot()
    e = html.escape
    now = time.time()

    def latest(source):
        return next((h for h in s["heartbeats"] if h.get("source") == source), None)

    def age(h):
        return "%.0f s ago" % (now - h["ts"]) if h else "never"

    bc, app = latest("broadcast"), latest("app")
    rows = []
    rows.append("<h1>Lens test server</h1><p>Server started %s, now %s, commit %s. Transcription model %s. Router key set: %s. Page refreshes each 2 s. "
                "<a href='/debug.json'>JSON</a> <a href='/api/info'>info</a></p>"
                % (s["server_started"], s["now"], e(s["commit"]), e(s["model"]), s["router_key_set"]))
    d = s["detector"]
    c = s["counters"]
    rows.append("<h2>Detector</h2><p>Backend <b>%s</b> (%s), spec %s. A note needs the top technique at or above <b>%.2f</b>, the war gate true and the "
                "quote gate false; from <b>%.2f</b> up to the note threshold the line is unsure. Speech check %s (below %.2f is no speech; a transcript under 4 words needs %.2f). On-screen text that repeats the speech (subtitles) is skipped. "
                "TikTok gate before the vision read: %s. Change at run time: <code>POST /api/detector</code>.</p>"
                % (e(d["backend"]), e(d["model"]), e(d["specVersion"]), d["note"], d["unsure"], "on" if d["speechCheck"] else "off", d["speechMin"], d["speechShort"],
                   "on" if d["tiktokGate"] else "off"))
    rows.append("<pre>decision calls (detect) %s, router calls (detect) %s, detect errors %s, notes %s, unsure %s, no-speech chunks dropped %s, "
                "speech check errors %s, subtitles skipped %s, TikTok gate calls %s (not TikTok %s, errors %s)\nOpenRouter spend %s</pre>"
                % (c.get("detect_calls_decision", 0), c.get("detect_calls_router", 0), c.get("detect_errors", 0), c.get("notes", 0), c.get("unsure", 0),
                   c.get("no_speech_dropped", 0), c.get("speech_check_errors", 0), c.get("screen_subtitles_skipped", 0), c.get("tiktok_gate_calls", 0), c.get("screen_gated_not_tiktok", 0),
                   c.get("tiktok_gate_errors", 0), e(json.dumps(s["detect_cost"]))))
    rows.append("<pre>Sentence buffer: speech results are whole sentences (open end waits up to %d s for the next chunk). "
                "sentences flushed %s, seqs skipped %s, late chunks %s, transcribe retries %s, router deadlines %s, duplicate notes %s\n"
                "Pause: audio held %s, released %s, dropped unheard %s. Frames: pending %s (replaced %s, read %s)\nDetector backends: %s\n%s</pre>"
                % (FLUSH_SECONDS, c.get("sentences_flushed", 0), c.get("seq_skipped", 0), c.get("late_chunks", 0), c.get("transcribe_retries", 0),
                   c.get("router_deadline", 0), c.get("duplicate_notes", 0), c.get("audio_held", 0), c.get("audio_released", 0),
                   c.get("audio_discarded_not_tiktok", 0), c.get("frames_pending", 0), c.get("frames_replaced_pending", 0), c.get("frames_pending_read", 0),
                   e("primary %s %s; fallback order %s; last primary error: %s; backends %s" % (
                       d["backend"], "up" if d.get("primaryUp", True) else "DOWN (retry each %d s)" % d.get("retryPrimaryEveryS", 60),
                       " > ".join(d.get("fallbackOrder", [])), d.get("primaryLastError") or "none", json.dumps(d.get("backends", {})))),
                   e("\n".join("%s: next seq %s, waiting %s, open %r, held %s" % (k, v["next"], v["waiting"], v["open"][:80], v["held"])
                                for k, v in s["buffers"].items()))))
    rows.append("<h3>Detections (newest first)</h3><table><tr><th>time</th><th>session</th><th>source</th><th>verdict</th><th>top 3</th><th>war</th>"
                "<th>quote</th><th>backend</th><th>ms</th><th>text</th></tr>")
    for x in s["detections"][:40]:
        verdict = "note" if x["note"] else ("unsure" if x["unsure"] else ("error" if x["error"] else "-"))
        rows.append("<tr%s><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%.2f</td><td>%.2f</td><td>%s</td><td>%s</td><td>%s%s</td></tr>"
                    % (" class='hit'" if x["note"] else "", x["t"], e(x["session"]), x["source"], verdict,
                       e(", ".join("%s %.2f" % (k, v) for k, v in x["top3"])), x["gates"].get("war", 0), x["gates"].get("quote", 0), x["backend"], x["ms"],
                       e(x["text"]), (" <small>%s</small>" % e(x["error"][:160])) if x["error"] else ""))
    rows.append("</table>")
    rows.append("<h2>Live Activity</h2><p>Which path updates the Island now: app <b>%s</b>, extension <b>%s</b>. APNs push: %s</p>"
                % (e(str((app or {}).get("updatePathActive", "-"))), e(str((bc or {}).get("updatePathActive", "-"))), e(json.dumps(s["push"]))))
    rows.append("<pre>config %s\nstate  %s</pre>" % (e(json.dumps(s["configs"], indent=1, ensure_ascii=False)), e(json.dumps(s["states"], indent=1, ensure_ascii=False))))
    rows.append("<h2>Now playing</h2><p>From the screen frames (model %s). When the front app is not TikTok, the server discards the frame, "
                "its text and the audio, and the Island shows paused.</p><pre>%s</pre>"
                % (e(s["vision_model"]), e(json.dumps({k: {kk: vv for kk, vv in v.items()} for k, v in s["now_playing"].items() if v}, indent=1, ensure_ascii=False))))
    rows.append("<h2>Router cost (credits)</h2><p>Per session and in total: the last 60 s and since the server started.</p><pre>%s</pre>"
                % e(json.dumps(s["cost"], indent=1)))
    rows.append("<h2>Last frames</h2><p>No image is written to disk, and by default no image is kept at all: the server keeps the text it read. "
                "Only a frame request with the debug flag (header <code>X-Lens-Debug: 1</code> or <code>debug=1</code>) keeps its TikTok image, in memory, "
                "for this page (the last %d). A frame of any other app is discarded at once and never shown here.</p><div class='frames'>" % KEEP_FRAMES)
    for f in s["frames"]:
        img = "<img src='/frame/%d.jpg' loading='lazy'>" % f["id"] if f["image"] else "<pre>%s</pre>" % (e("\n".join(l.get("text", "") for l in (f.get("lines") or []))[:600]) or "(image not kept)")
        rows.append("<figure>%s<figcaption>#%d %s %s, %s bytes, %ss, %s credits<pre>%s</pre></figcaption></figure>"
                    % (img, f["id"], f["t"], f["kind"], f["bytes"], f["latency"], f["credits"], e(json.dumps(f["parsed"], ensure_ascii=False, indent=1))))
    rows.append("</div><h2>Screen reads (newest first)</h2><table><tr><th>time</th><th>session</th><th>seq</th><th>kind</th><th>bytes</th><th>latency</th><th>credits</th><th>video</th><th>parsed</th></tr>")
    for r in s["screen_reads"][:25]:
        rows.append("<tr%s><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%ss</td><td>%s</td><td>%s%s</td><td>%s</td></tr>"
                    % (" class='hit'" if r.get("newVideo") else "", r["t"], e(r["session"]), r["seq"], r["kind"], r["bytes"], r["latency"], r["credits"],
                       r.get("videoId", "-"), " new" if r.get("newVideo") else "", e(json.dumps(r["parsed"], ensure_ascii=False))))
    rows.append("</table>")
    rows.append("<h2>Counters</h2><pre>%s</pre>" % e(json.dumps(s["counters"], indent=1)))

    def hb_table(title, h, keys):
        out = "<h2>%s <small>(last heartbeat %s)</small></h2>" % (title, age(h))
        if not h:
            return out + "<p>No heartbeat yet.</p>"
        out += "<table>" + "".join("<tr><th>%s</th><td>%s</td></tr>" % (e(k), e(str(h.get(k, "")))) for k in keys if k in h) + "</table>"
        extra = {k: v for k, v in h.items() if k not in keys}
        return out + "<details><summary>all fields</summary><pre>%s</pre></details>" % e(json.dumps(extra, indent=1))

    rows.append(hb_table("Broadcast extension (TikTok audio)", bc,
                         ["state", "session", "memoryMB", "memoryPeakMB", "updatePath", "updatePathActive", "activitiesVisible", "activityUpdatesFromExtension",
                          "activityLastError", "audioApp", "video", "rms", "rmsMax", "chunkSeconds", "chunksSent", "postErrors", "retries", "queued", "droppedChunks",
                          "silentSkipped", "lastUploadMs", "screenPath", "fps", "framesSeen", "framesTaken", "framesSame", "framesSent", "framesDropped",
                          "frameErrors", "frameBytes", "lastFrameMs", "lastFrameKB", "ocrMs", "uptime", "received"]))
    rows.append(hb_table("App (Live Activity, keep alive, PiP)", app,
                         ["state", "session", "scenePhase", "updatePath", "updatePathActive", "activityState", "activityUpdates", "activityUpdatesInBackground",
                          "activityUpdatesSkipped", "activityLastError", "pushToken", "keepAlive", "silentAudio", "audioSession", "otherAudioPlaying",
                          "pipEnabled", "pipActive", "backgroundTimeRemaining", "polls", "pollFailures", "lastResultId", "received"]))
    rows.append("<h2>Results (newest first)</h2><table><tr><th>id</th><th>time</th><th>session</th><th>seq</th><th>source</th><th>rms</th><th>latency</th>"
                "<th>technique</th><th>top 3</th><th>war / quote</th><th>backend</th><th>detect</th><th>text</th><th>chunk</th></tr>")
    for r in s["results"]:
        top3 = sorted((r.get("probs") or {}).items(), key=lambda kv: -kv[1])[:3]
        g = r.get("gates") or {}
        tech = r["technique"] or (("unsure: %s" % r.get("candidate")) if r.get("unsure") else "")
        speech = "" if r["speech"] or r["source"] != "audio" else " <small>(no speech%s)</small>" % (": " + e(r["noSpeech"]) if r.get("noSpeech") else "")
        rows.append("<tr%s><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%ss</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                    "<td>%s%s</td><td>%s</td></tr>"
                    % (" class='hit'" if r["technique"] else (" class='unsure'" if r.get("unsure") else ""), r["id"], r["t"], e(r["session"]), r["seq"], r["source"],
                       r["rms"], r["latency"], e(tech), e(", ".join("%s %.2f" % kv for kv in top3)),
                       ("%.2f / %.2f" % (g.get("war", 0), g.get("quote", 0))) if g else "", e(r.get("backend") or ""),
                       ("%s ms" % r["detect_ms"]) if r.get("detect_ms") is not None else "", e(r["text"]), speech,
                       ("<a href='/wav/%s'>wav</a>" % e(r["chunk"])) if r["chunk"] else ""))
    rows.append("</table>")
    rows.append("<h2>Last chunks (WAV, newest first)</h2><table><tr><th>time</th><th>session</th><th>seq</th><th>seconds</th><th>rms</th><th>bytes</th><th>encoding</th><th>attempt</th><th>file</th></tr>")
    for c in s["chunks"]:
        rows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><a href='/wav/%s'>%s</a></td></tr>"
                    % (c["t"], e(c["session"]), c["seq"], c["seconds"], c["rms"], c.get("bytes", ""), e(c.get("encoding", "")), e(str(c.get("attempt", ""))), e(c["name"]), e(c["name"])))
    rows.append("</table>")
    rows.append("<h2>Logs (newest first)</h2><pre>%s</pre>" % e("\n".join("%s [%s] %s" % (l["t"], l["source"], l["line"]) for l in s["logs"])))
    return ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<meta http-equiv='refresh' content='2'><title>Lens debug</title><style>"
            "body{font:14px/1.4 -apple-system,system-ui,sans-serif;margin:16px;background:#111;color:#eee}"
            "table{border-collapse:collapse;margin-bottom:8px}td,th{border:1px solid #333;padding:3px 6px;text-align:left;vertical-align:top}"
            "th{color:#aaa;font-weight:500}pre{background:#1b1b1b;padding:8px;white-space:pre-wrap;max-height:420px;overflow:auto}"
            "a{color:#8cf}tr.hit td{background:#3a2a00}tr.unsure td{background:#22262e}small{color:#999;font-weight:400}h2{margin-top:24px}"
            ".frames{display:flex;gap:12px;flex-wrap:wrap}figure{margin:0;width:220px}figure img{width:220px;border-radius:8px}"
            "figcaption{font-size:12px;color:#aaa}figcaption pre{max-height:220px;font-size:11px}"
            "</style></head><body>%s</body></html>" % "".join(rows))


if __name__ == "__main__":
    # The router takes 2 to 12 s per chunk; 6 workers keep up with 3 sessions at 4 s chunks (replay runs too).
    for _ in range(6):
        threading.Thread(target=worker, daemon=True).start()
    # One read per session at a time (vision_busy), so 4 threads serve 4 sessions.
    for _ in range(4):
        threading.Thread(target=vision_worker, daemon=True).start()
    threading.Thread(target=janitor, daemon=True).start()
    threading.Thread(target=sweeper, daemon=True).start()
    threading.Thread(target=deliver_loop, daemon=True).start()
    load_cache()
    pusher = Pusher()
    threading.Thread(target=pusher.loop, daemon=True).start()
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer.request_queue_size = 128  # the default listen backlog of 5 drops connections in a burst of polls and uploads
    print("lens server on :%d, commit %s, model %s, key set %s, detector %s, push %s" % (PORT, COMMIT, MODEL, bool(ROUTER_KEY), json.dumps(DETECT), push_status), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

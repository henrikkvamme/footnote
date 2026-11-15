# Footnote (iOS feasibility test)

A test app for the Decode the Noise challenge. The visible name is Footnote ("Captions with footnotes."); the code, the scheme, the bundle ids and the server host keep the working name Lens. The Dynamic Island is the whole UI over TikTok. There is no Picture in Picture window by default.

## How it works now (15 November): Jev first, then the agent

Two steps, both on Hostinger. This section is the truth; older text below about Hermes Agent, the speech check and a screen read of each frame is history.

1. **Gate: Jev.** The server turns the sound into a transcript (one cheap speech call per chunk, `gemini-3.1-flash-lite` on the Hostinger AI Router). Jev (System One on the Hostinger AI Router, about 1 s, very cheap) reads each sentence. No other model runs before Jev, and no screen frame is read: the newest frame only waits in memory. Jev answers a typed contract of yes or no questions (`techniques.json`, `gates`): `public` at or above 0.5 (public affairs, not cooking or sport), `claim` at or above 0.7 (a specific claim that sources can check), `quote` below 0.9 (the speaker asserts it). While the Hostinger router has no credits, the same Jev model answers through OpenRouter (`gateBy: jevor`, `typesafe/jev-1.14`, about 0.00001 USD per call). If a Jev call fails or takes over 2.5 s, Gemini Decision answers in its place, and the item says so (`gateBy: decision`).
2. **Agent: footnote-agent.** Only when all conditions hit, the claim, the transcript around it, the account, the caption and the frame in front go to `footnote-agent/` (TypeScript, Effect, XState, GPT-6 Luna with web search through the Hostinger AI Router, on the same VPS). It decides what to search and returns a typed result: stance, one line, sources. If it fails, one OpenRouter call with web search answers (`checker: single`).
3. From the moment Jev passes a claim until its result is delivered, Jev also looks at each frame (about 0.4 s, 0.00008 USD): is media in front, is the screen private, is it the same video as before. A language model (GPT-6 Luna) reads a frame only for the account and the caption: once for the claim's video and twice for each new video (3 reads of 58 frames in a test). The server needs this for the hold rule and for "Earlier video: @account". No Gemini model reads a frame.
4. Only `differ` with 2 independent source domains, and a speaker who asserts the claim (one more Jev question), reaches the Island. All other results stay silent.

5. A result shows when its check is done, also while the claim\'s video still plays (Henrik, 15 November). It waits only until 8 s after the claim (`SETTLE_SECONDS`), so that the last speaker question sees the sentences after the claim: a debunk states the claim first and refutes it next. Older text below that says "held until the next video" is history.

Known limits: a private screen is not detected while no claim is under check (the sound is still transcribed). Text that is only on the screen is not judged before a spoken claim passes Jev. Whisper on the router was not usable (its Cloudflare blocks file uploads), so the transcriber is a small Gemini model.

1. The app has one button, "Start listening". It starts the Live Activity, keeps the app alive with silent audio (`.playback` + `.mixWithOthers`) and opens the broadcast picker.
2. A ReplayKit Broadcast Upload Extension gets TikTok's app audio (`audioApp`). It cuts 3, 4, 5 or 8 s chunks (default 4 s) at a quiet point and sends them in order, one at a time, as 16 kHz mono mu-law. It does not send a silent chunk.
3. The same extension reads the screen (see "Screen reading").
4. The server transcribes each chunk, puts a note on a sentence that uses a known persuasion technique (never a true or false verdict) and computes one Live Activity state.
5. The Live Activity shows that state: compact, expanded, minimal and Lock Screen. On a new note, an alert can expand the Island.
6. The app screen shows the run as one text with numbered footnotes.

Three update paths, set at runtime in Debug (`/api/config`, because there is no App Group):

- `extension` (A): the broadcast extension polls the server and updates the activity itself.
- `app` (B): the app, kept alive by silent audio, polls the server and updates the activity.
- `push` (C): the server sends APNs Live Activity pushes. It needs the key file `/srv/junction-data/lens/apns.p8` on the VPS (key id in `LENS_APNS_KEY_ID`, sandbox host).
- `auto` (default): A and B together, and the server also pushes when the app sent a push token (no alert from the push).

## Screen reading

The extension takes at most one frame each 1 s (0.5, 1 or 2 fps in Debug). It drops the other ReplayKit buffers at once, holds no more than one frame, and skips a frame that is nearly the same as the last one. `Broadcast/FrameSampler.swift` scales the luma plane to 540 px wide with vImage (grey, about 0.6 MB). Then one of these paths, set in Debug:

- S1 (default): a JPEG at quality 0.5 (about 55 KB) goes to `POST /api/frame`. The server reads it with `gemini-3.5-flash-lite` through the Hostinger AI Router (transcription uses `gemini-3.1-flash-lite`) and gets strict JSON: `app` (best guess), `kind` (video, post, article, live, private or other), `author`, `title_or_description`, `on_screen_text`, hashtags, counts, `comments_visible` and the visible comments. The server keeps them under the old names (`handle`, `description`, `onScreenText`, `commentsOpen`).
- S2: Vision OCR on the phone (fast or accurate); only the text lines and their boxes go to `POST /api/screentext`. The accurate level needs about 100 MB in the simulator test, more than the extension may use (about 50 MB).

The server reads at most one frame at a time per session, waits at least 1 s after the last answer, and skips a frame with the same hash. A frame that comes while a read is busy waits, and the newest waiting frame is read next. A read that takes more than 10 s is dropped. A read that takes more than 4 s gets a second request to the same model on OpenRouter, and the first answer wins. A new video needs a new author and a new title, or the same change in two reads in a row, so one misread or a watermark in the video does not split a video. A change of app also starts a new video, and the new video's author, title and text replace the old ones also when they are empty (a news article after a TikTok video has no author). Each result carries `videoId`, `author` (the same as `handle`) and `app`. A chat, mail, bank or settings screen gives `pauseReason: "Paused: private screen"`: the frame gate asks "public media?" and "private?" in one decision call. A post or an article without a title is known by the start of its text. The detector also runs over the on-screen text, the description and the hashtags. The Island shows the author.

Debug in the app has "Measure screen pipeline": it runs the extension's code on a bundled TikTok screenshot (`App/BenchFrame.jpg`) and reports time and memory per step. The extension heartbeat reports `memoryMB` and `memoryPeakMB` on `/debug`.

### Privacy

- The broadcast sees the whole screen. Footnote analyses public media in any app or site: a video, a reel, a public post, a news article or a livestream (TikTok, Instagram, YouTube, X, Telegram channels, Facebook, news sites in a browser). The setting `apps` is `"any"` (default) or `"tiktok"` (only TikTok): per session in `POST /api/config`, or as the server default in `POST /api/detector`.
- A private or sensitive screen (messages and chats, mail, banking and payments, passwords and sign-in forms, health, photos, settings, the home screen, a keyboard) pauses the session: the server discards the frame at once, the read returns no text of it, and the Island state has `mode: "paused"` and `pauseReason: "Paused: private screen"` ("Paused: no media on screen" for a menu, "Paused: not TikTok" in TikTok mode). Audio that comes during the pause is deleted from disk at once and waits in memory for at most 10 s: when the next read shows media again, it is transcribed; when the read shows a private screen, or after 10 s, it is dropped without transcription. Text that comes through `POST /api/text` during a pause is dropped. A private screen pauses at once; a screen without media pauses only after 5 s of such reads in a row, and a pause ends after 2 media reads in a row, so one odd frame does not flip the Island. A new video needs the same new identity in two reads in a row (only the first video of a run starts on one read).
- No image is written to disk, and by default no image is kept in memory either. Only a frame request with the debug flag (`X-Lens-Debug: 1` or `debug=1`) keeps its TikTok image in memory for `/debug` (the last 5), and the page says so.

## Brand

The Footnote brand is in `hostinger-noise/brand/README.md` (do not change that directory from here). Colours, type and words are in `Shared/LensBrand.swift`; the Island, Lock Screen and page views are in `Shared/LensIslandViews.swift` and `App/ContentView.swift`. One accent colour (Marker); Iowan Old Style for speech and notes; SF Pro for small meta text.

## Layout

- `App/` - SwiftUI app: the start button and the page, Debug behind one quiet row (status, Island preview, update path, screen reading, chunk length, alerts, simulate, old PiP test behind a toggle, log, build info).
- `Broadcast/` - Broadcast Upload Extension (`no.henrikkvamme.lens.broadcast`). `ChunkUploader.swift` is the serial audio upload queue with retries. `FrameSampler.swift` and `FrameUploader.swift` read and send the screen.
- `Widget/` - Widget extension with the Live Activity (`no.henrikkvamme.lens.widget`).
- `Shared/` - Server URL, session id, Live Activity state, sync code shared by the app and the extension, brand, Island views.
- `server/` - Test server (Python). Hosted at <https://lens.srv2159196.hstgr.cloud/debug>.
- `project.yml` - XcodeGen spec. `Lens.xcodeproj` is generated from it and committed.

The app and the extension talk to the server with one session id, `lens-demo`. In the simulator, the `lensSession` user default of the app changes the session of the app only.

## Build

```sh
nix run nixpkgs#xcodegen -- generate      # after you add or remove files
xcodebuild -project Lens.xcodeproj -scheme Lens -sdk iphonesimulator build CODE_SIGNING_ALLOWED=NO
iphone-install build --ref main --project hostinger-noise/ios/Lens.xcodeproj --scheme Lens
```

## Server

`/debug` shows the detector settings and its last detections, the last heartbeats of the extension and the app, the update path, the results, the last chunks and the logs. `/debug.json` has the same data. `GET /api/info` gives the commit, the detector backend, its model and the thresholds. `POST /api/fake` makes a fake transcript without a router call, to test the Island (keyword rules); with `"detect": true` it runs the real detector.

- `POST /api/text` `{"session", "source": "speech"|"screen", "text", "lang"?, "t_start", "t_end", "final"}`: text that the phone transcribed or read on the device. Speech goes straight to the detector (no audio leaves the phone). A non-final text only warms the detector's cache; a note shows on a final sentence only. Screen text first gets a decision question "private screen or public media" on the text alone, so no image leaves the phone; private text pauses the session.
- Claim lane (the product since 15 November; the 10 technique questions and notes are off, `"techniques": true` in `POST /api/detector` brings them back). Each sentence and each on-screen text gets one decision call with the gates war, quote, claim and public (techniques.json). Jev answers it, hedged to Gemini Decision after 1.0 s; the item's `gateBy` says which one answered. A claim at or above 0.7 that is about public affairs and not quoted to debunk it publishes at once a check item with `status: "pending"` (the app lights the logo), and two checkers start in parallel: one OpenRouter call with web search (Exa, `google/gemini-3.1-flash-lite`, 2 to 6 s, about 0.008 USD) and Hermes Agent in its own container on the VPS (`footnote-hermes`, web search through Oxylabs, its model `claude-haiku-5.5` through the Hostinger AI Router, 14 to 120 s). The first usable answer is published; Hermes replaces it only when it is better sourced. `stance` is `differ` (at least 2 independent established sources say the claim is incorrect; the app adds "Sources contradict this."), `support`, `mixed` or `none`. A `differ` result is held until the next video (another account) and then published with `notify: true`. It never goes out while the claim's video is in front, also when it loops (a loop is the same play: no second check, no second notify), and it stays held while the broadcast is off or listening is stopped ("deliver now" still returns it). Safety exits: 8 s of screens without media, or 180 s after the claim while another video of the same account is in front. Before it goes out comes one more decision question: does the speaker make the claim, or report, question or refute it (a debunk video stays silent). Other stances go to the stream with `notify: false`. Footnote's own screen never becomes a claim: a read of the Footnote app (by its name, or its phrases such as "Sources contradict this" with no other account or caption) pauses as "no media on screen", and Footnote's phrases, its asterisk-marked claims and the claims, lines and source titles of the session's checks are cut from every screen text (Footnote's Island over a video is cut, the video goes on). Stored checks are on disk (`/srv/junction-data/lens-cache/checks.json`), one per claim: a later result joins the stored check of the same claim and replaces its result only when it is better sourced (`differ` with 2 source domains beats `support`, `mixed` and `none`). A sentence that says a stored claim (the sentence, the short claim or a sentence variant of an earlier take) skips the detector, so its pending item is out at once (`gateBy: "known claim"`) and answers from the store (`checker: "cache"`). A second check of the same claim in one play goes out silently (`duplicateOf`). The speaker question reads the whole video: the account, the caption, every sentence of the video so far and the screen text. 2 checks per video play, 30 new checks per hour. Kill switch: `"factCheck": false` in `POST /api/detector` or in a session's `POST /api/config`. `POST /api/cache {"forget": "<claim text>"}` drops a stored check; `{"recheck": "<claim text>"}` checks it again (the result can only improve the stored one). `deploy.sh` runs `warm_cache.py` after each deploy, so the demo claim is stored before the phone needs it.
- The check item, one per state, each with a new id in the `results` stream of `/api/latest` and `/api/events`: `{"id", "kind": "check", "of": <result id of the sentence or null for screen text>, "checkId": "c1", "claim": <a short complete statement, at most 60 characters>, "claimText": <the video's words, with the sentences before it>, "status": "pending"|"found"|"nothing", "stance": null|"differ"|"support"|"mixed"|"none", "notify": bool, "forced": bool, "line": <at most 90 characters on what the sources report, never true or false; null while pending>, "sources": [{"title", "url", "date": "YYYY-MM-DD" or "", "site"}] (at most 3, no social media, not the video's own account), "checker": "single"|"hermes"|"cache"|null, "gateBy", "videoId", "app", "author", "handle", "claimP", "ms", "costUsd"?, "deliveredBecause"?, "assertP"?}`. While a delivered item (`notify: true`) is younger than 10 s, the Island state has `mode: "checked"`, `flagText` (the short claim), `checkLine`, `checkStance`, `checkSource` (the publisher, such as "PolitiFact") and `checkOf`. While a check runs, the state has `checkPending: true`.
- `POST /api/reset` `{"session"}`: forgets the session's checks, held results, dedupe and video identity; the stored checks stay. For a new take without a new broadcast.
- `python3 server/check_smoke.py` checks the lane through the text path; `python3 server/check_takes.py` replays the demo video and the control video and presses "deliver now", 5 takes in one session and 3 in new sessions; a take passes when the pending item is out within 3 s of the claim, the press returns the demo claim with `differ`, 2 sources and `of` in under 1 s, and the control stays silent.
- `POST /api/now` `{"session"}` ("deliver now", the Action button): returns at once, as a flat check item with `notify: true` and `forced: true` and a new id each press, the newest finished `differ` check of the session in the last 30 min, held or delivered before, on any video (a claim the video itself refutes is skipped). Else, while a newer check runs, `{"pending": true, "kind": "check", "checkId", "claim", "of"}`, and its result goes out with `notify: true` the moment it arrives, whatever the stance; else the newest finished check. With no check in the session it starts one on the most checkable claim of the last 30 s (claim gate 0.5 or more) and returns its pending item. With no claim: `{"noClaim": true, "text": "No claim to check in the last 30 s."}`. A new run (`{"start": true}` in `POST /api/config`) forgets the video identity and the dedupe but keeps the checks; only `POST /api/reset` clears them.

### Technique detector

`server/detect.py` names the 10 UNLP 2025 techniques in one sentence (stdlib Python; module and CLI). All prompt text is in `server/techniques.json`.

- Backend `jev` (default): `jev-latest` on the Hostinger AI Router's System One API (`POST https://router.hostinger.com/v1/systemone`, `state` plus 13 `noul` questions: 10 techniques, a war gate, a quote or debunk gate and a claim gate). Pin a version with `FOOTNOTE_JEV_MODEL=jev-1.14`. About 1.1 s per sentence when the router is not loaded.
- Backend `msdecision`: `microsoft-decision-1` on the same API. Backend `decision`: Google `gemini-decision-1` through OpenRouter's Decisions API (`POST https://openrouter.ai/api/alpha/decisions`), about 0.5 s. Backend `router`: `gemini-3.5-flash-lite` on the Hostinger AI Router with strict JSON, the same questions (its probabilities are only 0 or 1).
- The scores per backend, the latency and each speed change are in `server/PERF.md`.
- A note needs the top technique at or above 0.7, the war gate true and the quote gate false. From 0.5 to 0.7 the result is `unsure`. Change the thresholds or the backend at run time with `POST /api/detector` (until the next deploy).
- The server keeps each session's chunk transcripts in seq order and runs the detector on complete sentences only, with the two sentences before as context and the video's on-screen text and caption. The open end of a chunk waits for the next chunk; it is judged as it is after 8 s, at a chunk without speech, at a pause or at a new video. Each speech result is one sentence, so `text` and `mark` are the whole sentence (`seqs` lists its chunks). The detector also runs once on each new burned-in text of a video; text that repeats the speech (subtitles) is skipped.
- A note on the same sentence of the same video as an earlier note (also a partial overlap) is a duplicate: the first note keeps both sources in `sources`, and the speech line of a duplicate stays without a note.
- Fallback per request: `jev`, then `msdecision`, the router LLM, and Gemini Decision on OpenRouter (`FOOTNOTE_FALLBACK`). A backend that refuses (HTTP 401, 402, 403 or 429) or fails 3 times in a row is down for 60 s and is then tried again. Each result names the backend that answered (`backend`) and the reason in `detectFallback`; `/api/info` and `/debug` show `fallbackOrder`, `primaryUp`, `primaryLastError` and the health of each backend.
- Hedged requests: when `jev` has no answer after 1.0 s, the same question also goes to Gemini Decision on OpenRouter (while it has credits), and the first answer wins. Transcription does the same after 2.5 s with the same model on OpenRouter. After 3 wins of the second request in a row, the second request starts at once. `POST /api/detector` changes `hedgeSeconds`, `hedgeTo`, `transcribeHedge` and `visionHedge` at run time (0 turns a hedge off, for a run on Hostinger models only). The Hostinger router's latency rose to 15 to 30 s for any call under load on 14 November, while OpenRouter answered in about 1 s. Answers are cached by the exact sentence and context; a sentence under 4 words is not judged.
- Next to each transcription, a decision `noul` checks that the chunk has intelligible speech. The transcription prompt names the language of the video's earlier speech; a short line in another alphabet (Russian speech that came back as Portuguese) is transcribed again, and is no speech if it stays wrong. A chunk without speech gets no line and no detection. Before each vision read, a decision `noul` checks that public media is in front (TikTok in TikTok mode, about 0.5 s); a private frame is dropped without the read. The speech check and this frame gate need audio and image input, which only Gemini Decision on OpenRouter reads (the Hostinger decision models read text only; an image part is accepted but has no effect). While OpenRouter is down, both are skipped (`speechCheckActive` and `frameGateActive` on `/api/info`): the transcription's `[no speech]` and the screen read decide alone.

```sh
cd hostinger-noise/ios/server
python3 detect.py --selftest
python3 detect.py --backend jev --no-fallback --no-hedge --in testdata/war_sentences.jsonl --out preds.jsonl --score
```

Deploy by hand (it is not in `infra/hostinger/apps.tsv`, so a push does not deploy it). The script stamps the commit for `/api/info` and refuses while a phone broadcast runs:

```sh
hostinger-noise/ios/server/deploy.sh
```

The keys are not in the repository. On the VPS the server reads `/srv/junction-data/lens/router.env` (Hostinger AI Router) and `/srv/junction-data/lens/openrouter.env` (OpenRouter, a key with a 10 USD limit), mounted through `server/.hostinger-volumes`. On a laptop, `detect.py` reads `~/.config/footnote/openrouter.env` and the router key from `infra/hostinger/secrets.env`.

Frames: no image is kept by default, also not in memory. Only a frame request with `X-Lens-Debug: 1` (or `debug=1`) keeps its image, in memory, for `/debug`. The text read from a frame is kept. Per-session state is freed after 10 minutes without a request.

Send a test chunk:

```sh
say -o t.wav --data-format=LEI16@16000 "Everyone in Europe already understands that the sanctions failed."
curl -X POST --data-binary @t.wav "https://lens.srv2159196.hstgr.cloud/api/audio?session=test-1&seq=1"
```

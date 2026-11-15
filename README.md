# Footnote

Footnote is an iPhone app that listens to what you watch. When a claim can be checked, it checks the claim and shows the sources. The Dynamic Island is the whole interface. Footnote never says true or false.

- Site: https://footnotecheck.com/
- API: https://api.footnotecheck.com/

We built Footnote at Junction 2026 for the Hostinger challenge "Decode the Noise". The server, the fact-check agent and the site run on a Hostinger VPS. The claim gate runs through the Hostinger AI Router. The fact-check agent uses Gemini 3.8 Flash with web search.

## How it works

1. The app starts a Live Activity and a ReplayKit broadcast. The broadcast extension sends the app audio in short chunks, and at most one screen frame each second, to the server.
2. The server transcribes each chunk. A fast gate model (Jev) reads each sentence and passes it on only when it is about public affairs, makes a specific claim that sources can check, and the speaker asserts it.
3. The fact-check agent searches the web for that claim and returns a stance, one line and the sources.
4. Only a result where 2 independent source sites differ from the claim reaches the Dynamic Island. All other results stay silent.
5. A private screen (chats, mail, banking, settings and similar) pauses the session. The server does not write images to disk.

The full description is in [`hostinger-noise/ios/README.md`](hostinger-noise/ios/README.md).

## Parts

| Directory | What it is |
| --- | --- |
| [`hostinger-noise/ios/`](hostinger-noise/ios/) | The iPhone app (SwiftUI app, broadcast upload extension, Live Activity widget, shared code), generated from `project.yml` with XcodeGen. The working name in the code is Lens. |
| [`hostinger-noise/ios/server/`](hostinger-noise/ios/server/) | The Footnote server (Python): transcription, the Jev gate, screen reading, the Live Activity state and APNs pushes. |
| [`hostinger-noise/ios/evals/`](hostinger-noise/ios/evals/) | Evals: the Jev gate, the agent, the video boundary, and a silent set with a latency budget. |
| [`footnote-agent/`](footnote-agent/) | The fact-check agent (TypeScript, Effect, XState). It gets one claim, searches the web with one Gemini 3.8 Flash call (`OPENROUTER_MODEL=google/gemini-3.8-flash`, `AGENT_REASONING=low`), and returns a stance and sources. |
| [`footnote-api/`](footnote-api/) | A Caddy reverse proxy that serves `api.footnotecheck.com` from the Footnote server. |
| [`footnote/`](footnote/) | The site at footnotecheck.com (TanStack Start and React): the demo videos, a claim check, the TestFlight beta status and a Stripe checkout. |

`footnote/` and `footnote-agent/` come from our team's app template. Their `README.md`, `AGENTS.md` and `tools/` are from that template. Some deploy scripts refer to files in our team repository that are not in this copy, such as `infra/hostinger/host.sh`.

## Keys

This repository holds no keys. Each part reads its keys from environment variables or from key files outside the repository.

- `footnote/`: `FOOTNOTE_AGENT_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID`. Optional: `FOOTNOTE_AGENT_URL`, `PUBLIC_URL`. See `footnote/.env.example`.
- `footnote-agent/`: `AI_ROUTER_API_KEY`, `FOOTNOTE_AGENT_KEY`, `AGENT_MODEL`. Optional: `OPENROUTER_API_KEY`. See `footnote-agent/.env.example`.
- `hostinger-noise/ios/server/`: `AI_ROUTER_API_KEY` (or a file in `LENS_KEY_FILE`), `OPENROUTER_API_KEY`, `LENS_AGENT_KEY` (or a file in `LENS_AGENT_KEY_FILE`). For APNs pushes: `LENS_APNS_KEY_FILE`, `LENS_APNS_KEY_ID`, `LENS_APNS_TEAM`.
- `hostinger-noise/ios/`: set your own Apple development team in `project.yml` (`DEVELOPMENT_TEAM`) before you build for a device.

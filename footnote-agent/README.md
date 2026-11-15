# footnote-agent

The fact-check step of Footnote. It gets one claim, searches the web with one model call through the Hostinger AI Router, and says whether established sources contradict it.

- Live: https://footnote-agent.srv2159196.hstgr.cloud
- Key: `FOOTNOTE_AGENT_KEY` in `.env.hostinger` (copy `.env.example` and fill in the values). Model: `AGENT_MODEL` (default `gpt-6-luna`, reasoning effort `none`).

## API

`POST /api/check` with `Authorization: Bearer <FOOTNOTE_AGENT_KEY>`:

```json
{
  "claim": "...",
  "context": "optional",
  "handle": "optional",
  "caption": "optional",
  "frames": ["optional, at most 2, ignored"],
  "lang": "optional"
}
```

200:

```json
{
  "claim": "at most 60 characters",
  "stance": "differ|support|mixed|none",
  "line": "at most 140 characters",
  "sources": [
    { "title": "...", "url": "https://...", "site": "usatoday.com", "date": "YYYY-MM-DD, optional" }
  ],
  "searches": 1,
  "ms": 7800,
  "model": "gpt-6-luna"
}
```

- 400: bad input. 401: no or wrong key. 503 `{"error": "router: insufficient credits"}`: the AI Router has no credits. 502: the search failed or took more than 17 s.
- `GET /api/health` gives `{"ok": true, "model": "..."}`.

## Flow

`src/domain/check-flow.machine.ts`: `searching` (one model call with web search, 17 s limit) goes to `done`, or to `secondPass` when the stance is `differ` with fewer than 2 sites. `secondPass` (7 s limit) looks for other sites; with still fewer than 2 sites the stance becomes `mixed`. `src/server/researcher.ts` calls the router and cleans the reply: no social media, one source per site, at most 4, no verdict words in the line.

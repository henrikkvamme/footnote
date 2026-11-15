"""Footnote technique detector: names the UNLP 2025 manipulation techniques in one sentence of war content.

Standard library only. Use it as a module (the server imports it) or as a CLI (the benchmark calls it).

Backends, same output shape:
  jev       jev-latest on the Hostinger AI Router's System One API (POST /v1/systemone, the same `state` and
            `questions` as the Decisions API). The default.
  msdecision  microsoft-decision-1 on the same API.
  decision  Google gemini-decision-1 through OpenRouter's Decisions API (POST /api/alpha/decisions):
            one request per item, 13 independent `noul` questions (10 techniques, 3 gates), no generated text.
  router    an LLM on the Hostinger AI Router (gemini-3.5-flash-lite) with strict JSON: one probability
            per question, the same instructions and criteria.

All prompt text is in techniques.json, so it can be tuned and versioned without code changes.

Item:   {"id", "text", "screen_text"?, "description"?, "before"?, "lang"?, "source"? ("speech" or "screen")}
Result: {"id", "probs": {technique: p}, "top", "confidence", "gates": {"war": p, "quote": p},
         "latency_ms", "backend", "model", "cost_usd", "credits", "error"?}
        plus the verdict: "note", "unsure", "technique" (top when note), "label", "short", "why".

CLI
  python detect.py --backend decision --in items.jsonl --out preds.jsonl [--score]
  python detect.py --selftest
"""

import argparse
import base64
import collections
import concurrent.futures
import hashlib
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC_FILE = os.environ.get("FOOTNOTE_TECHNIQUES", os.path.join(HERE, "techniques.json"))

DECISION_URL = os.environ.get("OPENROUTER_DECISIONS_URL", "https://openrouter.ai/api/alpha/decisions")
DECISION_MODEL = os.environ.get("FOOTNOTE_DECISION_MODEL", "google/gemini-decision-1")
ROUTER_URL = os.environ.get("AI_ROUTER_BASE_URL", "https://router.hostinger.com/v1").rstrip("/")
ROUTER_MODEL = os.environ.get("FOOTNOTE_ROUTER_MODEL", "gemini-3.5-flash-lite")
TIMEOUT = float(os.environ.get("FOOTNOTE_DETECT_TIMEOUT", "8"))
NOTE = 0.7  # top probability for a note
UNSURE = 0.5  # top probability for an unsure mark
GATE = 0.5  # war gate true at or above, quote gate true at or above
# Decision models on the Hostinger AI Router (POST /v1/systemone, the System One API of TypeSafe: the same `state` and
# `questions` as OpenRouter's Decisions API). Text input only.
SYSTEMONE_URL = ROUTER_URL + "/systemone"
JEV_MODEL = os.environ.get("FOOTNOTE_JEV_MODEL", "jev-latest")  # pin with jev-1.14
MSDECISION_MODEL = os.environ.get("FOOTNOTE_MSDECISION_MODEL", "microsoft-decision-1")
# Jev on OpenRouter's decisions API (backend "jevor"): the same model family, used while the Hostinger router has no credits.
JEVOR_MODEL = os.environ.get("FOOTNOTE_JEVOR_MODEL", "typesafe/jev-1.14")
# A backend that refuses (HTTP 401, 402, 403 or 429, such as "Insufficient credits") or fails 3 times in a row counts as
# down for DOWN_SECONDS: each request goes to the next backend in FALLBACK, and the primary is tried again after that.
DOWN_SECONDS = float(os.environ.get("FOOTNOTE_DOWN_SECONDS", "60"))
# The order: the other Hostinger decision model, the Hostinger router LLM, then Gemini Decision on OpenRouter (only while
# its account has credits: a 402 marks it down, and it is tried again after DOWN_SECONDS).
FALLBACK = [b for b in os.environ.get("FOOTNOTE_FALLBACK", "jevor,msdecision,router,decision").split(",") if b]
# The default backend when the router key exists. jev and msdecision tie on war_sentences (27 of 30, no false notes) and
# take about 1.1 s per call; jev made 1 false note on the real video lines, msdecision 3 (server/PERF.md).
PRIMARY = os.environ.get("FOOTNOTE_BACKEND", "jev")
# A hedged request: when the first call has no answer after HEDGE_SECONDS, a second identical call starts, and the
# first answer wins. The System One latency has a long tail (1 s typical, 5 s and more under load). 0 turns it off.
HEDGE_SECONDS = float(os.environ.get("FOOTNOTE_HEDGE_SECONDS", "2.5"))  # jev answers in 0.97 s at p50 (perf-jev); the hedge is only for a slow or failed call, so Jev itself is the gate
HEDGED = {"jev", "jevor", "msdecision", "decision"}
# The backend of the hedged second call: Gemini Decision on OpenRouter (about 0.5 s, the same quality on war_sentences)
# while it is up, else the same backend again.
HEDGE_TO = os.environ.get("FOOTNOTE_HEDGE_TO", "decision")
CACHE_SIZE = 4000  # answers kept by the hash of the exact input (the sentence and its context)
health: "dict[str, dict]" = {}  # backend -> {"until", "reason", "errors" (in a row), "last_error", "last_error_at"}
USER_AGENT = "footnote-detect/1"  # Cloudflare in front of the router rejects Python-urllib


def load_spec(path=SPEC_FILE):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


SPEC = load_spec()
TECHNIQUES = [t["id"] for t in SPEC["techniques"]]
BY_ID = {t["id"]: t for t in SPEC["techniques"]}
GATES = list(SPEC["gates"])


def _key_from_files(name, files):
    """A key from the environment, or from the first KEY=value file that has it."""
    if os.environ.get(name):
        return os.environ[name]
    for path in files:
        path = os.path.expanduser(path)
        if os.path.isfile(path):
            try:
                for line in open(path, encoding="utf-8"):
                    if line.startswith(name + "="):
                        return line.split("=", 1)[1].strip().strip('"')
            except OSError:
                pass
    return ""


def openrouter_key():
    return _key_from_files("OPENROUTER_API_KEY", ["/run/lens/openrouter.env", "~/.config/footnote/openrouter.env"])


def router_key():
    return _key_from_files("AI_ROUTER_API_KEY", [os.environ.get("LENS_KEY_FILE", "/run/lens/router.env"),
                                                 os.path.join(HERE, "../../../infra/hostinger/secrets.env")])


def default_backend():
    if router_key():
        return PRIMARY
    return "decision" if openrouter_key() else "router"


# MARK: Prompt building


def questions(spec=SPEC):
    """The 13 noul questions: the three gates (war, quote, claim) and the ten techniques. The same text for each backend."""
    common = spec.get("common", "")
    out = {}
    for name, g in spec["gates"].items():
        out[name] = {"type": "noul", "instructions": common + g["instructions"], "criteria": g["criteria"]}
    for t in spec["techniques"]:
        out[t["id"]] = {"type": "noul", "instructions": common + t["instructions"], "criteria": t["criteria"]}
    return out


QUESTIONS = questions()
GATE_QUESTIONS = {n: QUESTIONS[n] for n in GATES}
# The live product asks only the gates (the claim lane); the 10 technique questions come back with the server setting
# "techniques" (and always in the command line benchmark).
TECHNIQUES_ON = os.environ.get("FOOTNOTE_TECHNIQUES_ON", "0") == "1"


def asked():
    """The questions of each detector call now."""
    return QUESTIONS if TECHNIQUES_ON else GATE_QUESTIONS


def context_lines(item, spec=SPEC):
    """The shared input as plain strings: the text to judge first, then the context."""
    ctx = spec["context"]
    kind = "screen" if item.get("source") == "screen" else "speech"
    lines = [ctx[kind].format(text=item.get("text", "").strip())]
    for key in ("before", "screen_text", "description"):
        value = " ".join(str(item.get(key) or "").split())
        if value and value != item.get("text", "").strip():
            lines.append(ctx[key].format(**{key: value[:600]}))
    return lines


# MARK: HTTP


def _read_json(r):
    """The JSON body, returned as soon as it is complete. The AI Router's /v1/systemone sends the whole answer at once
    but ends the chunked stream 0.4 to 5 s later (perf-jev/README.md); r.read() would wait for that end. Leaving the
    `with` block closes the connection, so the unread end of the stream does no harm."""
    buf = b""
    while True:
        chunk = r.read1(65536)
        if not chunk:
            return json.loads(buf)
        buf += chunk
        if buf.rstrip().endswith(b"}"):
            try:
                return json.loads(buf)
            except ValueError:
                pass  # a "}" inside the body, not its end: read on


def _post(url, payload, key, timeout):
    """POST JSON with one retry on a timeout, 429 or 5xx. Returns (data, None) or (None, error)."""
    body = json.dumps(payload).encode()
    err = "no attempt"
    t0 = time.time()
    for attempt in range(2):
        if attempt and time.time() - t0 > timeout / 2:
            break  # retry only after a fast failure, so one call never takes much longer than `timeout`
        req = urllib.request.Request(url, data=body, headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": USER_AGENT,
            "X-Title": "Footnote", "HTTP-Referer": "https://lens.srv2159196.hstgr.cloud/"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return _read_json(r), None
        except urllib.error.HTTPError as e:
            err = "HTTP %s: %s" % (e.code, e.read()[:300].decode(errors="replace"))
            if e.code != 429 and e.code < 500:
                return None, err
        except Exception as e:  # noqa: BLE001 - every failure becomes an error result
            err = "%s: %s" % (type(e).__name__, e)
        if attempt == 0:
            time.sleep(0.3)
    return None, err


# MARK: Backends


def _decision(item, model=None):
    model = model or DECISION_MODEL
    key = openrouter_key()
    if not key:
        return None, "OPENROUTER_API_KEY is not set"
    state = context_lines(item)
    # Optional media for experiments and the benchmark: raw base64 WAV audio or a JPEG frame.
    if item.get("audio_b64"):
        state.append({"type": "input_audio", "input_audio": {"data": item["audio_b64"], "format": "wav"}})
    if item.get("image_b64"):
        state.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + item["image_b64"]}})
    qs = asked()
    data, err = _post(DECISION_URL, {"model": model, "state": state, "questions": qs}, key, TIMEOUT)
    if err:
        return None, err
    answers = data.get("answers") or {}
    probs, conf = {}, {}
    for name in qs:
        a = answers.get(name) or {}
        if a.get("type") == "noul" and isinstance(a.get("noul"), (int, float)):
            probs[name] = float(a["noul"])
            conf[name] = float(a.get("confidence", max(a["noul"], 1 - a["noul"])))
    if not probs:
        return None, "no noul answers in %r" % (json.dumps(data)[:200],)
    usage = data.get("usage") or {}
    return {"probs": probs, "conf": conf, "model": data.get("model", model), "cost_usd": usage.get("cost"),
            "input_tokens": usage.get("input_tokens"), "refused": [n for n in qs if n not in probs]}, None


def _router(item):
    key = router_key()
    if not key:
        return None, "AI_ROUTER_API_KEY is not set"
    names = list(asked())
    prompt = SPEC["router_prompt"].replace("{keys}", ", ".join('"%s"' % n for n in names))
    blocks = ["CONTENT", "\n".join(context_lines(item)), "", "QUESTIONS"]
    for n in names:
        q = QUESTIONS[n]
        blocks.append('"%s": %s\n  true if: %s\n  false if: %s' % (n, q["instructions"], q["criteria"]["true"], q["criteria"]["false"]))
    payload = {"model": ROUTER_MODEL, "response_format": {"type": "json_object"}, "temperature": 0,
               "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": "\n".join(blocks)}]}
    data, err = _post(ROUTER_URL + "/chat/completions", payload, key, max(TIMEOUT, 15))
    if err:
        return None, "router " + err
    try:
        text = (data["choices"][0]["message"]["content"] or "").strip()
        start, end = text.find("{"), text.rfind("}")
        parsed = json.loads(text[start : end + 1])
    except Exception as e:  # noqa: BLE001
        return None, "bad router JSON: %r" % (e,)
    probs = {}
    for n in names:
        v = parsed.get(n)
        try:
            probs[n] = min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            pass
    if not probs:
        return None, "router JSON has no probabilities"
    return {"probs": probs, "conf": {}, "model": data.get("model", ROUTER_MODEL),
            "credits": (data.get("usage") or {}).get("credits_charged"), "refused": [n for n in names if n not in probs]}, None


def _systemone(model):
    def call(item):
        key = router_key()
        if not key:
            return None, "AI_ROUTER_API_KEY is not set"
        qs = asked()
        data, err = _post(SYSTEMONE_URL, {"model": model, "state": context_lines(item), "questions": qs}, key, max(TIMEOUT, 12))
        if err:
            return None, err
        answers = data.get("answers") or {}
        probs, refused_q = {}, []
        for name in qs:
            a = answers.get(name) or {}
            if isinstance(a.get("noul"), (int, float)):
                probs[name] = float(a["noul"])
            else:
                refused_q.append(name)
        if not probs:
            return None, "no answers: %s" % json.dumps(data)[:200]
        return {"probs": probs, "conf": {}, "model": data.get("model", model), "credits": (data.get("usage") or {}).get("credits_charged"),
                "input_tokens": (data.get("usage") or {}).get("input_tokens"), "refused": refused_q}, None
    return call


BACKENDS = {"decision": _decision, "jevor": lambda item: _decision(item, JEVOR_MODEL), "router": _router, "jev": _systemone(JEV_MODEL), "msdecision": _systemone(MSDECISION_MODEL)}
MODELS = {"decision": DECISION_MODEL, "jevor": JEVOR_MODEL, "router": ROUTER_MODEL, "jev": JEV_MODEL, "msdecision": MSDECISION_MODEL}


# MARK: Verdict


def verdict(result, note=NOTE, unsure=UNSURE):
    """A note only when the top technique is at or above `note`, the war gate is true and the quote gate is
    false. Between `unsure` and `note` (with the same gates) the result is unsure. Adds the display fields."""
    probs, gates = result.get("probs") or {}, result.get("gates") or {}
    top = result.get("top")
    p = probs.get(top, 0.0) if top else 0.0
    gated = gates.get("war", 0.0) >= GATE and gates.get("quote", 1.0) < GATE
    result["note"] = bool(top) and gated and p >= note
    result["unsure"] = bool(top) and gated and unsure <= p < note
    result["technique"] = top if result["note"] else None
    info = BY_ID.get(top) if (result["note"] or result["unsure"]) else None
    for k in ("label", "short", "why", "explain"):
        result[k] = info[k] if info else None
    return result


def refused(err):
    """True for an error that says the account cannot be used now (no credits, bad key, rate limit)."""
    return bool(err) and err.startswith(("HTTP 401", "HTTP 402", "HTTP 403", "HTTP 429", "router HTTP 401", "router HTTP 402",
                                         "router HTTP 403", "router HTTP 429"))


def _health(backend):
    return health.setdefault(backend, {"until": 0.0, "reason": "", "errors": 0, "last_error": "", "last_error_at": 0.0})


def note_outcome(backend, err):
    """Counts a backend's errors; a refusal or 3 errors in a row mark it down for DOWN_SECONDS."""
    h = _health(backend)
    if not err:
        h["errors"] = 0
        return
    h.update(errors=h["errors"] + 1, last_error=err[:200], last_error_at=time.time())
    if refused(err) or h["errors"] >= 3:
        h.update(until=time.time() + DOWN_SECONDS, reason=err[:160], errors=0)


def is_up(backend):
    return time.time() >= _health(backend)["until"]


def decision_ok():
    """False while the decision backend is down (the speech check and the TikTok gate use it too)."""
    return is_up("decision")


def mark_down(err):
    note_outcome("decision", err)


def status():
    """The health of each backend, for /api/info and /debug."""
    now = time.time()
    return {b: {"up": now >= h["until"], "retryInS": max(0, round(h["until"] - now)), "reason": h["reason"], "errorsInARow": h["errors"],
                "lastError": h["last_error"], "lastErrorAgoS": round(now - h["last_error_at"]) if h["last_error_at"] else None}
            for b, h in health.items()}


def _call(backend, item):
    try:
        return BACKENDS[backend](item)
    except Exception as e:  # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, e)


_hedge_pool = concurrent.futures.ThreadPoolExecutor(max_workers=48, thread_name_prefix="hedge")
hedge_stats = {"calls": 0, "hedged": 0, "second_won": 0, "streak": 0}


def _hedge_backend(backend):
    if HEDGE_TO != backend and HEDGE_TO in BACKENDS and is_up(HEDGE_TO) and _usable(HEDGE_TO):
        return HEDGE_TO
    return backend


def _run(backend, item):
    """One backend call, hedged for the decision backends: a second call (to HEDGE_TO while it is up) after
    HEDGE_SECONDS, and the first answer wins. Returns (out, error, the backend that answered)."""
    if HEDGE_SECONDS <= 0 or backend not in HEDGED:
        out, err = _call(backend, item)
        note_outcome(backend, err)
        return out, err, backend
    hedge_stats["calls"] += 1
    t0 = time.time()
    first = _hedge_pool.submit(_call, backend, item)

    def first_done(f):
        # The first backend answered within HEDGE_SECONDS, even when the second won the race: it is fast again, so the
        # next call waits the full HEDGE_SECONDS for it. Without this the 0.3 s step latches after 3 slow calls, and a
        # backend that needs 0.7 s (jev on the router) never wins again (/api/info showed streak = calls = 437).
        if time.time() - t0 <= HEDGE_SECONDS and not f.exception() and not f.result()[1]:
            hedge_stats["streak"] = 0

    first.add_done_callback(first_done)
    # While the second call won the last 3 times (the first backend is slow now), it starts almost at once.
    try:
        out, err = first.result(timeout=0.3 if hedge_stats["streak"] >= 3 else HEDGE_SECONDS)
        note_outcome(backend, err)
        if not err:
            hedge_stats["streak"] = 0
        return out, err, backend
    except concurrent.futures.TimeoutError:
        pass
    hedge_stats["hedged"] += 1
    other = _hedge_backend(backend)
    second = _hedge_pool.submit(_call, other, item)
    names = {first: backend, second: other}
    pending = {first, second}
    out, err, answered = None, "no answer", backend
    while pending:
        done, pending = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
        for f in done:
            o, e = f.result()
            if names[f] != backend or f is first:
                note_outcome(names[f], e)
            if not e:
                if f is second:
                    hedge_stats["second_won"] += 1
                hedge_stats["streak"] = hedge_stats["streak"] + 1 if f is second else 0
                return o, None, names[f]
            out, err, answered = o, e, names[f]
    return out, err, answered


_cache: "collections.OrderedDict[str, tuple]" = collections.OrderedDict()
_cache_lock = threading.Lock()
cache_stats = {"hits": 0, "misses": 0}


def cache_key(item):
    return hashlib.sha1(json.dumps([SPEC["version"], TECHNIQUES_ON, context_lines(item)], ensure_ascii=False).encode()).hexdigest()


def _usable(backend):
    return backend in BACKENDS and (router_key() if backend in ("router", "jev", "msdecision") else openrouter_key())


def detect_one(item, backend=None, note=NOTE, unsure=UNSURE):
    """Detect techniques in one item. Never raises: a failure is a result with `error` and empty probs.
    When the backend is down or fails, the item goes to the next backend in FALLBACK; the result's `backend` is the
    one that answered, and `fallback` says why."""
    primary = backend or default_backend()
    t0 = time.time()
    key = cache_key(item)
    with _cache_lock:
        hit = _cache.get(key)
        if hit:
            _cache.move_to_end(key)
        cache_stats["hits" if hit else "misses"] += 1
    if hit:
        # The same sentence with the same context (an early partial text, a repeat, "Footnote now"): no new call.
        return _result(item, hit[0], hit[1], None, None, primary, t0, note, unsure, cached=True)
    chain = [primary] + [b for b in FALLBACK if b != primary and _usable(b)]
    fallback, out, err, backend = None, None, "no backend", primary
    for i, b in enumerate(chain):
        last = i == len(chain) - 1
        if not is_up(b) and not last:
            fallback = fallback or "%s down: %s" % (b, _health(b)["reason"])
            continue
        out, err, backend = _run(b, item)
        if not err:
            break
        fallback = fallback or "%s failed: %s" % (b, err[:160])
    if not err:
        with _cache_lock:
            _cache[key] = (backend, out)
            while len(_cache) > CACHE_SIZE:
                _cache.popitem(last=False)
    return _result(item, backend, out, err, fallback, primary, t0, note, unsure)


def _result(item, backend, out, err, fallback, primary, t0, note, unsure, cached=False):
    result = {"id": item.get("id"), "probs": {}, "top": None, "confidence": 0.0, "gates": {}, "latency_ms": round((time.time() - t0) * 1000),
              "backend": backend, "model": MODELS.get(backend, backend), "cost_usd": None, "credits": None}
    if fallback and backend != primary:
        result["fallback"] = fallback
    if err:
        result["error"] = err[:400]
        return verdict(result, note, unsure)
    probs = out["probs"]
    result["probs"] = {t: round(probs[t], 4) for t in TECHNIQUES if t in probs}
    result["gates"] = {g: round(probs[g], 4) for g in GATES if g in probs}
    if result["probs"]:
        top = max(result["probs"], key=result["probs"].get)
        result["top"] = top
        result["confidence"] = round(out["conf"].get(top, max(probs[top], 1 - probs[top])), 4)
    result |= {k: out[k] for k in ("model", "cost_usd", "credits", "input_tokens") if out.get(k) is not None}
    if out.get("refused"):
        result["refused"] = out["refused"]
    if cached:
        result |= {"cached": True, "cost_usd": None, "credits": None}
    return verdict(result, note, unsure)


def detect(items, backend=None, threshold=NOTE, unsure=UNSURE, workers=8):
    """Detect techniques in each item, in parallel. Returns one result per item, in order."""
    items = list(items)
    if not items:
        return []
    if len(items) == 1:
        return [detect_one(items[0], backend, threshold, unsure)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        return list(pool.map(lambda it: detect_one(it, backend, threshold, unsure), items))


def ask_text(state, question, timeout=6):
    """One noul question about text (a list of strings) on the decision backends: the Hostinger models first; when one
    has no answer after HEDGE_SECONDS (or fails), the next one starts too, and the first answer wins.
    Returns (probability, backend, error)."""
    order = [b for b in dict.fromkeys([PRIMARY] + FALLBACK + [HEDGE_TO]) if b in ("jev", "jevor", "msdecision", "decision") and is_up(b) and _usable(b)]

    def one(b):
        url, key = (DECISION_URL, openrouter_key()) if b in ("decision", "jevor") else (SYSTEMONE_URL, router_key())
        data, err = _post(url, {"model": MODELS[b], "state": state, "questions": {"q": question}}, key, timeout)
        if err:
            return None, err
        a = (data.get("answers") or {}).get("q") or {}
        return (float(a["noul"]), None) if isinstance(a.get("noul"), (int, float)) else (None, "no answer: %s" % json.dumps(a)[:120])

    if not order:
        return None, None, "no decision backend is up"
    # The hedge goes to HEDGE_TO first (the fast one), then down the order.
    order = order[:1] + sorted(order[1:], key=lambda b: b != HEDGE_TO)
    pending, err, t0 = {}, "no answer", time.time()
    step = 0.3 if hedge_stats["streak"] >= 3 else HEDGE_SECONDS  # the detector's hedge won 3 times in a row: hedge at once
    while order or pending:
        if order and (not pending or time.time() - t0 >= step * len(pending)):
            b = order.pop(0)
            pending[_hedge_pool.submit(one, b)] = b
        wait = max(0.05, t0 + step * len(pending) - time.time()) if order else None
        done, _ = concurrent.futures.wait(list(pending), timeout=wait, return_when=concurrent.futures.FIRST_COMPLETED)
        for f in done:
            b = pending.pop(f)
            p, e = f.result()
            note_outcome(b, e)
            if e is None:
                return p, b, None
            err = e
    return None, None, err


def ask_one(backend, state, names, timeout=20):
    """The questions `names` (gates or techniques of the spec) about text on one decision backend, with no hedge and no
    fallback (a background question: a few seconds are fine). Returns ({name: probability}, credits, error)."""
    url, key = (DECISION_URL, openrouter_key()) if backend in ("decision", "jevor") else (SYSTEMONE_URL, router_key())
    if not key:
        return None, None, "no key for %s" % backend
    data, err = _post(url, {"model": MODELS[backend], "state": state, "questions": {n: QUESTIONS[n] for n in names}}, key, timeout)
    note_outcome(backend, err)
    if err:
        return None, None, err
    answers = data.get("answers") or {}
    probs = {n: float(answers[n]["noul"]) for n in names if isinstance((answers.get(n) or {}).get("noul"), (int, float))}
    if not probs:
        return None, None, "no answers: %s" % json.dumps(data)[:160]
    return probs, (data.get("usage") or {}).get("credits_charged"), None


def ask_media(part, question, timeout=TIMEOUT, more=None, model=None, before=()):
    """One decision question about one media part (an image_url or input_audio part). `more` adds questions to the same
    call ({name: question}); their answers are in `extra_answers` after the call. Returns (probability, ms, cost_usd, error)."""
    key = openrouter_key()
    if not key:
        return None, 0, None, "OPENROUTER_API_KEY is not set"
    if not decision_ok():
        return None, 0, None, "decision down: " + _health("decision")["reason"]
    t0 = time.time()
    data, err = _post(DECISION_URL, {"model": model or DECISION_MODEL, "state": list(before) + [part], "questions": {"q": question} | (more or {})}, key, timeout)
    ms = round((time.time() - t0) * 1000)
    if more is not None:
        answers = (data or {}).get("answers") or {}
        more.clear()
        more.update({n: float(a["noul"]) for n, a in answers.items() if n != "q" and isinstance((a or {}).get("noul"), (int, float))})
    if refused(err):
        mark_down(err)
    if err:
        return None, ms, None, err
    a = (data.get("answers") or {}).get("q") or {}
    cost = (data.get("usage") or {}).get("cost")
    if not isinstance(a.get("noul"), (int, float)):
        return None, ms, cost, "no answer: %s" % json.dumps(a)[:120]
    return float(a["noul"]), ms, cost, None


FRAME_MODEL = os.environ.get("FOOTNOTE_FRAME_MODEL", JEVOR_MODEL)  # Jev reads the frames (0.4 s, about 0.00008 USD per frame)


def _image(jpeg):
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}


def ask_frame(jpeg, question, criteria_true="", criteria_false="", timeout=TIMEOUT, more=None, ref=None):
    """One noul question to Jev about a JPEG frame (`more`: see ask_media). With `ref`, the state has two screens: the
    reference frame, then this one. Returns (probability, ms, cost_usd, error)."""
    q = {"type": "noul", "instructions": question}
    if criteria_true or criteria_false:
        q["criteria"] = {"true": criteria_true, "false": criteria_false}
    before = ["Reference screen:", _image(ref), "Screen now:"] if ref else ()
    return ask_media(_image(jpeg), q, timeout, more, model=FRAME_MODEL, before=before)


# MARK: CLI


def score(items, preds, note=NOTE):
    """Summary for items that carry a gold `label` (a technique id or "none")."""
    rows = []
    by_id = {p["id"]: p for p in preds}
    for it in items:
        if "label" not in it:
            continue
        p = by_id.get(it["id"], {})
        gold = it["label"]
        pred = p.get("technique") or "none"
        rows.append({"lang": it.get("lang", "?"), "gold": gold, "pred": pred, "top": p.get("top"), "unsure": p.get("unsure"),
                     "error": bool(p.get("error")), "ms": p.get("latency_ms", 0)})
    out = {}
    for group in ["all"] + sorted({r["lang"] for r in rows}):
        rs = [r for r in rows if group == "all" or r["lang"] == group]
        pos = [r for r in rs if r["gold"] != "none"]
        neg = [r for r in rs if r["gold"] == "none"]
        out[group] = {
            "clear": len(pos),
            "note_exact": sum(r["pred"] == r["gold"] for r in pos),  # a note with the right technique
            "top_exact": sum(r["top"] == r["gold"] for r in pos),  # the right technique ranked first, note or not
            "missed": sum(r["pred"] == "none" for r in pos),
            "negatives": len(neg),
            "false_notes": sum(r["pred"] != "none" for r in neg),
            "false_unsure": sum(bool(r["unsure"]) for r in neg),
            "errors": sum(r["error"] for r in rs),
        }
    ms = sorted(r["ms"] for r in rows if not r["error"])
    if ms:
        out["latency_ms"] = {"median": statistics.median(ms), "p95": ms[min(len(ms) - 1, int(round(0.95 * (len(ms) - 1))))], "n": len(ms)}
    costs = [p.get("cost_usd") for p in preds if p.get("cost_usd")]
    credits = [p.get("credits") for p in preds if p.get("credits")]
    if costs:
        out["usd_per_1000"] = round(1000 * sum(costs) / len(costs), 4)
    if credits:
        out["credits_per_1000"] = round(1000 * sum(float(c) for c in credits) / len(credits), 4)
    return out


SELFTEST = [
    {"id": "st-loaded", "lang": "en", "label": "loaded_language", "text": "The Kyiv junta and its Nazi puppets keep butchering their own people for Western money."},
    {"id": "st-neutral", "lang": "en", "label": "none", "text": "Ukraine's parliament voted on Tuesday to extend martial law for another 90 days."},
    {"id": "st-uk-fear", "lang": "uk", "label": "appeal_to_fear", "text": "Якщо ви за кордоном і не стали на облік, вам заблокують усі банківські картки, і ви залишитеся ні з чим."},
]


def selftest(backends):
    ok = True
    assert len(TECHNIQUES) == 10 and set(GATES) == {"war", "quote", "claim", "public"}, "techniques.json must have 10 techniques and 4 gates"
    for t in SPEC["techniques"]:
        assert len(t["short"]) <= 9, "short label longer than 9: %s" % t["short"]
        assert t["why"].endswith(".") and t["criteria"].get("true") and t["criteria"].get("false"), t["id"]
    print("spec %s: %d techniques, %d gates, %d questions" % (SPEC["version"], len(TECHNIQUES), len(GATES), len(QUESTIONS)))
    for b in backends:
        preds = detect(SELFTEST, backend=b)
        for it, p in zip(SELFTEST, preds):
            got = p.get("technique") or "none"
            good = got == it["label"] and not p.get("error")
            ok &= good
            print("%-8s %-10s gold %-16s got %-16s top %s %.2f war %.2f quote %.2f %5d ms %s%s" % (
                b, it["id"], it["label"], got, p.get("top"), p["probs"].get(p.get("top"), 0) if p.get("top") else 0,
                p["gates"].get("war", 0), p["gates"].get("quote", 0), p["latency_ms"], "ok" if good else "FAIL", " " + p.get("error", "") if p.get("error") else ""))
    return ok


def main():
    ap = argparse.ArgumentParser(description="Footnote technique detector")
    ap.add_argument("--backend", choices=sorted(BACKENDS), default=None, help="default: FOOTNOTE_BACKEND (jev) when the router key exists, else decision")
    ap.add_argument("--no-fallback", action="store_true", help="measure one backend: no fallback to the next backend")
    ap.add_argument("--no-hedge", action="store_true", help="measure one call per item: no hedged second call")
    ap.add_argument("--in", dest="inp", help="items JSONL ('-' for stdin)")
    ap.add_argument("--out", help="predictions JSONL ('-' or empty for stdout)")
    ap.add_argument("--threshold", type=float, default=NOTE, help="top probability for a note (default 0.7)")
    ap.add_argument("--unsure", type=float, default=UNSURE, help="top probability for unsure (default 0.5)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--score", action="store_true", help="print a summary for items with a gold `label`")
    ap.add_argument("--selftest", action="store_true", help="check techniques.json and run 3 items on each backend with a key")
    args = ap.parse_args()
    global HEDGE_SECONDS, TECHNIQUES_ON
    TECHNIQUES_ON = True  # the benchmark scores the techniques
    if args.no_fallback:
        FALLBACK.clear()
    if args.no_hedge:
        HEDGE_SECONDS = 0
    if args.selftest:
        backends = [args.backend] if args.backend else [b for b, k in (("decision", openrouter_key()), ("router", router_key())) if k]
        sys.exit(0 if selftest(backends) else 1)
    if not args.inp:
        ap.error("--in is required")
    src = sys.stdin if args.inp == "-" else open(args.inp, encoding="utf-8")
    items = [json.loads(l) for l in src if l.strip()]
    for i, it in enumerate(items):
        it.setdefault("id", str(i))
    preds = detect(items, backend=args.backend, threshold=args.threshold, unsure=args.unsure, workers=args.workers)
    out = sys.stdout if not args.out or args.out == "-" else open(args.out, "w", encoding="utf-8")
    for p in preds:
        out.write(json.dumps(p, ensure_ascii=False) + "\n")
    if out is not sys.stdout:
        out.close()
    if args.score:
        print(json.dumps(score(items, preds, args.threshold), indent=1), file=sys.stderr)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Latency of the decision models: Jev and microsoft-decision-1 on the Hostinger AI Router (POST /v1/systemone) and
Gemini Decision on OpenRouter (POST /api/alpha/decisions). Standard library only, so it runs on the VPS as it is.

Each call records, from the moment the request is sent:
  connect  TCP and TLS (only for a new connection)
  headers  the status line and the headers arrive
  json     the body parses as a complete JSON object (the answer is usable here)
  end      the body ends (the end of the stream: what urllib's r.read() waits for)

  python3 bench.py --model jev-latest --questions 1 --n 20 --mode warm --out runs/mac-jev-latest-q1-warm.jsonl
  python3 bench.py --model gemini --questions 13 --n 20 --mode cold

mode cold: a new TCP and TLS connection per call, as detect.py does with urllib today.
mode warm: one keep-alive connection for all calls (a new one only when the server closes it).
--early: stop reading when the JSON is complete and close the connection (the fix that does not wait for the end).
Keys: AI_ROUTER_API_KEY and OPENROUTER_API_KEY from the environment or the files detect.py reads. Never printed.
"""
import argparse
import http.client
import json
import os
import socket
import ssl
import statistics
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import detect  # noqa: E402  (the same questions, context and key files as the server)

ROUTER = "https://router.hostinger.com/v1/systemone"
OPENROUTER = "https://openrouter.ai/api/alpha/decisions"
UA = "footnote-perf/1"
ITEM = {"text": "On 3 March 2024 the parliament voted 312 to 41 to extend martial law for another 90 days.",
        "before": "The host talks about the war in Ukraine and the new mobilisation law."}


def payload(model, nq):
    qs = detect.QUESTIONS
    if nq == 1:
        qs = {"claim": qs["claim"]}
    else:
        qs = dict(list(qs.items())[:nq])
    return {"model": model, "state": detect.context_lines(ITEM), "questions": qs}


class Conn:
    def __init__(self, url, key):
        u = urllib.parse.urlsplit(url)
        self.host, self.path, self.key = u.hostname, u.path, key
        self.ctx = ssl.create_default_context()
        self.c = None

    def open(self):
        t = time.perf_counter()
        self.c = http.client.HTTPSConnection(self.host, 443, timeout=30, context=self.ctx)
        self.c.connect()
        self.c.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return time.perf_counter() - t

    def close(self):
        if self.c:
            self.c.close()
        self.c = None

    def call(self, body, early):
        rec = {"connect": 0.0, "reused": self.c is not None}
        if self.c is None:
            rec["connect"] = self.open()
        t0 = time.perf_counter()
        self.c.request("POST", self.path, body=body, headers={
            "Authorization": "Bearer " + self.key, "Content-Type": "application/json", "User-Agent": UA,
            "X-Title": "Footnote", "HTTP-Referer": "https://lens.srv2159196.hstgr.cloud/"})
        r = self.c.getresponse()
        rec["headers"] = time.perf_counter() - t0
        rec["status"] = r.status
        rec["hdr"] = {k.lower(): v for k, v in r.getheaders() if k.lower() in (
            "content-length", "transfer-encoding", "connection", "keep-alive", "cf-ray", "via", "server", "retry-after",
            "x-ratelimit-remaining", "x-ratelimit-limit", "x-ratelimit-reset", "x-request-id", "x-generation-id")
            or k.lower().startswith("x-ratelimit")}
        buf, data = b"", None
        while True:
            chunk = r.read1(65536)
            if not chunk:
                break
            buf += chunk
            if data is None:
                try:
                    data = json.loads(buf)
                    rec["json"] = time.perf_counter() - t0
                    if early:
                        break
                except ValueError:
                    pass
        if early and data is not None and not r.isclosed():
            self.close()  # the rest of the stream is not read: this connection cannot be reused
        else:
            rec["end"] = time.perf_counter() - t0
            if r.getheader("connection", "").lower() == "close" or r.will_close:
                self.close()
        rec["total"] = rec["connect"] + rec.get("json", rec.get("end", 0.0))
        if data is None:
            rec["error"] = buf[:200].decode(errors="replace")
        else:
            usage = data.get("usage") or {}
            rec["credits"] = usage.get("credits_charged")
            rec["cost_usd"] = usage.get("cost")
            rec["answers"] = {k: v.get("noul") for k, v in (data.get("answers") or {}).items()}
            if r.status != 200:
                rec["error"] = json.dumps(data)[:200]
        return rec


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="jev-latest, jev-1.13, jev-1.14, microsoft-decision-1, or gemini")
    ap.add_argument("--questions", type=int, default=1, help="1 (the claim gate) or up to 13 (all of techniques.json)")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--mode", choices=["cold", "warm"], default="warm")
    ap.add_argument("--early", action="store_true")
    ap.add_argument("--gap", type=float, default=0.3, help="seconds between calls")
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--label", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if a.model == "gemini":
        url, key, model = OPENROUTER, detect.openrouter_key(), detect.DECISION_MODEL
    else:
        url, key, model = ROUTER, detect.router_key(), a.model
    if not key:
        sys.exit("no key for " + url)
    body = json.dumps(payload(model, a.questions)).encode()
    conn = Conn(url, key)
    recs = []
    for i in range(a.n):
        if a.mode == "cold":
            conn.close()
        t = time.time()
        try:
            rec = conn.call(body, a.early)
        except Exception as e:  # noqa: BLE001
            conn.close()
            rec = {"error": "%s: %s" % (type(e).__name__, e), "total": None}
        rec.update({"i": i, "at": time.strftime("%H:%M:%SZ", time.gmtime(t)), "model": a.model, "q": a.questions,
                    "mode": a.mode, "early": a.early, "label": a.label})
        recs.append(rec)
        print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in rec.items() if k not in ("hdr", "answers")}), flush=True)
        time.sleep(a.gap)
    conn.close()
    ok = [r for r in recs if not r.get("error") and r.get("total")]
    summary = {"model": a.model, "q": a.questions, "mode": a.mode, "early": a.early, "label": a.label, "n": len(recs),
               "errors": len(recs) - len(ok), "bytes": len(body)}
    for f in ("connect", "headers", "json", "end", "total"):
        xs = [r[f] for r in ok if r.get(f) is not None]
        if xs:
            summary[f] = {"p50": round(statistics.median(xs), 3), "p95": round(pct(xs, 95), 3), "max": round(max(xs), 3)}
    summary["credits"] = round(sum(r.get("credits") or 0 for r in recs), 5)
    summary["usd"] = round(sum(r.get("cost_usd") or 0 for r in recs), 5)
    summary["hdr"] = ok[-1]["hdr"] if ok else (recs[-1].get("hdr") if recs else None)
    print("SUMMARY " + json.dumps(summary), flush=True)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
            f.write(json.dumps({"summary": summary}) + "\n")


if __name__ == "__main__":
    main()

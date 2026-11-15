#!/usr/bin/env python3
"""Eval of footnote-agent: stance against known verdicts, and the quality of its sources.

Usage:
  python3 run.py                       # local agent at http://127.0.0.1:3017, cache tag "local"
  python3 run.py --live                # the deployed agent, cache tag "live"
  python3 run.py --url URL --tag NAME  # any agent
  python3 run.py --tag local --report  # report from the cache only, no calls

Raw answers are cached in cache/<tag>/<id>.json. Delete a file to call that claim again.
Fetched source pages are cached in cache/urls.json.
"""

import argparse
import concurrent.futures as cf
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
LIVE = "https://footnote-agent.srv2159196.hstgr.cloud"
LOCAL = "http://127.0.0.1:3017"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
STOP = set(
    "about after also been before being from have into just like made more most much only over said says that their them then there these they this those were what when which will with would your youre thats were literally actually really everyone people".split()
)
TWO_LEVEL = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "org.au", "gov.au", "co.jp", "or.jp", "co.nz", "com.br"}


def env_value(path, name):
    try:
        for line in Path(path).read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"')
    except FileNotFoundError:
        return None
    return None


def agent_key():
    return os.environ.get("FOOTNOTE_AGENT_KEY") or env_value(REPO / "footnote-agent/.env.hostinger", "FOOTNOTE_AGENT_KEY")


def openrouter_key():
    return os.environ.get("OPENROUTER_API_KEY") or env_value(Path.home() / ".config/footnote/openrouter.env", "OPENROUTER_API_KEY")


def openrouter_usage():
    key = openrouter_key()
    if not key:
        return None
    req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return float(json.load(res)["data"]["usage"])
    except Exception:
        return None


def check(url, key, row):
    body = {"claim": row["claim"], "context": row.get("context", row["claim"])}
    for field in ("handle", "caption"):
        if row.get(field):
            body[field] = row[field]
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/check",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=45) as res:
            answer = json.load(res)
        return {"answer": answer, "wall_ms": round((time.time() - started) * 1000)}
    except urllib.error.HTTPError as err:
        return {"error": f"HTTP {err.code}: {err.read()[:300].decode(errors='replace')}", "wall_ms": round((time.time() - started) * 1000)}
    except Exception as err:
        return {"error": str(err), "wall_ms": round((time.time() - started) * 1000)}


def host_of(url):
    match = re.match(r"^https?://([^/:?#]+)", url or "")
    return match.group(1).lower().removeprefix("www.") if match else ""


def registrable(host):
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def real_site(source):
    site = (source.get("site") or "").lower()
    host = host_of(source.get("url", ""))
    return bool(re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", site)) and (site == host or host.endswith("." + site) or site.endswith("." + host))


def words(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 4 and w not in STOP}


def fetch_page(url):
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            raw = res.read(400_000).decode(errors="replace")
            text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            return {"status": res.status, "text": re.sub(r"\s+", " ", text)[:60_000]}
    except urllib.error.HTTPError as err:
        return {"status": err.code, "text": ""}
    except Exception as err:
        return {"status": 0, "text": "", "error": str(err)[:200]}


def about_claim(claim, source, page):
    keys = words(claim)
    if not keys:
        return False
    hay = words(page.get("text", "") + " " + source.get("title", "") + " " + source.get("url", "").replace("-", " ").replace("/", " "))
    hits = len(keys & hay)
    return hits >= min(3, len(keys)) or hits / len(keys) >= 0.4


def pct(n, d):
    return f"{100 * n / d:.0f}% ({n}/{d})" if d else "n/a"


def quantile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=LOCAL)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--tag")
    ap.add_argument("--set", default=str(HERE / "set.jsonl"))
    ap.add_argument("--only", help="comma separated ids")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--report", action="store_true", help="no calls, cache only")
    ap.add_argument("--no-fetch", action="store_true", help="skip fetching source pages")
    ap.add_argument("--json", help="write the summary to this file")
    ap.add_argument("--agent-log", help="log of a local agent: sum its exact per-call OpenRouter cost lines")
    args = ap.parse_args()
    url = LIVE if args.live else args.url
    tag = args.tag or ("live" if args.live else "local")
    rows = [json.loads(line) for line in Path(args.set).read_text().splitlines() if line.strip()]
    if args.only:
        wanted = set(args.only.split(","))
        rows = [r for r in rows if r["id"] in wanted]
    cache = HERE / "cache" / tag
    cache.mkdir(parents=True, exist_ok=True)

    todo = [r for r in rows if not (cache / f"{r['id']}.json").exists()]
    spent = None
    if todo and not args.report:
        key = agent_key()
        if not key:
            sys.exit("FOOTNOTE_AGENT_KEY is not set and footnote-agent/.env.hostinger has none")
        before = openrouter_usage()
        log_start = Path(args.agent_log).stat().st_size if args.agent_log else 0
        print(f"calling {url} for {len(todo)} claims ({len(rows) - len(todo)} cached)", file=sys.stderr)
        with cf.ThreadPoolExecutor(args.workers) as pool:
            for row, result in zip(todo, pool.map(lambda r: check(url, key, r), todo)):
                result["id"] = row["id"]
                result["url"] = url
                (cache / f"{row['id']}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
                stance = result.get("answer", {}).get("stance", "ERROR")
                print(f"  {row['id']} {row['label']:5} -> {stance:7} {result['wall_ms']} ms", file=sys.stderr)
        time.sleep(4)
        after = openrouter_usage()
        exact = None
        if args.agent_log:
            with open(args.agent_log, errors="replace") as log:
                log.seek(log_start)
                exact = sum(float(x) for x in re.findall(r"cost ([0-9.]+) USD", log.read()))
        if exact is not None or (before is not None and after is not None):
            spent = exact if exact is not None else after - before
            costs_path = cache / "cost.json"
            costs = json.loads(costs_path.read_text()) if costs_path.exists() else []
            costs.append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "calls": len(todo), "usd": round(spent, 5), "source": "agent log" if exact is not None else "key usage delta", "key_delta": None if before is None or after is None else round(after - before, 5)})
            costs_path.write_text(json.dumps(costs, indent=1))

    results = {}
    for row in rows:
        path = cache / f"{row['id']}.json"
        if path.exists():
            results[row["id"]] = json.loads(path.read_text())

    url_cache_path = HERE / "cache" / "urls.json"
    url_cache = json.loads(url_cache_path.read_text()) if url_cache_path.exists() else {}
    if not args.no_fetch:
        needed = {
            s["url"]
            for r in results.values()
            for s in r.get("answer", {}).get("sources", [])
            if s.get("url") and s["url"] not in url_cache
        }
        with cf.ThreadPoolExecutor(8) as pool:
            for u, page in zip(needed, pool.map(fetch_page, needed)):
                url_cache[u] = page
        url_cache_path.write_text(json.dumps(url_cache, ensure_ascii=False))

    by_label = {"true": [], "false": []}
    stances = {"true": {}, "false": {}}
    false_alerts, misses, errors = [], [], []
    latencies, n_results, two_domains = [], 0, 0
    n_sources = opened = blocked = about = real = 0
    bad_sites = []
    for row in rows:
        result = results.get(row["id"])
        if result is None:
            continue
        answer = result.get("answer")
        label = row["label"]
        if answer is None:
            errors.append((row["id"], result.get("error", "")[:120]))
            stance = "error"
        else:
            stance = answer["stance"]
            latencies.append(answer.get("ms", result["wall_ms"]))
            sources = answer.get("sources", [])
            n_results += 1
            if len({registrable(host_of(s.get("url", ""))) for s in sources}) >= 2:
                two_domains += 1
            for s in sources:
                n_sources += 1
                if real_site(s):
                    real += 1
                else:
                    bad_sites.append((row["id"], s.get("site"), s.get("url")))
                page = url_cache.get(s.get("url"))
                if page is None:
                    continue
                if page["status"] == 200:
                    opened += 1
                    if about_claim(row["claim"], s, page):
                        about += 1
                elif page["status"] in (401, 403, 429, 202):
                    blocked += 1
        by_label[label].append(stance)
        stances[label][stance] = stances[label].get(stance, 0) + 1
        if label == "true" and stance == "differ":
            false_alerts.append((row["id"], row["claim"], answer["line"], [s["url"] for s in answer["sources"]]))
        if label == "false" and stance != "differ":
            misses.append((row["id"], row["kind"], stance, row["claim"]))

    n_true, n_false = len(by_label["true"]), len(by_label["false"])
    fa = sum(s == "differ" for s in by_label["true"])
    hit = sum(s == "differ" for s in by_label["false"])
    costs_path = cache / "cost.json"
    costs = json.loads(costs_path.read_text()) if costs_path.exists() else []
    calls, usd = sum(c["calls"] for c in costs), sum(c["usd"] for c in costs)
    summary = {
        "tag": tag,
        "url": url,
        "n": len(results),
        "false_alert_rate": fa / n_true if n_true else None,
        "recall_false": hit / n_false if n_false else None,
        "two_domains": two_domains / n_results if n_results else None,
        "sources": n_sources,
        "opened_200": opened / n_sources if n_sources else None,
        "blocked": blocked / n_sources if n_sources else None,
        "about_claim": about / n_sources if n_sources else None,
        "real_site": real / n_sources if n_sources else None,
        "p50_ms": quantile(latencies, 0.5),
        "p95_ms": quantile(latencies, 0.95),
        "errors": len(errors),
        "usd_per_check": usd / calls if calls else None,
        "usd_total": usd,
        "stances": stances,
    }

    print(f"\n== footnote-agent eval: {tag} ({url}), {len(results)} of {len(rows)} claims ==")
    print(f"false alert rate (true claim -> differ): {pct(fa, n_true)}")
    print(f"recall on false claims (-> differ):      {pct(hit, n_false)}")
    print(f"stances, true claims:  {stances['true']}")
    print(f"stances, false claims: {stances['false']}")
    print(f"results with >= 2 independent domains:  {pct(two_domains, n_results)}")
    print(f"sources that open (HTTP 200):           {pct(opened, n_sources)}  (bot-blocked 401/403/429: {pct(blocked, n_sources)})")
    print(f"sources that open and are about it:     {pct(about, n_sources)}")
    print(f"sources whose site is the real domain:  {pct(real, n_sources)}")
    if latencies:
        print(f"latency p50 / p95:                      {summary['p50_ms']} / {summary['p95_ms']} ms (mean {statistics.mean(latencies):.0f})")
    print(f"errors:                                 {len(errors)}")
    if calls:
        print(f"cost:                                   {usd:.4f} USD for {calls} calls = {usd / calls:.4f} USD per check")
    if spent is not None:
        print(f"this run:                               {spent:.4f} USD")
    for i, claim, line, urls in false_alerts:
        print(f"\nFALSE ALERT {i}: {claim}\n  line: {line}\n  sources: {', '.join(urls)}")
    if misses:
        print("\nmissed false claims:")
        for m in misses:
            print(f"  {m[0]} [{m[1]}] -> {m[2]}: {m[3]}")
    for i, err in errors:
        print(f"ERROR {i}: {err}")
    for i, site, u in bad_sites:
        print(f"BAD SITE {i}: {site!r} for {u}")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()

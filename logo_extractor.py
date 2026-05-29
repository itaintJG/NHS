#!/usr/bin/env python3
"""Standalone runner for the Apify "Website Logo Extractor" actor.

Actor: https://apify.com/botflowtech/website-logo-extractor

Given one or more website URLs, this calls the Apify actor synchronously and
prints the extracted logo data (the actor's dataset items) as JSON or CSV.

No third-party dependencies — uses only the Python standard library.

Quick start:
    export APIFY_TOKEN="apify_api_..."
    python3 logo_extractor.py https://example.com https://github.com

Read URLs from a file (one per line):
    python3 logo_extractor.py --urls-file sites.txt --output logos.json

Full control over the actor input (bypasses URL auto-building):
    python3 logo_extractor.py --input-file input.json

NOTE ON THE INPUT SCHEMA
------------------------
Different Apify actors name their URL input field differently (e.g. "startUrls",
"websites", "domains"). This script defaults to the standard Apify "startUrls"
format: {"startUrls": [{"url": "https://example.com"}, ...]}.

If the run errors with something like "Field input.startUrls is required" or your
results come back empty, open the actor's *Input* tab on apify.com to see the real
field name, then either:
  * pass --plain-field NAME to send {"NAME": ["https://example.com", ...]}, or
  * use --input-file to send a hand-written input JSON exactly as the actor expects.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_ACTOR_ID = "botflowtech~website-logo-extractor"
APIFY_BASE = "https://api.apify.com/v2"


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def build_input(urls: list[str], plain_field: str | None) -> dict:
    """Construct the actor input from a list of URLs.

    By default uses Apify's standard startUrls format. If --plain-field is given,
    sends a flat list under that field name instead.
    """
    if plain_field:
        return {plain_field: urls}
    return {"startUrls": [{"url": u} for u in urls]}


def normalize_url(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw


def collect_urls(args: argparse.Namespace) -> list[str]:
    urls: list[str] = []
    urls.extend(args.urls)
    if args.urls_file:
        with open(args.urls_file, "r", encoding="utf-8") as fh:
            urls.extend(line for line in fh.read().splitlines())
    # Filter blanks/comments and normalize, preserving order, de-duplicating.
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        u = u.strip()
        if not u or u.startswith("#"):
            continue
        u = normalize_url(u)
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def run_actor_sync(actor_id: str, token: str, payload: dict, timeout_secs: int) -> list[dict]:
    """Run the actor synchronously and return its dataset items.

    Uses the run-sync-get-dataset-items endpoint, which starts the run, waits for
    it to finish, and returns the resulting dataset items in one HTTP call.
    """
    url = (
        f"{APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items"
        f"?token={urllib.parse.quote(token)}&timeout={timeout_secs}"
    )
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_secs + 30) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        eprint(f"Apify API error (HTTP {e.code}):")
        eprint(detail)
        raise SystemExit(1)
    except urllib.error.URLError as e:
        eprint(f"Network error reaching Apify: {e.reason}")
        raise SystemExit(1)

    try:
        items = json.loads(body)
    except json.JSONDecodeError:
        eprint("Unexpected (non-JSON) response from Apify:")
        eprint(body[:2000])
        raise SystemExit(1)
    if not isinstance(items, list):
        # Errors sometimes come back as an object with an "error" key.
        eprint("Unexpected response shape from Apify:")
        eprint(json.dumps(items, indent=2)[:2000])
        raise SystemExit(1)
    return items


def to_csv(items: list[dict]) -> str:
    import csv
    import io

    if not items:
        return ""
    # Union of all keys across items, stable order: first-seen.
    fieldnames: list[str] = []
    for item in items:
        for k in item.keys():
            if k not in fieldnames:
                fieldnames.append(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for item in items:
        # Flatten non-scalar values to JSON strings so the CSV stays valid.
        row = {
            k: (v if isinstance(v, (str, int, float, bool)) or v is None else json.dumps(v))
            for k, v in item.items()
        }
        writer.writerow(row)
    return buf.getvalue()


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run the Apify Website Logo Extractor actor on one or more websites.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("urls", nargs="*", help="Website URLs to extract logos from.")
    p.add_argument("--urls-file", help="Path to a file with one URL per line (# for comments).")
    p.add_argument(
        "--input-file",
        help="Path to a JSON file used as the actor input verbatim "
        "(overrides URL auto-building).",
    )
    p.add_argument(
        "--plain-field",
        metavar="NAME",
        help="Send URLs as a flat list under this field name "
        '(e.g. --plain-field websites -> {"websites": [...]}) '
        "instead of the default startUrls format.",
    )
    p.add_argument(
        "--actor-id",
        default=os.environ.get("APIFY_ACTOR_ID", DEFAULT_ACTOR_ID),
        help=f"Apify actor ID (default: {DEFAULT_ACTOR_ID}).",
    )
    p.add_argument(
        "--token",
        default=os.environ.get("APIFY_TOKEN"),
        help="Apify API token (default: APIFY_TOKEN env var).",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Actor run timeout in seconds (default: 300).",
    )
    p.add_argument("--output", help="Write results to this file instead of stdout.")
    p.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Output format (default: json).",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if not args.token:
        eprint("Error: no Apify token. Set APIFY_TOKEN or pass --token.")
        eprint("Get one at https://console.apify.com/account/integrations")
        return 2

    if args.input_file:
        with open(args.input_file, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        eprint(f"Using input from {args.input_file}")
    else:
        urls = collect_urls(args)
        if not urls:
            eprint("Error: no URLs provided. Pass URLs as arguments, use --urls-file,")
            eprint("or supply a full input with --input-file.")
            return 2
        payload = build_input(urls, args.plain_field)
        eprint(f"Extracting logos for {len(urls)} site(s) via actor '{args.actor_id}'...")

    items = run_actor_sync(args.actor_id, args.token, payload, args.timeout)
    eprint(f"Done. Actor returned {len(items)} result item(s).")

    if args.format == "csv":
        rendered = to_csv(items)
    else:
        rendered = json.dumps(items, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(rendered)
            if not rendered.endswith("\n"):
                fh.write("\n")
        eprint(f"Wrote results to {args.output}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

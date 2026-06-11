#!/usr/bin/env python3
"""Run the Apify "Website Logo Extractor" actor on a list of websites.

Dependency-free (Python 3.8+ stdlib only). Calls Apify's
run-sync-get-dataset-items endpoint, which starts the actor, waits for it to
finish, and returns the dataset items in one request.

Token is read from the APIFY_TOKEN environment variable, or from a .env file
in the current directory (KEY=VALUE lines).

Examples:
    python logo_extractor.py https://www.apple.com https://www.google.com
    python logo_extractor.py --csv websites.csv --output logos.json
    python logo_extractor.py --csv websites.csv --csv-column "Company Website"
    python logo_extractor.py --urls-file sites.txt --format csv --output logos.csv
"""
import argparse
import csv
import io
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_ACTOR = "botflowtech~website-logo-extractor"
API_BASE = "https://api.apify.com/v2"

# Column names we'll treat as the URL/website column when auto-detecting.
URL_COLUMN_CANDIDATES = ("url", "urls", "website", "websites", "web", "domain",
                         "domains", "site", "link", "homepage")


def load_dotenv(path=".env"):
    """Populate os.environ from a simple KEY=VALUE .env file (no overwrite)."""
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def get_token(cli_token):
    token = cli_token or os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit(
            "Error: no Apify token. Set APIFY_TOKEN (env or .env file) "
            "or pass --token."
        )
    return token


def normalize_url(value):
    value = value.strip()
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value


def read_urls_file(path):
    urls = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            u = normalize_url(line)
            if u:
                urls.append(u)
    return urls


def _detect_column_index(header, requested):
    """Return the index of the URL column in a header row.

    If `requested` is given it must match (case-insensitive) or we error.
    Otherwise auto-detect against URL_COLUMN_CANDIDATES.
    """
    lowered = [h.strip().lower() for h in header]
    if requested:
        req = requested.strip().lower()
        if req in lowered:
            return lowered.index(req)
        sys.exit(
            f"Error: column '{requested}' not found. "
            f"Available columns: {', '.join(header)}"
        )
    for cand in URL_COLUMN_CANDIDATES:
        if cand in lowered:
            return lowered.index(cand)
    return None


def read_csv_records(path, requested_column):
    """Read a CSV, preserving every column so results can be merged back.

    Returns (columns, records, url_field):
      columns   - original header (or ["URL"] for a bare one-column list)
      records   - list of dicts keyed by column name, with a normalized URL
                  stored under "_url"
      url_field - the column name that holds the website URL
    Handles a header row or a bare one-column list, BOM, explicit column by
    name, and auto-detection of the URL column.
    """
    if not os.path.isfile(path):
        sys.exit(f"Error: CSV file not found: {path}")
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rows = [row for row in csv.reader(f) if row and any(c.strip() for c in row)]

    if not rows:
        sys.exit(f"Error: CSV '{path}' is empty.")

    header = rows[0]
    col_index = _detect_column_index(header, requested_column)

    records = []
    if col_index is not None:
        columns = header
        url_field = header[col_index]
        for row in rows[1:]:
            rec = {col: (row[i] if i < len(row) else "") for i, col in enumerate(header)}
            rec["_url"] = normalize_url(rec.get(url_field, ""))
            records.append(rec)
    elif requested_column is None and len(header) == 1:
        columns = ["URL"]
        url_field = "URL"
        first_cell = header[0].strip()
        start = 0 if _looks_like_url_value(first_cell) else 1
        for row in rows[start:]:
            rec = {"URL": row[0]}
            rec["_url"] = normalize_url(row[0])
            records.append(rec)
    else:
        sys.exit(
            "Error: could not find a URL column. Use --csv-column to name it. "
            f"Available columns: {', '.join(header)}"
        )

    records = [r for r in records if r.get("_url")]
    if not records:
        sys.exit(f"Error: no URLs found in CSV '{path}'.")
    return columns, records, url_field


def read_csv_urls(path, requested_column):
    """Return just the list of normalized URLs from a CSV."""
    _, records, _ = read_csv_records(path, requested_column)
    return [r["_url"] for r in records]


def _looks_like_url_value(value):
    v = value.strip().lower()
    if not v:
        return False
    if v.startswith(("http://", "https://")):
        return True
    # bare domain like example.com (has a dot, no spaces)
    return "." in v and " " not in v


def pick_best_logo(logos):
    """Choose the single best brand logo from the actor's candidate list.

    Preference: header img-logo > any img-logo > og-image > favicon >
    favicon-default > any candidate with a URL. Candidates with no URL
    (e.g. svg-inline) are ignored.
    Returns (url, type) or (None, None).
    """
    usable = [l for l in logos if l.get("url")]
    if not usable:
        return None, None

    def first(predicate):
        for l in usable:
            if predicate(l):
                return l
        return None

    candidate = (
        first(lambda l: l.get("type") == "img-logo" and "header" in l["url"].lower())
        or first(lambda l: l.get("type") == "img-logo")
        or first(lambda l: l.get("type") == "og-image")
        or first(lambda l: l.get("type") == "favicon")
        or first(lambda l: l.get("type") == "favicon-default")
        or usable[0]
    )
    return candidate["url"], candidate.get("type")


def logo_status(logo_url, logo_type):
    """'ready' if we got a real hosted brand logo, else 'review'."""
    if logo_type == "img-logo" and logo_url and not logo_url.startswith("data:"):
        return "ready"
    return "review"


def flatten_best(items):
    """Reduce each site result to one row: url, logo_url, logo_type, logo_status."""
    rows = []
    for item in items:
        logo_url, logo_type = pick_best_logo(item.get("logos", []))
        rows.append({
            "url": item.get("url"),
            "logo_url": logo_url,
            "logo_type": logo_type,
            "logo_status": logo_status(logo_url, logo_type),
        })
    return rows


def merge_records(columns, records, items):
    """Attach logo data to the original CSV rows, matched by normalized URL."""
    by_url = {}
    for item in items:
        logo_url, logo_type = pick_best_logo(item.get("logos", []))
        by_url[item.get("url")] = (logo_url, logo_type)

    out_columns = list(columns) + ["logo_url", "logo_type", "logo_status"]
    rows = []
    for rec in records:
        logo_url, logo_type = by_url.get(rec["_url"], (None, None))
        row = {col: rec.get(col, "") for col in columns}
        row["logo_url"] = logo_url
        row["logo_type"] = logo_type
        row["logo_status"] = logo_status(logo_url, logo_type)
        rows.append(row)
    return out_columns, rows


def build_input(urls, max_concurrency, field, input_file):
    if input_file:
        with open(input_file, "r", encoding="utf-8") as f:
            return json.load(f)
    payload = {field: urls}
    if max_concurrency:
        payload["maxConcurrency"] = max_concurrency
    return payload


def run_actor(actor, token, payload, memory=None):
    url = f"{API_BASE}/acts/{actor}/run-sync-get-dataset-items?token={token}"
    if memory:
        url += f"&memory={memory}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        sys.exit(f"Apify API error {e.code}: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"Network error calling Apify: {e.reason}")
    return json.loads(body)


def write_output(items, fmt, output_path, columns=None):
    if fmt == "csv":
        if columns:
            keys = list(columns)
        else:
            keys = []
            for item in items:
                for k in item.keys():
                    if k not in keys:
                        keys.append(k)
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            writer.writerow({k: _flatten(item.get(k, "")) for k in keys})
        text = buf.getvalue()
    else:
        text = json.dumps(items, indent=2, ensure_ascii=False)

    if output_path:
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        print(f"Wrote {len(items)} item(s) to {output_path}")
    else:
        print(text)


def _flatten(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def parse_args(argv):
    p = argparse.ArgumentParser(description="Apify Website Logo Extractor runner.")
    p.add_argument("urls", nargs="*", help="Website URLs to extract logos from.")
    p.add_argument("--csv", help="Path to a CSV file of websites.")
    p.add_argument("--csv-column", help="Name of the URL column in the CSV.")
    p.add_argument("--urls-file", help="Path to a text file, one URL per line.")
    p.add_argument("--input-file", help="Raw JSON input to send to the actor (overrides URL options).")
    p.add_argument("--actor", default=DEFAULT_ACTOR, help=f"Actor ID (default: {DEFAULT_ACTOR}).")
    p.add_argument("--field", default="urls", help="Input field name for the URL list (default: urls).")
    p.add_argument("--max-concurrency", type=int, help="maxConcurrency setting for the actor.")
    p.add_argument("--batch-size", type=int, default=10,
                   help="Sites per actor run (default 10). Larger batches can "
                        "exhaust the actor's memory and fail. 0 = no batching.")
    p.add_argument("--memory", type=int, default=4096,
                   help="Actor run memory in MB (default 4096). Higher avoids "
                        "out-of-memory failures on sites with many logos.")
    p.add_argument("--format", choices=("json", "csv"), default=None,
                   help="Output format. Defaults to csv when merging a CSV, else json.")
    p.add_argument("--output", help="Write output to this file instead of stdout.")
    p.add_argument("--token", help="Apify API token (else APIFY_TOKEN env / .env).")
    p.add_argument("--raw", action="store_true",
                   help="Return the actor's full output (all logo candidates) "
                        "instead of one best-logo row per site.")
    return p.parse_args(argv)


def _default_output(csv_path):
    base, _ = os.path.splitext(csv_path)
    return base + "-with-logos.csv"


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    load_dotenv()
    token = get_token(args.token)

    csv_columns = csv_records = None

    if args.input_file:
        urls = []
    else:
        urls = list(args.urls)
        if args.urls_file:
            urls += read_urls_file(args.urls_file)
        if args.csv:
            csv_columns, csv_records, _ = read_csv_records(args.csv, args.csv_column)
            urls += [r["_url"] for r in csv_records]
        urls = [normalize_url(u) for u in urls]
        urls = [u for u in urls if u]
        if not urls:
            sys.exit("Error: no URLs provided. Pass URLs, --csv, --urls-file, or --input-file.")
        # de-dupe, preserve order
        seen = set()
        deduped = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        urls = deduped
        print(f"Submitting {len(urls)} URL(s) to actor {args.actor} ...", file=sys.stderr)

    if args.input_file:
        payload = build_input(urls, args.max_concurrency, args.field, args.input_file)
        items = run_actor(args.actor, token, payload, args.memory)
    else:
        size = args.batch_size if args.batch_size and args.batch_size > 0 else len(urls)
        batches = [urls[i:i + size] for i in range(0, len(urls), size)]
        items = []
        for n, batch in enumerate(batches, 1):
            if len(batches) > 1:
                print(f"  batch {n}/{len(batches)} ({len(batch)} sites) ...", file=sys.stderr)
            payload = build_input(batch, args.max_concurrency, args.field, None)
            items.extend(run_actor(args.actor, token, payload, args.memory))

    merging = bool(args.csv) and not args.raw
    fmt = args.format or ("csv" if merging else "json")

    if args.raw:
        write_output(items, fmt, args.output)
    elif merging:
        out_columns, rows = merge_records(csv_columns, csv_records, items)
        output = args.output or _default_output(args.csv)
        write_output(rows, fmt, output, columns=out_columns)
        ready = sum(1 for r in rows if r["logo_status"] == "ready")
        print(f"  {ready} ready, {len(rows) - ready} need review", file=sys.stderr)
    else:
        write_output(flatten_best(items), fmt, args.output)


if __name__ == "__main__":
    main()

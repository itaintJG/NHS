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
import base64
import csv
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
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


# --- Downloading the actual logo image files -------------------------------

# Map common image content-types to file extensions, for when the URL has none.
_CONTENT_TYPE_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/bmp": ".bmp",
    "image/avif": ".avif",
}

_VALID_IMAGE_EXTS = set(_CONTENT_TYPE_EXT.values()) | {".jpeg", ".ico"}


def safe_filename(name):
    """Turn a company name / URL into a safe base filename (no extension)."""
    name = (name or "").strip()
    if not name:
        name = "logo"
    # Drop scheme and www. if a URL slipped in.
    name = re.sub(r"^https?://", "", name)
    name = re.sub(r"^www\.", "", name)
    name = name.strip("/")
    # Replace anything not alphanumeric / dash / underscore with a dash.
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-_.")
    return name or "logo"


def _ext_from_url(logo_url):
    path = urllib.parse.urlparse(logo_url).path
    ext = os.path.splitext(path)[1].lower()
    return ext if ext in _VALID_IMAGE_EXTS else ""


def _decode_data_uri(logo_url):
    """Return (bytes, ext) for a data: URI, or (None, None) if not decodable."""
    match = re.match(r"data:([^;,]*)(;base64)?,(.*)$", logo_url, re.DOTALL)
    if not match:
        return None, None
    mime, is_b64, payload = match.group(1), match.group(2), match.group(3)
    try:
        if is_b64:
            raw = base64.b64decode(payload)
        else:
            raw = urllib.parse.unquote_to_bytes(payload)
    except Exception:
        return None, None
    ext = _CONTENT_TYPE_EXT.get(mime.lower().strip(), ".img")
    return raw, ext


def download_logo(logo_url, dest_dir, base_name, token=None):
    """Download one logo to dest_dir/base_name.<ext>.

    Returns the saved file path, or None if nothing could be downloaded.
    """
    if not logo_url:
        return None

    # Inline data: URI — decode directly, no network call.
    if logo_url.startswith("data:"):
        raw, ext = _decode_data_uri(logo_url)
        if not raw:
            return None
        path = os.path.join(dest_dir, base_name + ext)
        with open(path, "wb") as f:
            f.write(raw)
        return path

    req = urllib.request.Request(
        logo_url,
        headers={"User-Agent": "Mozilla/5.0 (logo-extractor)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        print(f"    ! could not download {logo_url}: {e}", file=sys.stderr)
        return None

    if not raw:
        return None
    ext = _ext_from_url(logo_url) or _CONTENT_TYPE_EXT.get(content_type, ".img")
    path = os.path.join(dest_dir, base_name + ext)
    with open(path, "wb") as f:
        f.write(raw)
    return path


def download_logos(rows, dest_dir, name_field, url_field="logo_url", token=None):
    """Download every row's logo into dest_dir, named after name_field.

    Mutates each row, adding a 'logo_file' column with the saved path (or "").
    Returns the number of files successfully downloaded.
    """
    os.makedirs(dest_dir, exist_ok=True)
    used = {}
    saved = 0
    for row in rows:
        logo_url = row.get(url_field)
        if not logo_url:
            row["logo_file"] = ""
            continue
        base = safe_filename(row.get(name_field) or row.get("url"))
        # Avoid collisions when two companies share a name.
        used[base] = used.get(base, 0) + 1
        if used[base] > 1:
            base = f"{base}-{used[base]}"
        path = download_logo(logo_url, dest_dir, base, token=token)
        if path:
            row["logo_file"] = os.path.basename(path)
            saved += 1
            print(f"    saved {os.path.basename(path)}", file=sys.stderr)
        else:
            row["logo_file"] = ""
    return saved


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
    p.add_argument("--download", action="store_true",
                   help="Download the actual logo image files (not just URLs).")
    p.add_argument("--logo-dir", default="logos",
                   help="Folder to save downloaded logos into (default: logos).")
    p.add_argument("--name-column",
                   help="CSV column to name downloaded files after "
                        "(default: NAME if present, else the website URL).")
    return p.parse_args(argv)


def _default_output(csv_path):
    base, _ = os.path.splitext(csv_path)
    return base + "-with-logos.csv"


def _pick_name_field(requested, columns):
    """Choose which column to name downloaded logo files after."""
    if requested:
        # match case-insensitively against the real column names
        lowered = {c.lower(): c for c in columns}
        return lowered.get(requested.lower(), requested)
    for cand in ("name", "company", "company name", "builder", "business"):
        for c in columns:
            if c.lower() == cand:
                return c
    return "url"


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
        if args.download:
            print("Note: --download is ignored with --raw (no single best logo per site).",
                  file=sys.stderr)
    elif merging:
        out_columns, rows = merge_records(csv_columns, csv_records, items)
        if args.download:
            name_field = _pick_name_field(args.name_column, out_columns)
            print(f"Downloading logos into {args.logo_dir}/ ...", file=sys.stderr)
            saved = download_logos(rows, args.logo_dir, name_field)
            if "logo_file" not in out_columns:
                out_columns = list(out_columns) + ["logo_file"]
            print(f"  downloaded {saved} of {len(rows)} logo(s)", file=sys.stderr)
        output = args.output or _default_output(args.csv)
        write_output(rows, fmt, output, columns=out_columns)
        ready = sum(1 for r in rows if r["logo_status"] == "ready")
        print(f"  {ready} ready, {len(rows) - ready} need review", file=sys.stderr)
    else:
        rows = flatten_best(items)
        if args.download:
            print(f"Downloading logos into {args.logo_dir}/ ...", file=sys.stderr)
            saved = download_logos(rows, args.logo_dir, "url")
            print(f"  downloaded {saved} of {len(rows)} logo(s)", file=sys.stderr)
        write_output(rows, fmt, args.output)


if __name__ == "__main__":
    main()

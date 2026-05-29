# Website Logo Extractor

A small standalone Python script that runs the Apify
[**Website Logo Extractor**](https://apify.com/botflowtech/website-logo-extractor)
actor on one or more websites and returns the extracted logo data.

No third-party dependencies — just Python 3.8+ and an Apify API token.

## 1. Get an Apify API token

1. Create / sign in to an account at [apify.com](https://apify.com).
2. Go to **Settings → Integrations** (or
   <https://console.apify.com/account/integrations>) and copy your **Personal API
   token** (looks like `apify_api_...`).

> Running this actor consumes Apify platform credits / compute units on your
> account, per Apify's pricing.

## 2. Provide the token

```bash
export APIFY_TOKEN="apify_api_xxxxxxxxxxxxxxxxxxxx"
```

Or copy `.env.example` to `.env` and fill it in, then `source .env`. The token is
read from the environment and is never hardcoded.

## 3. Run it

```bash
# One or more URLs as arguments
python3 logo_extractor.py https://example.com github.com

# URLs from a file (one per line; blank lines and # comments ignored)
python3 logo_extractor.py --urls-file sites.txt

# Save results to a file
python3 logo_extractor.py --urls-file sites.txt --output logos.json

# CSV output
python3 logo_extractor.py example.com --format csv --output logos.csv
```

Results (the actor's dataset items) are printed to stdout as JSON by default.

## Input schema caveat

Apify actors name their URL input field differently. This script defaults to the
standard Apify `startUrls` format:

```json
{ "startUrls": [{ "url": "https://example.com" }] }
```

If a run errors (e.g. *"Field input.startUrls is required"*) or returns nothing,
check the actor's **Input** tab on apify.com for the real field name, then either:

- send a flat list under a different field name:

  ```bash
  python3 logo_extractor.py example.com --plain-field websites
  # -> {"websites": ["https://example.com"]}
  ```

- or write the exact input JSON yourself and pass it verbatim:

  ```bash
  python3 logo_extractor.py --input-file input.example.json
  ```

## Options

| Flag | Description |
| --- | --- |
| `urls...` | One or more website URLs (positional). |
| `--urls-file PATH` | Read URLs from a file, one per line. |
| `--input-file PATH` | Use this JSON file as the actor input verbatim. |
| `--plain-field NAME` | Send URLs as `{"NAME": [...]}` instead of `startUrls`. |
| `--actor-id ID` | Override the actor ID (default `botflowtech~website-logo-extractor`). |
| `--token TOKEN` | Apify token (default: `APIFY_TOKEN` env var). |
| `--timeout SECS` | Actor run timeout in seconds (default `300`). |
| `--output PATH` | Write results to a file instead of stdout. |
| `--format {json,csv}` | Output format (default `json`). |

## How it works

The script calls Apify's
[`run-sync-get-dataset-items`](https://docs.apify.com/api/v2/act-run-sync-get-dataset-items-post)
endpoint, which starts the actor, waits for it to finish, and returns the dataset
items in a single request.

> Note: this script must run in an environment with outbound internet access to
> `api.apify.com`.

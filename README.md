# Website Logo Extractor

A small standalone Python script that runs the Apify
[**Website Logo Extractor**](https://apify.com/botflowtech/website-logo-extractor)
actor on a list of websites, picks the best logo for each, and can merge the
results straight back onto your original CSV.

No third-party dependencies — just Python 3.8+ and an Apify API token.

## 1. Get an Apify API token

1. Create / sign in to an account at [apify.com](https://apify.com).
2. Go to **Settings → Integrations** (or
   <https://console.apify.com/account/integrations>) and copy your **Personal API
   token** (looks like `apify_api_...`).

> Running this actor consumes Apify platform credits on your account, per Apify's
> pricing (about $1 per 1,000 results).

## 2. Provide the token

Easiest: create a file named `.env` next to the script with one line:

```
APIFY_TOKEN=apify_api_xxxxxxxxxxxxxxxxxxxx
```

The script reads `.env` automatically, so you don't have to set anything each
time. (You can also use an `APIFY_TOKEN` environment variable or `--token`.)

## 3. Run it

```bash
# A CSV of websites -> writes <name>-with-logos.csv next to it
python logo_extractor.py --csv websites.csv

# Name the URL column explicitly if auto-detect can't find it
python logo_extractor.py --csv websites.csv --csv-column "Company Website"

# One or more URLs on the command line
python logo_extractor.py https://www.apple.com https://www.google.com

# URLs from a text file, one per line
python logo_extractor.py --urls-file sites.txt --output logos.json

# Also DOWNLOAD the actual logo image files (not just the URLs)
python logo_extractor.py --csv websites.csv --download
```

### Downloading the logo images

By default you get logo *URLs* (links). Add `--download` to also save the actual
image files to your computer:

```bash
python logo_extractor.py --csv utah-websites.csv --download
```

This saves each logo into a `logos/` folder, named after the company (e.g.
`Visionary-Homes.png`, `Adair-Homes.svg`), and adds a `logo_file` column to the
output CSV so you can see which file belongs to which row. Options:

- `--logo-dir FOLDER` — save into a different folder (default `logos`).
- `--name-column NAME` — name files after a specific CSV column (default: the
  `NAME` column if present, else the website URL).

Sites with no logo found are simply skipped (blank `logo_file`).

### Windows note

Run the command **from the folder the script and CSV are in**:

```cmd
cd C:\Users\you\OneDrive\NewHomesSection\logo-extractor
python logo_extractor.py --csv utah-websites.csv --csv-column "Company Website"
```

If `python` isn't found, try `py` or `python3`.

## What you get

By default the script reduces the actor's many logo candidates to **one best logo
per site** and, when you pass `--csv`, writes a new CSV with your original columns
plus three new ones:

| Column | Meaning |
| --- | --- |
| `logo_url` | The chosen logo's URL. |
| `logo_type` | How it was found: `img-logo`, `og-image`, `favicon`, etc. |
| `logo_status` | `ready` (a real hosted brand logo) or `review` (fell back to a favicon/og-image — eyeball it). |

The output file defaults to `<your-csv>-with-logos.csv`. Use `--output` to choose
a path, `--format json` for JSON, or `--raw` to get the actor's full candidate
list for every site instead of the one-row-per-site summary.

## Options

| Flag | Description |
| --- | --- |
| `urls...` | One or more website URLs (positional). |
| `--csv PATH` | Read websites from a CSV; results merge back onto its rows. |
| `--csv-column NAME` | URL column name in the CSV (default: auto-detect). |
| `--urls-file PATH` | Read URLs from a text file, one per line. |
| `--input-file PATH` | Send this raw JSON to the actor (overrides URL options). |
| `--output PATH` | Write output here (default: `<csv>-with-logos.csv` or stdout). |
| `--format {json,csv}` | Output format (default: csv when merging a CSV, else json). |
| `--raw` | Return all logo candidates per site, not just the best one. |
| `--download` | Download the actual logo image files, not just URLs. |
| `--logo-dir FOLDER` | Folder to save downloaded logos into (default `logos`). |
| `--name-column NAME` | CSV column to name downloaded files after. |
| `--batch-size N` | Sites per actor run (default 10; `0` = no batching). |
| `--memory N` | Actor run memory in MB (default 4096). |
| `--max-concurrency N` | Actor `maxConcurrency` setting. |
| `--field NAME` | Input field holding the URL list (default `urls`). |
| `--actor ID` | Override the actor ID. |
| `--token TOKEN` | Apify token (default: `.env` / `APIFY_TOKEN` env). |

## Putting logos on a uniform square canvas (`logo_box.py`)

After downloading logos, `logo_box.py` centers each one on a fixed-size square
(default **350×350**) so they're all uniform. The background is chosen per logo:

- **white** normally;
- **black** when the logo artwork is light/white (so it doesn't disappear).

Force one with `--background white|black` if you ever want to override.

```bash
# Needs Pillow once:
pip install Pillow
# (optional, only if you have .svg logos)
pip install cairosvg

# Process every image in ./logos -> ./logos-boxed
python logo_box.py

# Custom folders / size
python logo_box.py --in logos --out boxed --size 350

# One file, forced white background
python logo_box.py mylogo.png --background white
```

Each output is a 350×350 PNG named the same as the input logo. It auto-detects
whether a logo is light or dark and prints which background it used for each, so
you can spot-check. `--margin` controls the breathing room around the logo
(default 12%).

## How it works

The script calls Apify's
[`run-sync-get-dataset-items`](https://docs.apify.com/api/v2/act-run-sync-get-dataset-items-post)
endpoint, which starts the actor, waits for it to finish, and returns the dataset
items in a single request. Large lists are processed in batches (`--batch-size`)
to stay within the actor's memory.

> Note: this script must run on a machine with outbound internet access to
> `api.apify.com`.

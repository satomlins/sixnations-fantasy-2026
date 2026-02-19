Six Nations Fantasy – scraping + scoring breakdown
=================================================

This repo fetches match data from fantasy.sixnationsrugby.com and reproduces the fantasy scoring, broken down player-by-player and stat-by-stat.

Quick start
-----------

1) Set up the environment (Python >=3.12) and install deps

- Using uv (recommended):

    uv sync

- Or using pip:

    pip install -e .

2) Get your Authorization token

- Log in to https://fantasy.sixnationsrugby.com/
- Open DevTools → Network → pick any API call → Copy the `Authorization` header value

3) Export the token before fetching data

- macOS/Linux (zsh/bash):

    export SIXNATIONS_TOKEN="Token <paste-your-token>"

- Windows (PowerShell):

    $env:SIXNATIONS_TOKEN = "Token <paste-your-token>"

Notes:

- You can also provide the token without the leading "Token "; the code will normalise it.
- Optionally set a custom x-access-key if the default stops working:

    export SIXNATIONS_X_ACCESS_KEY="<current-access-key>"

4) Pull latest data (no notebook required)

- Using uv:

    uv run python fantasy_ingest.py

- Or with your active venv:

    python fantasy_ingest.py

Persist and explore with a dashboard
------------------------------------

After running the ingest script, a `data/` folder will contain a combined export of all available matches (1–15):

- `data/all_matches.duckdb` (table: `all_matches`)

Run the dashboard:

– Using uv:

    uv run python app.py

- Or with your active venv:

    python app.py

Then open http://127.0.0.1:8050/ and use the filters to explore:

- Breakdown by player with stacked bars (either % contribution or raw points)
- Toggle `Points Category` between `Total Points` and `Consistent Points`
- Minutes overlay on the same chart for quick workload context


What you get
------------

The ingest module (`fantasy_ingest.py`):

- Fetches the match JSON via the private API with a retrying session and the required `x-access-key` header
- Iterates over match IDs 1–15, fetching and appending only those that have player data available
- Flattens players from both teams into a DataFrame
- Sets each player's team from the match-level `clubdom`/`clubext` fields
- Computes per-stat fantasy points, including forward vs back multipliers for tries and 0.1 points per metre carried
- Displays:
  - raw stats per player
  - a `{stat}_points` column for each scored stat
  - `points_total` from the site
  - `total_points` (same as `points_total`)
  - `consistent_points` (all points except tries, yellow/red cards, player of the match, and lineout steals)
  - `computed_points_total` and `points_delta` for verification

Notes
-----

- If you see Unauthorized (401/403), ensure `SIXNATIONS_TOKEN` is set correctly. You can provide it with or without the leading `Token ` prefix; the code normalises it.
- The default `x-access-key` is pre-filled from live traffic (can change any time). If it ceases to work, set `SIXNATIONS_X_ACCESS_KEY` to the current value you observe in the browser.
- Team names are inferred from the payload where available and fall back to generic labels.

Development notes
-----------------

- Data files are written to a single combined DuckDB database: `data/all_matches.duckdb` (updated in place each run).
- The dashboard reads `data/all_matches.duckdb` if present, otherwise the latest DuckDB file from `data/`.
- The ingest step upserts rows by `(match_id, id)` so older matches remain unless refreshed.
- API pulls are throttled to at most once per 60 seconds by default (`SIXNATIONS_MIN_REFRESH_SECONDS`).
- The dashboard can auto-refresh from the API on startup:
  - `SIXNATIONS_REFRESH_ON_START=auto` (default): refresh only when `SIXNATIONS_TOKEN` is set
  - `SIXNATIONS_REFRESH_ON_START=true`: always try
  - `SIXNATIONS_REFRESH_ON_START=false`: never refresh
- Bars in the dashboard are sorted from largest to smallest by total, not alphabetically. Use the filters to group by player, position, team, or opponent.

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

3) Export the token before running the notebook

- macOS/Linux (zsh/bash):

    export SIXNATIONS_TOKEN="Token <paste-your-token>"

- Windows (PowerShell):

    $env:SIXNATIONS_TOKEN = "Token <paste-your-token>"

Notes:

- You can also provide the token without the leading "Token "; the code will normalise it.
- Optionally set a custom x-access-key if the default stops working:

    export SIXNATIONS_X_ACCESS_KEY="<current-access-key>"

4) Open and run the notebook

- In VS Code: open `ingest.ipynb`, select the `fantasy-2026` kernel, and Run All.
Persist and explore with a dashboard
------------------------------------

After running the notebook, a `data/` folder will contain a timestamped combined export of all available matches (1–15):

- `data/all_matches_<UTC timestamp>.parquet`
- `data/all_matches_<UTC timestamp>.csv`

Run the dashboard:

– Using uv:

    uv run python app.py

- Or with your active venv:

    python app.py

Then open http://127.0.0.1:8050/ and use the filters to explore:

- Breakdown by player with stacked bars (either % contribution or raw points)
- Detailed table with per-stat points columns


What you get
------------

The notebook:

- Fetches the match JSON via the private API with a retrying session and the required `x-access-key` header
- Iterates over match IDs 1–15, fetching and appending only those that have player data available
- Flattens players from both teams into a DataFrame
- Sets each player's team from the match-level `clubdom`/`clubext` fields
- Computes per-stat fantasy points, including forward vs back multipliers for tries and 0.1 points per metre carried
- Displays:
  - raw stats per player
  - a `{stat}_points` column for each scored stat
  - `points_total` from the site
  - `computed_points_total` and `points_delta` for verification

Notes
-----

- If you see Unauthorized (401/403), ensure `SIXNATIONS_TOKEN` is set correctly. You can provide it with or without the leading `Token ` prefix; the notebook normalises it.
- The default `x-access-key` is pre-filled from live traffic (can change any time). If it ceases to work, set `SIXNATIONS_X_ACCESS_KEY` to the current value you observe in the browser.
- Team names are inferred from the payload where available and fall back to generic labels.

Development notes
-----------------

- Data files are written to a single combined file: `data/all_matches_<UTC timestamp>.parquet/csv`.
- The dashboard reads the latest file from `data/` (prefers Parquet if present).
- Bars in the dashboard are sorted from largest to smallest by total, not alphabetically. Use the filters to group by player, position, team, or opponent.

Six Nations Fantasy – scraping + scoring breakdown
=================================================

This repo fetches match data from fantasy.sixnationsrugby.com and reproduces the fantasy scoring, broken down player-by-player and stat-by-stat.

Quick start
-----------

1) Create and/or activate a Python 3.12 env and install deps

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

Optionally set a specific match id:

    export SIXNATIONS_MATCH_ID=1

4) Open and run the notebook

- In VS Code: open `main.ipynb`, select the `fantasy-2026` kernel, and Run All.
Persist and explore with a dashboard
------------------------------------

After running the notebook, a `data/` folder will contain timestamped Parquet and CSV exports.

Run the dashboard:

- Using uv:

    uv run python app.py

- Or with your active venv:

    python app.py

Then open http://127.0.0.1:8050/ and use the filters to explore:

- Breakdown by player with stacked bars (either % contribution or raw points)
- Pie for a selected (or top) player
- Detailed table with per-stat points columns


What you get
------------

The notebook:

- Fetches the match JSON via the private API with a retrying session and the required `x-access-key` header
- Flattens players from both teams into a DataFrame
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
- Team names for home/away are currently hard-coded in the transform cell; swap them or derive them from the payload if needed.

Development notes
-----------------

- Data files are written to `data/match_<id>_<UTC timestamp>.parquet/csv`.
- The dashboard reads the latest file from `data/` (prefers Parquet if present).
- To compare multiple matches in one view, you can concatenate files in a small ETL step, or extend `app.py` to allow choosing a specific file.

import glob
import os
from datetime import datetime, timezone
from typing import List, Optional

import dash_bootstrap_components as dbc
import duckdb
import pandas as pd
import plotly.express as px
from dash import Dash, Input, Output, dcc, html
from dotenv import load_dotenv
from flask import request, send_from_directory
from fantasy_ingest import (
    CONSISTENT_POINTS_EXCLUDED_STATS,
    get_default_db_path,
    get_default_refresh_state_path,
    refresh_all_matches,
)

load_dotenv()

CONSISTENT_POINTS_EXCLUDED_POINT_COLS = {
    f"{stat}_points" for stat in CONSISTENT_POINTS_EXCLUDED_STATS
}
DERIVED_POINT_COLUMNS = {"total_points", "consistent_points", "good_points"}
STAT_LABEL_MAP = {
    "Try": "Tries",
    "Assists": "Assists",
    "Conversion": "Conversions",
    "Penalty": "Penalties",
    "DropGoal": "Drop goals",
    "DefendersBeaten": "Defenders beaten",
    "MetresCarried": "Metres carried",
    "FiftyTwentyTwo": "50-22s",
    "KicksRecovered": "Kicks recovered",
    "Offloads": "Offloads",
    "AttackingScrumWin": "Attacking scrum wins",
    "Tackles": "Tackles",
    "BreakdownSteal": "Breakdown steals",
    "LineoutSteal": "Lineout steals",
    "PenaltyConceded": "Penalties conceded",
    "PlayerOfTheMatch": "Player of the Match",
    "YellowCard": "Yellow cards",
    "RedCard": "Red cards",
}
STAT_LABEL_ORDER = [
    STAT_LABEL_MAP["Try"],
    STAT_LABEL_MAP["Assists"],
    STAT_LABEL_MAP["Conversion"],
    STAT_LABEL_MAP["Penalty"],
    STAT_LABEL_MAP["DropGoal"],
    STAT_LABEL_MAP["DefendersBeaten"],
    STAT_LABEL_MAP["MetresCarried"],
    STAT_LABEL_MAP["FiftyTwentyTwo"],
    STAT_LABEL_MAP["KicksRecovered"],
    STAT_LABEL_MAP["Offloads"],
    STAT_LABEL_MAP["AttackingScrumWin"],
    STAT_LABEL_MAP["Tackles"],
    STAT_LABEL_MAP["BreakdownSteal"],
    STAT_LABEL_MAP["LineoutSteal"],
    STAT_LABEL_MAP["PenaltyConceded"],
    STAT_LABEL_MAP["PlayerOfTheMatch"],
    STAT_LABEL_MAP["YellowCard"],
    STAT_LABEL_MAP["RedCard"],
]
STAT_COLOR_MAP = {
    STAT_LABEL_MAP["Try"]: "#00A6FB",
    STAT_LABEL_MAP["Assists"]: "#F94144",
    STAT_LABEL_MAP["Conversion"]: "#F3722C",
    STAT_LABEL_MAP["Penalty"]: "#F8961E",
    STAT_LABEL_MAP["DropGoal"]: "#F9C74F",
    STAT_LABEL_MAP["DefendersBeaten"]: "#90BE6D",
    STAT_LABEL_MAP["MetresCarried"]: "#43AA8B",
    STAT_LABEL_MAP["FiftyTwentyTwo"]: "#577590",
    STAT_LABEL_MAP["KicksRecovered"]: "#277DA1",
    STAT_LABEL_MAP["Offloads"]: "#B5179E",
    STAT_LABEL_MAP["AttackingScrumWin"]: "#7209B7",
    STAT_LABEL_MAP["Tackles"]: "#3A0CA3",
    STAT_LABEL_MAP["BreakdownSteal"]: "#00BBF9",
    STAT_LABEL_MAP["LineoutSteal"]: "#9B5DE5",
    STAT_LABEL_MAP["PenaltyConceded"]: "#F15BB5",
    STAT_LABEL_MAP["PlayerOfTheMatch"]: "#FF006E",
    STAT_LABEL_MAP["YellowCard"]: "#FFD166",
    STAT_LABEL_MAP["RedCard"]: "#D00000",
}
MOBILE_UA_MARKERS = (
    "mobi",
    "iphone",
    "ipad",
    "android",
    "ipod",
    "windows phone",
    "opera mini",
)


def _coerce_bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    normalized = series.astype(str).str.strip().str.lower()
    return normalized.isin({"1", "true", "t", "yes", "y"})


def _is_mobile_request() -> bool:
    try:
        user_agent = request.headers.get("User-Agent", "").lower()
    except RuntimeError:
        return False
    if not user_agent:
        return False
    return any(marker in user_agent for marker in MOBILE_UA_MARKERS)


def _infer_round_lookup(match_ids: pd.Series, fixtures_per_round: int = 3) -> dict[int, int]:
    numeric_match_ids = pd.to_numeric(match_ids, errors="coerce").dropna().astype(int)
    ordered_match_ids = sorted(pd.unique(numeric_match_ids))
    if not ordered_match_ids:
        return {}
    matches_per_round = max(1, int(fixtures_per_round))
    return {
        int(match_id): (index // matches_per_round) + 1
        for index, match_id in enumerate(ordered_match_ids)
    }


def _coerce_round_filter_values(rounds_sel) -> list[int]:
    if rounds_sel is None:
        return []
    if isinstance(rounds_sel, (str, int, float)):
        raw_values = [rounds_sel]
    else:
        raw_values = list(rounds_sel)

    round_values: list[int] = []
    for raw_round in raw_values:
        try:
            round_values.append(int(raw_round))
        except (TypeError, ValueError):
            continue
    return sorted(set(round_values))


def _build_filter_preview(options, max_items: int = 2, prefix: str = "All") -> str:
    items = [str(item).strip() for item in options if str(item).strip()]
    if not items:
        return prefix
    if len(items) <= max_items:
        return ", ".join(items)
    return f"{', '.join(items[:max_items])}..."


def _build_name_opponent_label(name_series: pd.Series, opponent_series: pd.Series) -> pd.Series:
    names = name_series.fillna("Player").astype(str).str.strip()
    names = names.where(names != "", "Player")
    opponents = opponent_series.fillna("Unknown").astype(str).str.strip()
    opponents = opponents.where(opponents != "", "Unknown")
    return names + " vs " + opponents


def _build_position_opponent_label(
    position_series: pd.Series, opponent_series: pd.Series
) -> pd.Series:
    positions = position_series.fillna("Position").astype(str).str.strip()
    positions = positions.where(positions != "", "Position")
    opponents = opponent_series.fillna("Unknown").astype(str).str.strip()
    opponents = opponents.where(opponents != "", "Unknown")
    return positions + " vs " + opponents


def _maybe_refresh_from_api() -> None:
    """Optionally refresh DuckDB from the API on app startup.

    Controlled by SIXNATIONS_REFRESH_ON_START:
    - auto (default): refresh only if SIXNATIONS_TOKEN is present
    - true/1/yes/on: always try to refresh
    - false/0/no/off: never refresh
    """
    mode = os.getenv("SIXNATIONS_REFRESH_ON_START", "auto").strip().lower()
    truthy = {"1", "true", "yes", "on"}
    falsy = {"0", "false", "no", "off"}

    if mode in falsy:
        return
    if mode == "auto" and not os.getenv("SIXNATIONS_TOKEN"):
        return
    if mode not in truthy and mode != "auto":
        return

    try:
        min_refresh_seconds = int(os.getenv("SIXNATIONS_MIN_REFRESH_SECONDS", "60"))
    except ValueError:
        min_refresh_seconds = 60

    try:
        refreshed_df, notices = refresh_all_matches(
            verbose=False,
            min_interval_seconds=max(0, min_refresh_seconds),
            allow_cached_on_rate_limit=True,
        )
        if notices and any("Refresh skipped" in n for n in notices):
            print(notices[0])
            return
        print(f"Refreshed fantasy data from API ({len(refreshed_df)} player rows).")
        if notices:
            print(f"Refresh notices: {len(notices)}")
    except Exception as exc:
        print(f"Fantasy API refresh skipped: {exc}")


def _latest_file(patterns: List[str]) -> Optional[str]:
    candidates: List[str] = []
    for pat in patterns:
        candidates.extend(glob.glob(pat))
    if not candidates:
        return None
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def _last_pulled_utc_label() -> str:
    """Best-effort label for when data was last pulled from the API."""
    state_path = get_default_refresh_state_path()
    try:
        if os.path.exists(state_path):
            with open(state_path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
            epoch = float(raw)
            dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (OSError, ValueError):
        pass

    db_path = get_default_db_path()
    if os.path.exists(db_path):
        dt = datetime.fromtimestamp(os.path.getmtime(db_path), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")

    return "Unknown"


def load_latest_df() -> pd.DataFrame:
    fixed_path = get_default_db_path()
    data_dir = os.path.dirname(fixed_path) or "."
    os.makedirs(data_dir, exist_ok=True)
    pattern = os.path.join(data_dir, "*.duckdb")
    path = fixed_path if os.path.exists(fixed_path) else _latest_file([pattern])
    if path is None:
        raise FileNotFoundError(
            f"No saved DuckDB data found in {data_dir}. Run `python fantasy_ingest.py` first."
        )
    con = duckdb.connect(path, read_only=True)
    try:
        tables = [
            row[0]
            for row in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        ]
        if "all_matches" in tables:
            table_name = "all_matches"
        elif len(tables) == 1:
            table_name = tables[0]
        else:
            raise ValueError(
                f"Expected a table named 'all_matches' in {path}, found: {tables or 'none'}"
            )
        df = con.execute(f"SELECT * FROM {table_name}").df()
    finally:
        con.close()

    # Basic sanity and derived columns
    if "team" not in df.columns:
        df["team"] = "Unknown"
    # Prefer more specific 'country' column from ingestion if available
    if "country" in df.columns:
        # Replace placeholder Home/Away with country where available
        df["team"] = df["team"].replace({"Home": pd.NA, "Away": pd.NA})
        df["team"] = df["country"].where(
            df["country"].notna() & (df["country"].astype(str).str.strip() != ""),
            df["team"],
        )
    if "position" not in df.columns:
        df["position"] = "Unknown"
    if "name" not in df.columns:
        df["name"] = df.get("id", "Player").astype(str)

    # Opponent: prefer ingested column, then infer per match for legacy rows.
    if "opponent" not in df.columns:
        df["opponent"] = pd.NA

    # Normalise blanks.
    df["opponent"] = df["opponent"].where(
        df["opponent"].notna() & (df["opponent"].astype(str).str.strip() != ""),
        pd.NA,
    )

    # Infer missing opponents within each match where exactly two teams are present.
    if "match_id" in df.columns:
        match_pairs = (
            df[["match_id", "team"]]
            .dropna()
            .drop_duplicates()
            .groupby("match_id")["team"]
            .agg(lambda s: sorted(pd.unique(s)))
            .reset_index(name="teams")
        )
        two_team_matches = match_pairs[
            match_pairs["teams"].apply(lambda teams: len(teams) == 2)
        ]
        opp_rows = []
        for _, row in two_team_matches.iterrows():
            team_a, team_b = row["teams"]
            match_id = row["match_id"]
            opp_rows.append(
                {"match_id": match_id, "team": team_a, "opponent_inferred": team_b}
            )
            opp_rows.append(
                {"match_id": match_id, "team": team_b, "opponent_inferred": team_a}
            )
        if opp_rows:
            opp_lookup = pd.DataFrame(opp_rows)
            df = df.merge(opp_lookup, on=["match_id", "team"], how="left")
            df["opponent"] = df["opponent"].fillna(df["opponent_inferred"])
            df = df.drop(columns=["opponent_inferred"], errors="ignore")
    else:
        # Single-match fallback.
        teams = sorted(df["team"].dropna().unique().tolist())
        if len(teams) == 2:
            opp_map = {teams[0]: teams[1], teams[1]: teams[0]}
            df["opponent"] = df["opponent"].fillna(df["team"].map(opp_map))

    if "round" in df.columns:
        df["round"] = pd.to_numeric(df["round"], errors="coerce").round().astype("Int64")
    else:
        df["round"] = pd.Series([pd.NA] * len(df), index=df.index, dtype="Int64")

    if df["round"].isna().all() and "match_id" in df.columns:
        inferred_round_lookup = _infer_round_lookup(df["match_id"])
        if inferred_round_lookup:
            match_ids_numeric = pd.to_numeric(df["match_id"], errors="coerce")
            df["round"] = match_ids_numeric.map(inferred_round_lookup).astype("Int64")

    # Ensure numeric for points columns
    for c in df.columns:
        if c.endswith("_points") or c in (
            "points_total",
            "total_points",
            "consistent_points",
            "computed_points_total",
            "points_delta",
        ):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    # Backward-compatible totals for older datasets
    if "points_total" not in df.columns:
        if "computed_points_total" in df.columns:
            df["points_total"] = df["computed_points_total"]
        else:
            df["points_total"] = 0.0

    # By definition, total points mirrors site points_total.
    df["total_points"] = pd.to_numeric(df["points_total"], errors="coerce").fillna(0.0)

    # Recompute consistent points from stat points so older DB rows are still accurate.
    point_cols = [
        c
        for c in df.columns
        if c.endswith("_points") and c not in DERIVED_POINT_COLUMNS
    ]
    consistent_point_cols = [
        c for c in point_cols if c not in CONSISTENT_POINTS_EXCLUDED_POINT_COLS
    ]
    if consistent_point_cols:
        df["consistent_points"] = df[consistent_point_cols].sum(axis=1)
    else:
        df["consistent_points"] = 0.0

    # Keep base position for filtering and store fixture-role variants separately.
    base_position = (
        df["position"].fillna("").astype(str).str.strip()
        if "position" in df.columns
        else pd.Series(["Unknown"] * len(df), index=df.index)
    )
    df["position"] = base_position
    df["position_base"] = base_position

    if "is_substitute" in df.columns:
        sub_mask = _coerce_bool_series(df["is_substitute"])
    elif "starter_status" in df.columns:
        sub_mask = df["starter_status"].astype(str).str.strip().str.upper().eq("R")
    elif "is_starter" in df.columns:
        sub_mask = ~_coerce_bool_series(df["is_starter"])
    else:
        sub_mask = pd.Series([False] * len(df), index=df.index)
    df["is_substitute"] = sub_mask

    if "position_fixture" not in df.columns:
        df["position_fixture"] = df["position_base"]
    else:
        fixture_pos = (
            df["position_fixture"].fillna("").astype(str).str.strip()
        )
        df["position_fixture"] = fixture_pos.where(
            fixture_pos != "",
            df["position_base"],
        )
    df.loc[df["is_substitute"], "position_fixture"] = (
        df.loc[df["is_substitute"], "position_base"] + " sub"
    )

    # Minutes are non-scoring but useful for plotting on a secondary axis (0..80)
    if "Minutes" in df.columns:
        df["Minutes"] = (
            pd.to_numeric(df["Minutes"], errors="coerce")
            .fillna(0.0)
            .clip(lower=0, upper=80)
        )
    return df


def melt_points(df: pd.DataFrame, points_basis: str = "total") -> pd.DataFrame:
    """Return long DataFrame with columns: name, team, position, opponent, stat, points, value.

    - points: from <stat>_points columns
    - value: raw stat column (e.g., Try count, Tackles count, MetresCarried metres)
    Also includes pct per player for convenience (used when x='name').
    """
    point_cols = [
        c
        for c in df.columns
        if c.endswith("_points") and c not in DERIVED_POINT_COLUMNS
    ]
    if points_basis == "consistent":
        point_cols = [
            c for c in point_cols if c not in CONSISTENT_POINTS_EXCLUDED_POINT_COLS
        ]
    if not point_cols:
        return pd.DataFrame(
            columns=[
                "name",
                "team",
                "position",
                "opponent",
                "stat",
                "points",
                "value",
                "pct",
            ]
        )  # empty

    id_cols = [
        c
        for c in ["match_id", "id", "name", "team", "position", "opponent", "round"]
        if c in df.columns
    ]
    if not id_cols:
        id_cols = ["name"]

    # Long points
    points_long = df.melt(
        id_vars=id_cols,
        value_vars=point_cols,
        var_name="stat",
        value_name="points",
    )
    points_long["stat"] = points_long["stat"].str.replace("_points", "", regex=False)

    # Long raw values for the same stats (if present)
    base_stats = sorted({c[:-7] for c in point_cols})
    value_cols = [s for s in base_stats if s in df.columns]
    if value_cols:
        values_long = df.melt(
            id_vars=id_cols,
            value_vars=value_cols,
            var_name="stat",
            value_name="value",
        )
    else:
        values_long = pd.DataFrame(
            columns=id_cols + ["stat", "value"]
        ).assign(value=pd.NA)

    merge_keys = id_cols + ["stat"]
    merged = points_long.merge(values_long, on=merge_keys, how="left")

    # Compute per-player totals for % if we later plot by player
    player_total_keys = [
        c for c in ["name", "team", "position", "opponent"] if c in merged.columns
    ]
    if not player_total_keys:
        player_total_keys = id_cols

    totals = (
        merged.groupby(player_total_keys, as_index=False)["points"]
        .sum()
        .rename(columns={"points": "sum_points"})
    )
    merged = merged.merge(totals, on=player_total_keys, how="left")
    merged["pct"] = (merged["points"] / merged["sum_points"].replace(0, pd.NA)) * 100
    merged["pct"] = merged["pct"].fillna(0.0)
    return merged


_maybe_refresh_from_api()
df = load_latest_df()
points_long = melt_points(df, points_basis="total")
FOOTER_YEAR = datetime.now(timezone.utc).year
LAST_PULLED_LABEL = _last_pulled_utc_label()

teams = sorted(df["team"].dropna().unique())
positions = sorted(df["position"].dropna().unique())
opponents = (
    sorted(df["opponent"].dropna().unique()) if df["opponent"].notna().any() else []
)
rounds = (
    sorted(int(r) for r in pd.unique(df["round"].dropna()))
    if "round" in df.columns and df["round"].notna().any()
    else []
)
team_preview = _build_filter_preview(teams)
position_preview = _build_filter_preview(positions)
opponent_preview = _build_filter_preview(opponents, prefix="All opponents")
round_preview = _build_filter_preview(
    [f"Round {r}" for r in rounds], prefix="All rounds"
)

external_stylesheets = [dbc.themes.DARKLY]
app = Dash(
    __name__,
    external_stylesheets=external_stylesheets,
    meta_tags=[
        {
            "name": "viewport",
            "content": "width=device-width, initial-scale=1, viewport-fit=cover",
        }
    ],
)
app.title = "Six Nations Fantasy – Scoring Explorer"


@app.server.route("/favicon.ico")
def favicon():
    assets_dir = os.path.join(os.path.dirname(__file__), "assets")
    return send_from_directory(
        assets_dir,
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )


def control_card():
    return dbc.Card(
        dbc.CardBody(
            [
                html.H5("Filters", className="card-title"),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                dbc.Label("Team"),
                                dcc.Dropdown(
                                    id="team-filter",
                                    options=[{"label": t, "value": t} for t in teams],
                                    value=[],
                                    multi=True,
                                    placeholder=team_preview,
                                    className="neo-dropdown",
                                ),
                            ],
                            md=3,
                        ),
                        dbc.Col(
                            [
                                dbc.Label("Opponent"),
                                dcc.Dropdown(
                                    id="opponent-filter",
                                    options=[
                                        {"label": o, "value": o} for o in opponents
                                    ],
                                    value=[],
                                    multi=True,
                                    placeholder=opponent_preview,
                                    disabled=(len(opponents) == 0),
                                    className="neo-dropdown",
                                ),
                            ],
                            md=3,
                        ),
                        dbc.Col(
                            [
                                dbc.Label("Position"),
                                dcc.Dropdown(
                                    id="position-filter",
                                    options=[
                                        {"label": p, "value": p} for p in positions
                                    ],
                                    value=[],
                                    multi=True,
                                    placeholder=position_preview,
                                    className="neo-dropdown",
                                ),
                            ],
                            md=3,
                        ),
                        dbc.Col(
                            [
                                dbc.Label("Round"),
                                dcc.Dropdown(
                                    id="round-filter",
                                    options=[
                                        {"label": f"Round {r}", "value": r}
                                        for r in rounds
                                    ],
                                    value=[],
                                    multi=True,
                                    placeholder=round_preview,
                                    disabled=(len(rounds) == 0),
                                    className="neo-dropdown",
                                ),
                            ],
                            md=3,
                        ),
                    ],
                    className="gy-2",
                ),
                html.Hr(),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                dbc.Label("Stack Metric"),
                                dcc.RadioItems(
                                    id="metric-mode",
                                    options=[
                                        {"label": "% of Player Total", "value": "pct"},
                                        {"label": "Points", "value": "points"},
                                    ],
                                    value="points",
                                    inline=True,
                                    className="neo-radio",
                                ),
                            ],
                            md=3,
                        ),
                        dbc.Col(
                            [
                                dbc.Label("Aggregation"),
                                dcc.RadioItems(
                                    id="aggregate-mode",
                                    options=[
                                        {"label": "Total", "value": "total"},
                                        {"label": "Average", "value": "average"},
                                    ],
                                    value="total",
                                    inline=True,
                                    className="neo-radio",
                                ),
                            ],
                            md=3,
                        ),
                        dbc.Col(
                            [
                                dbc.Label("Points Category"),
                                dcc.RadioItems(
                                    id="points-basis",
                                    options=[
                                        {"label": "Total Points", "value": "total"},
                                        {
                                            "label": "Consistent Points",
                                            "value": "consistent",
                                        },
                                    ],
                                    value="total",
                                    inline=True,
                                    className="neo-radio",
                                ),
                            ],
                            md=3,
                        ),
                        dbc.Col(
                            [
                                dbc.Label("Player Type"),
                                dcc.RadioItems(
                                    id="player-type-mode",
                                    options=[
                                        {"label": "Starters only", "value": "starters"},
                                        {"label": "All", "value": "all"},
                                        {"label": "Subs only", "value": "subs"},
                                    ],
                                    value="starters",
                                    inline=True,
                                    className="neo-radio",
                                ),
                            ],
                            md=3,
                        ),
                    ]
                ),
            ]
        ),
        className="panel filters-panel",
    )


## Removed summary card per feedback


app.layout = dbc.Container(
    [
        html.Div(
            [
                html.Div("Six Nations Fantasy", className="hero-title"),
                html.Div("Scoring Explorer", className="hero-subtitle"),
            ],
            className="hero",
        ),
        dbc.Row(
            [
                dbc.Col(control_card(), md=12),
            ],
            className="g-tight filters-section",
        ),
        dbc.Row(
            [
                dbc.Col(
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.H5("Breakdown", className="card-title"),
                                dcc.Graph(id="stacked-bar", className="neo-graph"),
                                dbc.Row(
                                    [
                                        dbc.Col(
                                            [
                                                dbc.Label(
                                                    "X Axis",
                                                    id="axis-group-label",
                                                    className="mt-2",
                                                ),
                                                dcc.Dropdown(
                                                    id="axis-group",
                                                    options=[
                                                        {
                                                            "label": "Player",
                                                            "value": "name",
                                                        },
                                                        {
                                                            "label": "Player vs Opponent",
                                                            "value": "name_opponent",
                                                        },
                                                        {
                                                            "label": "Position vs Opponent",
                                                            "value": "position_opponent",
                                                        },
                                                        {
                                                            "label": "Position",
                                                            "value": "position",
                                                        },
                                                        {"label": "Team", "value": "team"},
                                                        {
                                                            "label": "Opponent",
                                                            "value": "opponent",
                                                        },
                                                    ],
                                                    value="name",
                                                    clearable=False,
                                                    className="neo-dropdown",
                                                ),
                                            ],
                                            md=3,
                                        ),
                                        dbc.Col(
                                            html.Div(
                                                f"Data last updated: {LAST_PULLED_LABEL}",
                                                className="last-updated last-updated--right",
                                            ),
                                            className="d-flex align-items-end",
                                        ),
                                    ],
                                    align="end",
                                ),
                            ]
                        ),
                        className="panel",
                    ),
                    md=12,
                ),
            ],
            className="g-tight",
        ),
        html.Footer(
            [
                html.Div(
                    [
                        html.Div(
                            f"\u00a9 {FOOTER_YEAR} Scott Tomlins | website by Scott Tomlins",
                            className="footer-copy",
                        ),
                        html.Div(
                            [
                                html.A("Email", href="mailto:fantasy6n@stomlins.com", className="footer-link"),
                                html.Span("·", className="footer-sep"),
                                html.A("LinkedIn", href="https://linkedin.stomlins.com", target="_blank", rel="noopener noreferrer", className="footer-link"),
                                html.Span("·", className="footer-sep"),
                                html.A("GitHub", href="https://github.com/satomlins/", target="_blank", rel="noopener noreferrer", className="footer-link"),
                                html.Span("·", className="footer-sep"),
                                html.A("Medium", href="https://stomlins.medium.com/", target="_blank", rel="noopener noreferrer", className="footer-link"),
                            ],
                            className="footer-actions",
                        ),
                    ],
                    className="footer-center-stack",
                ),
            ],
            className="app-footer",
        ),
    ],
    fluid=True,
    className="neo-container",
)


def _apply_filters(
    df_in: pd.DataFrame,
    teams_sel,
    positions_sel,
    opp_sel,
    rounds_sel,
    player_type_mode: str,
):
    dff = df_in.copy()
    if teams_sel:
        dff = dff[dff["team"].isin(teams_sel)]
    mode = str(player_type_mode or "starters").strip().lower()
    if mode not in {"all", "starters", "subs"}:
        mode = "starters"

    if "is_substitute" in dff.columns:
        sub_mask = _coerce_bool_series(dff["is_substitute"])
    elif "starter_status" in dff.columns:
        sub_mask = dff["starter_status"].astype(str).str.strip().str.upper().eq("R")
    elif "is_starter" in dff.columns:
        sub_mask = ~_coerce_bool_series(dff["is_starter"])
    else:
        sub_mask = pd.Series([False] * len(dff), index=dff.index)

    if mode == "starters":
        dff = dff[~sub_mask]
    elif mode == "subs":
        dff = dff[sub_mask]

    if positions_sel:
        dff = dff[dff["position"].isin(positions_sel)]
    if opp_sel and "opponent" in dff.columns:
        dff = dff[dff["opponent"].isin(opp_sel)]
    round_values = _coerce_round_filter_values(rounds_sel)
    if round_values and "round" in dff.columns:
        dff = dff[dff["round"].isin(round_values)]
    return dff


@app.callback(
    Output("stacked-bar", "figure"),
    Output("axis-group-label", "children"),
    Input("team-filter", "value"),
    Input("position-filter", "value"),
    Input("opponent-filter", "value"),
    Input("round-filter", "value"),
    Input("axis-group", "value"),
    Input("metric-mode", "value"),
    Input("aggregate-mode", "value"),
    Input("points-basis", "value"),
    Input("player-type-mode", "value"),
)
def refresh(
    teams_sel,
    positions_sel,
    opp_sel,
    rounds_sel,
    axis_group,
    metric_mode,
    aggregate_mode,
    points_basis,
    player_type_mode,
):
    is_mobile = _is_mobile_request()
    axis_selector_label = "Y Axis" if is_mobile else "X Axis"

    # Apply filters and create long form for plotting
    dff = _apply_filters(
        df,
        teams_sel,
        positions_sel,
        opp_sel,
        rounds_sel,
        player_type_mode,
    )
    longf = melt_points(dff, points_basis=points_basis)

    # Guard empty data
    if longf.empty:
        empty_fig = px.bar()
        empty_fig.add_annotation(
            text="No data for current filters",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font={"size": 16},
        )
        empty_fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        empty_fig.update_xaxes(visible=False)
        empty_fig.update_yaxes(visible=False)
        return empty_fig, axis_selector_label

    # Validate selected grouping axis.
    valid_axis_groups = {
        "name",
        "name_opponent",
        "position_opponent",
        "position",
        "team",
        "opponent",
    }
    if axis_group not in valid_axis_groups:
        axis_group = "name"

    # Optional combined grouping for single-fixture style comparisons.
    if axis_group == "name_opponent":
        dff = dff.copy()
        longf = longf.copy()
        dff["name_opponent"] = _build_name_opponent_label(
            dff["name"]
            if "name" in dff.columns
            else pd.Series(["Player"] * len(dff), index=dff.index),
            dff["opponent"]
            if "opponent" in dff.columns
            else pd.Series([pd.NA] * len(dff), index=dff.index),
        )
        longf["name_opponent"] = _build_name_opponent_label(
            longf["name"]
            if "name" in longf.columns
            else pd.Series(["Player"] * len(longf), index=longf.index),
            longf["opponent"]
            if "opponent" in longf.columns
            else pd.Series([pd.NA] * len(longf), index=longf.index),
        )
    elif axis_group == "position_opponent":
        dff = dff.copy()
        longf = longf.copy()
        dff["position_opponent"] = _build_position_opponent_label(
            dff["position"]
            if "position" in dff.columns
            else pd.Series(["Position"] * len(dff), index=dff.index),
            dff["opponent"]
            if "opponent" in dff.columns
            else pd.Series([pd.NA] * len(dff), index=dff.index),
        )
        longf["position_opponent"] = _build_position_opponent_label(
            longf["position"]
            if "position" in longf.columns
            else pd.Series(["Position"] * len(longf), index=longf.index),
            longf["opponent"]
            if "opponent" in longf.columns
            else pd.Series([pd.NA] * len(longf), index=longf.index),
        )

    aggregate_mode = str(aggregate_mode or "total").strip().lower()
    if aggregate_mode not in {"total", "average"}:
        aggregate_mode = "total"
    agg_fn = "mean" if aggregate_mode == "average" else "sum"

    # Aggregate to group level
    dlong = longf.dropna(subset=[axis_group]).copy()
    agg = dlong.groupby([axis_group, "stat"], as_index=False).agg(
        points=("points", agg_fn), value=("value", agg_fn)
    )
    totals_by_group = (
        agg.groupby(axis_group, as_index=False)["points"]
        .sum()
        .rename(columns={"points": "group_total"})
    )
    agg = agg.merge(totals_by_group, on=axis_group, how="left")
    agg["pct"] = (agg["points"] / agg["group_total"].replace(0, pd.NA)) * 100
    agg["pct"] = agg["pct"].fillna(0.0)

    # Order groups by total points desc.
    order = totals_by_group.sort_values("group_total", ascending=False)[axis_group].tolist()
    height_reference_rows = min(len(order), 30)

    # Show fewer rows on mobile for readability/performance.
    max_groups = 20 if is_mobile else 30
    if len(order) > max_groups:
        order = order[:max_groups]
        agg = agg[agg[axis_group].isin(order)].copy()

    agg[axis_group] = pd.Categorical(agg[axis_group], categories=order, ordered=True)

    # If grouping by player name, attach the player's country/team for hover info.
    team_label_present = False
    team_for_name_map = None
    if axis_group == "name":
        try:
            mode_team = (
                dff[["name", "team"]]
                .dropna()
                .groupby("name")["team"]
                .agg(lambda s: s.mode().iat[0] if not s.mode().empty else s.iloc[0])
            )
            team_for_name_map = mode_team.to_dict()
            agg["team_label"] = agg[axis_group].astype(str).map(team_for_name_map)
            team_label_present = True
        except Exception:
            team_label_present = False

    # Friendly labels for stats and axes.
    agg["stat_label"] = agg["stat"].map(STAT_LABEL_MAP).fillna(agg["stat"])
    label_map = {
        "name": "Player",
        "name_opponent": "Player vs Opponent",
        "position_opponent": "Position vs Opponent",
        "position": "Position",
        "team": "Team",
        "opponent": "Opponent",
    }

    y_col = "pct" if metric_mode == "pct" else "points"
    points_basis_label = "Consistent Points" if points_basis == "consistent" else "Points"
    aggregation_label = "Average" if aggregate_mode == "average" else "Total"
    points_category_title = f"{aggregation_label} {points_basis_label}"

    # Keep a stable legend/stack order so colors never reshuffle across filters.
    present_stat_labels = set(agg["stat_label"].dropna().astype(str))
    ordered_known_labels = [
        label for label in STAT_LABEL_ORDER if label in present_stat_labels
    ]
    ordered_unknown_labels = sorted(present_stat_labels.difference(ordered_known_labels))
    stat_order = ordered_known_labels + ordered_unknown_labels
    color_discrete_map = dict(STAT_COLOR_MAP)
    for idx, label in enumerate(ordered_unknown_labels):
        color_discrete_map[label] = px.colors.qualitative.Dark24[
            idx % len(px.colors.qualitative.Dark24)
        ]

    custom_data = ["stat_label", "points", "value"] + (
        ["team_label"] if team_label_present else []
    )
    points_hover_label = "Avg Points" if aggregate_mode == "average" else "Points"
    value_hover_label = "Avg Value" if aggregate_mode == "average" else "Value"
    value_hover_fmt = ".1f" if aggregate_mode == "average" else ".0f"

    if is_mobile:
        fig_bar = px.bar(
            agg,
            x=y_col,
            y=axis_group,
            color="stat_label",
            orientation="h",
            custom_data=custom_data,
            color_discrete_map=color_discrete_map,
            category_orders={axis_group: order, "stat_label": stat_order},
        )
        hover_tmpl = (
            f"{label_map.get(axis_group, 'Player')}: %{{y}}<br>"
            "Stat: %{customdata[0]}<br>"
            f"{points_hover_label}: %{{customdata[1]:.1f}}<br>"
        )
        if metric_mode == "pct":
            hover_tmpl += "%: %{x:.1f}<br>"
        if team_label_present:
            hover_tmpl += "Team: %{customdata[3]}<br>"
        hover_tmpl += (
            f"{value_hover_label}: %{{customdata[2]:{value_hover_fmt}}}<extra></extra>"
        )
        fig_bar.update_traces(hovertemplate=hover_tmpl)

        # For horizontal bars, reverse category draw order so largest appears at the top.
        order_top_first = list(order)[::-1]
        fig_bar.update_yaxes(
            categoryorder="array",
            categoryarray=order_top_first,
            tickmode="array",
            tickvals=order_top_first,
            ticktext=order_top_first,
            title_text=None,
        )
        if metric_mode == "pct":
            fig_bar.update_xaxes(title_text="% of Group Total", range=[0, 100])
        else:
            fig_bar.update_xaxes(title_text=points_category_title)
    else:
        fig_bar = px.bar(
            agg,
            x=axis_group,
            y=y_col,
            color="stat_label",
            custom_data=custom_data,
            color_discrete_map=color_discrete_map,
            category_orders={axis_group: order, "stat_label": stat_order},
        )
        hover_tmpl = (
            f"{label_map.get(axis_group, 'Player')}: %{{x}}<br>"
            "Stat: %{customdata[0]}<br>"
            f"{points_hover_label}: %{{customdata[1]:.1f}}<br>"
        )
        if metric_mode == "pct":
            hover_tmpl += "%: %{y:.1f}<br>"
        if team_label_present:
            hover_tmpl += "Team: %{customdata[3]}<br>"
        hover_tmpl += (
            f"{value_hover_label}: %{{customdata[2]:{value_hover_fmt}}}<extra></extra>"
        )
        fig_bar.update_traces(hovertemplate=hover_tmpl)
        fig_bar.update_xaxes(categoryorder="array", categoryarray=order, title_text=None)
        if metric_mode == "pct":
            fig_bar.update_yaxes(title_text="% of Group Total", range=[0, 100])
        else:
            fig_bar.update_yaxes(title_text=points_category_title)

    # Overlay Minutes as a secondary-axis scatter (dots), showing average minutes per group.
    if "Minutes" in dff.columns:
        mins_group = (
            dff.dropna(subset=[axis_group])[[axis_group, "Minutes"]]
            .groupby(axis_group, as_index=False)["Minutes"]
            .mean()
        )
        mins_map = dict(zip(mins_group[axis_group], mins_group["Minutes"]))

        scatter_kwargs = {}
        if team_label_present and team_for_name_map is not None and axis_group == "name":
            scatter_kwargs["customdata"] = [team_for_name_map.get(str(cat)) for cat in order]

        if is_mobile:
            mins_x = [float(mins_map.get(cat)) if cat in mins_map else None for cat in order]
            if scatter_kwargs:
                scatter_hover = (
                    f"{label_map.get(axis_group, 'Player')}: %{{y}}<br>"
                    "Team: %{customdata}<br>"
                    "Minutes: %{x:.0f}<extra></extra>"
                )
            else:
                scatter_hover = (
                    f"{label_map.get(axis_group, 'Player')}: %{{y}}<br>"
                    "Minutes: %{x:.0f}<extra></extra>"
                )
            fig_bar.add_scatter(
                x=mins_x,
                y=order,
                mode="markers",
                name="Minutes",
                marker=dict(color="#45d9e7", size=8, line=dict(color="#2bbfcc", width=0.5)),
                xaxis="x2",
                hovertemplate=scatter_hover,
                **scatter_kwargs,
            )
            fig_bar.update_layout(
                xaxis2=dict(
                    title="Minutes",
                    overlaying="x",
                    side="top",
                    range=[0, 80],
                    showgrid=False,
                    zerolinecolor="#1d242c",
                    tickfont=dict(size=12),
                )
            )
        else:
            mins_y = [float(mins_map.get(cat)) if cat in mins_map else None for cat in order]
            if scatter_kwargs:
                scatter_hover = (
                    f"{label_map.get(axis_group, 'Player')}: %{{x}}<br>"
                    "Team: %{customdata}<br>"
                    "Minutes: %{y:.0f}<extra></extra>"
                )
            else:
                scatter_hover = (
                    f"{label_map.get(axis_group, 'Player')}: %{{x}}<br>"
                    "Minutes: %{y:.0f}<extra></extra>"
                )
            fig_bar.add_scatter(
                x=order,
                y=mins_y,
                mode="markers",
                name="Minutes",
                marker=dict(color="#45d9e7", size=8, line=dict(color="#2bbfcc", width=0.5)),
                yaxis="y2",
                hovertemplate=scatter_hover,
                **scatter_kwargs,
            )
            fig_bar.update_layout(
                yaxis2=dict(
                    title="Minutes",
                    overlaying="y",
                    side="right",
                    range=[0, 80],
                    showgrid=False,
                    zerolinecolor="#1d242c",
                    tickfont=dict(size=12),
                )
            )

    chart_height = max(520, 220 + (24 * height_reference_rows)) if is_mobile else 520
    margin_cfg = dict(l=20, r=20, t=56, b=96) if is_mobile else dict(l=20, r=20, t=60, b=140)
    legend_y = -0.12 if is_mobile else -0.26

    fig_bar.update_layout(
        legend_title_text="",
        margin=margin_cfg,
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={
            "family": "Inter, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
            "size": 15,
            "color": "#8f9ba8",
        },
        legend={
            "font": {"size": 11, "color": "#8f9ba8"},
            "bgcolor": "rgba(0,0,0,0)",
            "orientation": "h",
            "yanchor": "top",
            "y": legend_y,
            "x": 0,
            "xanchor": "left",
            "tracegroupgap": 3,
            "itemwidth": 30,
        },
        barmode="relative",
        height=chart_height,
    )

    if is_mobile:
        fig_bar.update_xaxes(
            tickfont={"size": 12},
            gridcolor="#1d242c",
            zerolinecolor="#1d242c",
            automargin=True,
            title_standoff=4,
        )
        fig_bar.update_yaxes(
            title_text=None,
            tickfont={"size": 12},
            gridcolor="#1d242c",
            zerolinecolor="#1d242c",
            automargin=True,
        )
    else:
        fig_bar.update_xaxes(
            title_text=None,
            tickfont={"size": 12},
            gridcolor="#1d242c",
            zerolinecolor="#1d242c",
            tickangle=-30,
            automargin=True,
            title_standoff=4,
        )
        fig_bar.update_yaxes(
            tickfont={"size": 12},
            gridcolor="#1d242c",
            zerolinecolor="#1d242c",
        )

    return fig_bar, axis_selector_label


def main() -> None:
    # Dash >=2.17 deprecates run_server in favor of run
    debug_mode = os.getenv("DASH_DEBUG", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    app.run(debug=debug_mode, port=int(os.getenv("PORT", "8050")))


if __name__ == "__main__":
    main()

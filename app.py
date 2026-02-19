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
from fantasy_ingest import CONSISTENT_POINTS_EXCLUDED_STATS, refresh_all_matches

load_dotenv()

CONSISTENT_POINTS_EXCLUDED_POINT_COLS = {
    f"{stat}_points" for stat in CONSISTENT_POINTS_EXCLUDED_STATS
}
DERIVED_POINT_COLUMNS = {"total_points", "consistent_points", "good_points"}


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
    state_path = os.path.join("data", ".last_api_refresh")
    try:
        if os.path.exists(state_path):
            with open(state_path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
            epoch = float(raw)
            dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (OSError, ValueError):
        pass

    db_path = os.path.join("data", "all_matches.duckdb")
    if os.path.exists(db_path):
        dt = datetime.fromtimestamp(os.path.getmtime(db_path), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")

    return "Unknown"


def load_latest_df() -> pd.DataFrame:
    os.makedirs("data", exist_ok=True)
    fixed_path = os.path.join("data", "all_matches.duckdb")
    path = fixed_path if os.path.exists(fixed_path) else _latest_file(["data/*.duckdb"])
    if path is None:
        raise FileNotFoundError(
            "No saved DuckDB data found in ./data. Run `python fantasy_ingest.py` first."
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
        for c in ["match_id", "id", "name", "team", "position", "opponent"]
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
players = sorted(df["name"].dropna().unique())

external_stylesheets = [dbc.themes.DARKLY]
app = Dash(__name__, external_stylesheets=external_stylesheets)
app.title = "Six Nations Fantasy – Scoring Explorer"


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
                                    value=teams,
                                    multi=True,
                                    placeholder="Select team(s)",
                                    className="neo-dropdown",
                                ),
                            ],
                            md=4,
                        ),
                        dbc.Col(
                            [
                                dbc.Label("Position"),
                                dcc.Dropdown(
                                    id="position-filter",
                                    options=[
                                        {"label": p, "value": p} for p in positions
                                    ],
                                    value=positions,
                                    multi=True,
                                    placeholder="Select position(s)",
                                    className="neo-dropdown",
                                ),
                            ],
                            md=4,
                        ),
                        dbc.Col(
                            [
                                dbc.Label("Opponent"),
                                dcc.Dropdown(
                                    id="opponent-filter",
                                    options=[
                                        {"label": o, "value": o} for o in opponents
                                    ],
                                    value=opponents if opponents else None,
                                    multi=True,
                                    placeholder="Select opponent(s)",
                                    disabled=(len(opponents) == 0),
                                    className="neo-dropdown",
                                ),
                            ],
                            md=4,
                        ),
                    ],
                    className="gy-2",
                ),
                html.Hr(),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                dbc.Label("Players"),
                                dcc.Dropdown(
                                    id="player-filter",
                                    options=[{"label": n, "value": n} for n in players],
                                    value=[],
                                    multi=True,
                                    placeholder="Filter by player(s) (optional)",
                                    className="neo-dropdown",
                                ),
                            ],
                            md=6,
                        ),
                        dbc.Col(
                            [
                                dbc.Label("X Axis"),
                                dcc.Dropdown(
                                    id="xaxis-group",
                                    options=[
                                        {"label": "Player", "value": "name"},
                                        {"label": "Position", "value": "position"},
                                        {"label": "Team", "value": "team"},
                                        {"label": "Opponent", "value": "opponent"},
                                    ],
                                    value="name",
                                    clearable=False,
                                    className="neo-dropdown",
                                ),
                            ],
                            md=2,
                        ),
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
                                    inline=False,
                                    className="neo-radio",
                                ),
                            ],
                            md=2,
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
                                    inline=False,
                                    className="neo-radio",
                                ),
                            ],
                            md=2,
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
                    f"\u00a9 {FOOTER_YEAR} Scott Tomlins",
                    className="footer-copy",
                ),
                html.Div(
                    f"Data Last Updated: {LAST_PULLED_LABEL}",
                    className="footer-pulled",
                ),
                html.A(
                    [
                        html.Img(
                            src="/assets/linkedin-icon.svg",
                            className="linkedin-logo-img",
                            alt="",
                            **{"aria-hidden": "true"},
                        ),
                        html.Span("LinkedIn", className="footer-sr"),
                    ],
                    href="https://linkedin.stomlins.com",
                    target="_blank",
                    rel="noopener noreferrer",
                    className="footer-linkedin",
                    title="Scott Tomlins on LinkedIn",
                ),
            ],
            className="app-footer",
        ),
    ],
    fluid=True,
    className="neo-container",
)


def _apply_filters(df_in: pd.DataFrame, teams_sel, positions_sel, opp_sel, players_sel):
    dff = df_in.copy()
    if teams_sel:
        dff = dff[dff["team"].isin(teams_sel)]
    if positions_sel:
        dff = dff[dff["position"].isin(positions_sel)]
    if opp_sel and "opponent" in dff.columns:
        dff = dff[dff["opponent"].isin(opp_sel)]
    if players_sel:
        dff = dff[dff["name"].isin(players_sel)]
    return dff


@app.callback(
    Output("stacked-bar", "figure"),
    Input("team-filter", "value"),
    Input("position-filter", "value"),
    Input("opponent-filter", "value"),
    Input("player-filter", "value"),
    Input("xaxis-group", "value"),
    Input("metric-mode", "value"),
    Input("points-basis", "value"),
)
def refresh(
    teams_sel,
    positions_sel,
    opp_sel,
    players_sel,
    xaxis_group,
    metric_mode,
    points_basis,
):
    # Apply filters and create long form for plotting
    dff = _apply_filters(df, teams_sel, positions_sel, opp_sel, players_sel)
    longf = melt_points(dff, points_basis=points_basis)

    # Guard empty data
    if longf.empty:
        empty_fig = px.bar(title="No data for current filters")
        empty_fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        return empty_fig

    # Validate x-axis group
    if xaxis_group not in {"name", "position", "team", "opponent"}:
        xaxis_group = "name"

    # Aggregate to group level
    dlong = longf.dropna(subset=[xaxis_group]).copy()
    agg = dlong.groupby([xaxis_group, "stat"], as_index=False).agg(
        points=("points", "sum"), value=("value", "sum")
    )
    totals_by_group = (
        agg.groupby(xaxis_group, as_index=False)["points"]
        .sum()
        .rename(columns={"points": "group_total"})
    )
    agg = agg.merge(totals_by_group, on=xaxis_group, how="left")
    agg["pct"] = (agg["points"] / agg["group_total"].replace(0, pd.NA)) * 100
    agg["pct"] = agg["pct"].fillna(0.0)

    # Order groups by total points desc
    order = totals_by_group.sort_values("group_total", ascending=False)[
        xaxis_group
    ].tolist()

    # Keep the chart readable when grouping by player.
    if xaxis_group == "name" and len(order) > 30:
        order = order[:30]
        agg = agg[agg[xaxis_group].isin(order)].copy()

    agg[xaxis_group] = pd.Categorical(agg[xaxis_group], categories=order, ordered=True)

    # If grouping by player name, attach the player's country/team for hover info
    team_label_present = False
    team_for_name_map = None
    if xaxis_group == "name":
        try:
            # Use the most frequent team per name across the filtered data
            mode_team = (
                dff[["name", "team"]]
                .dropna()
                .groupby("name")["team"]
                .agg(lambda s: s.mode().iat[0] if not s.mode().empty else s.iloc[0])
            )
            team_for_name_map = mode_team.to_dict()
            agg["team_label"] = agg[xaxis_group].astype(str).map(team_for_name_map)
            team_label_present = True
        except Exception:
            team_label_present = False

    # Friendly labels for stats and axes
    stat_label_map = {
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
    agg["stat_label"] = agg["stat"].map(stat_label_map).fillna(agg["stat"])
    label_map = {
        "name": "Player",
        "position": "Position",
        "team": "Team",
        "opponent": "Opponent",
    }

    # Build stacked bar figure
    y_col = "pct" if metric_mode == "pct" else "points"
    points_category_title = (
        "Consistent Points" if points_basis == "consistent" else "Total Points"
    )
    bar_title = (
        f"% Contribution by Stat ({points_category_title})"
        if metric_mode == "pct"
        else f"{points_category_title} by Stat"
    ) + f" by {label_map.get(xaxis_group, 'Player')}"

    # Order stack so the largest contribution sits at the bottom (trace order defines stack order)
    stat_strength = (
        agg.groupby("stat_label", as_index=False)[y_col]
        .sum()
        .sort_values(y_col, ascending=False)
    )
    stat_order = stat_strength["stat_label"].tolist()

    COLOR_SEQ = px.colors.qualitative.Dark24
    fig_bar = px.bar(
        agg,
        x=xaxis_group,
        y=y_col,
        color="stat_label",
        custom_data=["stat_label", "points", "value"]
        + (["team_label"] if team_label_present else []),
        title=bar_title,
        color_discrete_sequence=COLOR_SEQ,
        category_orders={xaxis_group: order, "stat_label": stat_order},
    )
    hover_tmpl = (
        f"{label_map.get(xaxis_group, 'Player')}: %{{x}}<br>"
        "Stat: %{customdata[0]}<br>"
        "Points: %{customdata[1]:.1f}<br>"
    )
    if metric_mode == "pct":
        hover_tmpl += "%: %{y:.1f}<br>"
    if team_label_present:
        hover_tmpl += "Team: %{customdata[3]}<br>"
    hover_tmpl += "Count: %{customdata[2]:.0f}<extra></extra>"
    fig_bar.update_traces(hovertemplate=hover_tmpl)
    # Ensure x-axis categories are ordered by total size (desc), not alphabetically
    fig_bar.update_xaxes(categoryorder="array", categoryarray=order)
    if metric_mode == "pct":
        fig_bar.update_yaxes(title_text="% of Group Total", range=[0, 100])
    else:
        fig_bar.update_yaxes(title_text=points_category_title)

    # Overlay Minutes as a secondary-axis scatter (dots), showing average minutes per group (max 80)
    if "Minutes" in dff.columns:
        mins_group = (
            dff.dropna(subset=[xaxis_group])[[xaxis_group, "Minutes"]]
            .groupby(xaxis_group, as_index=False)["Minutes"]
            .mean()
        )
        mins_map = dict(zip(mins_group[xaxis_group], mins_group["Minutes"]))
        mins_y = [
            float(mins_map.get(cat)) if cat in mins_map else None for cat in order
        ]

        scatter_kwargs = {}
        if (
            team_label_present
            and team_for_name_map is not None
            and xaxis_group == "name"
        ):
            # Provide team in hover for minutes as well when grouping by Player
            scatter_kwargs["customdata"] = [
                team_for_name_map.get(str(cat)) for cat in order
            ]
            scatter_hover = f"{label_map.get(xaxis_group, 'Player')}: %{{x}}<br>Team: %{{customdata}}<br>Minutes: %{{y:.0f}}<extra></extra>"
        else:
            scatter_hover = f"{label_map.get(xaxis_group, 'Player')}: %{{x}}<br>Minutes: %{{y:.0f}}<extra></extra>"

        fig_bar.add_scatter(
            x=order,
            y=mins_y,
            mode="markers",
            name="Minutes",
            marker=dict(color="#7dd3fc", size=8, line=dict(color="#0ea5e9", width=0.5)),
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
                zerolinecolor="#1f2937",
                tickfont=dict(size=12),
            )
        )
    fig_bar.update_layout(
        legend_title_text="",
        xaxis_title=label_map.get(xaxis_group, "Player"),
        # Reduce top margin and add space at the bottom to place legend below the chart
        margin=dict(l=20, r=20, t=60, b=140),
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={
            "family": "IBM Plex Sans, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
            "size": 15,
            # Move legend below the chart and allow wrapping across multiple rows
        },
        # Move legend below the chart and allow wrapping across multiple rows
        legend={
            "font": {"size": 11},
            "bgcolor": "rgba(0,0,0,0)",
            "orientation": "h",
            "yanchor": "top",
            "y": -0.26,
            "x": 0,
            "xanchor": "left",
            "tracegroupgap": 3,
            "itemwidth": 30,
        },
        barmode="relative",
        height=520,
        # Lift the title to the very top so it doesn't collide with the legend
        title={
            "y": 0.98,
            "x": 0.0,
            "xanchor": "left",
            "yanchor": "top",
            "pad": {"t": 2, "b": 0},
        },
    )
    fig_bar.update_xaxes(
        tickfont={"size": 12},
        gridcolor="#1f2937",
        zerolinecolor="#1f2937",
        tickangle=-30,
        automargin=True,
        title_standoff=4,
    )
    fig_bar.update_yaxes(
        tickfont={"size": 12}, gridcolor="#1f2937", zerolinecolor="#1f2937"
    )

    return fig_bar


if __name__ == "__main__":
    # Dash >=2.17 deprecates run_server in favor of run
    app.run(debug=True, port=int(os.getenv("PORT", "8050")))

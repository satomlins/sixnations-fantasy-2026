import os
import glob
from typing import List, Optional

import pandas as pd
import plotly.express as px
from dash import Dash, dcc, html, Input, Output, State, dash_table
import dash_bootstrap_components as dbc


def _latest_file(patterns: List[str]) -> Optional[str]:
    candidates: List[str] = []
    for pat in patterns:
        candidates.extend(glob.glob(pat))
    if not candidates:
        return None
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def load_latest_df() -> pd.DataFrame:
    os.makedirs("data", exist_ok=True)
    path = _latest_file(["data/*.parquet", "data/*.csv"])  # prefer parquet
    if path is None:
        raise FileNotFoundError(
            "No saved data found in ./data. Run the notebook cell that persists the DataFrame first."
        )
    if path.endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)

    # Basic sanity and derived columns
    if "team" not in df.columns:
        df["team"] = "Unknown"
    if "position" not in df.columns:
        df["position"] = "Unknown"
    if "name" not in df.columns:
        df["name"] = df.get("id", "Player").astype(str)

    # Opponent: infer if exactly two teams present (single match dump)
    teams = sorted(df["team"].dropna().unique().tolist())
    if len(teams) == 2:
        opp_map = {teams[0]: teams[1], teams[1]: teams[0]}
        df["opponent"] = df["team"].map(opp_map)
    else:
        df["opponent"] = None

    # Ensure numeric for points columns
    for c in df.columns:
        if c.endswith("_points") or c in ("points_total", "computed_points_total", "points_delta"):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df


def melt_points(df: pd.DataFrame) -> pd.DataFrame:
    """Return long DataFrame with columns: name, team, position, opponent, stat, points, value.

    - points: from <stat>_points columns
    - value: raw stat column (e.g., Try count, Tackles count, MetresCarried metres)
    Also includes pct per player for convenience (used when x='name').
    """
    point_cols = [c for c in df.columns if c.endswith("_points")]
    if not point_cols:
        return pd.DataFrame(columns=["name", "team", "position", "opponent", "stat", "points", "value", "pct"])  # empty

    # Long points
    points_long = df.melt(
        id_vars=["name", "team", "position", "opponent"],
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
            id_vars=["name", "team", "position", "opponent"],
            value_vars=value_cols,
            var_name="stat",
            value_name="value",
        )
    else:
        values_long = pd.DataFrame(columns=["name", "team", "position", "opponent", "stat", "value"]).assign(value=pd.NA)

    merged = points_long.merge(
        values_long, on=["name", "team", "position", "opponent", "stat"], how="left"
    )

    # Compute per-player totals for % if we later plot by player
    totals = (
        merged.groupby(["name", "team", "position", "opponent"], as_index=False)["points"].sum()
        .rename(columns={"points": "sum_points"})
    )
    merged = merged.merge(totals, on=["name", "team", "position", "opponent"], how="left")
    merged["pct"] = (merged["points"] / merged["sum_points"].replace(0, pd.NA)) * 100
    merged["pct"] = merged["pct"].fillna(0.0)
    return merged


df = load_latest_df()
points_long = melt_points(df)

teams = sorted(df["team"].dropna().unique())
positions = sorted(df["position"].dropna().unique())
opponents = sorted(df["opponent"].dropna().unique()) if df["opponent"].notna().any() else []
players = sorted(df["name"].dropna().unique())

external_stylesheets = [dbc.themes.DARKLY]
app = Dash(__name__, external_stylesheets=external_stylesheets)
app.title = "Six Nations Fantasy – Scoring Explorer"


def control_card():
    return dbc.Card(
        dbc.CardBody([
            html.H5("Filters", className="card-title"),
            dbc.Row([
                dbc.Col([
                    dbc.Label("Team"),
                    dcc.Dropdown(
                        id="team-filter",
                        options=[{"label": t, "value": t} for t in teams],
                        value=teams,
                        multi=True,
                        placeholder="Select team(s)"
                    ),
                ], md=4),
                dbc.Col([
                    dbc.Label("Position"),
                    dcc.Dropdown(
                        id="position-filter",
                        options=[{"label": p, "value": p} for p in positions],
                        value=positions,
                        multi=True,
                        placeholder="Select position(s)"
                    ),
                ], md=4),
                dbc.Col([
                    dbc.Label("Opponent"),
                    dcc.Dropdown(
                        id="opponent-filter",
                        options=[{"label": o, "value": o} for o in opponents],
                        value=opponents if opponents else None,
                        multi=True,
                        placeholder="Select opponent(s)",
                        disabled=(len(opponents) == 0)
                    ),
                ], md=4),
            ], className="gy-2"),
            html.Hr(),
            dbc.Row([
                dbc.Col([
                    dbc.Label("Players"),
                    dcc.Dropdown(
                        id="player-filter",
                        options=[{"label": n, "value": n} for n in players],
                        value=[],
                        multi=True,
                        placeholder="Filter by player(s) (optional)"
                    ),
                ], md=6),
                dbc.Col([
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
                    ),
                ], md=3),
                dbc.Col([
                    dbc.Label("Stack Metric"),
                    dcc.RadioItems(
                        id="metric-mode",
                        options=[
                            {"label": "% of Player Total", "value": "pct"},
                            {"label": "Points", "value": "points"},
                        ],
                        value="pct",
                        inline=False,
                    ),
                ], md=3),
            ])
        ]), className="panel"
    )

## Removed summary card per feedback


app.layout = dbc.Container([
    dbc.Navbar(
        dbc.Container([
            dbc.NavbarBrand("Six Nations Fantasy – Scoring Explorer", className="ms-2 fw-bold"),
        ]),
        dark=True, className="mb-4 rounded neo-navbar"
    ),
    dbc.Row([
        dbc.Col(control_card(), md=12),
], className="g-3"),
    dbc.Row([
        dbc.Col(dbc.Card(dbc.CardBody([
            html.H5("Breakdown", className="card-title"),
            dcc.Graph(id="stacked-bar", className="neo-graph")
        ]), className="panel"), md=12),
], className="g-3"),
    dbc.Row([
        dbc.Col(dbc.Card(dbc.CardBody([
            html.H5("Detailed Table", className="card-title"),
            dash_table.DataTable(
                id="detail-table",
                columns=[{"name": c, "id": c} for c in (
                    ["name", "team", "opponent", "position", "points_total", "computed_points_total", "points_delta"] +
                    [c for c in df.columns if c.endswith("_points")]
                )],
                data=[],
                filter_action="native",
                page_size=15,
                sort_action="native",
                style_table={"overflowX": "auto"},
                style_cell={
                    "textAlign": "left",
                    "minWidth": "120px",
                    "maxWidth": "220px",
                    "whiteSpace": "normal",
                },
            )
        ]), className="panel"), md=12),
], className="g-3"),
], fluid=True, className="neo-container")


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
    Output("detail-table", "data"),
    Input("team-filter", "value"),
    Input("position-filter", "value"),
    Input("opponent-filter", "value"),
    Input("player-filter", "value"),
    Input("xaxis-group", "value"),
    Input("metric-mode", "value"),
)
def refresh(teams_sel, positions_sel, opp_sel, players_sel, xaxis_group, metric_mode):
    # Apply filters and create long form for plotting
    dff = _apply_filters(df, teams_sel, positions_sel, opp_sel, players_sel)
    longf = melt_points(dff)

    # Guard empty data
    if longf.empty:
        empty_fig = px.bar(title="No data for current filters")
        empty_fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        return empty_fig, []

    # Validate x-axis group
    if xaxis_group not in {"name", "position", "team", "opponent"}:
        xaxis_group = "name"

    # Aggregate to group level
    dlong = longf.dropna(subset=[xaxis_group]).copy()
    agg = (
        dlong.groupby([xaxis_group, "stat"], as_index=False)
        .agg(points=("points", "sum"), value=("value", "sum"))
    )
    totals_by_group = agg.groupby(xaxis_group, as_index=False)["points"].sum().rename(columns={"points": "group_total"})
    agg = agg.merge(totals_by_group, on=xaxis_group, how="left")
    agg["pct"] = (agg["points"] / agg["group_total"].replace(0, pd.NA)) * 100
    agg["pct"] = agg["pct"].fillna(0.0)

    # Order groups by total points desc
    order = totals_by_group.sort_values("group_total", ascending=False)[xaxis_group].tolist()
    agg[xaxis_group] = pd.Categorical(agg[xaxis_group], categories=order, ordered=True)

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
    label_map = {"name": "Player", "position": "Position", "team": "Team", "opponent": "Opponent"}

    # Build stacked bar figure
    y_col = "pct" if metric_mode == "pct" else "points"
    bar_title = ("% Contribution by Stat" if metric_mode == "pct" else "Points by Stat") + f" by {label_map.get(xaxis_group, 'Player')}"

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
        custom_data=["stat_label", "points", "value"],
        title=bar_title,
        color_discrete_sequence=COLOR_SEQ,
        category_orders={"stat_label": stat_order},
    )
    hover_tmpl = (
        f"{label_map.get(xaxis_group, 'Player')}: %{{x}}<br>"
        "Stat: %{customdata[0]}<br>"
        "Points: %{customdata[1]:.1f}<br>"
    )
    if metric_mode == "pct":
        hover_tmpl += "%: %{y:.1f}<br>"
    hover_tmpl += "Count: %{customdata[2]:.0f}<extra></extra>"
    fig_bar.update_traces(hovertemplate=hover_tmpl)
    if metric_mode == "pct":
        fig_bar.update_yaxes(title_text="% of Group Total", range=[0, 100])
    else:
        fig_bar.update_yaxes(title_text="Points")
    fig_bar.update_layout(
        legend_title_text="Stat",
        xaxis_title=label_map.get(xaxis_group, "Player"),
        margin=dict(l=20, r=20, t=180, b=20),
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(
            family="Inter, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
            size=15,
            color="#f8fafc",
        ),
        legend=dict(font=dict(size=12), bgcolor="rgba(0,0,0,0)", orientation="h", yanchor="bottom", y=1.22, x=0, xanchor="left", tracegroupgap=12),
        barmode="relative",
        height=520,
        title=dict(y=0.94, x=0.0, xanchor="left", yanchor="top", pad=dict(t=4, b=0))
    )
    fig_bar.update_xaxes(tickfont=dict(size=12), gridcolor="#1f2937", zerolinecolor="#1f2937", tickangle=-30, automargin=True)
    fig_bar.update_yaxes(tickfont=dict(size=12), gridcolor="#1f2937", zerolinecolor="#1f2937")

    # Detail table data
    table_cols = [
        "name", "team", "opponent", "position", "points_total", "computed_points_total", "points_delta"
    ] + [c for c in dff.columns if c.endswith("_points")]
    table_data = dff[table_cols].to_dict("records") if not dff.empty else []

    return fig_bar, table_data


if __name__ == "__main__":
    # Dash >=2.17 deprecates run_server in favor of run
    app.run(debug=True, port=int(os.getenv("PORT", "8050")))

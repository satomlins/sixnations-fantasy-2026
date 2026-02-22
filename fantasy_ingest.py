import argparse
import os
import re
import time
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Any, Optional

import duckdb
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:
    pass

BASE_URL = "https://fantasy.sixnationsrugby.com"
DEFAULT_X_ACCESS_KEY = "600@18.23@"
DEFAULT_MATCH_IDS: tuple[int, ...] = tuple(range(1, 16))
DEFAULT_MIN_REFRESH_SECONDS = 60
DEFAULT_DATA_DIR = "data"
DATA_DIR_ENV_VAR = "SIXNATIONS_DATA_DIR"
DEFAULT_DB_FILENAME = "all_matches.duckdb"
DEFAULT_REFRESH_STATE_FILENAME = ".last_api_refresh"
DEFAULT_MAX_JOURNEES = 10
SEARCHJOUEURS_PAGE_SIZE = 100

STARTER_STATUS_STARTER = "T"
STARTER_STATUS_SUB = "R"
STARTER_STATUS_DNP = "N"

CONSISTENT_POINTS_EXCLUDED_STATS: tuple[str, ...] = (
    "Try",
    "YellowCard",
    "RedCard",
    "PlayerOfTheMatch",
    "LineoutSteal",
)

# Backward-compatible alias for older imports.
GOOD_POINTS_EXCLUDED_STATS = CONSISTENT_POINTS_EXCLUDED_STATS

POSITION_MAP = {
    "6": "Back-three",
    "7": "Centre",
    "8": "Fly-half",
    "9": "Scrum-half",
    "10": "Back-row",
    "11": "Second-row",
    "12": "Prop",
    "13": "Hooker",
}

BACKS = {"Back-three", "Centre", "Fly-half", "Scrum-half"}

SCORING = {
    "Try": None,
    "Assists": 4,
    "Conversion": 2,
    "Penalty": 3,
    "DropGoal": 5,
    "DefendersBeaten": 2,
    "MetresCarried": 0.1,
    "FiftyTwentyTwo": 7,
    "KicksRecovered": 2,
    "Offloads": 2,
    "AttackingScrumWin": 1,
    "Tackles": 1,
    "BreakdownSteal": 5,
    "LineoutSteal": 7,
    "PenaltyConceded": -1,
    "PlayerOfTheMatch": 15,
    "YellowCard": -5,
    "RedCard": -8,
    "Minutes": 0,
}

STAT_NAME_MAP = {
    "Min": "Minutes",
    "T": "Try",
    "As": "Assists",
    "C": "Conversion",
    "Pen": "Penalty",
    "MC": "MetresCarried",
    "DB": "DefendersBeaten",
    "Ta": "Tackles",
    "CPen": "PenaltyConceded",
    "50-22": "FiftyTwentyTwo",
    "KR": "KicksRecovered",
    "DG": "DropGoal",
    "OF": "Offloads",
    "LS": "LineoutSteal",
    "BS": "BreakdownSteal",
    "POTM": "PlayerOfTheMatch",
    "SW": "AttackingScrumWin",
    "YC": "YellowCard",
    "RC": "RedCard",
}

CONSISTENT_POINTS_EXCLUDED_COLUMNS = {
    f"{stat}_points" for stat in CONSISTENT_POINTS_EXCLUDED_STATS
}
GOOD_POINTS_EXCLUDED_COLUMNS = CONSISTENT_POINTS_EXCLUDED_COLUMNS
DERIVED_POINT_COLUMNS = {"total_points", "consistent_points", "good_points"}
_LOOKUP_NORMALISER_RE = re.compile(r"[^a-z0-9]+")


def get_data_dir() -> str:
    raw = os.getenv(DATA_DIR_ENV_VAR, DEFAULT_DATA_DIR).strip()
    return raw or DEFAULT_DATA_DIR


def get_default_db_path() -> str:
    return os.path.join(get_data_dir(), DEFAULT_DB_FILENAME)


def get_default_refresh_state_path() -> str:
    return os.path.join(get_data_dir(), DEFAULT_REFRESH_STATE_FILENAME)


def _build_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def _normalise_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    token_str = str(token).strip()
    if not token_str:
        return None
    if token_str.lower().startswith("token "):
        return token_str
    return f"Token {token_str}"


def _read_last_refresh_epoch(state_path: str) -> Optional[float]:
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
        if not raw:
            return None
        return float(raw)
    except (FileNotFoundError, ValueError, OSError):
        return None


def _write_last_refresh_epoch(state_path: str, epoch: float) -> None:
    state_dir = os.path.dirname(state_path)
    if state_dir:
        os.makedirs(state_dir, exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as fh:
        fh.write(str(float(epoch)))


def _seconds_until_refresh_allowed(
    min_interval_seconds: int,
    state_path: str,
) -> float:
    if min_interval_seconds <= 0:
        return 0.0
    last_epoch = _read_last_refresh_epoch(state_path)
    if last_epoch is None:
        return 0.0
    elapsed = time.time() - last_epoch
    return max(0.0, float(min_interval_seconds) - elapsed)


def _build_api_headers(
    token: Optional[str] = None,
    x_access_key: Optional[str] = None,
    include_content_type: bool = True,
) -> dict[str, str]:
    headers = {
        "accept": "application/json",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "x-access-key": x_access_key
        or os.getenv("SIXNATIONS_X_ACCESS_KEY", DEFAULT_X_ACCESS_KEY),
    }
    if include_content_type:
        headers["content-type"] = "application/json"

    auth = _normalise_token(token or os.getenv("SIXNATIONS_TOKEN"))
    if auth:
        headers["authorization"] = auth
    return headers


def _normalise_lookup_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = _LOOKUP_NORMALISER_RE.sub(" ", text)
    return " ".join(text.split())


def _decode_starter_flag(status_code: Optional[str]) -> Optional[bool]:
    if not status_code:
        return None
    code = str(status_code).strip().upper()
    if code == STARTER_STATUS_STARTER:
        return True
    if code in {STARTER_STATUS_SUB, STARTER_STATUS_DNP}:
        return False
    return None


def _fetch_current_journee_id(
    language: str = "en",
    token: Optional[str] = None,
    x_access_key: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Optional[int]:
    request_url = f"{BASE_URL}/v1/private/journee?lg={language}"
    headers = _build_api_headers(
        token=token,
        x_access_key=x_access_key,
        include_content_type=False,
    )

    local_session = session or _build_session()
    close_local_session = session is None
    try:
        response = local_session.get(
            request_url,
            headers=headers,
            allow_redirects=True,
            timeout=20,
        )
    finally:
        if close_local_session:
            local_session.close()

    if response.status_code in (401, 403):
        raise RuntimeError(
            "Unauthorized (401/403). Ensure SIXNATIONS_TOKEN is set correctly."
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return None
    journee = payload.get("journee")
    if not isinstance(journee, dict):
        return None
    try:
        return int(journee.get("id"))
    except (TypeError, ValueError):
        return None


def _fetch_journee_round_map(
    language: str = "en",
    token: Optional[str] = None,
    x_access_key: Optional[str] = None,
    max_journees: int = DEFAULT_MAX_JOURNEES,
    session: Optional[requests.Session] = None,
) -> dict[int, int]:
    headers = _build_api_headers(
        token=token,
        x_access_key=x_access_key,
        include_content_type=False,
    )
    local_session = session or _build_session()
    close_local_session = session is None

    round_by_match_id: dict[int, int] = {}
    found_any = False
    empty_streak = 0

    try:
        for journee_id in range(1, max(1, int(max_journees)) + 1):
            request_url = (
                f"{BASE_URL}/v1/private/journeecalendrier/{journee_id}?lg={language}"
            )
            response = local_session.get(
                request_url,
                headers=headers,
                allow_redirects=True,
                timeout=20,
            )

            if response.status_code in (401, 403):
                raise RuntimeError(
                    "Unauthorized (401/403). Ensure SIXNATIONS_TOKEN is set correctly."
                )
            response.raise_for_status()

            payload = response.json()
            journee = payload.get("journee", {}) if isinstance(payload, dict) else {}
            matches = journee.get("matchs") or []

            if not matches:
                if found_any:
                    empty_streak += 1
                    if empty_streak >= 2:
                        break
                continue

            found_any = True
            empty_streak = 0

            for match_obj in matches:
                try:
                    match_id = int(match_obj.get("id"))
                except (TypeError, ValueError):
                    continue
                round_by_match_id[match_id] = journee_id
    finally:
        if close_local_session:
            local_session.close()

    return round_by_match_id


def _fetch_searchjoueurs_players(
    journee_id: int,
    language: str = "en",
    token: Optional[str] = None,
    x_access_key: Optional[str] = None,
    page_size: int = SEARCHJOUEURS_PAGE_SIZE,
    session: Optional[requests.Session] = None,
) -> list[dict[str, Any]]:
    request_url = f"{BASE_URL}/v1/private/searchjoueurs?lg={language}"
    headers = _build_api_headers(
        token=token,
        x_access_key=x_access_key,
        include_content_type=True,
    )

    local_session = session or _build_session()
    close_local_session = session is None

    players: list[dict[str, Any]] = []
    total_expected: Optional[int] = None
    current_page_size = max(1, int(page_size))
    page_index = 0

    try:
        while True:
            payload = {
                "filters": {
                    "nom": "",
                    "club": "",
                    "position": "",
                    "budget_ok": False,
                    "valeur_max": 25,
                    "engage": False,
                    "partant": False,
                    "dreamteam": False,
                    "quota": "",
                    "idj": str(journee_id),
                    "pageIndex": page_index,
                    "pageSize": current_page_size,
                    "loadSelect": 0,
                    "searchonly": 1,
                }
            }
            response = local_session.post(
                request_url,
                headers=headers,
                json=payload,
                allow_redirects=True,
                timeout=20,
            )

            if response.status_code in (401, 403):
                raise RuntimeError(
                    "Unauthorized (401/403). Ensure SIXNATIONS_TOKEN is set correctly."
                )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                break

            page_players = data.get("joueurs") or []
            if not isinstance(page_players, list) or not page_players:
                break

            players.extend([p for p in page_players if isinstance(p, dict)])

            if total_expected is None:
                raw_total = data.get("total")
                try:
                    total_expected = int(str(raw_total).strip())
                except (TypeError, ValueError):
                    total_expected = None

            page_index += 1
            if total_expected is not None and len(players) >= total_expected:
                break
    finally:
        if close_local_session:
            local_session.close()

    return players


def _build_starter_history_lookup(
    players: Sequence[dict[str, Any]],
) -> dict[tuple[str, str], dict[int, str]]:
    lookup: dict[tuple[str, str], dict[int, str]] = {}

    for player in players:
        player_name = _normalise_lookup_text(player.get("nom"))
        team_name = _normalise_lookup_text(player.get("club"))
        if not player_name or not team_name:
            continue

        forme = player.get("forme")
        items = forme.get("items") if isinstance(forme, dict) else []
        if not isinstance(items, list):
            items = []

        history: dict[int, str] = {}
        for idx, code in enumerate(items, start=1):
            norm_code = str(code).strip().upper()
            if not norm_code:
                continue
            history[idx] = norm_code

        if not history:
            continue

        key = (player_name, team_name)
        existing = lookup.get(key)
        if existing is None or len(history) > len(existing):
            lookup[key] = history

    return lookup


def _infer_round_map_from_match_ids(
    match_ids: Iterable[Any], fixtures_per_round: int = 3
) -> dict[int, int]:
    parsed_match_ids: list[int] = []
    for match_id in match_ids:
        try:
            parsed_match_ids.append(int(match_id))
        except (TypeError, ValueError):
            continue

    unique_match_ids = sorted(set(parsed_match_ids))
    if not unique_match_ids:
        return {}

    matches_per_round = max(1, int(fixtures_per_round))
    return {
        match_id: (index // matches_per_round) + 1
        for index, match_id in enumerate(unique_match_ids)
    }


def annotate_starter_status(
    df: pd.DataFrame,
    language: str = "en",
    token: Optional[str] = None,
    x_access_key: Optional[str] = None,
    verbose: bool = True,
) -> tuple[pd.DataFrame, list[str]]:
    notices: list[str] = []

    if df.empty:
        df = df.copy()
        df["round"] = pd.Series(dtype="Int64")
        df["starter_status"] = pd.Series(dtype="object")
        df["is_starter"] = pd.Series(dtype="boolean")
        df["is_substitute"] = pd.Series(dtype="boolean")
        df["position_fixture"] = pd.Series(dtype="object")
        return df, notices

    required_cols = {"match_id", "name", "team", "position"}
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        msg = f"Starter/sub enrichment skipped: missing columns {missing_cols}."
        notices.append(msg)
        if verbose:
            print(msg)
        out = df.copy()
        out["round"] = pd.Series([pd.NA] * len(out), index=out.index, dtype="Int64")
        out["starter_status"] = pd.NA
        out["is_starter"] = pd.array([pd.NA] * len(out), dtype="boolean")
        out["is_substitute"] = pd.array([pd.NA] * len(out), dtype="boolean")
        out["position_fixture"] = out.get("position", pd.Series(dtype="object"))
        return out, notices

    inferred_round_by_match_id = _infer_round_map_from_match_ids(df["match_id"])

    session = _build_session()
    try:
        current_journee_id = _fetch_current_journee_id(
            language=language,
            token=token,
            x_access_key=x_access_key,
            session=session,
        )
        if current_journee_id is None:
            raise RuntimeError("could not determine current journee id")

        round_by_match_id = _fetch_journee_round_map(
            language=language,
            token=token,
            x_access_key=x_access_key,
            session=session,
        )
        if not round_by_match_id:
            raise RuntimeError("no journee/match mapping returned")

        search_players = _fetch_searchjoueurs_players(
            journee_id=current_journee_id,
            language=language,
            token=token,
            x_access_key=x_access_key,
            session=session,
        )
        starter_lookup = _build_starter_history_lookup(search_players)
        if not starter_lookup:
            raise RuntimeError("no starter history rows returned")
    except Exception as exc:
        msg = f"Starter/sub enrichment skipped due to error -> {exc}"
        notices.append(msg)
        if verbose:
            print(msg)
        out = df.copy()
        if inferred_round_by_match_id:
            numeric_match_ids = pd.to_numeric(out["match_id"], errors="coerce")
            out["round"] = numeric_match_ids.map(inferred_round_by_match_id).astype(
                "Int64"
            )
        else:
            out["round"] = pd.Series([pd.NA] * len(out), index=out.index, dtype="Int64")
        out["starter_status"] = pd.NA
        out["is_starter"] = pd.array([pd.NA] * len(out), dtype="boolean")
        out["is_substitute"] = pd.array([pd.NA] * len(out), dtype="boolean")
        out["position_fixture"] = out.get("position", pd.Series(dtype="object"))
        return out, notices
    finally:
        session.close()

    out = df.copy()
    round_numbers: list[Optional[int]] = []
    starter_statuses: list[Optional[str]] = []

    for match_id, player_name, team_name in zip(
        out["match_id"], out["name"], out["team"]
    ):
        try:
            match_id_int = int(match_id)
        except (TypeError, ValueError):
            round_numbers.append(None)
            starter_statuses.append(None)
            continue

        round_number = round_by_match_id.get(match_id_int)
        if round_number is None:
            round_number = inferred_round_by_match_id.get(match_id_int)
        round_numbers.append(int(round_number) if round_number is not None else None)
        if round_number is None:
            starter_statuses.append(None)
            continue

        player_key = (
            _normalise_lookup_text(player_name),
            _normalise_lookup_text(team_name),
        )
        player_history = starter_lookup.get(player_key) or {}
        starter_statuses.append(player_history.get(int(round_number)))

    out["round"] = pd.array(round_numbers, dtype="Int64")
    out["starter_status"] = pd.Series(starter_statuses, index=out.index, dtype="object")
    out["is_starter"] = pd.array(
        [_decode_starter_flag(code) for code in starter_statuses],
        dtype="boolean",
    )
    out["is_substitute"] = pd.array(
        [
            (str(code).strip().upper() == STARTER_STATUS_SUB)
            if code not in (None, "")
            else pd.NA
            for code in starter_statuses
        ],
        dtype="boolean",
    )

    base_position = (
        out["position"]
        .fillna("")
        .astype(str)
        .str.strip()
    )
    out["position"] = base_position
    out["position_fixture"] = base_position
    sub_mask = out["starter_status"] == STARTER_STATUS_SUB
    out.loc[sub_mask, "position_fixture"] = base_position[sub_mask] + " sub"

    matched_count = int(out["starter_status"].notna().sum())
    if verbose:
        print(f"Starter/sub enrichment matched {matched_count}/{len(out)} rows.")
    if matched_count < len(out):
        notices.append(
            f"Starter/sub enrichment: {len(out) - matched_count} rows had no status mapping."
        )

    return out, notices


def fetch_match_payload(
    match_id: int,
    language: str = "en",
    token: Optional[str] = None,
    x_access_key: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> dict[str, Any]:
    """Fetch one match payload from the Six Nations private fantasy API."""
    request_url = f"{BASE_URL}/v1/private/match/{match_id}?lg={language}"
    headers = _build_api_headers(
        token=token,
        x_access_key=x_access_key,
        include_content_type=True,
    )

    local_session = session or _build_session()
    close_local_session = session is None

    try:
        response = local_session.get(
            request_url,
            headers=headers,
            allow_redirects=True,
            timeout=20,
        )
    finally:
        if close_local_session:
            local_session.close()

    if response.status_code in (401, 403):
        raise RuntimeError(
            "Unauthorized (401/403). Ensure SIXNATIONS_TOKEN is set correctly."
        )
    response.raise_for_status()
    return response.json()


def fetch_available_match_payloads(
    match_ids: Iterable[int] = DEFAULT_MATCH_IDS,
    language: str = "en",
    token: Optional[str] = None,
    x_access_key: Optional[str] = None,
    verbose: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    payloads: list[dict[str, Any]] = []
    notices: list[str] = []
    session = _build_session()

    try:
        for raw_match_id in match_ids:
            match_id = int(raw_match_id)
            try:
                payload = fetch_match_payload(
                    match_id=match_id,
                    language=language,
                    token=token,
                    x_access_key=x_access_key,
                    session=session,
                )
            except Exception as exc:
                msg = f"Match {match_id}: skipping due to error -> {exc}"
                notices.append(msg)
                if verbose:
                    print(msg)
                continue

            match_obj = payload.get("match", {}) if isinstance(payload, dict) else {}
            dom_players = match_obj.get("joueursdom") or []
            ext_players = match_obj.get("joueursext") or []
            if not dom_players and not ext_players:
                msg = f"Match {match_id}: no player data yet, skipping."
                notices.append(msg)
                if verbose:
                    print(msg)
                continue

            payloads.append({"match_id": match_id, "data": payload})
            if verbose:
                print(f"Fetched match {match_id}")
    finally:
        session.close()

    return payloads, notices


def _to_int_or_zero(value: Any) -> int:
    try:
        if value is None:
            return 0
        if isinstance(value, int):
            return value
        return int(float(str(value).strip().replace(",", "")))
    except Exception:
        return 0


def _to_float_or_zero(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        return float(str(value).strip().replace(",", ""))
    except Exception:
        return 0.0


def _pick_from_dict(data: dict[str, Any], keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        if key in data and data[key]:
            return str(data[key])
    return None


def extract_team_names(match_obj: dict[str, Any]) -> tuple[str, str]:
    dom_name = match_obj.get("clubdom") or None
    ext_name = match_obj.get("clubext") or None

    dom_keys = (
        "clubdom",
        "nomdom",
        "paysdom",
        "home_team",
        "homeTeam",
        "equipeDom",
        "equipe_dom",
    )
    ext_keys = (
        "clubext",
        "nomext",
        "paysext",
        "away_team",
        "awayTeam",
        "equipeExt",
        "equipe_ext",
    )

    def as_name(value: Any) -> Optional[str]:
        if isinstance(value, dict):
            return _pick_from_dict(
                value,
                (
                    "nom",
                    "name",
                    "libelle",
                    "label",
                    "pays",
                    "country",
                    "display_name",
                    "shortName",
                    "short_name",
                ),
            )
        if value is None:
            return None
        return str(value)

    for key in dom_keys:
        if dom_name:
            break
        candidate = as_name(match_obj.get(key))
        if candidate:
            dom_name = candidate
            break

    for key in ext_keys:
        if ext_name:
            break
        candidate = as_name(match_obj.get(key))
        if candidate:
            ext_name = candidate
            break

    return str(dom_name or "Home"), str(ext_name or "Away")


def flatten_players(
    players: Sequence[dict[str, Any]],
    team_name: str,
    opponent_name: Optional[str],
    legend: Sequence[dict[str, Any]],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    legend_map = {
        index: STAT_NAME_MAP.get(legend_item.get("label_short", ""), f"crit_{index}")
        for index, legend_item in enumerate(legend)
    }

    for player in players:
        pos_name = POSITION_MAP.get(str(player.get("position", "")), str(player.get("position", "")))
        row: dict[str, Any] = {
            "id": player.get("id"),
            "name": player.get("nom"),
            "team": team_name,
            "opponent": opponent_name,
            "position": pos_name,
            "points_total": player.get("points", 0),
        }

        for index, crit in enumerate(player.get("criteres", [])):
            stat_col = legend_map.get(index, f"crit_{index}")
            raw_val = crit.get("value")
            val_num = (
                _to_float_or_zero(raw_val)
                if stat_col == "MetresCarried"
                else _to_int_or_zero(raw_val)
            )
            row[stat_col] = val_num

            if stat_col == "Try":
                row["Try_points"] = (10 if pos_name in BACKS else 15) * int(val_num)
            elif stat_col == "MetresCarried":
                row["MetresCarried_points"] = int(val_num // 10)
            elif stat_col in SCORING:
                row[f"{stat_col}_points"] = int(val_num) * SCORING[stat_col]

        rows.append(row)

    return pd.DataFrame(rows)


def _ordered_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    base_cols = [
        "match_id",
        "id",
        "name",
        "team",
        "opponent",
        "position",
        "points_total",
    ]
    ordered_stats = [
        "Minutes",
        "Try",
        "Assists",
        "Conversion",
        "Penalty",
        "MetresCarried",
        "DefendersBeaten",
        "Tackles",
        "PenaltyConceded",
        "FiftyTwentyTwo",
        "KicksRecovered",
        "DropGoal",
        "Offloads",
        "LineoutSteal",
        "BreakdownSteal",
        "PlayerOfTheMatch",
        "AttackingScrumWin",
        "YellowCard",
        "RedCard",
    ]

    ordered_cols = [stat for stat in ordered_stats if stat in df.columns]
    for stat in ordered_stats:
        pts_col = f"{stat}_points"
        if pts_col in df.columns:
            ordered_cols.append(pts_col)

    trailing = [col for col in df.columns if col not in base_cols + ordered_cols]
    return df[base_cols + ordered_cols + trailing]


def finalize_points_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Compute total/computed/consistent points columns on a player dataframe."""
    numeric_cols = [
        col
        for col in df.columns
        if col not in {"id", "name", "team", "position", "opponent"}
    ]
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")

    point_cols = [
        col
        for col in df.columns
        if col.endswith("_points") and col not in DERIVED_POINT_COLUMNS
    ]
    if point_cols:
        df[point_cols] = df[point_cols].fillna(0)

    if "points_total" not in df.columns:
        df["points_total"] = 0
    df["points_total"] = pd.to_numeric(df["points_total"], errors="coerce").fillna(0)

    df["computed_points_total"] = df[point_cols].sum(axis=1) if point_cols else 0
    df["total_points"] = df["points_total"]

    consistent_point_cols = [
        col for col in point_cols if col not in CONSISTENT_POINTS_EXCLUDED_COLUMNS
    ]
    df["consistent_points"] = (
        df[consistent_point_cols].sum(axis=1) if consistent_point_cols else 0
    )
    # Backward-compatible alias.
    df["good_points"] = df["consistent_points"]

    df["points_delta"] = df["total_points"] - df["computed_points_total"]
    return df


def build_players_dataframe(payload_items: Sequence[dict[str, Any]]) -> pd.DataFrame:
    df_parts: list[pd.DataFrame] = []

    for item in payload_items:
        match_id = item["match_id"]
        match_obj = item["data"].get("match", {})
        legend = match_obj.get("legende", [])
        dom_players = match_obj.get("joueursdom") or []
        ext_players = match_obj.get("joueursext") or []
        team_dom, team_ext = extract_team_names(match_obj)

        dfd = (
            flatten_players(dom_players, team_dom, team_ext, legend)
            if dom_players
            else pd.DataFrame()
        )
        dfe = (
            flatten_players(ext_players, team_ext, team_dom, legend)
            if ext_players
            else pd.DataFrame()
        )

        if dfd.empty and dfe.empty:
            continue

        dfi = pd.concat([dfd, dfe], ignore_index=True)
        dfi["match_id"] = match_id
        df_parts.append(dfi)

    if not df_parts:
        raise RuntimeError("No usable player rows found across requested matches.")

    df = pd.concat(df_parts, ignore_index=True, sort=True)
    df = _ordered_dataframe(df)
    df = finalize_points_columns(df)
    return df


def _qident(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def load_duckdb_table(
    db_path: Optional[str] = None,
    table_name: str = "all_matches",
) -> pd.DataFrame:
    if db_path is None:
        db_path = get_default_db_path()
    if not os.path.exists(db_path):
        return pd.DataFrame()
    con = duckdb.connect(db_path, read_only=True)
    try:
        table_exists = con.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_name = ?
            """,
            [table_name],
        ).fetchone()[0]
        if table_exists == 0:
            return pd.DataFrame()
        return con.execute(f"SELECT * FROM {_qident(table_name)}").df()
    finally:
        con.close()


def upsert_dataframe_to_duckdb(
    df: pd.DataFrame,
    db_path: Optional[str] = None,
    table_name: str = "all_matches",
    replace_match_ids: Optional[Iterable[int]] = None,
) -> None:
    if db_path is None:
        db_path = get_default_db_path()
    directory = os.path.dirname(db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    table_ident = _qident(table_name)

    con = duckdb.connect(db_path)
    try:
        con.register("df", df)
        con.execute(f"CREATE TABLE IF NOT EXISTS {table_ident} AS SELECT * FROM df LIMIT 0")

        required_keys = ["match_id", "id"]
        missing_keys = [key for key in required_keys if key not in df.columns]
        if missing_keys:
            raise RuntimeError(f"Missing key columns in df: {missing_keys}")

        existing_cols = [
            row[0]
            for row in con.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'main' AND table_name = ?
                ORDER BY ordinal_position
                """,
                [table_name],
            ).fetchall()
        ]

        df_cols = list(df.columns)

        desc_df = con.execute("DESCRIBE SELECT * FROM df").df()
        name_key = "column_name" if "column_name" in desc_df.columns else "name"
        type_key = "column_type" if "column_type" in desc_df.columns else "type"
        df_types = {}
        if name_key in desc_df.columns and type_key in desc_df.columns:
            df_types = dict(zip(desc_df[name_key], desc_df[type_key]))

        for col in df_cols:
            if col in existing_cols:
                continue
            col_type = df_types.get(col, "VARCHAR")
            con.execute(f"ALTER TABLE {table_ident} ADD COLUMN {_qident(col)} {col_type}")
            existing_cols.append(col)

        select_exprs = []
        for col in existing_cols:
            if col in df_cols:
                select_exprs.append(_qident(col))
            else:
                select_exprs.append(f"NULL AS {_qident(col)}")
        con.execute(
            f"CREATE OR REPLACE TEMP VIEW df_aligned AS SELECT {', '.join(select_exprs)} FROM df"
        )

        if replace_match_ids is not None:
            for match_id in sorted({int(mid) for mid in replace_match_ids}):
                con.execute(
                    f"DELETE FROM {table_ident} WHERE match_id = ?",
                    [match_id],
                )
        else:
            con.execute(
                f"""
                DELETE FROM {table_ident} AS target
                USING df_aligned AS src
                WHERE target.match_id = src.match_id
                  AND target.id = src.id
                """
            )
        con.execute(f"INSERT INTO {table_ident} SELECT * FROM df_aligned")

        # Backfill derived totals for legacy rows that were already in the table.
        if "total_points" in existing_cols and "points_total" in existing_cols:
            con.execute(
                f"""
                UPDATE {table_ident}
                SET total_points = COALESCE(total_points, points_total, 0)
                """
            )

        consistent_cols = [
            col
            for col in existing_cols
            if col.endswith("_points")
            and col not in CONSISTENT_POINTS_EXCLUDED_COLUMNS
            and col not in DERIVED_POINT_COLUMNS
        ]

        if "consistent_points" in existing_cols:
            legacy_consistent_source = (
                "good_points" if "good_points" in existing_cols else "NULL"
            )
            if consistent_cols:
                consistent_expr = " + ".join(
                    [f"COALESCE({_qident(col)}, 0)" for col in consistent_cols]
                )
                con.execute(
                    f"""
                    UPDATE {table_ident}
                    SET consistent_points = COALESCE(consistent_points, {legacy_consistent_source}, {consistent_expr})
                    """
                )
            else:
                con.execute(
                    f"""
                    UPDATE {table_ident}
                    SET consistent_points = COALESCE(consistent_points, {legacy_consistent_source}, 0)
                    """
                )

        if "good_points" in existing_cols and "consistent_points" in existing_cols:
            con.execute(
                f"""
                UPDATE {table_ident}
                SET good_points = COALESCE(good_points, consistent_points, 0)
                """
            )
        elif "good_points" in existing_cols:
            good_cols = [
                col
                for col in existing_cols
                if col.endswith("_points")
                and col not in CONSISTENT_POINTS_EXCLUDED_COLUMNS
                and col not in DERIVED_POINT_COLUMNS
            ]
            if good_cols:
                good_expr = " + ".join(
                    [f"COALESCE({_qident(col)}, 0)" for col in good_cols]
                )
                con.execute(
                    f"""
                    UPDATE {table_ident}
                    SET good_points = COALESCE(good_points, {good_expr})
                    """
                )
            else:
                con.execute(
                    f"""
                    UPDATE {table_ident}
                    SET good_points = COALESCE(good_points, 0)
                    """
                )
    finally:
        con.close()


def refresh_all_matches(
    match_ids: Iterable[int] = DEFAULT_MATCH_IDS,
    language: str = "en",
    token: Optional[str] = None,
    x_access_key: Optional[str] = None,
    db_path: Optional[str] = None,
    table_name: str = "all_matches",
    min_interval_seconds: int = DEFAULT_MIN_REFRESH_SECONDS,
    refresh_state_path: Optional[str] = None,
    allow_cached_on_rate_limit: bool = True,
    verbose: bool = True,
) -> tuple[pd.DataFrame, list[str]]:
    if db_path is None:
        db_path = get_default_db_path()
    if refresh_state_path is None:
        refresh_state_path = get_default_refresh_state_path()
    match_ids = [int(mid) for mid in match_ids]

    wait_seconds = _seconds_until_refresh_allowed(
        min_interval_seconds=min_interval_seconds,
        state_path=refresh_state_path,
    )
    if wait_seconds > 0:
        msg = (
            f"Refresh skipped due to 60s API throttle. Retry in {wait_seconds:.0f}s."
            if min_interval_seconds == 60
            else f"Refresh skipped due to API throttle. Retry in {wait_seconds:.0f}s."
        )
        if allow_cached_on_rate_limit:
            cached_df = load_duckdb_table(db_path=db_path, table_name=table_name)
            if not cached_df.empty:
                if verbose:
                    print(msg)
                return cached_df, [msg]
        raise RuntimeError(msg)

    payloads, notices = fetch_available_match_payloads(
        match_ids=match_ids,
        language=language,
        token=token,
        x_access_key=x_access_key,
        verbose=verbose,
    )
    if not payloads:
        raise RuntimeError(
            "No matches returned with player data. Check SIXNATIONS_TOKEN and try again."
        )

    df = build_players_dataframe(payloads)
    df, starter_notices = annotate_starter_status(
        df,
        language=language,
        token=token,
        x_access_key=x_access_key,
        verbose=verbose,
    )
    if starter_notices:
        notices.extend(starter_notices)

    upsert_dataframe_to_duckdb(
        df,
        db_path=db_path,
        table_name=table_name,
        replace_match_ids=match_ids,
    )
    _write_last_refresh_epoch(refresh_state_path, time.time())
    return df, notices


def _parse_match_ids(raw_value: str) -> list[int]:
    parts = [p.strip() for p in raw_value.split(",") if p.strip()]
    ids: list[int] = []
    for part in parts:
        if "-" in part:
            start_s, end_s = [x.strip() for x in part.split("-", 1)]
            start_i, end_i = int(start_s), int(end_s)
            lo, hi = min(start_i, end_i), max(start_i, end_i)
            ids.extend(range(lo, hi + 1))
        else:
            ids.append(int(part))
    if not ids:
        return list(DEFAULT_MATCH_IDS)
    return sorted(set(ids))


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch Six Nations fantasy data and upsert it into DuckDB."
    )
    parser.add_argument(
        "--match-ids",
        default="1-15",
        help="Comma/range list, e.g. '1-5,8,10-12' (default: 1-15).",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="API language query parameter (default: en).",
    )
    parser.add_argument(
        "--db-path",
        default=get_default_db_path(),
        help=(
            "DuckDB path "
            f"(default: {get_default_db_path()}, overridable via {DATA_DIR_ENV_VAR})."
        ),
    )
    parser.add_argument(
        "--table-name",
        default="all_matches",
        help="DuckDB table name (default: all_matches).",
    )
    parser.add_argument(
        "--min-refresh-seconds",
        type=int,
        default=DEFAULT_MIN_REFRESH_SECONDS,
        help=f"Minimum seconds between API pulls (default: {DEFAULT_MIN_REFRESH_SECONDS}).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce log output.",
    )
    return parser


def main() -> None:
    args = _build_cli_parser().parse_args()
    match_ids = _parse_match_ids(args.match_ids)

    df, notices = refresh_all_matches(
        match_ids=match_ids,
        language=args.language,
        db_path=args.db_path,
        table_name=args.table_name,
        min_interval_seconds=max(0, int(args.min_refresh_seconds)),
        verbose=not args.quiet,
    )

    if notices and any("Refresh skipped" in n for n in notices):
        print(notices[0])
        print(f"Using cached data: {args.db_path} ({len(df)} player rows)")
    else:
        print(f"Saved: {args.db_path} ({len(df)} player rows)")
    if notices and not args.quiet:
        print(f"Notices: {len(notices)}")


if __name__ == "__main__":
    main()

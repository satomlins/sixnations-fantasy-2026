# sixnations-fantasy-2026 (fantasy6n)

Six Nations Fantasy dashboard (Dash) plus the ingest script that built its data. Live at https://fantasy6n.stomlins.com. The 2026 tournament is over: the site is an archive. What the code does is in `README.md`. Hosting conventions: `USING_ORACLE.md` in `../stomlins-oracle`.

Project knowledge (purpose, status, decisions, deployment plan, host, DNS and credentials' whereabouts, to-dos) lives in the brain: `notes/projects/sixnations-fantasy-2026.md` in `satomlins/the-brain` (`~/projects/the-brain` on the Mac, `~/the-brain` on the brain VM). This file holds code facts only.

> **DEPLOY = merge to `main`.** That is the whole process. The host polls GitHub every 3 minutes, checks out the new `main` and runs `host/install`; the result lands on ntfy topic `stomlins-oracle`.
>
> Production runs whatever `main` is. Never deploy from another branch. Because merging deploys, only merge when the user says so.

## The data cannot be regenerated

`/var/lib/fantasy6n/all_matches.duckdb` on the host is the only live copy of the season's data and it cannot be regenerated. Backups: `~/projects/stomlins-oracle/backups/all_matches.duckdb` (in the private stomlins-oracle repo) and `all_matches.duckdb.bak-2026-10-10` on the host.

- Never delete, move, overwrite or "refresh" that file. Do not run `fantasy6n-ingest` on the host.
- `host/install` never opens it and must stay that way. It only checks the file exists.
- A start of the dashboard can refresh from the API and write the database, depending on `SIXNATIONS_REFRESH_ON_START` and `SIXNATIONS_TOKEN` in the env file. So every restart is a small risk, and `host/install` restarts only when the running code changed. Think before merging a code change: that merge restarts the service.

## Styling

The page follows the shared design system (`~/projects/stomlins-design`; read its `GUIDE.md` before restyling).

- `assets/00-stomlins.css` is the core stylesheet, v0.2.0, and `stomlins_plotly.py` is the chart template, v0.1.0. Both are vendored unchanged: never edit them here, re-copy them from that repo. Dash loads `assets/` alphanumerically, so the `00-` prefix puts the core before `assets/styles.css`.
- `assets/styles.css` holds only what the core lacks: the filter grid, the Dash 4 dropdown and radio overrides, and the footer icon masks (`assets/icons/*.svg`).
- Site settings: `--accent: #45d9e7` (the cyan), `--label-case: lowercase`, lowercase wordmark, `--page-width: 1200px`.
- No Bootstrap and no web fonts: the layout is plain `html.*` components. `dash-bootstrap-components` is still listed in `pyproject.toml` and checked by `host/install` but nothing imports it; it was left in so that the restyle did not change the host's venv. Remove it in a change of its own.
- `app.py` calls `stomlins_plotly.register()` once, so every figure gets the template. The 18 stat colours in `app.py` and the cyan Minutes markers are data colours and stay.

## Commands

```bash
uv sync
uv run fantasy6n-dashboard   # local, needs a DuckDB file in ./data (or SIXNATIONS_DATA_DIR)
```

## Deployment

The deployment plan, host and public path are in the brain note (Deploy). In short: merging to `main` deploys, by the host-wide GitOps rule (`GITOPS.md` in `stomlins-oracle`); production runs whatever `main` is.

- Unit: `deploy/fantasy6n.service`, installed by `host/install`, gunicorn on `127.0.0.1:8051`. App dir on the host `/home/opc/fantasy6n`.
- Env file `/etc/sysconfig/fantasy6n` (secrets and settings, including `SIXNATIONS_DATA_DIR`), made by hand, not in git. Data: `/var/lib/fantasy6n/` (see above).

`host/install` is everything project-specific. Each run it:

1. Validates before changing anything: required files, the env file and the database exist, `systemd-analyze verify` passes on the unit, and from a synced venv `app.py`, `fantasy_ingest.py` and `stomlins_plotly.py` parse and their dependencies import. The modules are parsed, not imported, because importing `app.py` can trigger the API refresh.
2. Installs `deploy/fantasy6n.service` to `/etc/systemd/system/` if it differs. The unit file in the repo is the source of truth; do not edit the installed copy.
3. Restarts the service only when the running code changed (`app.py`, `fantasy_ingest.py`, `stomlins_plotly.py`, anything under `assets/`, `pyproject.toml`, `uv.lock` or the unit; hash kept in `/var/lib/fantasy6n-install/running.sha256`, deliberately outside the data directory). If that stamp is missing and the service is up, it adopts the running process and does not restart.
4. Fails unless `http://127.0.0.1:8051/` answers 200.

A doc-only merge (this file, `README.md`) changes nothing on the host.

When a deploy fails, the ntfy carries the last 20 lines of installer output, and that commit is not retried until `main` moves again or someone runs it by hand.

```bash
# On the host. Use the full path: sudo's secure_path omits /usr/local/bin.
sudo /usr/local/bin/gitops-pull status           # deployed SHA, main SHA, held/failed
sudo /usr/local/bin/gitops-pull run fantasy6n    # deploy now, or retry a failed commit
curl -sI https://fantasy6n.stomlins.com/
```

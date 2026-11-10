# BIDS data scraper (Mendelu coursework)

Nightly collector of real-estate listings and secondary open data for the BIDS
course project (South Moravian Region property market). Python 3.12; the user-
facing README, config comments and dashboard are in Czech, keep that.

- What it collects and why: [docs/project.md](docs/project.md)
- Operation, data model, extension: [README.md](README.md)

## Commands

```sh
pip install -r requirements.txt
python -m unittest discover -s tests -v     # offline; never touches the real portals
python -m collector --config <file> listings --source sreality --max-pages 1   # live check
cp .env.example .env && docker compose up --build   # local stack on http://127.0.0.1:8080
python -m collector pull                    # local copy of the live data; teammates use the one-liner in README
```

Snapshot tests create a symlink and fail on Windows without symlink rights;
they run in Linux CI.

## Layout

```text
collector/sources/   one module per portal: iter_listings() -> (record, raw payload)
collector/db.py      SQLite schema and views (views are recreated on every connect)
collector/trigger.py manual runs requested from the dashboard
collector/pull.py    `pull`: login, newest snapshot, files from the `download` table checked by SHA-256
config.yaml          sources, schedule, downloads; baked into the image
datasette/           table descriptions and canned queries for /db/
web/                 dashboard, login page + auth.py (password check), nginx.conf (non-root, :8080)
tests/fixtures/      trimmed portal responses, personal data replaced
```

## Invariants

- One image runs all four services (collector, Datasette, auth, nginx), because
  the server's deploy pins every service of the stack to one image digest. The
  server's Compose file, proxy and password `.env` live outside this public
  repository; keep `docker-compose.yml` here in step with them.
- The repository is shared with classmates and the teacher: no server paths,
  credentials or personal data, and test fixtures stay anonymised.
- Main CI tests, builds, smoke-tests and dispatches infra's Deploy with
  `service=bids` and the image digest. Pushing `main` deploys.
- Raw payloads are always stored so parsers can be fixed in ETL. Never store
  broker contacts or other personal data (see `DROP_DETAIL` in `sreality.py`).
- A source change must fail loudly (run `failed`), never save empty data. One
  failing source must not stop the others.
- Everything except /login, /auth/login, /auth/logout, /healthz and /pull.py (the script
  only, public on GitHub anyway) sits behind
  nginx `auth_request` to `web/auth.py`. Login is password-only: each
  `BIDS_PASSWORD_HASH_<ROLE>` is one password and the password picks the role.
  `auth.py` uses the standard library only.
- The dashboard's "Spustit sběr teď" posts to /api/run, served by the
  collector daemon itself (`collector/trigger.py`, port 8082, stack-internal):
  it only wakes the main loop, so manual and nightly scrapes never overlap,
  with a cooldown between manual runs.
- `collector/pull.py` is served as is at the public `/pull.py`, so teammates fetch and run it
  with one command (README). Keep it standard library only, Python 3.8+, with no imports from
  the package. It depends on the login form and cookie (`web/auth.py`), the JSON listing
  `/api/exports/`, snapshot names `collector-YYYY-MM-DD.sqlite.gz` and files served as
  `/files/<name>/<file>` with their SHA-256 in `download`; `tests/test_pull.py` and the CI
  smoke test (which runs the one-liner) cover that contract.
- Datasette's `base_url` only prefixes its links; nginx strips `/db/` before
  proxying.
- Every SQL in `web/index.html` and `datasette/metadata.yml` must run on the
  schema (`tests/test_queries.py`).
- Be polite to the portals: keep the request delay and the nightly schedule.

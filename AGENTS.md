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
docker compose up --build                   # local stack on http://127.0.0.1:8080
```

Snapshot tests create a symlink and fail on Windows without symlink rights;
they run in Linux CI.

## Layout

```text
collector/sources/   one module per portal: iter_listings() -> (record, raw payload)
collector/db.py      SQLite schema and views (views are recreated on every connect)
config.yaml          sources, schedule, downloads; baked into the image
datasette/           table descriptions and canned queries for /db/
web/                 dashboard (index.html) and nginx.conf (runs non-root on 8080)
tests/fixtures/      trimmed real portal responses
```

## Invariants

- One image runs all three services (collector, Datasette, nginx): infra's
  digest deploy pins every service of a stack to one image. Production Compose,
  Caddy route and login live in `pjunak/infra` (`stacks/bids`, `stacks/caddy`);
  keep `docker-compose.yml` here in step with `stacks/bids`.
- Main CI tests, builds, smoke-tests and dispatches infra's Deploy with
  `service=bids` and the image digest. Pushing `main` deploys.
- Raw payloads are always stored so parsers can be fixed in ETL. Never store
  broker contacts or other personal data (see `DROP_DETAIL` in `sreality.py`).
- A source change must fail loudly (run `failed`), never save empty data. One
  failing source must not stop the others.
- Datasette's `base_url` only prefixes its links; nginx strips `/db/` before
  proxying.
- Every SQL in `web/index.html` and `datasette/metadata.yml` must run on the
  schema (`tests/test_queries.py`).
- Be polite to the portals: keep the request delay and the nightly schedule.

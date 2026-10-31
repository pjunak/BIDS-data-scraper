# BIDS data scraper

Sběr dat pro projekt BIDS (trh s nemovitostmi v Jihomoravském kraji, Brno vs. okolí).

- **Každou noc** se stáhne snímek všech aktivních inzerátů ze **Sreality, Bezrealitek a iDNES Reality**
  (byty + domy, prodej + pronájem) a vytvoří se snapshot celé databáze ke stažení.
- **Týdně / měsíčně** se stahují **sekundární zdroje** (MF cenová mapa nájemného, jízdní řády IDS JMK, ČSÚ…)
  a archivuje se každá nová verze.
- **https://bids.junak.eu** (jen heslo, žádné jméno) ukazuje živý stav sběru, logy, prohlížení databáze
  přes SQL a stažení dat (celá SQLite databáze nebo CSV).

Zdroje, KPI a poznámky k ETL jsou v [docs/project.md](docs/project.md).

## Architektura

```
push do main ─▶ GitHub Actions: testy, build obrazu, smoke test ─▶ ghcr.io/pjunak/bids-data-scraper
                                                                     │ digest
                                                                     ▼
                                     pjunak/infra Deploy (stack „bids“) ─▶ server /opt/stacks/bids
Server: jeden obraz, čtyři služby
  ├─ bids-collector   python -m collector daemon (plánuje sám sebe)     ┐
  ├─ bids-datasette   živá databáze jen pro čtení, /db/                 ├─▶ ./data
  ├─ bids-auth        kontrola hesla a session (web/auth.py)            │
  └─ bids             nginx: přihlášení, přehled, /logs/, /files/, …    ┘
        ▲
Caddy (HTTPS) ◀── bids.junak.eu
```

**Kód** je v gitu, **data** jen na serveru v `/opt/stacks/bids/data`. Nasazení data neohrozí.
Produkční Compose soubor a směrování jsou v repozitáři `pjunak/infra` (`stacks/bids`, `stacks/caddy`);
`docker-compose.yml` tady slouží k lokálnímu běhu.

## Přihlášení

Web chce jen heslo. Každý řádek `BIDS_PASSWORD_HASH_<ROLE>` v `.env` je jedno heslo a heslo samo určuje
roli (zatím jen `user`, admin později = další řádek). Přihlášení platí 30 dní, změna hesla odhlásí všechny
s tou rolí, pokusů je nejvýš 10 za minutu z jedné adresy. Hash hesla vytvoří `python web/auth.py hash`
(na serveru viz `stacks/bids/README.md` v infra). Kontrolu dělá nginx (`auth_request`) proti službě
`auth`, takže je chráněné všechno včetně `/db/`, `/exports/`, `/files/` a `/logs/`.

## Struktura repozitáře

```
collector/            sběrač
  sources/            jeden modul = jeden realitní portál
  downloads.py        sekundární soubory (přímé URL nebo dohledání odkazů na stránce)
  snapshots.py        noční .sqlite.gz ke stažení
  db.py               schéma databáze a pohledy
config.yaml           co a kdy se stahuje (zdroje, URL, plán); je součástí obrazu
datasette/            popis tabulek a připravené dotazy pro /db/
web/                  přehled, přihlášení (login.html, auth.py) + nginx
tests/                offline testy, nikdy nesahají na skutečné portály; fixtures/ = zkrácené skutečné odpovědi
.github/workflows/    testy, build a předání obrazu do infra
```

## Data

| Tabulka / pohled | Obsah |
|---|---|
| `run` | každý běh: úloha, čas, `ok`/`failed`/`running`, počet položek, chyba |
| `listing_snapshot` | 1 řádek = 1 inzerát v 1 dni (cena, plocha, dispozice, lokalita, GPS) |
| `listing_lifecycle` | pohled: první/poslední výskyt, změny ceny → KPI doba inzerce a zlevnění |
| `listing_attributes` | pohled: stav, vlastnictví, energetická třída, pozemek, novostavba (Sreality, Bezrealitky) |
| `listing_payload` | surová data z portálu (JSON), ukládá se jen při změně |
| `listing_detail` | surový detail inzerátu (Sreality), jen pro nové inzeráty |
| `download` | stažené soubory sekundárních zdrojů (soubory v `files/`) |

Surová data se ukládají vždy, takže chybu parseru jde opravit později v ETL. Běhy `failed` v ETL ignorujte.
U pronájmu je `price` měsíční nájem. Kontakty na makléře se z detailů neukládají.

**Stažení dat** (vše na bids.junak.eu): celá databáze `/exports/collector-latest.sqlite.gz` (posledních 7 nocí
v `/exports/`), CSV libovolné tabulky nebo SQL dotazu přes `/db/`, sekundární soubory v `/files/`.

## Nasazení

Každý push do `main` spustí testy, sestaví obraz, ověří ho (nginx, Datasette, přehled) a teprve potom ho
publikuje do GHCR a předá jeho digest workflow **Deploy** v `pjunak/infra`. Pull requesty se jen testují.
Repozitář potřebuje proměnnou `INFRA_REPO` (`pjunak/infra`) a secret `INFRA_DISPATCH_TOKEN`.
Výsledek nasazení je v Actions repozitáře infra. Na server se odsud nikdo nepřipojuje.

Změna `config.yaml` (zdroje, čas sběru) je změna obrazu, tedy push sem.

## Příkazy

Na serveru (`cd /opt/stacks/bids`):

```bash
docker compose exec collector python -m collector status        # poslední běhy a počty
docker compose exec collector python -m collector listings [--source sreality] [--max-pages 2]
docker compose exec collector python -m collector downloads [--force] [--only gtfs_ids_jmk]
docker compose exec collector python -m collector snapshot       # snapshot databáze hned
docker compose exec collector python -m collector export --out /data/export --since 2026-11-01
docker compose logs -f collector
```

Lokálně:

```bash
python -m unittest discover -s tests -v       # testy (bez Dockeru, bez internetu)
cp .env.example .env                          # heslo change-me-please, nebo vlastní hash
docker compose up --build                     # celý projekt na http://127.0.0.1:8080, data v ./data
```

## Rozšiřování

**Nový sekundární soubor:** jen záznam v `config.yaml` → `downloads`. `type: direct` pro pevnou URL,
`type: discover` pro stránku, na které se mění odkazy (`page_url` + `link_regex`). Na webu se objeví sám,
hezký název stačí doplnit do `DOWNLOAD_NAMES` ve `web/index.html`.

**Nový realitní portál:**
1. `collector/sources/<portal>.py` s funkcí `iter_listings(http, cfg, max_pages=None)`, která vrací dvojice
   `(záznam, surová_data)`. Záznam má klíče jako v `listing_snapshot` (`source`, `listing_id`, `offer_type`,
   `estate_type`, `price`, `area_m2`, …). Volitelně `fetch_detail(http, listing_id)`, jinak `fetch_detail = None`.
2. Registrovat v `collector/sources/__init__.py`, přidat sekci do `config.yaml`.
3. Test v `tests/test_sources.py` se zkrácenou skutečnou odpovědí portálu v `tests/fixtures/`; přidat portál
   do `SOURCES` ve `web/index.html`.

**Nový dotaz pro tým:** do `datasette/metadata.yml` → `queries`. Test `tests/test_queries.py` ověří, že všechny
dotazy webu i Datasette sedí na aktuální schéma, takže přejmenovaný sloupec neprojde CI.

## Když zdroj selže

Zdroje jsou neoficiální rozhraní webů. Kód chyby hlásí hlasitě (běh `failed`, červený čtvereček na webu,
chyba v logu) místo tichého ukládání prázdných dat. Jeden rozbitý zdroj nezastaví ostatní.

- **Sreality**: JSON API webu `/api/v1/estates/search` a `/api/v1/estates/{id}`.
- **Bezrealitky**: GraphQL dotaz v `collector/sources/bezrealitky_query.graphql`; když API odmítne pole,
  chyba ho jmenuje.
- **iDNES**: parsování HTML, nejkřehčí zdroj; při změně webu upravit selektory v `collector/sources/idnes.py`.

Opravu ověřte proti skutečnému webu (`listings --source X --max-pages 1`) a přidejte novou zkrácenou odpověď
do `tests/fixtures/`. Mezi požadavky je pauza ~1,5 s, stahuje se v noci.

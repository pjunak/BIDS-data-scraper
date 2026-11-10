# BIDS data scraper

Sběr dat pro projekt předmětu BIDS (PEF MENDELU): trh s byty a domy v Jihomoravském kraji, Brno vs. okolí.

- **Každou noc** se stáhne snímek všech aktivních inzerátů ze **Sreality, Bezrealitek a iDNES Reality**
  (byty + domy, prodej + pronájem) a vytvoří se snapshot celé databáze ke stažení.
- **Týdně až čtvrtletně** se stahují **sekundární zdroje** (ČSÚ, MPSV, MF cenová mapa nájemného, Policie ČR,
  jízdní řády IDS JMK) a archivuje se každá nová verze.
- **https://bids.junak.eu** (pro tým, chráněno heslem) ukazuje stav sběru a log, umožňuje prohlížet databázi
  přes SQL, stáhnout data (SQLite nebo CSV) a spustit sběr ručně.

Zdroje, KPI a poznámky k ETL jsou v [docs/project.md](docs/project.md).

## Jak to funguje

Jeden Docker obraz běží jako čtyři služby:

| Služba | Co dělá |
|---|---|
| `collector` | `python -m collector daemon`: noční sběr inzerátů, stahování sekundárních souborů, ruční sběr na požádání |
| `datasette` | živá databáze jen pro čtení na `/db/` (tabulky, SQL, export CSV) |
| `auth` | přihlášení heslem (`web/auth.py`) |
| `web` | nginx: přehled, přihlášení, `/logs/`, `/files/`, `/exports/` |

Data (databáze, logy, stažené soubory, snapshoty) jsou v adresáři `data/`, mimo git. Každý push do `main`
projde testy, sestaví a ověří obraz a nasadí ho na server.

## Data

| Tabulka / pohled | Obsah |
|---|---|
| `run` | každý běh: úloha, čas, `ok`/`failed`/`running`, počet položek, chyba |
| `listing_snapshot` | 1 řádek = 1 inzerát v 1 běhu (cena, plocha, dispozice, lokalita, GPS) |
| `listing_lifecycle` | pohled: první a poslední výskyt, změny ceny (KPI doba inzerce a zlevnění) |
| `listing_attributes` | pohled: stav, vlastnictví, energetická třída, pozemek, novostavba (Sreality, Bezrealitky) |
| `listing_payload` | surová data z portálu (JSON), ukládají se jen při změně |
| `listing_detail` | surový detail inzerátu (Sreality), jen pro nově viděné inzeráty |
| `download` | verze stažených sekundárních souborů (soubory v `files/`) |

Surová data se ukládají vždy, takže chybu parseru jde opravit později v ETL. Běhy `failed` v ETL ignorujte.
U pronájmu je `price` měsíční nájem. Kontakty na prodávající a makléře se neukládají.

**Stažení dat** na webu: celá databáze `/exports/collector-latest.sqlite.gz` (posledních 7 nocí v `/exports/`),
CSV libovolné tabulky nebo SQL dotazu přes `/db/`, sekundární soubory v `/files/`. Automaticky do počítače
viz [Lokální kopie dat](#lokální-kopie-dat).

## Lokální kopie dat

Pro ETL a analýzu si data stáhněte k sobě jedním příkazem. Stačí Python 3.8 nebo novější, nic dalšího se
neinstaluje a repozitář není potřeba klonovat. Spusťte ho v terminálu ve složce, kam chcete data uložit
(na Windows v PowerShellu a místo `python3` napište `python`):

```bash
python3 -c "import urllib.request as u; exec(u.urlopen('https://bids.junak.eu/pull.py').read())"
```

Zeptá se na heslo (stejné jako na webu) a uloží data do složky `bids-data`. Pro aktualizaci spusťte stejný
příkaz znovu ve stejné složce, stáhne jen to, co se od minula změnilo. Bez hesla je veřejný jen samotný skript
`/pull.py` (stejný jako `collector/pull.py` tady na GitHubu), data ne.

- `bids-data/collector.sqlite` je vždy nejnovější noční snapshot databáze, pořád pod stejným názvem (vzniká
  po nočním sběru, takže je nejvýš den starý). Každý nový snapshot ho přepíše, vlastní tabulky si proto ukládejte do jiného souboru. Otevřete ho
  v DB Browser for SQLite, pandas nebo SSIS.
- `bids-data/files/<zdroj>/` obsahuje sekundární soubory pod původními názvy, např.
  `csu_mzdy_kraje/csu_MZDR_mzdy_kraje.csv` nebo `policie_mapa_kriminality/202301.zip`. Názvy se nemění:
  novější verze souboru přepíše starší pod stejným názvem, takže cesty v ETL můžou zůstat napevno. Zdroje
  s jedním souborem za měsíc nebo čtvrtletí (Policie, historie MPSV, cenová mapa MF) jen přibývají o další
  soubory, ty ETL projde smyčkou přes složku. Výjimka: ČSÚ má v názvu `csu_median_mezd_kraje` číslo vydání,
  takže nové roční vydání přibude jako druhý soubor (novější poznáte podle `fetched_at` v tabulce
  `download`). Každý soubor se ověří podle SHA-256; vlastní soubory ve složce zůstanou.
- Přihlášení se uloží do `bids-data/.pull-session` a platí 30 dní, pak se příkaz zeptá znovu. S volbou
  `--password` se neptá vůbec, takže příkaz poběží i bez obsluhy (skript, vyučující).
- Na macOSu s Pythonem z python.org nejdřív spusťte *Install Certificates.command* ze složky Pythonu, jinak
  skončí chybou `CERTIFICATE_VERIFY_FAILED`.

Volby se píšou za příkaz, například `python3 -c "import urllib.request as u; exec(u.urlopen('https://bids.junak.eu/pull.py').read())" --no-files`:

| Volba | Co dělá |
|---|---|
| `--no-files` | jen databáze |
| `--only csu_mzdy_kraje` | jen vybrané soubory (lze opakovat) |
| `--all-versions` | i starší verze souborů, s datem stažení na začátku názvu (další spuštění je nesmaže) |
| `--data D:/bids` | jiná složka než `./bids-data` |
| `--password HESLO` | heslo rovnou v příkazu, nic se neptá (jinak proměnná `BIDS_PASSWORD`, jinak dotaz) |

Celý příkaz i s heslem patří do dokumentace pro vyučujícího, ne sem: repozitář je veřejný a heslo má držet
stranou roboty, kteří hledají databáze na internetu.

V klonu repozitáře dělá totéž `python -m collector pull`, jen ukládá do `./data` (tam, kde data čte i lokální
`docker compose`).

## Struktura repozitáře

```
collector/            sběrač (Python 3.12)
  __main__.py         příkazová řádka a plánovač
  jobs.py             úlohy: inzeráty, stahování souborů, snapshot
  sources/            jeden modul = jeden realitní portál
  downloads.py        sekundární soubory (přímá URL nebo odkazy nalezené na stránce)
  db.py               schéma databáze a pohledy
  snapshots.py        noční .sqlite.gz ke stažení
  trigger.py          ruční spuštění sběru z webu
  pull.py             lokální kopie dat z webu; web ho sám nabízí na /pull.py (jen standardní knihovna)
config.yaml           co a kdy se stahuje (zdroje, URL, plán)
datasette/            popis tabulek a připravené dotazy pro /db/
web/                  přehled (index.html), přihlášení (login.html, auth.py), nginx.conf
tests/                offline testy, nikdy nesahají na skutečné portály; fixtures/ = zkrácené ukázkové odpovědi
```

## Spuštění a příkazy

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v       # testy, bez Dockeru a bez internetu

cp .env.example .env                          # heslo change-me-please, nebo vlastní: python web/auth.py hash
docker compose up --build                     # celý projekt na http://127.0.0.1:8080, data v ./data
```

Jednotlivé úlohy (lokálně i na serveru):

```bash
docker compose exec collector python -m collector status        # poslední běhy a počty
docker compose exec collector python -m collector listings [--source sreality] [--max-pages 2]
docker compose exec collector python -m collector downloads [--force] [--only gtfs_ids_jmk]
docker compose exec collector python -m collector snapshot       # snapshot databáze hned
docker compose exec collector python -m collector export --out /data/export --since 2026-11-01
```

**Ruční sběr:** tlačítko *Spustit sběr teď* na přehledu stáhne všechny portály hned. Převezme ho běžící
sběrač, takže se ruční a noční sběr nepřekrývají; ručně jde nejvýš jednou za hodinu
(`schedule.manual_cooldown_min`).

**Přihlášení:** jen heslo, bez jména. Každý řádek `BIDS_PASSWORD_HASH_<ROLE>` v `.env` je jedno heslo
a heslo samo určuje roli. Přihlášení platí 30 dní, pokusů je nejvýš 10 za minutu z jedné adresy.

## Rozšiřování

**Nový sekundární soubor:** jen záznam v `config.yaml` → `downloads`. `type: direct` pro pevnou URL,
`type: discover` pro stránku, na které se odkazy mění (`page_url` + `link_regex`). Na webu se objeví sám;
hezký název stačí doplnit do `DOWNLOAD_NAMES` ve `web/index.html`.

**Nový realitní portál:**
1. `collector/sources/<portal>.py` s `iter_listings()` a `fetch_detail` podle popisu
   v `collector/sources/__init__.py`.
2. Registrovat v `collector/sources/__init__.py`, přidat sekci do `config.yaml`.
3. Test v `tests/test_sources.py` se zkrácenou odpovědí portálu v `tests/fixtures/`; přidat portál
   do `SOURCES` ve `web/index.html`.

**Nový dotaz pro tým:** do `datasette/metadata.yml` → `queries`. Test `tests/test_queries.py` ověří, že všechny
dotazy webu i Datasette sedí na aktuální schéma.

## Když zdroj selže

Zdroje jsou neoficiální rozhraní webů. Chyba se hlásí hlasitě (běh `failed`, červený čtvereček na webu,
chyba v logu) místo tichého ukládání prázdných dat. Jeden rozbitý zdroj nezastaví ostatní.

- **Sreality**: JSON API webu `/api/v1/estates/search` a `/api/v1/estates/{id}`.
- **Bezrealitky**: GraphQL dotaz v `collector/sources/bezrealitky_query.graphql`; když API odmítne pole,
  chyba ho jmenuje.
- **iDNES**: parsování HTML, nejkřehčí zdroj; při změně webu upravit selektory v `collector/sources/idnes.py`.

Opravu ověřte proti skutečnému webu (`listings --source X --max-pages 1`) a přidejte zkrácenou odpověď
do `tests/fixtures/` (bez osobních údajů). Mezi požadavky je pauza ~1,5 s a sbírá se v noci, aby portály
nebyly zatěžovány.

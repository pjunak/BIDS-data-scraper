# Project context

BIDS (*Business Intelligence a datové sklady*), 1st semester of the N-OI
master's programme, PEF MENDELU. Team: Petr Junák, Jan Kopecký. The proposal
(*zadání*) and the course requirements are in the team's Google Drive folder
`Mendelu/BIDS/Projekt`.

**Topic:** the residential property market in Jihomoravský kraj, Brno compared
with its surroundings and the rest of the region. Audience: people looking for
housing and small buy-to-let investors.

**Final submission:** presentation, documentation (introduction and goal,
methodology with sources, ETL and data warehouse design, results, discussion,
references) and the working solution: Power BI file, Visual Studio (SSIS)
solution, DB create scripts and ETL scripts. Both members attend the defence.

This repository is only the collection layer. ETL, the warehouse and the report
are separate work.

## KPIs (from the proposal)

1. Average and median price per m² by municipality, district, Brno city
   district, property type, disposition and condition.
2. Price vs. distance from Brno centre (bands such as Brno, < 15 km, 15–30 km,
   > 30 km).
3. Affordability: average annual regional gross wages needed for a typical
   flat (e.g. 2+kk, ~55 m²).
4. Gross rental yield: annual rent per m² ÷ purchase price per m² in the same
   location and category, excluding locations with too few listings.
5. Listing duration and asking-price changes, from the daily snapshots
   (`listing_lifecycle`).
6. Location quality vs. price: population trend, unemployment and crimes per
   1,000 inhabitants against price per m².
7. Price premium of new builds and energy class A–B, Brno vs. elsewhere
   (`listing_attributes`).

Candidate extra KPI: asking prices (listings) vs. realised prices (ČSÚ).

## Sources

### Listings, collected nightly

| Source | Access | Location data | Notes |
| --- | --- | --- | --- |
| Sreality.cz | JSON API of the website, `/api/v1/estates/search` (region 14) and `/api/v1/estates/{id}` | GPS, city, city part, district, Brno city district (`quarter`), Sreality `municipality_id` in the raw payload | Largest portal, agency listings. Detail (condition, ownership, energy class, land area) fetched once per new listing |
| Bezrealitky.cz | Public GraphQL API, query in `collector/sources/bezrealitky_query.graphql`, region OSM relation `R442311` | GPS, address text | Owner listings only, so few (~250). Condition, ownership, PENB and land area in the list payload |
| reality.iDNES.cz | HTML of the search result pages | Address text only (street, municipality, district) | Most fragile; no GPS, so it must be geocoded from text |

Rent listings carry the monthly rent in `price`. Sreality's
`price_summary_czk` is already normalised to a month.

### Secondary files

Configured in `config.yaml` → `downloads`, archived by version in
`/files/<name>/`:

| Name | Content |
| --- | --- |
| `mf_cenova_mapa_najemneho` | MF rent price map: rent per m² by municipality and cadastral area, quarterly since 2019 (asking prices) |
| `gtfs_ids_jmk` | IDS JMK public transport timetables (GTFS), for travel time to Brno |
| `csu_pocet_obyvatel_obce` | ČSÚ OBY02A: population of every municipality per year (~200 MB, whole country); municipality code in `UZ01234596C` |
| `csu_mzdy_kraje` | ČSÚ MZDR: average gross monthly wage by region and year; region in `Uz0123vm.KRAJ` (`CZ064`) |
| `csu_median_mezd_kraje` | ČSÚ 110080: average and median wage by region (median = `SPKVANTIL_kod` Q5, JMK = `uzemi_kod` 3115); ends a year earlier than MZDR |
| `csu_ceny_nemovitosti` | ČSÚ CEN0402: realised purchase prices of flats and houses in Kč/m² by region, district and municipality size, yearly and three-year periods |
| `mpsv_nezamestnanost_obce` | MPSV: monthly unemployment by municipality since 2025, one CSV (`;`, decimal comma); `obec_kod`, `kraj_kod` 116 = JMK, rows split by sex |
| `mpsv_nezamestnanost_obce_historie` | MPSV: monthly unemployment by municipality 2014-03 to 2024-12, one JSON per month; JMK = districts `Okres/3701`–`3707` |
| `policie_mapa_kriminality` | Police crime map: one ZIP per month since 2023 (CSV, one row per offence and type, deduplicate on `id`). Points are anonymised to areas inside a municipality; the last two months are re-checked because the police regenerate them |
| `policie_oblasti_obci` | Police anonymisation areas with `kod_obec`; join offences on the point (`def_bod_x/y`, 6 decimals) to get the municipality |

All files cover the whole country; filter to the region in ETL. The municipality
to district/region lookup is in the ČSÚ code lists, e.g.
`https://data.csu.gov.cz/api/katalog/v1/dimenze/UZ25/polozky?nadrazenaPolozkaKod=CZ064&kodUrovne=OBEC`
(the 673 municipalities of Jihomoravský kraj).

### Useful for ETL and the discussion, not downloaded

- RÚIAN (ČÚZK): addresses, municipalities, cadastral areas and Brno city
  districts with geometry; the practical way to map GPS to municipality codes.
- ČSÚ housing construction and population balance per municipality
  (migration), a sharper suburbanisation signal than population alone.
- Brno police security reports, ČSÚ regional commentary on property prices.

## ETL notes

- Ignore runs with `status = 'failed'`; their snapshot is incomplete and would
  distort "last seen".
- Map listings to municipality codes (and Brno city districts): GPS point in
  polygon against RÚIAN for Sreality and Bezrealitky, text match on the
  municipality name for iDNES.
- De-duplicate across portals: the same flat is often listed by several
  agencies and portals. Match on location (GPS or municipality), area and
  price similarity.
- Handle listings without price ("cena na dotaz") or area; Sreality houses also
  include cottages (`category_sub_cb` Chata, Chalupa), which may be excluded.
- Raw JSON stays in `listing_payload` / `listing_detail`, so any field the
  parsers do not extract can still be read in ETL (`json_extract`).

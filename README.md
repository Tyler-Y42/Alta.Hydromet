# Alta Hydromet

Live: https://tyler-y42.github.io/Alta.Hydromet/

Hydromet dashboard for upper Little Cottonwood Canyon (Alta / Albion Basin). For any date window it shows
QC'd MesoWest precipitation, detected storms, the streamflow response to each storm, SNOTEL, and a plain
text readout for field notes, with CSV export of the processed series.

## Files
- `index.html`: the whole page (QC, storm detection, charts, self test). No build step.
- `keys.js`: MesoWest / Synoptic token (public by design, read only).
- `scripts/fetch_county.py`: copies Salt Lake County Flood Control gauges (Contrail portal) to JSON.
- `.github/workflows/county.yml`: runs that script every 15 min and force pushes the `data` branch
  (one commit, no history buildup). Run it by hand with a `since=YYYY-MM` input to backfill.

## Sources
| Data | Source | How |
|---|---|---|
| Precip, temp, wind, snow depth | Synoptic timeseries API (PC064, AGD, PC056, ELBUT, SPC, UTALC, ATH20, HDP) | browser, CORS |
| SNOTEL daily | NRCS AWDB REST (766, 366, 628) | browser, CORS |
| LCC @ Tanners Flat, BCC @ Canyon Mouth, LCC @ Crestwood, LCC @ 300 W | Salt Lake County Contrail | GitHub Action to `data` branch (portal has no CORS and needs a guest session) |
| LCC @ Jordan R. | USGS NWIS IV 10168000 (archive host for windows older than ~110 d; OGC API fallback) | browser, CORS |

CBRFC LCTU1 (the old LCC canyon mouth point) closed in 2008 and has no observations.

## Verifying
Open `index.html?selftest` (or the button at the bottom of the page). It runs the unit tests and checks the
parser against the 10/7 to 10/8/2026 reference values. The handoff's AGD rolling 24 h value of 2.79 mm was
read before AGD's 13:50 report arrived; with that report the correct value is 3.05 mm (both are tested).

## Caveats
- Scheduled GitHub Actions run late (15 to 45 min) and are disabled after 60 days without repository
  activity. The page shows how old the county copy is and turns the chip amber past 90 min.
- Tipping buckets undercatch snow, and unheated ones hold it until it melts; the page flags storms where a
  gauge caught nothing below 1 °C.

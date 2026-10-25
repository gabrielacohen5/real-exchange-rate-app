# Real Exchange Rate Explorer

A Streamlit application for comparing monthly nominal and real bilateral exchange rates using IMF observations.

## Requirements

- Python 3.12
- Internet connection to the IMF API

## Installation

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Open the local URL displayed by Streamlit (usually http://localhost:8501).

## Usage

1. Select two countries or the Euro area.
2. Choose start/end months (January 1976 or later).
3. Select Real, Nominal, or Both and click **Analyze exchange rates**.
4. Inspect the interactive plot and monthly table; download the CSV.

## IMF data source (updated)

The app uses the IMF SDMX 3.0 public API (no key needed) and requests SDMX-JSON:

- CPI: `https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.STA/CPI/+/{ISO3}.CPI._T.IX.M?c%5BTIME_PERIOD%5D=ge%3A{START}%2Ble%3A{END}`
- Exchange rate: `https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.STA/ER/+/{ISO3}.XDC_USD.PA_RT.M?c%5BTIME_PERIOD%5D=ge%3A{START}%2Ble%3A{END}`

`{ISO3}` is the country code, `{START}` and `{END}` are `YYYY-MM`. Euro area uses `G163`. The app retrieves two CPI series and up to two exchange-rate series. For the USA, FX is set to exactly 1 USD/USD without requesting an exchange-rate series. The parser supports SDMX-JSON 2.0 and the older SDMX-JSON 1.0 layout. All observations are from IMF; missing observations are never fabricated.

**Validation:** Python compilation and mocked SDMX response tests can verify code paths without IMF network access. Live IMF API availability was not independently verified in this environment. Country/month coverage is not guaranteed.

## Economic definitions

If `E1` and `E2` are local currency units per US dollar:

- Nominal `S = E2/E1`: Country 2 currency units per one Country 1 currency unit.
- Real `RER = S × CPI1/CPI2`.
- Benchmark `RER* = arithmetic mean of valid monthly RER` across the selected dates.
- Real index `100 × RER/RER*`.

Missing, zero, negative, or nonnumeric observations are excluded from calculations. The nominal series is never indexed. The real index rises when Country 1 depreciates in real terms relative to Country 2 under this convention. Historical coverage depends on IMF reporting. Euro area historical currency consistency should be assessed before interpreting pre-euro observations.

## Troubleshooting

- **HTTP 404 / 400 / empty results**: Check the selected country, dataflow availability, and time range; the IMF may not publish all observations.
- **HTTP 429 / timeout**: Wait and retry; cached successful responses are reused for 12 hours.
- **Missing months**: Try more recent dates or different countries.
- **Country not supported**: ISO 3166 country inclusion does not imply IMF series availability.

## Important limitation

The endpoint templates match the supplied tested public URLs, but a live end-to-end IMF response was not accessible for verification here. If the IMF changes its SDMX response schema or country coverage, an update may be required.


## Parser update

The app now tries SDMX-CSV first, with alternate CSV and SDMX-JSON response types as fallbacks.
If the IMF responds with an unsupported structure, the error reports the request URL and
format-specific diagnostic information rather than claiming the country has no data.
Live IMF network requests were not verified in the development environment.

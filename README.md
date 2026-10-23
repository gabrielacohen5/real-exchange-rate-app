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

## Data source and compatibility

The application calls the IMF SDMX 3.0 endpoint at `https://api.imf.org/external/sdmx/3.0`, using the IMF IFS dataflow and the requested legacy indicator names:

- `I{ISO3}_CPI_TIX_M`
- `I{ISO3}_XDC_USD_PA_RT_M`

The Euro area code is `G163`. The API can change dataflow identifiers, versions, key dimensions, or availability. These legacy identifiers **may not resolve** in the current SDMX 3.0 IFS flow. The application shows a descriptive error rather than inventing observations. Check the IMF's current dataflow and series catalog before relying on it in production. This version has not been end-to-end validated against a live IMF response.

## Economic definitions

If `E1` and `E2` are local currency units per US dollar:

- Nominal `S = E2/E1`: Country 2 currency units per one Country 1 currency unit.
- Real `RER = S × CPI1/CPI2`.
- Benchmark `RER* = arithmetic mean of valid monthly RER` across the selected dates.
- Real index `100 × RER/RER*`.

Missing, zero, negative, or nonnumeric observations are excluded from calculations. The nominal series is never indexed. The real index rises when Country 1 depreciates in real terms relative to Country 2 under this convention. Historical coverage depends on IMF reporting. Euro area historical currency consistency should be assessed before interpreting pre-euro observations.

## Troubleshooting

- **HTTP 404 / 400 / empty results**: The IMF may have migrated or renamed the dataflow or indicator; confirm the latest SDMX catalog and update `fetch_indicator` accordingly.
- **HTTP 429 / timeout**: Wait and retry; cached successful responses are reused for 12 hours.
- **Missing months**: Try more recent dates or different countries.
- **Country not supported**: ISO 3166 country inclusion does not imply IMF series availability.

## Important limitation

IMF API connectivity and current series-key compatibility could not be live-tested in this environment. Therefore this is a runnable application scaffold with full calculations and UI, **not a verified production-ready IMF integration**.

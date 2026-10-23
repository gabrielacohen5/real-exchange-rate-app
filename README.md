# real-exchange-rate-app
# IMF Real & Nominal Exchange Rate Explorer

A Python 3.12 Streamlit application for comparing monthly real and nominal exchange rates using IMF CPI and exchange-rate observations from January 1976 onward, subject to series availability.

## Installation

```bash
python3.12 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Features

- Choose Country 1 and Country 2 (including IMF Euro area code `G163`). Country choices are populated from an IMF area codelist when available, with a curated fallback.
- Select the start and end months (date-picker day is ignored).
- Display Real, Nominal, or Both on separate axes in an interactive Plotly chart.
- Download all selected months, source observations, nominal rate, RER, benchmark, and real index as CSV.
- Cache downloaded series for 24 hours; changing sample dates recalculates the benchmark.

## Economic definitions

Let `E1` and `E2` be local currency units per USD, `P1` and `P2` the CPI series:

- `S = E2 / E1` (units of Country 2 currency per one unit of Country 1 currency).
- `RER = S × P1 / P2`.
- `RER* = mean(RER)` over months in the selected sample with complete, positive input observations.
- `Real Index = 100 × RER / RER*`.

The nominal series is shown in original currency units, never rebased. Missing data are preserved in the CSV and excluded from the benchmark. CPI series may have different index reference bases across countries; compare the real-index dynamics with this caveat in mind.

## Data source and limitations

Source: [IMF SDMX 3.0 API](https://api.imf.org/external/sdmx/3.0). Keys used:

- CPI: `{ISO3}.CPI._T.IX.M`
- Exchange rates: `{ISO3}.XDC_USD.PA_RT.M`
- Euro area: `G163`

The implementation tries two IMF SDMX 3.0 URL conventions for the `DF_CPI` and `DF_ER` dataflows. IMF dataflow identifiers, versions, dimension ordering, response formats, and country availability can change; if the IMF API rejects a request, verify the current dataflow metadata and adjust `fetch_series()` accordingly. **These endpoint variants have not been live-verified in this environment.** The app reports errors rather than fabricating observations.

## Python compatibility

Designed for Python 3.12; packages are version-bounded to compatible major releases.

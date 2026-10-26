# Real Exchange Rate Explorer

Streamlit app using IMF SDMX 3.0 monthly CPI and exchange-rate data. Built from the user-provided working CSV-based implementation.

## Install and run (Python 3.12)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Select two countries (including Euro area G163), a start and end month, and Real, Nominal or Both. Download the resulting CSV from the table.

## IMF source

The app deliberately retains the working implementation's **CSV** data route:

- CPI: `https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.STA/CPI/~/{ISO3}.CPI._T.IX.M`
- FX: `https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.STA/ER/~/{ISO3}.XDC_USD.PA_RT.M`

The request uses `Accept: text/csv` and `c[TIME_PERIOD]=ge:1976-M01`. It parses explicit `TIME_PERIOD` and `OBS_VALUE` columns, avoiding the previously problematic SDMX-JSON dimension indexing. USD/USD is defined as 1. Availability differs by economy. Requests are cached for 12 hours.

## Methodology

`S = FX2 / FX1` (Country 2 units per Country 1 unit); `RER = S * CPI1 / CPI2`; `RER* = mean(valid monthly RER in selected period)`; `Real Index = 100 * RER / RER*`. Nominal S is never normalized. A rise in the index means Country 1 real depreciation under this convention. Missing observations are not filled.

## Troubleshooting

If no chart appears, expand **Data availability and diagnostics** to identify which series lacks overlapping months. If the IMF changes its CSV response format or dataflow, the app raises an informative error. Network access is required.

## Verification

Python syntax and local offline tests can be checked without IMF connectivity. Live API availability and coverage should be tested in your local environment.

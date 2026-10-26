# IMF Real Exchange Rate Explorer

A Python 3.12 / Streamlit app that charts bilateral nominal and real exchange rates using monthly IMF CPI and exchange-rate observations.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## GitHub + Streamlit Community Cloud

1. Create a GitHub repository and commit `app.py`, `requirements.txt`, and `README.md` at its root.
2. In [Streamlit Community Cloud](https://share.streamlit.io/), select **Create app**, choose the repository and branch, and set the entry point to `app.py`.
3. In advanced settings, choose **Python 3.12**, then deploy. The app makes live requests to the IMF API; no API key is required.

## Definitions

Country 1 is the base currency; Country 2 is the quote currency. Let `X1` and `X2` denote the IMF's domestic-currency-per-USD exchange rates:

- `S = X2 / X1` (Country 2 currency per one unit of Country 1 currency)
- `RER = S × CPI1 / CPI2`
- `RER* = mean(RER)` across valid months in the user-selected sample
- `Real Index = 100 × RER / RER*`

The real index is rebased each time the sample changes. Nominal `S` remains in actual currency units. **Both** uses two axes. The CSV contains source observations and calculated fields, with blanks for missing months.

## IMF source and limitations

The app requests the IMF SDMX 3.0 API at `https://api.imf.org/external/sdmx/3.0` with monthly keys `{ISO3}.CPI._T.IX.M` and `{ISO3}.XDC_USD.PA_RT.M`, using `G163` for the Euro area. The app attempts IMF IFS dataflow versions 1.0 and 1.1 and reports API errors if unavailable. The IMF may migrate dataflows, change their dimension order, or not provide these indicators in a given release. If so, update the `fetch_series` endpoint and keys using the IMF's current SDMX dataflow metadata; the app does **not** substitute unrelated data.

The included dropdown provides a broad curated selection of IMF economies plus the Euro area, **not a dynamically discovered exhaustive list of all IMF countries**. Extend `COUNTRIES` for additional ISO3 codes. Coverage starts January 1976 when available; later-starting CPI and FX series restrict the overlap. No interpolation or synthetic data is used. The CPI indices may have different reference years, and the resulting real index is a within-sample measure, not an absolute PPP valuation.

## Troubleshooting

- **HTTP 404/422 or empty response:** IMF may have changed the IFS dataflow, dimension order, or indicator availability. Inspect IMF SDMX metadata for the correct current flow.
- **No overlapping data:** select another pair or inspect the available history.
- **Conflicting monthly observations:** the API may be returning multiple distinct series under one key. Refine the SDMX dimensions rather than arbitrarily taking the first observation.
- **HTTP 429/5xx or timeout:** wait and retry; cached successful responses last 24 hours.

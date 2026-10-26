# Real Exchange Rate Analysis App

A local Streamlit application that retrieves monthly CPI and exchange-rate observations from the IMF SDMX 3.0 API, calculates bilateral nominal and real exchange rates, and exports the selected-period data.

## Requirements

- Python 3.12
- Internet access to `api.imf.org`

## Install and run

From the folder containing these files:

```bash
python -m venv .venv
```

Activate the environment:

- Windows PowerShell: `.venv\\Scripts\\Activate.ps1`
- macOS/Linux: `source .venv/bin/activate`

Install dependencies and start the app:

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

Streamlit will print a local address, normally http://localhost:8501.

## Using the app

1. Choose two distinct countries in the sidebar. ISO3 countries are listed with the IMF special code `G163` for the Euro area.
2. Select the first and last month. The supported period begins January 1976.
3. Select Real, Nominal, or Both. Both uses separate y-axes.
4. Explore the chart using hover, zoom, and the range slider, then download the selected interval as CSV.

## Data and calculations

The app calls the IMF monthly CPI series (`{ISO3}.CPI._T.IX.M`) and exchange-rate series (`{ISO3}.XDC_USD.PA_RT.M`) using the provided SDMX 3.0 endpoint templates. The exchange-rate observations are local currency units per USD. For the United States, the USD/USD rate is set to 1 and no exchange-rate API call is made.

Let `q1` and `q2` be each country's local currency units per USD. The nominal quote is `S = q2 / q1`, in Country 2 currency units per one Country 1 currency unit. The real rate is `RER = S × CPI1 / CPI2`. The period benchmark `RER*` is the arithmetic mean of valid monthly RER observations within the selected interval, and the normalized real index is `100 × RER(t) / RER*`. Missing, nonnumeric, zero, or negative inputs are omitted from the RER benchmark. The exported file retains all months in the period, with unavailable values blank.

An increase in this quote-based real index indicates a real appreciation of Country 1 against Country 2; a decline indicates real depreciation. CPI series can use different index bases, so interpret the normalized index as relative movement and understand that the RER* level depends on CPI units.

## API availability

Coverage differs by country and can start later than January 1976. If an IMF request fails or the selected pair has no overlapping valid months, the app gives an error and suggests selecting another period or country. API results are cached for 12 hours to avoid repeated requests; restarting Streamlit or clearing its cache refreshes them.

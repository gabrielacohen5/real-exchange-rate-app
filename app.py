"""Interactive real exchange rate analysis using IMF SDMX 3.0 monthly data."""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import pycountry
import requests
import streamlit as st
from plotly.subplots import make_subplots

API_ROOT = "https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.STA"
MIN_MONTH = pd.Timestamp("1976-01-01")
REQUEST_TIMEOUT = 40
EURO_AREA = ("Euro area", "G163")

st.set_page_config(page_title="Real Exchange Rate Analysis", page_icon="🌍", layout="wide")
st.title("Real Exchange Rate Analysis")
st.caption("Compare nominal exchange rates and inflation-adjusted currency values using monthly IMF data.")


def country_options() -> list[tuple[str, str]]:
    """Build a stable ISO3 selector list, adding IMF's special Euro area code."""
    countries = [(c.name, c.alpha_3) for c in pycountry.countries if hasattr(c, "alpha_3")]
    countries.append(EURO_AREA)
    return sorted(countries, key=lambda item: item[0].casefold())


def imf_url(measure: str, iso3: str, start: str, end: str) -> str:
    """Construct the supplied IMF SDMX 3.0 monthly series URL."""
    if measure == "CPI":
        path = f"CPI/+/{iso3}.CPI._T.IX.M"
    else:
        path = f"ER/+/{iso3}.XDC_USD.PA_RT.M"
    return f"{API_ROOT}/{path}?c%5BTIME_PERIOD%5D=ge%3A{start}%2Ble%3A{end}"


def _period_from_obs(obs: Any, time_values: list[Any]) -> str | None:
    if isinstance(obs, dict):
        for k in ("TIME_PERIOD", "time_period", "period", "TIME_PERIOD_ID"):
            if k in obs:
                return str(obs[k])
        obs = obs.get("value", obs.get("OBS_VALUE"))
    if isinstance(obs, list) and obs:
        obs = obs[0]
    if isinstance(obs, int) and 0 <= obs < len(time_values):
        return str(time_values[obs])
    return None


def parse_sdmx_json(payload: dict[str, Any], label: str) -> pd.Series:
    """Read SDMX-JSON 3.x observations, accepting sparse or explicit time keys.

    IMF's JSON representation uses observation keys whose final component is the
    time-dimension index. Metadata dimension names and values are used where present.
    """
    try:
        # SDMX 3.0 commonly places `structure` beside `data`; some deployments
        # wrap it in `data.structures`. Accept both response layouts.
        structure = payload.get("structure")
        if structure is None:
            structures = payload.get("data", {}).get("structures", [])
            structure = structures[0] if structures else {}
        dimensions = structure.get("dimensions", {})
        obs_dims = dimensions.get("observation", [])
        time_dim = next((d for d in obs_dims if d.get("id") == "TIME_PERIOD"), None)
        if time_dim is None:
            time_dim = next((d for d in obs_dims if "TIME" in d.get("id", "").upper()), None)
        time_values = [v.get("id", v.get("name")) for v in (time_dim or {}).get("values", [])]
        dataset = payload["data"]["dataSets"][0]
        output: dict[str, float] = {}
        for series in dataset.get("series", {}).values():
            for key, raw in series.get("observations", {}).items():
                period = _period_from_obs(raw, time_values)
                # SDMX keys can be comma-delimited dimensions; use the last index as TIME_PERIOD.
                if period is None:
                    try:
                        ix = int(str(key).split(":")[-1])
                        if ix < len(time_values):
                            period = str(time_values[ix])
                    except (ValueError, IndexError):
                        pass
                val = raw[0] if isinstance(raw, list) and raw else raw
                if isinstance(val, dict):
                    val = val.get("value", val.get("OBS_VALUE"))
                if period is not None and val is not None:
                    try:
                        number = float(val)
                        if pd.notna(number):
                            output[period] = number
                    except (TypeError, ValueError):
                        continue
        # Some SDMX payloads flatten observations at dataset level.
        for key, raw in dataset.get("observations", {}).items():
            period = _period_from_obs(raw, time_values)
            if period is None:
                try:
                    ix = int(str(key).split(":")[-1])
                    if ix < len(time_values):
                        period = str(time_values[ix])
                except (ValueError, IndexError):
                    pass
            val = raw[0] if isinstance(raw, list) and raw else raw
            try:
                if period is not None and val is not None:
                    output[period] = float(val)
            except (TypeError, ValueError):
                pass
        if not output:
            raise ValueError("The response contained no usable observations.")
        dates = pd.to_datetime(list(output.keys()), errors="coerce")
        values = pd.Series(list(output.values()), index=dates, name=label).sort_index()
        values = values[~values.index.isna()]
        values.index = values.index.to_period("M").to_timestamp()
        return values[~values.index.duplicated(keep="last")]
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ValueError(f"Unrecognized IMF SDMX response ({label}).") from exc


@st.cache_data(ttl=12 * 60 * 60, show_spinner=False)
def fetch_series(measure: str, iso3: str, start: str, end: str) -> pd.Series:
    url = imf_url(measure, iso3, start, end)
    try:
        response = requests.get(url, headers={"Accept": "application/vnd.sdmx.data+json;version=2"}, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return parse_sdmx_json(response.json(), f"{measure}_{iso3}")
    except requests.Timeout as exc:
        raise RuntimeError(f"The IMF request for {measure} ({iso3}) timed out. Please try again.") from exc
    except requests.RequestException as exc:
        detail = getattr(getattr(exc, "response", None), "status_code", None)
        suffix = f" (HTTP {detail})" if detail else ""
        raise RuntimeError(f"Could not retrieve {measure} data for {iso3}{suffix}. The IMF service may be unavailable or this series may not exist.") from exc
    except (ValueError, requests.JSONDecodeError) as exc:
        raise RuntimeError(f"The IMF response for {measure} ({iso3}) was empty or could not be read.") from exc


def get_monthly(measure: str, iso3: str, start: str, end: str) -> pd.Series:
    """Fetch a series; provide the correct USD exchange rate for USA locally."""
    if measure == "ER" and iso3 == "USA":
        idx = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="MS")
        return pd.Series(1.0, index=idx, name="ER_USA")
    return fetch_series(measure, iso3, start, end)


def country_label(code: str, options: list[tuple[str, str]]) -> str:
    return next(name for name, iso in options if iso == code)


options = country_options()
name_by_code = {code: name for name, code in options}

with st.sidebar:
    st.header("Analysis settings")
    c1 = st.selectbox("Country 1", options=[code for _, code in options], index=next(i for i, (_, code) in enumerate(options) if code == "USA"), format_func=lambda x: f"{name_by_code[x]} ({x})")
    c2 = st.selectbox("Country 2", options=[code for _, code in options], index=next(i for i, (_, code) in enumerate(options) if code == "JPN"), format_func=lambda x: f"{name_by_code[x]} ({x})")
    now_month = pd.Timestamp.today().to_period("M").to_timestamp()
    start_date = st.date_input("Start month", value=date(2000, 1, 1), min_value=MIN_MONTH.date(), max_value=now_month.date())
    end_date = st.date_input("End month", value=now_month.date().replace(day=1), min_value=MIN_MONTH.date(), max_value=now_month.date())
    display = st.radio("Series to display", ["Real", "Nominal", "Both"], horizontal=False)
    st.caption("Country 1 is the home currency. The nominal quote is Country 2 currency units per Country 1 unit.")

start_ts = pd.Timestamp(start_date).to_period("M").to_timestamp()
end_ts = pd.Timestamp(end_date).to_period("M").to_timestamp()
if start_ts < MIN_MONTH or end_ts < MIN_MONTH:
    st.error("Choose months from January 1976 onward.")
    st.stop()
if start_ts > end_ts:
    st.error("Start month must be on or before end month.")
    st.stop()
if c1 == c2:
    st.error("Choose two different countries to compare.")
    st.stop()

start_str, end_str = start_ts.strftime("%Y-%m"), end_ts.strftime("%Y-%m")
with st.spinner("Retrieving monthly CPI and exchange rate data from the IMF…"):
    try:
        cpi1 = get_monthly("CPI", c1, start_str, end_str).rename("CPI_Country1")
        cpi2 = get_monthly("CPI", c2, start_str, end_str).rename("CPI_Country2")
        er1 = get_monthly("ER", c1, start_str, end_str).rename("LCU_per_USD_Country1")
        er2 = get_monthly("ER", c2, start_str, end_str).rename("LCU_per_USD_Country2")
    except (RuntimeError, ValueError) as exc:
        st.error(str(exc))
        st.info("Check your internet connection, selected dates, and IMF country series availability. Some economies have shorter CPI histories.")
        st.stop()

data = pd.concat([cpi1, cpi2, er1, er2], axis=1).sort_index()
data = data.loc[(data.index >= start_ts) & (data.index <= end_ts)]
data.index.name = "Date"
# Convert LCU/USD quotations to currency2 per currency1.
data["Nominal_Exchange_Rate_S_C2_per_C1"] = data["LCU_per_USD_Country2"] / data["LCU_per_USD_Country1"]
valid = (
    data[["CPI_Country1", "CPI_Country2", "Nominal_Exchange_Rate_S_C2_per_C1"]].notna().all(axis=1)
    & (data[["CPI_Country1", "CPI_Country2", "Nominal_Exchange_Rate_S_C2_per_C1"]] > 0).all(axis=1)
)
data["RER"] = float("nan")
data.loc[valid, "RER"] = data.loc[valid, "Nominal_Exchange_Rate_S_C2_per_C1"] * data.loc[valid, "CPI_Country1"] / data.loc[valid, "CPI_Country2"]
benchmark = data.loc[valid, "RER"].mean()
data["Benchmark_RER_star"] = benchmark
data["Real_Exchange_Rate_Index"] = 100 * data["RER"] / benchmark if pd.notna(benchmark) and benchmark > 0 else float("nan")

if not valid.any():
    st.warning("No months have valid CPI and exchange-rate values for both countries in this date range. Try another period or country pair.")
    st.stop()

st.subheader(f"{name_by_code[c1]} vs. {name_by_code[c2]}")
col1, col2, col3 = st.columns(3)
col1.metric("Valid months", f"{int(valid.sum()):,} of {len(data):,}")
col2.metric("Benchmark real exchange rate (RER*)", f"{benchmark:,.4g}")
latest = data["Real_Exchange_Rate_Index"].dropna().iloc[-1]
col3.metric("Latest real index", f"{latest:.2f}", delta=f"{latest - 100:+.2f} vs. 100")
st.caption("The real index is 100 at the selected-period average. Above 100 indicates Country 1 is stronger in real terms relative to that benchmark; below 100 indicates weaker. The benchmark is recalculated for each selected period.")

fig = make_subplots(specs=[[{"secondary_y": True}]])
if display in ("Real", "Both"):
    fig.add_trace(go.Scatter(x=data.index, y=data["Real_Exchange_Rate_Index"], name="Real exchange rate index", mode="lines", line={"color": "#1769aa", "width": 2}, connectgaps=False), secondary_y=False)
    fig.add_hline(y=100, line_dash="dash", line_color="#555", annotation_text="Selected-period average (100)", annotation_position="bottom right", secondary_y=False)
if display in ("Nominal", "Both"):
    fig.add_trace(go.Scatter(x=data.index, y=data["Nominal_Exchange_Rate_S_C2_per_C1"], name=f"Nominal rate ({c2} per {c1})", mode="lines", line={"color": "#e67e22", "width": 2}, connectgaps=False), secondary_y=True)
fig.update_layout(title=f"Monthly exchange rates: {name_by_code[c1]} ({c1}) and {name_by_code[c2]} ({c2})", hovermode="x unified", legend={"orientation": "h", "y": 1.12}, margin={"t": 100, "b": 35}, template="plotly_white")
fig.update_xaxes(title_text="Month", rangeslider_visible=True)
fig.update_yaxes(title_text="Real exchange rate index (period average = 100)", secondary_y=False, showgrid=True)
fig.update_yaxes(title_text=f"Nominal exchange rate ({c2} per {c1})", secondary_y=True, showgrid=False)
st.plotly_chart(fig, use_container_width=True)

with st.expander("How to read the chart and calculations"):
    st.markdown(f"""
- **Nominal rate, S(t):** units of **{name_by_code[c2]}'s currency per one unit of {name_by_code[c1]}'s currency**. It is calculated as (Country 2 local currency units per USD) ÷ (Country 1 local currency units per USD).
- **Real exchange rate:** `S(t) × CPI₁(t) / CPI₂(t)`. Months with a missing or nonpositive CPI or exchange rate are excluded.
- **Real index:** `100 × RER(t) / mean(RER)` over valid months in this selected period. An increase in this quote-based real index means Country 1's currency has appreciated in real terms relative to Country 2 since the period average.
- CPI index base years can differ by country; the selected-period normalization makes the real index interpretable as relative movement, while the level of RER* depends on the CPI series' units.
""")

export = data.reset_index()
export.insert(1, "Country1", name_by_code[c1] + " (" + c1 + ")")
export.insert(2, "Country2", name_by_code[c2] + " (" + c2 + ")")
export["Benchmark_RER_star"] = benchmark
csv = export.to_csv(index=False, date_format="%Y-%m").encode("utf-8")
st.download_button("Download analysis as CSV", data=csv, file_name=f"rer_{c1}_{c2}_{start_str}_to_{end_str}.csv", mime="text/csv", type="primary")
st.caption("CSV includes every month in the selected interval. Missing/unavailable observations remain blank, and the benchmark is included on each row.")

"""IMF monthly bilateral real exchange rate explorer (Python 3.12)."""
from __future__ import annotations

import io
import re
from datetime import date
from xml.etree import ElementTree as ET

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

BASE = "https://api.imf.org/external/sdmx/3.0"
START = pd.Period("1976-01", freq="M")
TIMEOUT = 35

# ISO3 codes; G163 is the IMF Euro area aggregate.
COUNTRIES = {
    "Argentina": "ARG", "Australia": "AUS", "Austria": "AUT", "Belgium": "BEL",
    "Brazil": "BRA", "Canada": "CAN", "Chile": "CHL", "China": "CHN",
    "Colombia": "COL", "Czechia": "CZE", "Denmark": "DNK", "Egypt": "EGY",
    "Euro area": "G163", "Finland": "FIN", "France": "FRA", "Germany": "DEU",
    "Greece": "GRC", "Hong Kong SAR": "HKG", "Hungary": "HUN", "India": "IND",
    "Indonesia": "IDN", "Ireland": "IRL", "Israel": "ISR", "Italy": "ITA",
    "Japan": "JPN", "Kenya": "KEN", "Malaysia": "MYS", "Mexico": "MEX",
    "Netherlands": "NLD", "New Zealand": "NZL", "Nigeria": "NGA", "Norway": "NOR",
    "Pakistan": "PAK", "Peru": "PER", "Philippines": "PHL", "Poland": "POL",
    "Portugal": "PRT", "Romania": "ROU", "Saudi Arabia": "SAU", "Singapore": "SGP",
    "South Africa": "ZAF", "South Korea": "KOR", "Spain": "ESP", "Sweden": "SWE",
    "Switzerland": "CHE", "Thailand": "THA", "Türkiye": "TUR", "Ukraine": "UKR",
    "United Kingdom": "GBR", "United States": "USA", "Vietnam": "VNM",
}


class IMFError(RuntimeError):
    pass


def parse_month(value: object) -> pd.Period | None:
    """Parse an IMF monthly time label without accidentally accepting annual data."""
    s = str(value).strip()
    match = re.match(r"^(\d{4})[-/]?M?(0?[1-9]|1[0-2])$", s, re.I)
    if not match:
        return None
    return pd.Period(f"{match.group(1)}-{int(match.group(2)):02d}", freq="M")


def parse_csv(payload: bytes) -> pd.Series:
    """Read SDMX CSV in either compact or generic form; reject ambiguous duplicates."""
    frame = pd.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=False)
    frame.columns = [c.strip().upper() for c in frame.columns]
    time_col = next((c for c in ("TIME_PERIOD", "TIME_PERIOD_ID") if c in frame), None)
    value_col = next((c for c in ("OBS_VALUE", "OBS_VALUE_NUM") if c in frame), None)
    if not time_col or not value_col:
        raise IMFError("The IMF response did not contain TIME_PERIOD and OBS_VALUE columns.")
    frame["month"] = frame[time_col].map(parse_month)
    frame = frame.loc[frame["month"].notna()].copy()
    frame["value"] = pd.to_numeric(frame[value_col], errors="coerce")
    frame = frame.loc[frame["value"].notna() & (frame["value"] > 0)]
    if frame.empty:
        return pd.Series(dtype="float64")
    # Duplicate months with identical values are safe; conflicting values are not.
    conflicts = frame.groupby("month")["value"].nunique()
    if (conflicts > 1).any():
        examples = ", ".join(str(p) for p in conflicts[conflicts > 1].index[:4])
        raise IMFError(f"IMF returned conflicting values for months: {examples}. Check the series dimensions.")
    return frame.drop_duplicates("month").set_index("month")["value"].sort_index().astype(float)


def parse_xml(payload: bytes) -> pd.Series:
    """Parse SDMX-ML generic/structure-specific observations."""
    root = ET.fromstring(payload)
    values: list[tuple[pd.Period, float]] = []
    for obs in root.iter():
        if obs.tag.rsplit("}", 1)[-1] != "Obs":
            continue
        month = parse_month(obs.attrib.get("TIME_PERIOD", ""))
        raw = obs.attrib.get("OBS_VALUE")
        for child in obs:
            name = child.tag.rsplit("}", 1)[-1]
            if name == "ObsDimension":
                month = parse_month(child.attrib.get("value", ""))
            elif name == "ObsValue":
                raw = child.attrib.get("value")
        try:
            value = float(raw) if raw is not None else float("nan")
        except ValueError:
            continue
        if month is not None and pd.notna(value) and value > 0:
            values.append((month, value))
    if not values:
        return pd.Series(dtype="float64")
    frame = pd.DataFrame(values, columns=["month", "value"])
    conflicts = frame.groupby("month")["value"].nunique()
    if (conflicts > 1).any():
        raise IMFError("IMF XML contains conflicting values for the same month.")
    return frame.drop_duplicates("month").set_index("month")["value"].sort_index()


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_series(iso3: str, indicator: str) -> pd.Series:
    """Fetch one explicitly keyed monthly IMF series; never substitute other indicators."""
    key = f"{iso3}.{indicator}.M"
    # IMF external SDMX 3.0: dataflow ID and complete dimension key.
    # Dataflow names differ across IMF migrations; try both the user-specified
    # compact indicator dataflow and the legacy-compatible IFS dataflow.
    endpoints = [
        f"{BASE}/data/IMF.STA,IFS,1.0/{key}",
        f"{BASE}/data/IMF.STA,IFS,1.1/{key}",
    ]
    errors = []
    for endpoint in endpoints:
        try:
            response = requests.get(
                endpoint,
                params={"startPeriod": "1976-01", "format": "csvfile"},
                headers={"Accept": "text/csv, application/vnd.sdmx.csv;version=2, application/vnd.sdmx.csv"},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            content = response.content
            series = parse_xml(content) if content.lstrip().startswith(b"<") else parse_csv(content)
            if not series.empty:
                return series.loc[series.index >= START]
            errors.append("No positive monthly observations")
        except (requests.RequestException, ValueError, ET.ParseError, IMFError) as exc:
            errors.append(str(exc)[:180])
    raise IMFError(f"Could not retrieve {key}. IMF API responses: {' | '.join(errors)}")


def compute(country1: str, country2: str) -> pd.DataFrame:
    cpi1 = fetch_series(country1, "CPI._T.IX")
    cpi2 = fetch_series(country2, "CPI._T.IX")
    fx1 = fetch_series(country1, "XDC_USD.PA_RT")
    fx2 = fetch_series(country2, "XDC_USD.PA_RT")
    df = pd.concat({"P1": cpi1, "P2": cpi2, "XDC_per_USD_1": fx1, "XDC_per_USD_2": fx2}, axis=1)
    # (Country 2 currency / USD) / (Country 1 currency / USD)
    df["S"] = df["XDC_per_USD_2"] / df["XDC_per_USD_1"]
    df["RER"] = df["S"] * df["P1"] / df["P2"]
    return df.sort_index()


st.set_page_config(page_title="IMF Real Exchange Rate Explorer", page_icon="🌍", layout="wide")
st.title("IMF Real Exchange Rate Explorer")
st.caption("Monthly CPI and exchange-rate data · IMF · January 1976 onward where available")

names = sorted(COUNTRIES)
left, right = st.columns(2)
with left:
    name1 = st.selectbox("Country 1 (base currency)", names, index=names.index("United States"))
with right:
    name2 = st.selectbox("Country 2 (quote currency)", names, index=names.index("Euro area"))
if name1 == name2:
    st.warning("Select two different economies.")
    st.stop()

st.latex(r"S_t = \frac{\text{Country 2 currency}}{\text{Country 1 currency}},\qquad RER_t=S_t\frac{P_{1,t}}{P_{2,t}}")

try:
    with st.spinner("Fetching monthly IMF data…"):
        full = compute(COUNTRIES[name1], COUNTRIES[name2])
except IMFError as exc:
    st.error(str(exc))
    st.info("The IMF may not publish these series for the selected economy, or its SDMX dataflow may be temporarily unavailable. Try another country pair or retry later.")
    st.stop()

valid = full.dropna(subset=["RER", "S"])
if valid.empty:
    st.warning("There are no months with both CPI series and both exchange-rate series. Try another pair.")
    st.stop()

first, last = valid.index.min(), valid.index.max()
st.caption(f"Overlapping observations available: {first} to {last} ({len(valid):,} valid months).")
col_a, col_b, col_c = st.columns([1, 1, 1])
with col_a:
    start_date = st.date_input("Start month", value=first.to_timestamp().date(), min_value=first.to_timestamp().date(), max_value=last.to_timestamp().date())
with col_b:
    end_date = st.date_input("End month", value=last.to_timestamp().date(), min_value=first.to_timestamp().date(), max_value=last.to_timestamp().date())
with col_c:
    view = st.radio("Display", ["Real", "Nominal", "Both"], horizontal=True, index=2)
start = pd.Period(start_date, freq="M")
end = pd.Period(end_date, freq="M")
if start > end:
    st.error("Start month must be before or equal to end month.")
    st.stop()

# Reindex to preserve gaps as missing rows in CSV and breaks in charts.
months = pd.period_range(start, end, freq="M")
sample = full.reindex(months).copy()
benchmark = sample["RER"].mean(skipna=True)
if pd.isna(benchmark) or benchmark <= 0:
    st.warning("The selected period contains no valid RER observations. Expand the date range.")
    st.stop()
sample["RER_star"] = benchmark
sample["Real_Index"] = 100 * sample["RER"] / benchmark
sample.index.name = "Month"

m1, m2, m3 = st.columns(3)
m1.metric("Average RER (RER*)", f"{benchmark:,.4f}")
m2.metric("Valid RER months", f"{sample['RER'].count():,} / {len(sample):,}")
m3.metric("Latest real index in sample", f"{sample['Real_Index'].dropna().iloc[-1]:,.2f}")

x = sample.index.to_timestamp()
fig = go.Figure()
if view in ("Real", "Both"):
    fig.add_trace(go.Scatter(x=x, y=sample["Real_Index"], mode="lines", name="Real index (sample mean = 100)", connectgaps=False, yaxis="y"))
    fig.add_hline(y=100, line_dash="dot", line_color="gray")
if view in ("Nominal", "Both"):
    fig.add_trace(go.Scatter(x=x, y=sample["S"], mode="lines", name=f"Nominal S ({name2} / {name1})", connectgaps=False, yaxis="y2" if view == "Both" else "y"))
fig.update_layout(
    title=f"{name1} vs {name2} · {start} to {end}",
    xaxis_title="Month", hovermode="x unified", legend=dict(orientation="h", y=1.12),
    yaxis=dict(title="Real index (sample mean = 100)" if view != "Nominal" else f"Country 2 currency per Country 1 currency"),
    margin=dict(t=85),
)
if view == "Both":
    fig.update_layout(yaxis2=dict(title="Nominal S (actual currency units)", overlaying="y", side="right", showgrid=False))
st.plotly_chart(fig, use_container_width=True)

export = sample.reset_index()
export["Month"] = export["Month"].astype(str)
st.download_button("Download sample CSV", data=export.to_csv(index=False).encode("utf-8"), file_name=f"imf_rer_{COUNTRIES[name1]}_{COUNTRIES[name2]}_{start}_{end}.csv", mime="text/csv")
with st.expander("Data definitions and limitations"):
    st.markdown("""
- **P1, P2:** IMF monthly CPI index levels. **XDC_per_USD_1/2:** domestic currency per US dollar.
- **S = XDC_per_USD_2 / XDC_per_USD_1**, in Country 2 currency per unit of Country 1 currency.
- **RER = S × P1 / P2**. **RER\*** is the arithmetic mean of valid RER values *within the selected sample*.
- **Real_Index = 100 × RER / RER\***. Nominal S is not normalized.
- Missing, zero, or invalid source values are not filled. The CSV includes missing months with blank calculations.
- IMF series availability varies by economy and time; CPI base years can differ, so the real index is benchmarked to the chosen sample.
- IMF SDMX dataflows and indicator availability may change; API errors are reported rather than replaced with invented data.
""")

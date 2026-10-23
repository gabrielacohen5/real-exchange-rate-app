"""IMF monthly real and nominal exchange-rate explorer (Python 3.12)."""
from __future__ import annotations

import io
import re
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

BASE = "https://api.imf.org/external/sdmx/3.0"
START = "1976-01"
TIMEOUT = 35

# IMF country list via SDMX codelist; fallback list ensures the UI works if metadata is unavailable.
FALLBACK = {
    "Australia": "AUS", "Brazil": "BRA", "Canada": "CAN", "China": "CHN",
    "Euro area": "G163", "France": "FRA", "Germany": "DEU", "India": "IND",
    "Japan": "JPN", "Mexico": "MEX", "South Africa": "ZAF",
    "South Korea": "KOR", "Switzerland": "CHE", "United Kingdom": "GBR",
    "United States": "USA", "Argentina": "ARG", "Chile": "CHL", "Colombia": "COL",
    "Indonesia": "IDN", "New Zealand": "NZL", "Norway": "NOR", "Sweden": "SWE",
    "Türkiye": "TUR", "Singapore": "SGP", "Thailand": "THA",
}

session = requests.Session()
session.headers.update({"User-Agent": "IMF-RER-Explorer/1.0", "Accept": "application/vnd.sdmx.csv;version=2, text/csv;q=0.9, application/csv;q=0.8"})


def _csv_series(payload: bytes, country: str) -> pd.Series:
    df = pd.read_csv(io.BytesIO(payload), low_memory=False)
    df.columns = [str(c).strip() for c in df.columns]
    date_col = next((c for c in ("TIME_PERIOD", "TIME_PERIOD_ID", "TIME") if c in df), None)
    value_col = next((c for c in ("OBS_VALUE", "OBS_VALUE_ID", "VALUE") if c in df), None)
    if date_col is None or value_col is None:
        raise ValueError(f"Unexpected IMF CSV columns: {', '.join(df.columns[:15])}")
    if "REF_AREA" in df.columns:
        df = df[df["REF_AREA"].astype(str).str.upper() == country]
    dates = pd.to_datetime(df[date_col].astype(str), errors="coerce")
    values = pd.to_numeric(df[value_col], errors="coerce")
    result = pd.Series(values.to_numpy(), index=dates.dt.to_period("M"), dtype="float64")
    result = result[result.index.notna()].groupby(level=0).first().sort_index()
    return result


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_series(country: str, indicator: str) -> pd.Series:
    """Query current IMF SDMX dataflows, reporting failures transparently."""
    key = f"{country}.{indicator}.M"
    # CPI and exchange-rate series may reside in different IMF dataflows.
    # The SDMX 3.0 route requires /data/dataflow/{agency}/{flow}/+/{key}.
    flows = ("CPI", "IFS") if indicator.startswith("CPI") else ("ER", "IFS")
    errors = []
    for flow in flows:
        url = f"{BASE}/data/dataflow/IMF.STA/{flow}/+/{key}"
        try:
            response = session.get(
                url,
                params={"startPeriod": START},
                headers={"Accept": "application/vnd.sdmx.data+csv;version=2.0"},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            if not response.content.strip():
                errors.append(f"{flow}: empty response")
                continue
            series = _csv_series(response.content, country)
            if not series.empty:
                return series
            errors.append(f"{flow}: no matching monthly observations")
        except (requests.RequestException, ValueError, pd.errors.ParserError) as exc:
            errors.append(f"{flow}: {str(exc)[:140]}")
    raise ValueError(
        f"IMF data unavailable for {country} / {indicator}. "
        + " | ".join(errors)
        + ". Confirm current dataflow IDs and series dimensions in the IMF Data Portal."
    )


@st.cache_data(ttl=86400, show_spinner=False)
def get_countries() -> dict[str, str]:
    """Load IMF reference-area codelist where available, with a reliable fallback."""
    urls = [f"{BASE}/codelist/IMF/CL_AREA/+/", f"{BASE}/codelist/IMF/CL_REF_AREA/+/" ]
    names = dict(FALLBACK)
    for url in urls:
        try:
            r = session.get(url, headers={"Accept": "application/vnd.sdmx.structure+json;version=2"}, timeout=12)
            r.raise_for_status()
            obj = r.json()
            lists = obj.get("data", {}).get("codelists", [])
            for cl in lists:
                for code in cl.get("codes", []):
                    ident = str(code.get("id", ""))
                    if not re.fullmatch(r"[A-Z]{3}", ident):
                        continue
                    label = code.get("name", ident)
                    if isinstance(label, dict):
                        label = label.get("en") or next(iter(label.values()), ident)
                    if isinstance(label, list):
                        label = next((x.get("value") for x in label if x.get("locale") == "en"), ident)
                    if isinstance(label, str) and label:
                        names.setdefault(label, ident)
            if len(names) > len(FALLBACK):
                break
        except (requests.RequestException, ValueError, KeyError, TypeError):
            continue
    names["Euro area"] = "G163"
    return dict(sorted(names.items()))


def compute(country1: str, country2: str, start: str, end: str) -> tuple[pd.DataFrame, float]:
    p1 = fetch_series(country1, "CPI._T.IX")
    p2 = fetch_series(country2, "CPI._T.IX")
    e1 = fetch_series(country1, "XDC_USD.PA_RT")
    e2 = fetch_series(country2, "XDC_USD.PA_RT")
    # E_i = units of local currency i per USD. Therefore S (currency 2 / currency 1) = E2 / E1.
    df = pd.concat({"P1": p1, "P2": p2, "E1_per_USD": e1, "E2_per_USD": e2}, axis=1)
    start_period = pd.Period(start, freq="M")
    end_period = pd.Period(end, freq="M")
    df.index = pd.PeriodIndex(df.index, freq="M")
    df = df.sort_index()
    df = df.loc[
        (df.index >= start_period) &
        (df.index <= end_period)
    ].copy()
    df["Nominal_S"] = df["E2_per_USD"] / df["E1_per_USD"]
    df["RER"] = df["Nominal_S"] * df["P1"] / df["P2"]
    df = df.replace([float("inf"), float("-inf")], float("nan"))
    df.loc[(df[["P1", "P2", "E1_per_USD", "E2_per_USD"]] <= 0).any(axis=1), ["Nominal_S", "RER"]] = float("nan")
    valid = df["RER"].dropna()
    if valid.empty:
        raise ValueError("No months have all four positive CPI and exchange-rate observations in this period.")
    benchmark = float(valid.mean())
    df["RER_star"] = benchmark
    df["Real_Index"] = 100 * df["RER"] / benchmark
    df.index = df.index.astype(str)
    df.index.name = "Month"
    return df, benchmark


st.set_page_config(page_title="IMF Exchange Rate Explorer", page_icon="🌐", layout="wide")
st.title("IMF Real & Nominal Exchange Rate Explorer")
st.caption("Monthly IMF data · sample-average real exchange rate = 100 · Python 3.12")

countries = get_countries()
labels = list(countries)
left, right = st.columns(2)
with left:
    c1 = st.selectbox("Country 1 (base currency)", labels, index=labels.index("United States") if "United States" in labels else 0)
with right:
    default2 = "Euro area" if "Euro area" in labels else labels[min(1, len(labels)-1)]
    c2 = st.selectbox("Country 2 (quote currency)", labels, index=labels.index(default2))

range1, range2, chart_col = st.columns([1, 1, 1])
with range1:
    start = st.date_input("Start month", value=date(1999, 1, 1), min_value=date(1976, 1, 1), max_value=date.today(), format="YYYY-MM-DD")
with range2:
    end = st.date_input("End month", value=date.today().replace(day=1), min_value=date(1976, 1, 1), max_value=date.today(), format="YYYY-MM-DD")
with chart_col:
    mode = st.radio("Show", ["Real", "Nominal", "Both"], horizontal=True, index=2)

start_month, end_month = start.strftime("%Y-%m"), end.strftime("%Y-%m")
if countries[c1] == countries[c2]:
    st.warning("Choose two different countries or areas.")
    st.stop()
if start_month > end_month:
    st.error("Start month must not be after end month.")
    st.stop()

try:
    with st.spinner("Retrieving monthly CPI and exchange rates from IMF…"):
        df, benchmark = compute(countries[c1], countries[c2], start_month, end_month)
except ValueError as exc:
    st.error(str(exc))
    st.info("The IMF API may use different dataflow versions or may not publish the selected series. Check the IMF Data Portal and try another country or date range.")
    st.stop()

st.metric("Sample-average RER (RER*)", f"{benchmark:,.6g}")
st.caption(f"S(t) = units of {c2} currency per 1 unit of {c1} currency. Valid RER months: {df['RER'].notna().sum()} of {len(df)} selected months.")
fig = go.Figure()
if mode in ("Real", "Both"):
    fig.add_trace(go.Scatter(x=df.index, y=df["Real_Index"], name="Real index (sample mean = 100)", mode="lines", yaxis="y"))
if mode in ("Nominal", "Both"):
    fig.add_trace(go.Scatter(x=df.index, y=df["Nominal_S"], name=f"Nominal S ({c2}/{c1})", mode="lines", yaxis="y2" if mode == "Both" else "y"))
fig.update_layout(
    hovermode="x unified", height=520, margin=dict(l=30, r=45, t=30, b=30),
    xaxis=dict(title="Month", type="category", nticks=14),
    yaxis=dict(title="Real index (100 = sample average)" if mode != "Nominal" else f"{c2} currency / {c1} currency"),
    legend=dict(orientation="h", y=1.12),
)
if mode == "Both":
    fig.update_layout(yaxis2=dict(title=f"Nominal: {c2} currency / {c1} currency", overlaying="y", side="right", showgrid=False))
st.plotly_chart(fig, use_container_width=True)
st.download_button("Download selected sample as CSV", df.to_csv().encode("utf-8"), file_name=f"imf_rer_{countries[c1]}_{countries[c2]}_{start_month}_{end_month}.csv", mime="text/csv")
with st.expander("View monthly observations and calculation details"):
    st.dataframe(df, use_container_width=True)
    st.markdown("**Formulas:** `S = E2_per_USD / E1_per_USD`; `RER = S × P1 / P2`; `RER* = mean(valid monthly RER in selected period)`; `Real Index = 100 × RER / RER*`. Missing observations are retained as blanks in the CSV and excluded from the mean.")


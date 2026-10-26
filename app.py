"""IMF monthly bilateral real exchange-rate dashboard (Python 3.12)."""
from __future__ import annotations

from datetime import date
from io import StringIO
import time

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import pycountry
import requests
import streamlit as st

API = "https://api.imf.org/external/sdmx/3.0"
FIRST_MONTH = pd.Period("1976-01", freq="M")

# ISO 3166 country names/codes provide a searchable selection. IMF coverage is
# determined by the API, not by this list; some ISO territories lack IMF data.
COUNTRIES = {f"{c.name} ({c.alpha_3})": c.alpha_3 for c in pycountry.countries}
COUNTRIES["Euro area (G163)"] = "G163"
COUNTRIES = dict(sorted(COUNTRIES.items()))
CURRENCY_NAMES = {c.alpha_3: getattr(c, "name", c.alpha_3) for c in pycountry.countries}


def parse_monthly_csv(content: str, country: str) -> pd.Series:
    """Parse IMF SDMX-CSV with named columns; never silently choose a series."""
    frame = pd.read_csv(StringIO(content), low_memory=False)
    frame.columns = frame.columns.str.strip().str.upper()
    required = {"TIME_PERIOD", "OBS_VALUE"}
    if not required.issubset(frame.columns):
        raise ValueError(f"Unexpected IMF CSV columns: {list(frame.columns)}")
    if "COUNTRY" in frame.columns:
        frame = frame.loc[frame["COUNTRY"].astype(str).str.upper() == country]
    # SDMX CSV sometimes includes additional metadata rows or series.
    periods = frame["TIME_PERIOD"].astype(str).str.replace(r"-M(\d{2})$", r"-\1", regex=True)
    frame = frame.assign(month=pd.to_datetime(periods, format="%Y-%m", errors="coerce"),
                         value=pd.to_numeric(frame["OBS_VALUE"], errors="coerce"))
    frame = frame.dropna(subset=["month", "value"])
    if frame.empty:
        return pd.Series(dtype="float64", name=country)
    if frame["month"].duplicated().any():
        raise ValueError(f"IMF returned multiple observations for {country} in the same month.")
    return frame.set_index("month")["value"].sort_index().rename(country)


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def fetch_imf(country: str, indicator: str) -> pd.Series:
    """Fetch a single monthly series from IMF.STA's CPI or ER dataflow."""
    if indicator == "FX" and country == "USA":
        # USD per USD is identically 1; no IMF observation is necessary.
        months = pd.period_range(FIRST_MONTH, pd.Period(date.today(), freq="M"), freq="M")
        return pd.Series(1.0, index=months.to_timestamp(), name=country)

    if indicator == "CPI":
        flow, key = "CPI", f"{country}.CPI._T.IX.M"
    elif indicator == "FX":
        flow, key = "ER", f"{country}.XDC_USD.PA_RT.M"
    else:
        raise ValueError("Unknown indicator")

    url = f"{API}/data/dataflow/IMF.STA/{flow}/~/{key}"
    params = {"c[TIME_PERIOD]": "ge:1976-M01"}
    headers = {"Accept": "text/csv", "User-Agent": "streamlit-imf-rer-demo/1.0"}
    error = None
    for attempt in range(3):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=35)
            if response.status_code in (204, 404):
                return pd.Series(dtype="float64", name=country)
            response.raise_for_status()
            if not response.text.strip():
                return pd.Series(dtype="float64", name=country)
            return parse_monthly_csv(response.text, country)
        except (requests.RequestException, ValueError) as exc:
            error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError(f"Unable to retrieve {indicator} for {country}: {error}")


def calculate(country1: str, country2: str) -> pd.DataFrame:
    """Return all monthly observations and the unnormalized bilateral RER."""
    p1 = fetch_imf(country1, "CPI").rename("P1_CPI")
    p2 = fetch_imf(country2, "CPI").rename("P2_CPI")
    fx1 = fetch_imf(country1, "FX").rename("FX1_local_per_USD")
    fx2 = fetch_imf(country2, "FX").rename("FX2_local_per_USD")
    months = pd.period_range(FIRST_MONTH, pd.Period(date.today(), freq="M"), freq="M").to_timestamp()
    df = pd.DataFrame(index=months)
    df.index.name = "Month"
    for series in (p1, p2, fx1, fx2):
        df = df.join(series, how="left")
    for col in ("P1_CPI", "P2_CPI", "FX1_local_per_USD", "FX2_local_per_USD"):
        df.loc[df[col] <= 0, col] = float("nan")
    # IMF FX_i is local currency i per USD. Hence FX2 / FX1 is
    # currency 2 per currency 1, including when either country is USA.
    df["S_currency2_per_currency1"] = df["FX2_local_per_USD"] / df["FX1_local_per_USD"]
    df["RER"] = df["S_currency2_per_currency1"] * df["P1_CPI"] / df["P2_CPI"]
    return df



st.set_page_config(page_title="Real Exchange Rate Explorer", page_icon="🌍", layout="wide")
st.markdown("""<style>
.block-container {padding-top:2rem;max-width:1350px}
h1,h2,h3 {color:#172a46}
[data-testid="stSidebar"] {background:#f1f4f8}
</style>""", unsafe_allow_html=True)

labels = list(COUNTRIES)
def chosen(code: str) -> int:
    return next((i for i, label in enumerate(labels) if COUNTRIES[label] == code), 0)

with st.sidebar:
    st.header("Analysis settings")
    label1 = st.selectbox("Country 1 (base currency)", labels, index=chosen("USA"))
    label2 = st.selectbox("Country 2 (quote currency)", labels, index=chosen("GBR"))
    current = pd.Period(date.today(), freq="M")
    years = list(range(1976, current.year + 1))
    month_names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    with st.form("analysis_form"):
        start_year = st.selectbox("Start year", years, index=years.index(2016))
        start_month = st.selectbox("Start month", month_names, index=0)
        end_year = st.selectbox("End year", years, index=years.index(min(2023, current.year)))
        end_month = st.selectbox("End month", month_names, index=11)
        view = st.radio("Display", ["Real", "Nominal", "Both"], index=2, horizontal=True)
        submitted = st.form_submit_button("Analyze exchange rates", use_container_width=True, type="primary")
    st.caption("Source: IMF SDMX 3.0 • Monthly CPI and exchange rates")

country1, country2 = COUNTRIES[label1], COUNTRIES[label2]
start = pd.Period(f"{start_year}-{month_names.index(start_month)+1:02d}", freq="M")
end = pd.Period(f"{end_year}-{month_names.index(end_month)+1:02d}", freq="M")
st.title("🌍 Real Exchange Rate Explorer")
st.caption("Monthly IMF observations · Inflation-adjusted currency comparisons")
st.write("Compare currencies using monthly consumer prices and exchange rates. The real index averages 100 over your selected period.")
if country1 == country2:
    st.warning("Select two different countries or areas.")
    st.stop()
if start > end:
    st.error("Start month must not be later than end month.")
    st.stop()
if end > pd.Period(date.today(), freq="M"):
    st.error("End month cannot be in the future.")
    st.stop()

with st.spinner("Retrieving IMF monthly observations..."):
    try:
        all_data = calculate(country1, country2)
    except Exception as exc:
        st.error(f"Unable to retrieve or parse IMF data: {exc}")
        st.stop()

sample = all_data.loc[start.to_timestamp():end.to_timestamp()].copy()
valid = sample["RER"].dropna()
with st.expander("Data availability and diagnostics", expanded=valid.empty):
    cols = ["P1_CPI", "P2_CPI", "FX1_local_per_USD", "FX2_local_per_USD", "RER"]
    diagnostics = []
    for col in cols:
        series = all_data[col].dropna()
        diagnostics.append({"Series": col, "All valid months": len(series),
                            "First available": series.index.min().strftime("%Y-%m") if len(series) else "—",
                            "Last available": series.index.max().strftime("%Y-%m") if len(series) else "—",
                            "Valid in selected period": int(sample[col].notna().sum())})
    st.dataframe(pd.DataFrame(diagnostics), hide_index=True, use_container_width=True)
    st.caption("Missing observations are never interpolated or fabricated. USD/USD is defined as 1.")

if valid.empty:
    st.warning("No overlapping valid CPI and exchange-rate observations in the selected period. See diagnostics above; choose a period with overlapping coverage.")
    st.stop()
benchmark = valid.mean()
sample["RER_star"] = benchmark
sample["Real_Index"] = 100 * sample["RER"] / benchmark
sample["Country_1"] = country1
sample["Country_2"] = country2

m1, m2, m3 = st.columns(3)
m1.metric("Benchmark RER*", f"{benchmark:,.5f}")
m2.metric("Valid months", f"{len(valid)} / {len(sample)}")
m3.metric("Mean Real Index", f"{sample['Real_Index'].mean():.2f}")
st.caption(f"Nominal exchange rate: units of {country2} currency per 1 unit of {country1} currency. Real Index: sample mean = 100.")

fig = make_subplots(specs=[[{"secondary_y": True}]])
if view in ("Real", "Both"):
    fig.add_trace(go.Scatter(x=sample.index, y=sample["Real_Index"], name="Real Exchange Rate Index",
                             mode="lines", line=dict(color="#1876b9", width=2.7),
                             hovertemplate="%{x|%Y-%m}<br>Real Index: %{y:.2f}<extra></extra>"), secondary_y=False)
    fig.add_hline(y=100, line_dash="dash", line_color="#8794a5", opacity=0.8,
                  annotation_text="Benchmark = 100", annotation_position="top left")
if view in ("Nominal", "Both"):
    fig.add_trace(go.Scatter(x=sample.index, y=sample["S_currency2_per_currency1"], name="Nominal Exchange Rate",
                             mode="lines", line=dict(color="#ed9140", width=2.7),
                             hovertemplate="%{x|%Y-%m}<br>Nominal: %{y:.5f}<extra></extra>"), secondary_y=True)
fig.update_yaxes(title_text="Real Exchange Rate Index (mean = 100)", secondary_y=False,
                 visible=view != "Nominal", showgrid=view != "Nominal")
fig.update_yaxes(title_text=f"Nominal ({country2} per {country1})", secondary_y=True,
                 visible=view != "Real", showgrid=False)
fig.update_layout(title=f"{country1} / {country2} · {start} to {end}", height=510,
                  hovermode="x unified", legend=dict(orientation="h", y=1.1),
                  margin=dict(l=40, r=40, t=100, b=30), template="plotly_white")
st.plotly_chart(fig, use_container_width=True)
st.info("An increase in the Real Index indicates real depreciation of Country 1 relative to Country 2; a decrease indicates real appreciation. The index is relative to the selected-period average, not an absolute PPP valuation.")

st.subheader("Monthly observations and calculations")
export = sample.reset_index().rename(columns={"Month":"Date", "P1_CPI":"CPI_Country_1", "P2_CPI":"CPI_Country_2",
     "FX1_local_per_USD":"FX1_local_per_USD", "FX2_local_per_USD":"FX2_local_per_USD",
     "S_currency2_per_currency1":"Nominal_S", "RER_star":"Benchmark_RER_star", "Real_Index":"Real_Exchange_Rate_Index"})
export = export[["Date", "Country_1", "Country_2", "CPI_Country_1", "CPI_Country_2",
                 "FX1_local_per_USD", "FX2_local_per_USD", "Nominal_S", "RER", "Benchmark_RER_star", "Real_Exchange_Rate_Index"]]
st.dataframe(export, hide_index=True, use_container_width=True)
st.download_button("⬇ Download selected data (CSV)", export.to_csv(index=False).encode("utf-8-sig"),
                   file_name=f"rer_{country1}_{country2}_{start}_{end}.csv", mime="text/csv")
with st.expander("Methodology and interpretation"):
    st.latex(r"S_t=\frac{FX_{2,t}}{FX_{1,t}},\qquad RER_t=S_t\frac{P_{1,t}}{P_{2,t}}")
    st.latex(r"RER^*=\operatorname{mean}(RER_t),\qquad RealIndex_t=100\frac{RER_t}{RER^*}")
    st.markdown("""IMF FX rates are local currency per US dollar; their ratio yields Country 2 currency per Country 1 currency. USA FX is fixed at 1. The benchmark is recalculated from valid months in the selected window. CPI series can have different base periods; the normalized index is a relative measure, not an absolute PPP valuation. Some IMF country/area series may have limited coverage.""")

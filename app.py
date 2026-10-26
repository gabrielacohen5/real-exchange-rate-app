"""IMF monthly bilateral real exchange-rate dashboard (Python 3.12)."""
from __future__ import annotations
from datetime import date
from io import StringIO
import time
import altair as alt
import pandas as pd
import pycountry
import requests
import streamlit as st
API = "https://api.imf.org/external/sdmx/3.0"
FIRST_MONTH = pd.Period("1976-01", freq="M")
ISO 3166 country names/codes provide a searchable selection. IMF coverage is
determined by the API, not by this list; some ISO territories lack IMF data.
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

st.set_page_config(page_title="IMF Real Exchange Rate Explorer", page_icon="🌍", layout="wide")
st.title("🌍 IMF Real Exchange Rate Explorer")
st.caption("Monthly IMF CPI and exchange-rate series · January 1976 onward, where available")
st.latex(r"S_t = \frac{FX_{2,t}}{FX_{1,t}},\qquad RER_t=S_t\frac{P_{1,t}}{P_{2,t}},\qquad I_t=100\frac{RER_t}{\overline{RER}_{\mathrm{sample}}}")
labels = list(COUNTRIES)
def chosen(code: str) -> int:
return next((i for i, label in enumerate(labels) if COUNTRIES[label] == code), 0)
c1, c2 = st.columns(2)
with c1:
label1 = st.selectbox("Country 1 (base currency)", labels, index=chosen("USA"))
with c2:
label2 = st.selectbox("Country 2 (quote currency)", labels, index=chosen("GBR"))
country1, country2 = COUNTRIES[label1], COUNTRIES[label2]
if country1 == country2:
st.warning("Choose two different countries or areas.")
st.stop()
with st.spinner("Downloading IMF monthly CPI and exchange-rate observations..."):
try:
all_data = calculate(country1, country2)
except Exception as exc:
st.error(str(exc))
st.info("The IMF service may be temporarily unavailable, or its dataset/key may have changed. See the README troubleshooting section.")
st.stop()
valid = all_data["RER"].dropna()
if valid.empty:
st.warning("No overlapping, valid CPI and FX observations were found for this pair. Try another pair. IMF coverage varies by country and period.")
with st.expander("IMF series availability"):
st.dataframe(all_data.notna().sum().rename("Nonmissing months"))
st.stop()
available_start, available_end = valid.index.min().to_period("M"), valid.index.max().to_period("M")
st.caption(f"Overlapping RER coverage: {available_start} to {available_end} ({len(valid):,} valid months)")
min_month = FIRST_MONTH
max_month = pd.Period(date.today(), freq="M")
months = pd.period_range(min_month, max_month, freq="M")
month_options = [str(m) for m in months]
a, b, c = st.columns([1, 1, 1])
with a:
start_str = st.selectbox("Start month", month_options, index=months.get_loc(available_start))
with b:
end_str = st.selectbox("End month", month_options, index=months.get_loc(available_end))
with c:
view = st.radio("Show series", ["Both", "Real", "Nominal"], horizontal=True)
start, end = pd.Period(start_str, freq="M"), pd.Period(end_str, freq="M")
if start > end:
st.error("Start month must not be later than end month.")
st.stop()
sample = all_data.loc[start.to_timestamp():end.to_timestamp()].copy()
mean_rer = sample["RER"].mean(skipna=True)
if pd.isna(mean_rer) or mean_rer <= 0:
st.warning("No valid real exchange-rate observations in the selected period. Adjust the dates.")
st.stop()
sample["RER_star_sample_mean"] = mean_rer
sample["Real_Index"] = 100 * sample["RER"] / mean_rer
sample["Valid_RER"] = sample["RER"].notna()
m1, m2, m3 = st.columns(3)
m1.metric("Sample mean RER (RER)", f"{mean_rer:,.4f}")
m2.metric("Valid months", f"{sample['Valid_RER'].sum():,} / {len(sample):,}")
m3.metric("Mean Real Index", f"{sample['Real_Index'].mean():.2f}")
st.caption(f"Nominal S: units of {country2} currency per 1 unit of {country1} currency. "
"Real Index = 100 at the selected sample's mean RER.")
chart_data = sample.reset_index()
base = alt.Chart(chart_data).encode(x=alt.X("Month:T", title="Month"))
real_line = base.mark_line(color="#1677b8", strokeWidth=2).encode(
y=alt.Y("Real_Index:Q", title="Real Index (sample mean = 100)", scale=alt.Scale(zero=False)),
tooltip=[alt.Tooltip("Month:T", format="%Y-%m"), alt.Tooltip("Real_Index:Q", format=".2f")])
nominal_line = base.mark_line(color="#e58b2a", strokeWidth=2).encode(
y=alt.Y("S_currency2_per_currency1:Q", title=f"Nominal S ({country2}/{country1})", scale=alt.Scale(zero=False)),
tooltip=[alt.Tooltip("Month:T", format="%Y-%m"), alt.Tooltip("S_currency2_per_currency1:Q", format=".5f")])
if view == "Both":
chart = alt.layer(real_line, nominal_line).resolve_scale(y="independent")
elif view == "Real":
chart = real_line
else:
chart = nominal_line
st.altair_chart(chart.properties(height=450).interactive(), use_container_width=True)
st.subheader("Monthly observations and calculations")
st.dataframe(sample.reset_index(), use_container_width=True, hide_index=True)
st.download_button("⬇️ Download selected sample (CSV)", data=sample.reset_index().to_csv(index=False).encode("utf-8"),
file_name=f"imf_rer_{country1}_{country2}_{start}_{end}.csv", mime="text/csv")
with st.expander("Methodology and missing data"):
st.markdown("""
Source: IMF SDMX 3.0, IMF.STA CPI and ER dataflows. CPI key: `{ISO3}.CPI._T.IX.M`; FX key: `{ISO3}.XDC_USD.PA_RT.M`.
FX direction: IMF FX is local currency per USD; `S = FX2 / FX1`. USD/USD is fixed at 1.
Real exchange rate: `RER = S × CPI1 / CPI2`.
Benchmark: `RER` is the arithmetic mean over valid overlapping months in the selected window. The Real Index is `100 × RER / RER`.
Missing observations: No interpolation, forward fill, or fabricated historical data. Rows with missing components remain in the CSV, with blank derived values.
Interpretation: CPI index reference bases can differ across countries; this is a relative index around a selected-period mean, not a claim about absolute purchasing-power parity.
Euro area: `G163` is queried as supplied by IMF; historical Euro area CPI/FX coverage may be limited and should not be assumed to exist before the euro.
Country selector: ISO country/territory names are listed for convenience; not every code is an IMF reporting economy or has both required series.
""")

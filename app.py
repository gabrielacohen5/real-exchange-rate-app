"""Real Exchange Rate Explorer — IMF SDMX 3.0 monthly data."""
from __future__ import annotations

import io
import re
from datetime import date

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pycountry
import requests
import streamlit as st

BASE = "https://api.imf.org/external/sdmx/3.0"
EARLIEST = pd.Period("1976-01", freq="M")
TIMEOUT = 35

st.set_page_config(page_title="Real Exchange Rate Explorer", page_icon="🌐", layout="wide")


def countries() -> dict[str, str]:
    result = {f"{c.name} ({c.alpha_3})": c.alpha_3 for c in pycountry.countries}
    result["Euro area (G163)"] = "G163"
    return dict(sorted(result.items()))


def month_bounds(start: pd.Period, end: pd.Period) -> tuple[str, str]:
    return str(start), str(end)


def extract_series(payload: object, indicator: str) -> pd.Series:
    """Parse SDMX-JSON 2.0 observations, using dimension positions, not assumed ordering."""
    if not isinstance(payload, dict) or "data" not in payload or "structures" not in payload:
        raise ValueError("Unexpected IMF response format (expected SDMX-JSON 2.0).")
    structures = payload["structures"]
    datasets = payload["data"]
    if not structures or not datasets:
        return pd.Series(dtype="float64")
    structure = structures[0]
    dims = structure.get("dimensions", {}).get("observation", [])
    time_dim = next((i for i, d in enumerate(dims) if d.get("id", "").upper() in ("TIME_PERIOD", "TIME")), None)
    if time_dim is None:
        raise ValueError("IMF response has no observation time dimension.")
    time_values = dims[time_dim].get("values", [])
    rows = []
    for dataset in datasets:
        for key, observation in dataset.get("observations", {}).items():
            indexes = [int(v) for v in key.split(":")]
            if len(indexes) <= time_dim or indexes[time_dim] >= len(time_values):
                continue
            raw_date = time_values[indexes[time_dim]].get("id")
            raw_value = observation[0] if isinstance(observation, list) and observation else None
            try:
                period = pd.Period(str(raw_date), freq="M")
                value = float(raw_value)
                if pd.notna(value) and value > 0:
                    rows.append((period, value))
            except (ValueError, TypeError):
                continue
    if not rows:
        return pd.Series(dtype="float64", name=indicator)
    frame = pd.DataFrame(rows, columns=["month", "value"])
    # Duplicate months may occur if an IMF response contains multiple series.
    # Never silently average different observations.
    if frame["month"].duplicated().any():
        if frame.groupby("month")["value"].nunique().gt(1).any():
            raise ValueError(f"Multiple conflicting IMF values for {indicator}.")
        frame = frame.drop_duplicates("month")
    return frame.set_index("month")["value"].sort_index().rename(indicator)


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def fetch_indicator(indicator: str, start: str, end: str) -> pd.Series:
    """Fetch one IMF Data Portal indicator from the SDMX 3.0 data endpoint.

    IMF's SDMX 3.0 API requires a dataflow identifier and its series key.
    The requested indicator names are IMF legacy indicator identifiers; their
    availability in a specific SDMX dataflow is not guaranteed.
    """
    # IMF's IFS dataflow (legacy IFS indicator identifiers).
    # The endpoint and dataflow can change with IMF data migrations.
    url = f"{BASE}/data/IMF.STA,IFS,1.0/{indicator}"
    params = {"startPeriod": start, "endPeriod": end, "format": "csvfile"}
    headers = {"Accept": "text/csv, application/vnd.sdmx.csv;version=2"}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"IMF API request failed for {indicator}: {exc}") from exc
    try:
        frame = pd.read_csv(io.StringIO(response.text), low_memory=False)
    except Exception as exc:
        raise ValueError(f"IMF returned unreadable CSV for {indicator}.") from exc
    time_col = next((c for c in frame.columns if c.upper() in ("TIME_PERIOD", "TIME")), None)
    value_col = next((c for c in frame.columns if c.upper() in ("OBS_VALUE", "VALUE")), None)
    if not time_col or not value_col:
        raise ValueError(f"Unexpected IMF CSV columns for {indicator}: {list(frame.columns)[:12]}")
    if "INDICATOR" in frame.columns:
        frame = frame.loc[frame["INDICATOR"].astype(str) == indicator]
    frame = frame[[time_col, value_col]].copy()
    frame["month"] = pd.to_datetime(frame[time_col], errors="coerce").dt.to_period("M")
    frame["value"] = pd.to_numeric(frame[value_col], errors="coerce")
    frame = frame.dropna(subset=["month", "value"])
    frame = frame.loc[frame["value"] > 0]
    if frame["month"].duplicated().any():
        if frame.groupby("month")["value"].nunique().gt(1).any():
            raise ValueError(f"Conflicting values for {indicator} in IMF response")
        frame = frame.drop_duplicates("month")
    if frame.empty:
        return pd.Series(dtype=float, name=indicator)
    return frame.set_index("month")["value"].sort_index().rename(indicator)


def calculate(cpi1: pd.Series, cpi2: pd.Series, fx1: pd.Series, fx2: pd.Series,
              start: pd.Period, end: pd.Period, name1: str, name2: str) -> tuple[pd.DataFrame, float]:
    months = pd.period_range(start, end, freq="M")
    df = pd.DataFrame(index=months)
    df["Country 1"] = name1
    df["Country 2"] = name2
    df["CPI Country 1"] = cpi1.reindex(months)
    df["CPI Country 2"] = cpi2.reindex(months)
    df["FX Country 1 (LCU/USD)"] = fx1.reindex(months)
    df["FX Country 2 (LCU/USD)"] = fx2.reindex(months)
    df["Nominal S (Country 2 / Country 1)"] = df["FX Country 2 (LCU/USD)"] / df["FX Country 1 (LCU/USD)"]
    df["RER"] = df["Nominal S (Country 2 / Country 1)"] * df["CPI Country 1"] / df["CPI Country 2"]
    df["RER"] = df["RER"].where(df["RER"].gt(0) & df["RER"].map(pd.notna))
    benchmark = float(df["RER"].mean())
    df["Benchmark RER*"] = benchmark
    df["Real Index (100 = period mean)"] = 100 * df["RER"] / benchmark if pd.notna(benchmark) and benchmark > 0 else float("nan")
    df.index.name = "Date"
    return df, benchmark


def chart(df: pd.DataFrame, view: str, name1: str, name2: str) -> go.Figure:
    both = view == "Both"
    fig = make_subplots(specs=[[{"secondary_y": True}]]) if both else go.Figure()
    x = df.index.to_timestamp()
    real = df["Real Index (100 = period mean)"]
    nominal = df["Nominal S (Country 2 / Country 1)"]
    if view in ("Real", "Both"):
        trace = go.Scatter(x=x, y=real, name="Real exchange rate index", mode="lines", line=dict(color="#5b50c8", width=2.5), connectgaps=False)
        if both:
            fig.add_trace(trace, secondary_y=False)
            fig.add_hline(y=100, line_dash="dash", line_color="gray", annotation_text="Period mean = 100", secondary_y=False)
        else:
            fig.add_trace(trace)
            fig.add_hline(y=100, line_dash="dash", line_color="gray", annotation_text="Period mean = 100")
    if view in ("Nominal", "Both"):
        trace = go.Scatter(x=x, y=nominal, name="Nominal exchange rate", mode="lines", line=dict(color="#e38b3d", width=2.3), connectgaps=False)
        if both:
            fig.add_trace(trace, secondary_y=True)
        else:
            fig.add_trace(trace)
    if both:
        fig.update_yaxes(title_text="Real index (period average = 100)", secondary_y=False)
        fig.update_yaxes(title_text=f"{name2} currency units per 1 {name1} currency unit", secondary_y=True)
    else:
        fig.update_yaxes(title_text="Real index (period average = 100)" if view == "Real" else f"{name2} currency units per 1 {name1} currency unit")
    fig.update_layout(title=f"{name1} vs {name2}", xaxis_title="Month", hovermode="x unified", legend=dict(orientation="h", y=1.08), height=540, margin=dict(t=90, b=35))
    return fig


def main() -> None:
    st.title("🌐 Real Exchange Rate Explorer")
    st.caption("Monthly IMF observations · Inflation-adjusted currency comparisons")
    st.markdown("Compare currencies using monthly consumer prices and exchange rates. The real index is centered on **100**, the average over your selected period.")
    opts = countries()
    labels = list(opts)
    def default(code: str) -> int:
        return next((i for i, name in enumerate(labels) if opts[name] == code), 0)
    with st.sidebar:
        st.header("Analysis settings")
        c1 = st.selectbox("Country 1 (base currency)", labels, index=default("USA"))
        c2 = st.selectbox("Country 2 (quote currency)", labels, index=default("G163"))
        now = pd.Period(date.today(), freq="M")
        years = list(range(1976, now.year + 1))
        sy = st.selectbox("Start year", years, index=years.index(max(1976, now.year - 10)))
        sm = st.selectbox("Start month", list(range(1, 13)), index=0, format_func=lambda m: date(2000, m, 1).strftime("%B"))
        ey = st.selectbox("End year", years, index=len(years)-1)
        em = st.selectbox("End month", list(range(1, 13)), index=min(now.month, 12)-1, format_func=lambda m: date(2000, m, 1).strftime("%B"))
        view = st.radio("Display", ["Real", "Nominal", "Both"], index=2, horizontal=True)
        run = st.button("Analyze exchange rates", type="primary", use_container_width=True)
    if not run:
        st.info("Choose two countries and a date range, then select **Analyze exchange rates**.")
        return
    start, end = pd.Period(f"{sy}-{sm:02d}", freq="M"), pd.Period(f"{ey}-{em:02d}", freq="M")
    if start < EARLIEST or end > now or start > end:
        st.error("Choose a valid range from January 1976 through the current month, with the start before the end.")
        return
    if opts[c1] == opts[c2]:
        st.error("Select two different countries or currency areas.")
        return
    code1, code2 = opts[c1], opts[c2]
    names = (c1.rsplit(" (", 1)[0], c2.rsplit(" (", 1)[0])
    try:
        with st.spinner("Retrieving IMF observations…"):
            cpi1 = fetch_indicator(f"I{code1}_CPI_TIX_M", str(start), str(end))
            cpi2 = fetch_indicator(f"I{code2}_CPI_TIX_M", str(start), str(end))
            fx1 = fetch_indicator(f"I{code1}_XDC_USD_PA_RT_M", str(start), str(end))
            fx2 = fetch_indicator(f"I{code2}_XDC_USD_PA_RT_M", str(start), str(end))
            df, benchmark = calculate(cpi1, cpi2, fx1, fx2, start, end, *names)
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        st.error(str(exc))
        st.info("The IMF may not publish these legacy indicator keys in the selected SDMX dataflow. Check IMF Data Portal availability and the API dataflow/version.")
        return
    valid = int(df["RER"].notna().sum())
    if valid == 0:
        st.warning("No months contain all four required valid observations. Try a shorter or more recent period or different countries.")
        return
    if valid < len(df):
        st.warning(f"{len(df) - valid} of {len(df)} months lack at least one required value. The benchmark uses only {valid} complete months.")
    a, b, c = st.columns(3)
    a.metric("Complete months", f"{valid} / {len(df)}")
    b.metric("Benchmark RER*", f"{benchmark:,.4f}")
    c.metric("Latest real index", f"{df['Real Index (100 = period mean)'].dropna().iloc[-1]:,.2f}")
    st.plotly_chart(chart(df, view, *names), use_container_width=True)
    st.caption("Index above 100: Country 1 is relatively more depreciated than its selected-period average. Below 100: relatively more appreciated. This is not a causal or equilibrium valuation estimate.")
    st.subheader("Monthly data and calculations")
    export = df.reset_index()
    export["Date"] = export["Date"].astype(str)
    st.dataframe(export, use_container_width=True, hide_index=True)
    st.download_button("Download CSV", data=export.to_csv(index=False).encode("utf-8-sig"), file_name=f"rer_{code1}_{code2}_{start}_{end}.csv", mime="text/csv")
    with st.expander("Calculation methodology"):
        st.latex(r"S_t = E_{2,t}/E_{1,t}")
        st.latex(r"RER_t = S_t (P_{1,t}/P_{2,t})")
        st.latex(r"RER^* = \\frac{1}{N}\\sum_{t\\in\\text{valid months}} RER_t")
        st.latex(r"RealIndex_t = 100\\frac{RER_t}{RER^*}")
        st.write("E is local currency units per USD. The nominal series is not normalized. CPI base-year conventions can affect RER levels, but not the normalized index if consistent within each series.")


if __name__ == "__main__":
    main()

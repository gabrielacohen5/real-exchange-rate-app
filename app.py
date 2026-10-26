"""Real Exchange Rate Explorer — IMF SDMX 3.0 monthly data."""
from __future__ import annotations

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
    """Parse common SDMX-JSON 1.x/2.x envelopes and observation representations.

    IMF SDMX 3.0 is an API protocol version, not a guarantee about the JSON
    representation. Unexpected/error payloads are reported with useful context.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"IMF {indicator}: JSON response was not an object.")

    def describe_error(obj: dict) -> str | None:
        for key in ("error", "errors", "message", "detail", "title"):
            if obj.get(key):
                return str(obj[key])[:500]
        return None

    # Some gateways nest the SDMX payload under 'data'. Others use a top-level
    # 'dataSets' (SDMX-JSON 1.x) or 'data' array (SDMX-JSON 2.x).
    root = payload
    for _ in range(3):
        if any(k in root for k in ("dataSets", "datasets", "structures")):
            break
        nested = root.get("data")
        if isinstance(nested, dict):
            root = nested
        else:
            break

    error = describe_error(root) or describe_error(payload)
    if error and not any(k in root for k in ("dataSets", "datasets", "structures")):
        raise ValueError(f"IMF {indicator} API message: {error}")

    datasets = root.get("dataSets", root.get("datasets", root.get("data", [])))
    structures = root.get("structures", root.get("structure", {}))
    if isinstance(datasets, dict):
        datasets = datasets.get("dataSets", datasets.get("datasets", [datasets]))
    if isinstance(structures, list):
        structure = structures[0] if structures else {}
    else:
        structure = structures
    if not isinstance(datasets, list) or not isinstance(structure, dict):
        raise ValueError(f"IMF {indicator}: unrecognized SDMX JSON structure. "
                         f"Top-level fields: {list(payload)[:12]}. "
                         "Check the requested dataflow and response content type.")

    dimensions = structure.get("dimensions", {})
    if isinstance(dimensions, list):
        # Some SDMX-JSON 2.0 responses represent dimensions as a flat array.
        obs_dims = dimensions
    else:
        obs_dims = dimensions.get("observation", [])
    time_pos = next((i for i, dim in enumerate(obs_dims)
                     if str(dim.get("id", "")).upper() in ("TIME_PERIOD", "TIME")), None)
    if time_pos is None:
        raise ValueError(f"IMF {indicator}: no TIME_PERIOD observation dimension. "
                         f"Structure fields: {list(structure)[:12]}")
    time_values = obs_dims[time_pos].get("values", [])
    rows = []

    def add_observations(observations: object) -> None:
        if not isinstance(observations, dict):
            return
        for key, observation in observations.items():
            try:
                parts = [int(part) for part in str(key).split(":")]
                if len(parts) <= time_pos:
                    continue
                time_item = time_values[parts[time_pos]]
                month_text = time_item.get("id", time_item.get("value", "")) if isinstance(time_item, dict) else str(time_item)
                month = pd.Period(str(month_text)[:7], freq="M")
                if isinstance(observation, dict):
                    raw = observation.get("value", observation.get("obsValue"))
                elif isinstance(observation, (list, tuple)):
                    raw = observation[0] if observation else None
                else:
                    raw = observation
                value = float(raw)
                if pd.notna(value) and 0 < value < float("inf"):
                    rows.append((month, value))
            except (ValueError, TypeError, IndexError, KeyError, OverflowError):
                continue

    for dataset in datasets:
        if not isinstance(dataset, dict):
            continue
        add_observations(dataset.get("observations", {}))
        series_collection = dataset.get("series", {})
        if isinstance(series_collection, dict):
            for series in series_collection.values():
                if isinstance(series, dict):
                    add_observations(series.get("observations", {}))
    if not rows:
        return pd.Series(dtype=float, name=indicator)
    frame = pd.DataFrame(rows, columns=["month", "value"])
    conflicts = (
        frame.groupby("month")["value"]
        .agg(["count", "nunique", "min", "max"]))
    conflicts = conflicts[conflicts["nunique"] > 1]
    if not conflicts.empty:
    raise ValueError(
        f"IMF returned conflicting values for {indicator}. "
        f"Conflict details: {conflicts.head(5).to_dict('index')}")
    return (frame.drop_duplicates("month").set_index("month")["value"]
            .sort_index().rename(indicator))


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def fetch_indicator(code: str, kind: str, start: str, end: str) -> pd.Series:
    """Fetch CPI or FX from the exact IMF.STA SDMX 3.0 dataflow templates."""
    if kind == "CPI":
        flow, key = "CPI", f"{code}.CPI._T.IX.M"
    elif kind == "FX":
        flow, key = "ER", f"{code}.XDC_USD.PA_RT.M"
    else:
        raise ValueError(f"Unsupported series type: {kind}")

    # The IMF examples encode the time constraint as:
    # c[TIME_PERIOD]=ge:YYYY-MM+le:YYYY-MM
    url = f"{BASE}/data/dataflow/IMF.STA/{flow}/+/{key}"
    params = {"c[TIME_PERIOD]": f"ge:{start}+le:{end}"}
    try:
        response = requests.get(
            url, params=params,
            headers={"Accept": "application/vnd.sdmx.data+json;version=2.0, application/vnd.sdmx.data+json;version=1.0;q=0.9, application/json;q=0.8"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"IMF {flow} request failed for {code}: {exc}") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError(f"IMF returned non-JSON data for {code} {flow}.") from exc
    try:
        return extract_series(payload, f"{code} {kind}")
    except ValueError as exc:
        raise ValueError(f"{exc} | URL: {response.url} | Content-Type: "
                         f"{response.headers.get('Content-Type', 'unknown')}") from exc


def usd_fx(start: pd.Period, end: pd.Period) -> pd.Series:
    """USD per USD is exactly one; no IMF exchange-rate call required."""
    return pd.Series(1.0, index=pd.period_range(start, end, freq="M"), name="USA FX")


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
            cpi1 = fetch_indicator(code1, "CPI", str(start), str(end))
            cpi2 = fetch_indicator(code2, "CPI", str(start), str(end))
            fx1 = usd_fx(start, end) if code1 == "USA" else fetch_indicator(code1, "FX", str(start), str(end))
            fx2 = usd_fx(start, end) if code2 == "USA" else fetch_indicator(code2, "FX", str(start), str(end))
            df, benchmark = calculate(cpi1, cpi2, fx1, fx2, start, end, *names)
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        st.error(str(exc))
        st.info("Check the country code, requested months, and IMF data availability. Some countries have limited CPI or FX coverage.")
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
        st.latex(r"RER^* = \frac{1}{N}\sum_{t\in\text{valid months}} RER_t")
        st.latex(r"RealIndex_t = 100\frac{RER_t}{RER^*}")
        st.write("E is local currency units per USD. The nominal series is not normalized. CPI base-year conventions can affect RER levels, but not the normalized index if consistent within each series.")


if __name__ == "__main__":
    main()

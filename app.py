"""Real Exchange Rate Explorer — IMF SDMX 3.0 monthly data."""
from __future__ import annotations

from datetime import date
from io import StringIO

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


def _make_series(rows: list[tuple[pd.Period, float]], indicator: str) -> pd.Series:
    if not rows:
        return pd.Series(dtype=float, name=indicator)
    frame = pd.DataFrame(rows, columns=["month", "value"])
    if frame.groupby("month")["value"].nunique().gt(1).any():
        raise ValueError(f"IMF returned conflicting values for {indicator} in the same month.")
    return frame.drop_duplicates("month").set_index("month")["value"].sort_index().rename(indicator)


def extract_series(payload: object, indicator: str) -> pd.Series:
    """Parse the SDMX-JSON 1.0/2.0 layouts, including series-level observations."""
    if not isinstance(payload, dict):
        raise ValueError("IMF returned a non-object JSON response.")
    if "data" in payload and "structures" in payload:
        datasets = payload.get("data") or []
        structures = payload.get("structures") or []
        structure = structures[0] if structures else {}
    elif "dataSets" in payload and "structure" in payload:
        datasets = payload.get("dataSets") or []
        structure = payload["structure"]
    else:
        raise ValueError(f"Unrecognized IMF JSON keys: {list(payload)[:12]}")
    if isinstance(datasets, dict):
        datasets = [datasets]
    dimensions = structure.get("dimensions", {})
    obs_dims = dimensions.get("observation", [])
    time_pos = next((i for i, d in enumerate(obs_dims)
                     if d.get("id", "").upper() in {"TIME_PERIOD", "TIME"}), None)
    if time_pos is None:
        raise ValueError("IMF JSON response has no TIME_PERIOD observation dimension.")
    time_values = obs_dims[time_pos].get("values", [])
    rows = []

    def add(observations):
        for key, observation in observations.items():
            try:
                idx = int(str(key).split(":")[time_pos])
                month = pd.Period(str(time_values[idx]["id"])[:7], freq="M")
                raw = observation[0] if isinstance(observation, (list, tuple)) else observation
                value = float(raw)
                if pd.notna(value) and 0 < value < float("inf"):
                    rows.append((month, value))
            except (ValueError, TypeError, IndexError, KeyError, OverflowError):
                continue

    for dataset in datasets:
        add(dataset.get("observations", {}))
        for series in dataset.get("series", {}).values():
            add(series.get("observations", {}))
    return _make_series(rows, indicator)


def extract_csv(content: str, indicator: str) -> pd.Series:
    """Parse IMF SDMX-CSV, whose rows contain TIME_PERIOD and OBS_VALUE."""
    try:
        frame = pd.read_csv(StringIO(content), dtype=str, sep=None, engine="python", on_bad_lines="error")
    except (ValueError, pd.errors.ParserError) as exc:
        raise ValueError(f"Could not parse IMF CSV: {exc}") from exc
    frame.columns = [str(col).strip().lstrip("\ufeff").upper() for col in frame.columns]
    if not {"TIME_PERIOD", "OBS_VALUE"}.issubset(frame.columns):
        raise ValueError(f"IMF CSV lacks TIME_PERIOD / OBS_VALUE. Columns: {list(frame.columns)[:12]}")
    rows = []
    for period, raw in zip(frame["TIME_PERIOD"], frame["OBS_VALUE"]):
        try:
            month = pd.Period(str(period)[:7], freq="M")
            value = float(raw)
            if pd.notna(value) and 0 < value < float("inf"):
                rows.append((month, value))
        except (ValueError, TypeError, OverflowError):
            continue
    return _make_series(rows, indicator)


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def fetch_indicator(code: str, kind: str, start: str, end: str) -> pd.Series:
    """Retrieve monthly IMF data, preferring SDMX-CSV over version-dependent JSON."""
    if kind == "CPI":
        flow, key = "CPI", f"{code}.CPI._T.IX.M"
    elif kind == "FX":
        flow, key = "ER", f"{code}.XDC_USD.PA_RT.M"
    else:
        raise ValueError(f"Unsupported series type: {kind}")
    url = f"{BASE}/data/dataflow/IMF.STA/{flow}/+/{key}"
    params = {"c[TIME_PERIOD]": f"ge:{start}+le:{end}"}
    errors = []
    # CSV is easier to parse reliably and avoids the differing JSON schemas.
    for accept in ("csvfile", "application/vnd.sdmx.data+csv;version=2", "application/vnd.sdmx.data+json;version=2"):
        try:
            response = requests.get(url, params=params, headers={"Accept": accept}, timeout=TIMEOUT)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if "json" in content_type or response.text.lstrip().startswith("{"):
                result = extract_series(response.json(), f"{code} {kind}")
            else:
                result = extract_csv(response.text, f"{code} {kind}")
            return result
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{accept}: {str(exc)[:220]}")
    raise RuntimeError(
        f"Could not retrieve {code} {kind} from IMF. URL: {url}. "
        f"Server responses: {' | '.join(errors)}. "
        "Check the IMF API response and available formats; no data was substituted."
    )


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

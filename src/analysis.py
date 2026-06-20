"""
Inc. 5000 (2025) analyzer — an interactive marimo notebook over the scraper's
CSV output (`output/inc5000_2025.csv`, produced by `src/parser.py`).

It loads the data, drops columns that are entirely empty (the paywalled
Revenue Range / Employee Growth / Year Founded fields come out blank for
non-subscribers), parses `growth_3yr` from a display string ("37,364%") into a
number, and flags `growth_3yr` outliers *per group* with IsolationForest — a
company is an outlier relative to its own industry / city / state, not the whole
list. Outliers are kept and highlighted (not dropped), and everything is
visualized: a per-group box plot, a top-groups bar chart, an outlier-colored
strip plot, and a sortable summary table.

Run it:

    uv run marimo edit src/analysis.py     # interactive editor
    uv run marimo run  src/analysis.py     # read-only app

See AGENTS.md for project conventions (always go through `uv`).
"""

import marimo

__generated_with = "0.23.10"
app = marimo.App(width="medium")


@app.cell
def _():
    from pathlib import Path

    import altair as alt
    import marimo as mo
    import polars as pl
    from sklearn.ensemble import IsolationForest

    # The strip plot can plot a few thousand points; lift Altair's 5k-row guard.
    alt.data_transformers.disable_max_rows()

    return IsolationForest, Path, alt, mo, pl


@app.cell
def _(mo):
    mo.md("""
    # Inc. 5000 (2025) analyzer

    Outlier detection on **3-year growth** with `IsolationForest`, fit
    *per group*. Pick a grouping dimension and tune the knobs below; every
    chart and table updates reactively.
    """)
    return


@app.cell
def _(Path):
    # The scraper writes here (see `OUTPUT` in src/parser.py). Resolve relative to
    # the repo root (this file lives in src/) so it works from any working dir.
    CSV_PATH = Path(__file__).resolve().parent.parent / "output" / "inc5000_2025.csv"
    return (CSV_PATH,)


@app.cell
def _(CSV_PATH, pl):
    # Read everything as strings (infer_schema_length=0) so we control parsing of
    # the decorated cells ("37,364%", "1,000") ourselves rather than letting the
    # CSV reader guess.
    raw = pl.read_csv(CSV_PATH, infer_schema_length=0)
    return (raw,)


@app.cell
def _(pl, raw):
    def _nonempty_count(col: str) -> int:
        # Non-null AND non-blank (paywalled cells are present-but-empty strings).
        return raw.select(
            pl.col(col).str.strip_chars().str.len_chars().fill_null(0).gt(0).sum()
        ).item()

    # Drop columns that are entirely empty rather than hard-coding the three
    # paywalled names — so this keeps working if the user ever subscribes and
    # those columns start to fill.
    kept_cols = [c for c in raw.columns if _nonempty_count(c) > 0]
    dropped_cols = [c for c in raw.columns if c not in kept_cols]

    df = raw.select(kept_cols)

    # rank: digits only -> Int64 (ranks render with thousands separators).
    if "rank" in df.columns:
        df = df.with_columns(
            pl.col("rank").str.replace_all(r"\D", "").cast(pl.Int64, strict=False)
        )

    # growth_3yr: "37,364%" -> 37364.0 ; blanks -> null.
    df = df.with_columns(
        pl.col("growth_3yr")
        .str.replace_all(r"[%,]", "")
        .str.strip_chars()
        .cast(pl.Float64, strict=False)
    )

    # Can't score rows without a growth value.
    df = df.drop_nulls("growth_3yr")
    return df, dropped_cols


@app.cell
def _(df, dropped_cols, mo):
    mo.md(f"""
    **Loaded {df.height:,} companies** with growth data.
    Dropped empty columns: `{", ".join(dropped_cols) or "none"}`.
    """)
    return


@app.cell
def _(mo):
    group_by = mo.ui.dropdown(
        ["industry", "city", "state"], value="industry", label="Group by"
    )
    contamination = mo.ui.slider(
        0.01, 0.20, value=0.05, step=0.01, label="Contamination", show_value=True
    )
    min_group_size = mo.ui.slider(
        5, 50, value=10, step=1, label="Min group size", show_value=True
    )
    exclude_outliers = mo.ui.switch(value=False, label="Exclude outliers from stats")
    top_n = mo.ui.slider(
        5, 25, value=12, step=1, label="Top-N groups in charts", show_value=True
    )

    mo.vstack(
        [
            mo.hstack([group_by, top_n], justify="start", gap=2),
            mo.hstack([contamination, min_group_size], justify="start", gap=2),
            exclude_outliers,
        ]
    )
    return contamination, exclude_outliers, group_by, min_group_size, top_n


@app.cell
def _(IsolationForest, pl):
    def flag_outliers(
        data: pl.DataFrame, group_col: str, contamination: float, min_size: int
    ) -> pl.DataFrame:
        """Add an `is_outlier` bool column. IsolationForest is fit separately
        within each group on the 1-D `growth_3yr` feature; groups smaller than
        `min_size` are skipped (all treated as inliers)."""
        parts = []
        for (_key,), part in data.group_by([group_col], maintain_order=True):
            if part.height >= min_size:
                values = part["growth_3yr"].to_numpy().reshape(-1, 1)
                model = IsolationForest(contamination=contamination, random_state=42)
                is_outlier = model.fit_predict(values) == -1
            else:
                is_outlier = [False] * part.height
            parts.append(part.with_columns(pl.Series("is_outlier", is_outlier)))
        return pl.concat(parts)

    return (flag_outliers,)


@app.cell
def _(contamination, df, flag_outliers, group_by, min_group_size):
    scored = flag_outliers(
        df, group_by.value, contamination.value, min_group_size.value
    )
    return (scored,)


@app.cell
def _(group_by, mo, scored):
    _n_out = int(scored["is_outlier"].sum())
    mo.md(
        f"""
        Flagged **{_n_out:,} outliers** ({_n_out / scored.height:.1%}) across
        **{scored[group_by.value].n_unique():,}** distinct `{group_by.value}`
        values.
        """
    )
    return


@app.cell
def _(group_by, pl, scored, top_n):
    # The groups shown in the charts: the top-N by company count. Shared by the
    # box plot and strip plot so they line up.
    top_groups = (
        scored.group_by(group_by.value)
        .agg(pl.len().alias("count"))
        .sort("count", descending=True)
        .head(top_n.value)
        .get_column(group_by.value)
        .to_list()
    )
    return (top_groups,)


@app.cell
def _(alt, exclude_outliers, group_by, mo, pl, scored, top_groups):
    # Box plot of growth per group. Log x — growth is extremely right-skewed
    # (max ~37,000%), so a linear axis bunches the bulk up near the left. Altair
    # draws its own whisker outliers; the text layer labels each group's median.
    _col = group_by.value
    _data = scored.filter(scored[_col].is_in(top_groups))
    if exclude_outliers.value:
        _data = _data.filter(~pl.col("is_outlier"))
    _base = alt.Chart(_data).encode(
        y=alt.Y(
            f"{_col}:N",
            sort=alt.EncodingSortField("growth_3yr", op="median", order="descending"),
            title=_col,
        ),
    )
    _box = _base.mark_boxplot(extent="min-max").encode(
        x=alt.X(
            "growth_3yr:Q",
            scale=alt.Scale(type="log"),
            title="3-year growth (%, log scale)",
        ),
    )
    _median_lbl = _base.mark_text(align="left", dx=4, fontSize=10).encode(
        x=alt.X("median(growth_3yr):Q"),
        text=alt.Text("median(growth_3yr):Q", format=",.0f"),
    )
    _excl = " (outliers excluded)" if exclude_outliers.value else ""
    _chart = (_box + _median_lbl).properties(
        title=f"Growth distribution by {_col} (top {len(top_groups)}){_excl}",
        height=alt.Step(24),
        width=640,
    )
    # Selection disabled: we never read the chart's `.value` back into Python,
    # and enabling it would make marimo call transformed_data() on these
    # transform-bearing charts (boxplot aggregation / jitter), which needs the
    # heavy vegafusion package.
    mo.ui.altair_chart(_chart, chart_selection=False, legend_selection=False)
    return


@app.cell
def _(alt, exclude_outliers, group_by, mo, pl, scored, top_groups):
    # Bar chart: company count + mean/median growth per group. Respects the
    # exclude-outliers toggle for the growth stats.
    _col = group_by.value
    _src = scored.filter(~pl.col("is_outlier")) if exclude_outliers.value else scored
    _stats = (
        _src.filter(pl.col(_col).is_in(top_groups))
        .group_by(_col)
        .agg(
            pl.len().alias("count"),
            pl.col("growth_3yr").mean().alias("mean"),
            pl.col("growth_3yr").median().alias("median"),
        )
    )
    _count_base = alt.Chart(_stats).encode(
        x=alt.X("count:Q", title="Company count"),
        y=alt.Y(
            f"{_col}:N",
            sort=alt.SortField("count", order="descending"),
            title=_col,
        ),
    )
    _count = (
        _count_base.mark_bar(color="#4C78A8")
        + _count_base.mark_text(align="left", dx=3, fontSize=10).encode(
            text=alt.Text("count:Q", format=",")
        )
    ).properties(title="Companies", width=280, height=alt.Step(22))

    # Fold mean/median into long form so they can share a grouped-bar encoding.
    _long = _stats.unpivot(
        index=_col, on=["mean", "median"], variable_name="stat", value_name="growth"
    )
    _growth_base = alt.Chart(_long).encode(
        x=alt.X("growth:Q", title="3-year growth (%)"),
        y=alt.Y(
            f"{_col}:N",
            sort=alt.EncodingSortField("growth", op="max", order="descending"),
            title=None,
        ),
        yOffset="stat:N",
    )
    _growth = (
        _growth_base.mark_bar().encode(color=alt.Color("stat:N", title=None))
        + _growth_base.mark_text(align="left", dx=3, fontSize=9).encode(
            text=alt.Text("growth:Q", format=",.0f")
        )
    ).properties(
        title="Growth" + (" (outliers excluded)" if exclude_outliers.value else ""),
        width=280,
        height=alt.Step(22),
    )
    mo.hstack(
        [
            mo.ui.altair_chart(_count, chart_selection=False, legend_selection=False),
            mo.ui.altair_chart(_growth, chart_selection=False, legend_selection=False),
        ]
    )
    return


@app.cell
def _(alt, exclude_outliers, group_by, mo, pl, scored, top_groups):
    # Strip plot: every company as a point, x = growth (log), y = group, colored
    # by inlier/outlier. A random y-offset jitter spreads overlapping points.
    _col = group_by.value
    _data = scored.filter(scored[_col].is_in(top_groups))
    if exclude_outliers.value:
        _data = _data.filter(~pl.col("is_outlier"))
    _chart = (
        alt.Chart(_data)
        .mark_circle(opacity=0.55)
        .encode(
            x=alt.X(
                "growth_3yr:Q",
                scale=alt.Scale(type="log"),
                title="3-year growth (%, log scale)",
            ),
            y=alt.Y(f"{_col}:N", title=_col),
            yOffset=alt.YOffset("jitter:Q", scale=alt.Scale(domain=[0, 1])),
            size=alt.Size(
                "is_outlier:N",
                scale=alt.Scale(domain=[False, True], range=[18, 70]),
                legend=None,
            ),
            color=alt.Color(
                "is_outlier:N",
                scale=alt.Scale(domain=[False, True], range=["#4C78A8", "#E45756"]),
                title="Outlier",
            ),
            order=alt.Order("is_outlier:N"),  # draw outliers on top
            tooltip=["company:N", "growth_3yr:Q", f"{_col}:N", "is_outlier:N"],
        )
        .transform_calculate(jitter="random()")
        .properties(
            title=(
                f"Per-company growth by {_col}"
                + (
                    " (outliers excluded)"
                    if exclude_outliers.value
                    else " — red = outlier"
                )
            ),
            height=alt.Step(46),
            width=640,
        )
    )
    # Selection disabled: we never read the chart's `.value` back into Python,
    # and enabling it would make marimo call transformed_data() on these
    # transform-bearing charts (boxplot aggregation / jitter), which needs the
    # heavy vegafusion package.
    mo.ui.altair_chart(_chart, chart_selection=False, legend_selection=False)
    return


@app.cell
def _(group_by, mo, pl, scored):
    # Sortable / searchable per-group summary.
    summary = (
        scored.group_by(group_by.value)
        .agg(
            pl.len().alias("count"),
            pl.col("is_outlier").sum().alias("outliers"),
            pl.col("growth_3yr").mean().round(0).alias("mean_growth"),
            pl.col("growth_3yr").median().round(0).alias("median_growth"),
            pl.col("growth_3yr").min().alias("min_growth"),
            pl.col("growth_3yr").max().alias("max_growth"),
        )
        .sort("count", descending=True)
    )
    mo.ui.table(summary, selection=None, pagination=True)
    return


if __name__ == "__main__":
    app.run()

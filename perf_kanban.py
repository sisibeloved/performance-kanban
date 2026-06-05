#!/usr/bin/env python3
"""pyperformance 测试结果观察工具 — Performance Kanban

用法:
    streamlit run perf_kanban.py [json_dir]
    streamlit run perf_kanban.py              # 默认当前目录
    streamlit run perf_kanban.py /path/to/json
"""

import sys
import io
import os
import json
import base64
import statistics
from dataclasses import dataclass, field
from math import exp, log
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

# =============================================================================
# Data Layer — 纯函数，无 Streamlit 依赖
# =============================================================================


@dataclass
class BenchmarkResult:
    """单个 benchmark 用例的解析结果"""
    name: str
    mean: float           # 均值（秒）
    values: list[float]   # 原始采样值
    metadata: dict = field(default_factory=dict)


@dataclass
class BenchmarkData:
    """单个 JSON 文件对应的所有 benchmark 结果"""
    name: str                          # 自动标签
    file: str                          # 原始文件名
    metadata: dict = field(default_factory=dict)
    benchmarks: dict[str, BenchmarkResult] = field(default_factory=dict)


def _extract_python_version(metadata: dict) -> str:
    """尝试从 metadata 或 benchmark command 中提取 Python 版本"""
    # 直接字段
    for key in ("python_version", "python_implementation_version"):
        v = metadata.get(key, "")
        if v:
            return v
    return ""


def _extract_commit(metadata: dict) -> str:
    """尝试从 metadata 提取 commit id"""
    for key in ("commit_id", "commit", "git_commit"):
        v = metadata.get(key, "")
        if v:
            return v[:8]
    return ""


def _derive_label(metadata: dict, filename: str) -> str:
    """从 metadata 推导可读标签，确保可辨识"""
    parts = []
    py_ver = _extract_python_version(metadata)
    if py_ver:
        parts.append(py_ver)
    commit = _extract_commit(metadata)
    if commit:
        parts.append(commit)
    if not parts:
        # 无版本信息时用文件名，保证唯一性
        parts.append(Path(filename).stem)
    return " @ ".join(parts) if len(parts) <= 2 else " | ".join(parts)


def load_benchmark(path: str) -> BenchmarkData:
    """解析 pyperf 格式的 JSON 文件，返回 BenchmarkData"""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    global_meta = raw.get("metadata", {})
    benchmarks: dict[str, BenchmarkResult] = {}

    for bench_entry in raw.get("benchmarks", []):
        runs = bench_entry.get("runs", [])
        if not runs:
            continue

        # 用例名：优先从 benchmark 级 metadata 取（真实 pyperf），
        # 回退到第一个 run 的 metadata（兼容旧格式模拟数据）
        bench_meta = bench_entry.get("metadata", {})
        bench_name = bench_meta.get("name", "")
        if not bench_name:
            run_meta = runs[0].get("metadata", {})
            bench_name = run_meta.get("name", "unknown")
        else:
            run_meta = bench_meta

        # 合并所有 run 的 values（跳过只有 warmups 的 calibration run）
        all_values: list[float] = []
        for run in runs:
            vals = run.get("values", [])
            all_values.extend(vals)

        if not all_values:
            continue

        mean_val = statistics.mean(all_values)
        benchmarks[bench_name] = BenchmarkResult(
            name=bench_name,
            mean=mean_val,
            values=all_values,
            metadata=run_meta,
        )

    label = _derive_label(global_meta, path)
    return BenchmarkData(
        name=label,
        file=os.path.basename(path),
        metadata=global_meta,
        benchmarks=benchmarks,
    )


def compute_speedup(baseline_mean: float, candidate_mean: float) -> Optional[float]:
    """Speedup = baseline / candidate。> 1 表示更快，< 1 表示更慢。"""
    if candidate_mean <= 0 or baseline_mean <= 0:
        return None
    return baseline_mean / candidate_mean


def get_all_benchmark_names(data_list: list[BenchmarkData]) -> list[str]:
    """获取所有数据集中出现的用例名（排序）"""
    names: set[str] = set()
    for d in data_list:
        names.update(d.benchmarks.keys())
    return sorted(names)


def _format_time(seconds: float) -> tuple[str, str]:
    """将秒转换为合适的单位，保留2位小数。返回 (值字符串, 单位)"""
    if seconds < 1e-6:
        return f"{seconds * 1e9:.2f}", "ns"
    elif seconds < 1e-3:
        return f"{seconds * 1e6:.2f}", "us"
    elif seconds < 1.0:
        return f"{seconds * 1e3:.2f}", "ms"
    else:
        return f"{seconds:.2f}", "s"


def build_comparison_df(
    baseline: BenchmarkData,
    candidates: list[BenchmarkData],
    benchmark_names: list[str],
) -> tuple[pd.DataFrame, list[tuple]]:
    """
    构建对比表格 DataFrame（MultiIndex 二级表头）。
    baseline 两列：绝对值 | 单位
    每个 candidate 三列：绝对值 | 单位 | Speedup
    返回 (DataFrame, speedup列名元组列表)
    """
    # 构建 MultiIndex 列
    base_label = baseline.name or "baseline"
    if base_label in {c.name for c in candidates}:
        base_label = f"{base_label} (baseline)"
    col_tuples: list[tuple] = [
        ("", "用例名"),
        (base_label, "值"),
        (base_label, "单位"),
    ]
    for cand in candidates:
        col_tuples.append((cand.name, "值"))
        col_tuples.append((cand.name, "单位"))
        col_tuples.append((cand.name, "Speedup"))

    columns = pd.MultiIndex.from_tuples(col_tuples)
    speedup_cols = [(c.name, "Speedup") for c in candidates]

    rows = []
    for name in benchmark_names:
        base_result = baseline.benchmarks.get(name)
        if base_result is None:
            continue
        base_mean = base_result.mean
        base_val, base_unit = _format_time(base_mean)

        row = [name, base_val, base_unit]
        for cand in candidates:
            cand_result = cand.benchmarks.get(name)
            if cand_result is None:
                row.extend([None, "—", None])
            else:
                val_str, unit = _format_time(cand_result.mean)
                su = compute_speedup(base_mean, cand_result.mean)
                su_val = round(su, 4) if su is not None else None
                row.extend([val_str, unit, su_val])

        rows.append(row)

    df = pd.DataFrame(rows, columns=columns)
    return df, speedup_cols


def build_trend_df(
    baseline: BenchmarkData,
    candidates: list[BenchmarkData],
    benchmark_names: list[str],
) -> tuple[pd.DataFrame, list[tuple]]:
    """
    构建趋势表格 DataFrame（MultiIndex 二级表头，含 baseline 列和趋势列）。
    返回 (DataFrame, speedup列名元组列表)
    """
    col_tuples: list[tuple] = [("", "用例名"), ("baseline", "值"), ("baseline", "单位")]
    for cand in candidates:
        col_tuples.append((cand.name, "值"))
        col_tuples.append((cand.name, "单位"))
        col_tuples.append((cand.name, "Speedup"))
    col_tuples.append(("", "趋势"))

    columns = pd.MultiIndex.from_tuples(col_tuples)
    speedup_cols = [(c.name, "Speedup") for c in candidates]

    rows = []
    for name in benchmark_names:
        base_result = baseline.benchmarks.get(name)
        if base_result is None:
            continue
        base_mean = base_result.mean
        base_val, base_unit = _format_time(base_mean)

        row = [name, base_val, base_unit]
        speedup_series: list[float] = []

        for cand in candidates:
            cand_result = cand.benchmarks.get(name)
            if cand_result is None:
                row.extend([None, "—", None])
            else:
                val_str, unit = _format_time(cand_result.mean)
                su = compute_speedup(base_mean, cand_result.mean)
                su_val = round(su, 4) if su is not None else None
                row.extend([val_str, unit, su_val])
                if su is not None:
                    speedup_series.append(su)

        # 趋势方向
        if len(speedup_series) >= 2:
            diff = speedup_series[-1] - speedup_series[0]
            epsilon = 0.02
            if diff > epsilon:
                row.append("↑ 提升")
            elif diff < -epsilon:
                row.append("↓ 回归")
            else:
                row.append("↔ 波动")
        elif len(speedup_series) == 1:
            row.append("— 单点")
        else:
            row.append("—")

        rows.append(row)

    return pd.DataFrame(rows, columns=columns), speedup_cols


def merge_benchmarks(
    sources: dict[str, BenchmarkData],
    replacements: dict[str, str],
    base_key: str,
    label: str,
) -> BenchmarkData:
    """
    合成新的 BenchmarkData：以 base_key 的数据为基础，
    replacements 指定用例名 -> 源文件 key 的映射，从对应源中替换用例数据。
    """
    base = sources[base_key]
    merged_benchmarks: dict[str, BenchmarkResult] = {}

    for bname, bresult in base.benchmarks.items():
        if bname in replacements and replacements[bname] in sources:
            src = sources[replacements[bname]]
            if bname in src.benchmarks:
                merged_benchmarks[bname] = src.benchmarks[bname]
                continue
        merged_benchmarks[bname] = bresult

    return BenchmarkData(
        name=label,
        file=f"synthetic:{label}",
        metadata=dict(base.metadata),
        benchmarks=merged_benchmarks,
    )


def filter_df_by_speedup(
    df: pd.DataFrame,
    low: float,
    high: float,
    speedup_cols: list[tuple],
) -> pd.DataFrame:
    """筛选：保留至少一个 Speedup 超出 [low, high] 范围的行"""
    if df.empty or not speedup_cols:
        return df

    mask = pd.Series([False] * len(df), index=df.index)
    for col in speedup_cols:
        if col in df.columns:
            vals = pd.to_numeric(df[col], errors="coerce")
            mask = mask | vals.lt(low) | vals.gt(high)
    return df[mask]


def style_speedup_cell(val, improve_thresh: float, regress_thresh: float) -> str:
    """根据 Speedup 值和阈值返回 CSS 样式"""
    if pd.isna(val) or val is None:
        return ""
    try:
        v = float(val)
    except (ValueError, TypeError):
        return ""

    if v >= improve_thresh:
        return "background-color: #c6efce; color: #006100"
    elif v <= regress_thresh:
        return "background-color: #ffc7ce; color: #9c0006"
    else:
        return "background-color: #ffffff; color: #000000"


def apply_speedup_styling(
    df: pd.DataFrame,
    improve_thresh: float,
    regress_thresh: float,
    speedup_cols: list[tuple],
):
    """对 DataFrame 应用 Speedup 颜色编码 + 列宽配置"""
    styler = df.style

    def _style_col(col):
        if col.name in speedup_cols:
            return [
                style_speedup_cell(v, improve_thresh, regress_thresh)
                for v in col
            ]
        return ["" for _ in col]

    styler = styler.apply(_style_col)
    styler = styler.set_table_styles(
        [
            {
                "selector": ".col_heading.level0",
                "props": [("text-align", "center")],
            }
        ]
    )

    # 数值列右对齐
    styler = styler.set_properties(
        **{"text-align": "right"},
        subset=[c for c in df.columns if isinstance(c, tuple) and c[1] in ("值", "Speedup")]
    )

    return styler


def build_gmean_row(
    df: pd.DataFrame,
    speedup_cols: list[tuple],
    name_col: tuple = ("", "用例名"),
) -> pd.DataFrame:
    """
    构建几何平均行（单行 DataFrame，列与对比表一致）。
    几何平均仅填入 Speedup 列，其余列留空。
    """
    gmeans = _geomean_speedup(df, speedup_cols)
    row: dict = {}
    for col in df.columns:
        if col == name_col:
            row[col] = "几何平均"
        elif col in speedup_cols:
            top = col[0]
            row[col] = round(gmeans[top], 4) if top in gmeans else None
        else:
            row[col] = ""
    return pd.DataFrame([row], columns=df.columns)


# 对比表 HTML 渲染所用的列宽（像素）
_COMPARISON_COL_WIDTHS = {
    "用例名": 200,
    "值": 92,
    "单位": 46,
    "Speedup": 104,
    "趋势": 90,
}
# 固定在左侧的列数：用例名 + baseline 值/单位
_COMPARISON_PINNED = 3


def _col_width(col: tuple | str) -> int:
    sub = col[1] if isinstance(col, tuple) else col
    return _COMPARISON_COL_WIDTHS.get(sub, 100)


def build_speedup_chart_df(
    df: pd.DataFrame,
    speedup_cols: list[tuple],
    name_col: tuple = ("", "用例名"),
) -> pd.DataFrame:
    """
    构建 Speedup 柱状图用的 DataFrame：用例名为索引，各候选 Speedup 为列。
    索引名与列名**必须是普通字符串**——若把 MultiIndex 元组(如 ('', '用例名'))
    作为索引名传给 st.bar_chart，Vega-Lite 会将其当作字段访问路径解析并抛
    "Access path missing closing bracket"，中断整次重跑渲染(连带使排序看似失效)。
    """
    chart_df = df[[name_col] + list(speedup_cols)].copy()
    chart_df.columns = ["用例名"] + [c[0] for c in speedup_cols]
    return chart_df.set_index("用例名")


def render_comparison_table_html(
    df: pd.DataFrame,
    speedup_cols: list[tuple],
    improve_thresh: float,
    regress_thresh: float,
    append_gmean: bool = True,
) -> str:
    """
    将对比表渲染为 HTML 字符串。相比 st.dataframe(glide canvas)，HTML 表格能：
    - 完整渲染二级表头（含被固定的 baseline，借助 colspan）
    - 用 CSS position:sticky 固定左侧列，且保留两级表头
    - 精确控制列宽、让一级表头居中
    - 在最底部常驻几何平均行（数据已在外部排序，几何平均追加在末尾不参与排序）
    """
    if append_gmean and not df.empty and speedup_cols:
        df = pd.concat([df, build_gmean_row(df, speedup_cols)], ignore_index=True)

    cols = list(df.columns)
    widths = [_col_width(c) for c in cols]
    offsets = []
    acc = 0
    for w in widths:
        offsets.append(acc)
        acc += w
    total_w = acc

    styler = df.style.hide(axis="index")

    # Speedup 颜色编码
    def _color_speedup(col):
        if col.name in speedup_cols:
            return [
                style_speedup_cell(v, improve_thresh, regress_thresh) for v in col
            ]
        return ["" for _ in col]

    styler = styler.apply(_color_speedup)
    # Speedup 数值统一精度；缺失显示 —
    styler = styler.format(
        {c: "{:.3f}" for c in speedup_cols}, na_rep="—"
    )

    # 一级表头行高（px），用作二级表头 sticky 的 top 偏移，使两行表头都吸顶不重叠
    h0 = 30
    table_styles: list[dict] = [
        {"selector": "", "props": [
            ("border-collapse", "collapse"),
            ("table-layout", "fixed"),
            # 占满容器：窄于屏幕时拉伸填满，宽于屏幕时退化为最小宽度并横向滚动
            ("width", "100%"),
            ("min-width", f"{total_w}px"),
            ("font-size", "13px"),
            ("color", "#1a1a1a"),
        ]},
        # 一级表头：吸顶 top:0，固定行高
        {"selector": ".col_heading.level0", "props": [
            ("text-align", "center"),
            ("background-color", "#e6e9f0"),
            ("border", "1px solid #cfd4dc"),
            ("padding", "5px 6px"),
            ("box-sizing", "border-box"),
            ("height", f"{h0}px"),
            ("position", "sticky"),
            ("top", "0"),
            ("z-index", "2"),
        ]},
        # 二级表头：吸顶在一级表头下方 top:h0
        {"selector": ".col_heading.level1", "props": [
            ("text-align", "center"),
            ("background-color", "#f0f2f6"),
            ("border", "1px solid #cfd4dc"),
            ("padding", "5px 6px"),
            ("position", "sticky"),
            ("top", f"{h0}px"),
            ("z-index", "2"),
        ]},
        {"selector": "tbody td", "props": [
            ("border", "1px solid #e6e6e6"),
            ("padding", "3px 6px"),
            ("background-color", "#ffffff"),
            ("white-space", "nowrap"),
            ("overflow", "hidden"),
            ("text-overflow", "ellipsis"),
        ]},
        {"selector": "tbody tr:last-child td", "props": [
            ("font-weight", "bold"),
            ("border-top", "2px solid #9aa0a6"),
            ("background-color", "#fafbfc"),
        ]},
    ]

    for i, (col, off) in enumerate(zip(cols, offsets)):
        sub = col[1] if isinstance(col, tuple) else col
        if sub in ("值", "Speedup"):
            table_styles.append({"selector": f"tbody td.col{i}", "props": [
                ("text-align", "right"),
            ]})
        # 固定列：用例名 + baseline 值/单位 同时吸顶+贴左（角落），z-index 最高，
        # 两级表头各自用正确的 top（level0:0 / level1:h0），用更高特异性盖过通用规则。
        if i < _COMPARISON_PINNED:
            table_styles.append({"selector": f"tbody .col{i}", "props": [
                ("position", "sticky"),
                ("left", f"{off}px"),
                ("z-index", "1"),
            ]})
            table_styles.append({"selector": f"thead .col_heading.level0.col{i}", "props": [
                ("position", "sticky"),
                ("left", f"{off}px"),
                ("top", "0"),
                ("z-index", "4"),
            ]})
            table_styles.append({"selector": f"thead .col_heading.level1.col{i}", "props": [
                ("position", "sticky"),
                ("left", f"{off}px"),
                ("top", f"{h0}px"),
                ("z-index", "4"),
            ]})

    styler = styler.set_table_styles(table_styles)

    # 用 <colgroup> 精确设定列宽（table-layout:fixed 下权威，且不受二级表头
    # colspan 影响）。固定列给死像素宽以保证 sticky 偏移正确；非固定列留空，
    # 由浏览器平分剩余宽度，使表格撑满容器。
    col_tags = "".join(
        f'<col style="width:{w}px"/>' if i < _COMPARISON_PINNED else "<col/>"
        for i, w in enumerate(widths)
    )
    colgroup = f"<colgroup>{col_tags}</colgroup>"
    html = styler.to_html()
    tag_end = html.find(">", html.find("<table")) + 1
    html = html[:tag_end] + colgroup + html[tag_end:]

    return (
        '<div style="overflow:auto; max-height:600px; '
        'border:1px solid #cfd4dc; border-radius:4px;">'
        f"{html}"
        "</div>"
    )


# =============================================================================
# Export Layer — 导出为 Markdown / 图片
# =============================================================================

def _flatten_df_for_export(df: pd.DataFrame) -> list[dict]:
    """
    将 MultiIndex DataFrame 展平为导出友好的列规格列表。
    值+单位合并为一列。返回 [{"type", "header", "col"/"val_col"/"unit_col"}, ...]
    """
    specs = []
    cols = list(df.columns)
    i = 0
    while i < len(cols):
        col = cols[i]
        if not isinstance(col, tuple):
            specs.append({"type": "raw", "header": str(col), "col": col})
            i += 1
            continue

        top, sub = col
        if sub == "用例名":
            specs.append({"type": "raw", "header": "Benchmark", "col": col})
            i += 1
        elif sub == "值":
            label = top if top else "Baseline"
            unit_col = (top, "单位")
            specs.append({
                "type": "value_unit", "header": label,
                "val_col": col, "unit_col": unit_col,
            })
            i += 2  # skip 值 + 单位
        elif sub == "单位":
            i += 1  # handled by 值
        elif sub == "Speedup":
            specs.append({"type": "speedup", "header": "Speedup", "col": col})
            i += 1
        elif sub == "趋势":
            specs.append({"type": "raw", "header": "Trend", "col": col})
            i += 1
        else:
            specs.append({
                "type": "raw",
                "header": f"{top} {sub}" if top else sub,
                "col": col,
            })
            i += 1
    return specs


def _fmt(val):
    """Format a cell value, handling None/NaN"""
    if val is None:
        return "-"
    if isinstance(val, float) and pd.isna(val):
        return "-"
    return str(val)


def _sort_df_for_export(
    df: pd.DataFrame,
    export_sort_by=None,
    export_sort_ascending: bool = True,
) -> pd.DataFrame:
    """Return an export-only sorted view without mutating the table DataFrame."""
    if export_sort_by is None or df.empty or export_sort_by not in df.columns:
        return df

    return df.sort_values(
        by=export_sort_by,
        ascending=export_sort_ascending,
        na_position="last",
        kind="mergesort",
    )


def _js_literal(value: str) -> str:
    """Serialize a value for a <script> block without allowing script close tags."""
    return json.dumps(value).replace("</", "<\\/")


def _build_export_buttons_iframe_html(
    markdown_text: str,
    image_bytes: bytes,
    markdown_file_name: str,
    image_file_name: str,
) -> str:
    """Build a browser-side export button group for downloads and Markdown copy."""
    markdown_b64 = base64.b64encode(markdown_text.encode("utf-8")).decode("ascii")
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    markdown_literal = _js_literal(markdown_text)
    markdown_name = _js_literal(markdown_file_name)[1:-1]
    image_name = _js_literal(image_file_name)[1:-1]
    return f"""
<div class="export-actions">
  <a
    class="export-action-button"
    download="{markdown_name}"
    href="data:text/markdown;base64,{markdown_b64}"
  >导出 Markdown</a>
  <button id="copy-md" class="export-action-button" type="button">
    复制 Markdown 到剪贴板
  </button>
  <a
    class="export-action-button"
    download="{image_name}"
    href="data:image/jpeg;base64,{image_b64}"
  >导出 JPG</a>
  <span id="copy-md-status" class="export-action-status"></span>
</div>
<style>
  body {{
    margin: 0;
  }}
  .export-actions {{
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }}
  .export-action-button {{
    appearance: none;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    background: #ffffff;
    color: #111827;
    cursor: pointer;
    display: inline-flex;
    font: 14px sans-serif;
    line-height: 1.2;
    padding: 0.45rem 0.75rem;
    text-decoration: none;
  }}
  .export-action-button:hover {{ border-color: #9ca3af; }}
  .export-action-status {{
    color: #4b5563;
    font: 13px sans-serif;
  }}
</style>
<script>
(() => {{
  const markdown = {markdown_literal};
  const button = document.getElementById("copy-md");
  const status = document.getElementById("copy-md-status");
  button.addEventListener("click", async () => {{
    try {{
      await navigator.clipboard.writeText(markdown);
      status.textContent = "已复制";
    }} catch (error) {{
      status.textContent = "复制失败";
    }}
  }});
}})();
</script>
"""


def _geomean_speedup(
    df: pd.DataFrame, speedup_cols: list[tuple]
) -> dict[str, float]:
    """计算每个 candidate 的 Speedup 几何平均"""
    results = {}
    for col in speedup_cols:
        if col not in df.columns:
            continue
        vals = pd.to_numeric(df[col], errors="coerce").dropna()
        positive = vals[vals > 0]
        if len(positive) == 0:
            continue
        try:
            gm = exp(sum(log(v) for v in positive) / len(positive))
            results[col[0]] = gm
        except (ValueError, ZeroDivisionError):
            pass
    return results


def export_df_to_markdown(
    df: pd.DataFrame,
    speedup_cols: list[tuple],
    improve_thresh: float,
    regress_thresh: float,
    baseline_label: str = "",
    export_sort_by=None,
    export_sort_ascending: bool = True,
) -> str:
    """将对比/趋势表格导出为 Markdown 格式（值+单位合并）"""
    export_df = _sort_df_for_export(df, export_sort_by, export_sort_ascending)
    lines = []

    if baseline_label:
        lines.append(f"**Baseline:** `{baseline_label}`")
        lines.append("")

    # Geomean summary
    gmeans = _geomean_speedup(export_df, speedup_cols)
    if gmeans:
        parts = [f"{name}: {v:.4f}x" for name, v in gmeans.items()]
        lines.append("**Geomean Speedup:** " + " | ".join(parts))
        lines.append("")

    specs = _flatten_df_for_export(export_df)
    headers = [s["header"] for s in specs]

    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")

    for _, row in export_df.iterrows():
        cells = []
        for s in specs:
            t = s["type"]
            if t == "speedup":
                val = row.get(s["col"])
                if val is not None and not (isinstance(val, float) and pd.isna(val)):
                    try:
                        v = float(val)
                        text = f"{v:.4f}"
                        if v >= improve_thresh or v <= regress_thresh:
                            text = f"**{text}**"
                        cells.append(text)
                    except (ValueError, TypeError):
                        cells.append(_fmt(val))
                else:
                    cells.append("-")
            elif t == "value_unit":
                val = row.get(s["val_col"])
                unit = row.get(s["unit_col"], "")
                val_str = _fmt(val)
                if val_str != "-" and unit and str(unit) != "—":
                    cells.append(f"{val_str} {unit}")
                else:
                    cells.append(val_str)
            else:
                cells.append(_fmt(row.get(s.get("col"))))
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


# 图表中 CJK 文字 → ASCII 替换（避免字体缺失）
_IMAGE_TEXT_MAP = {
    "↑ 提升": "Improve", "↓ 回归": "Regress", "↔ 波动": "Stable",
    "— 单点": "Single", "—": "-",
}


def _setup_matplotlib_cjk():
    """为 matplotlib 配置 CJK 字体，返回是否真正可渲染中文"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    from matplotlib.ft2font import FT2Font

    # 仅接受同时覆盖中文与拉丁字符的字体（CJK-only 字体会让 ASCII 变方框，
    # Latin-only 字体则中文变方框），从而避免误判与混排缺字
    cjk_names = [
        "WenQuanYi Micro Hei", "Noto Sans CJK SC", "Noto Sans SC",
        "Source Han Sans SC", "SimHei", "Microsoft YaHei", "AR PL UMing CN",
    ]
    for name in cjk_names:
        try:
            fp = fm.findfont(fm.FontProperties(family=name), fallback_to_default=False)
            if not fp or "lastresort" in str(fp).lower():
                continue
            font = FT2Font(fp)
            if font.get_char_index(ord("中")) == 0 or font.get_char_index(ord("a")) == 0:
                continue
            plt.rcParams["font.sans-serif"] = [name] + plt.rcParams.get(
                "font.sans-serif", []
            )
            return True
        except Exception:
            pass
    return False


def _image_header_layout(
    specs: list[dict],
    name_label: str,
    mean_label: str,
    trend_label: str,
) -> tuple[list[str], list[tuple]]:
    """
    由导出列规格推导 JPG 的二级表头布局。
    返回 (leaf_headers, groups)：
    - leaf_headers：每列的二级标签(值/Speedup 或 用例名/趋势)
    - groups：[(分组名, 起始列, 结束列), ...]，一级分组(baseline / 各 candidate)需跨列居中。
      baseline 只有"值"一列(无 Speedup) → 单列分组；candidate 含"值"+Speedup → 跨两列。
      用例名 / 趋势 无一级分组，不计入 groups。
    """
    leaf_map = {"Benchmark": name_label, "Trend": trend_label}
    leaf_headers: list[str] = []
    groups: list[tuple] = []
    ci = 0
    i = 0
    while i < len(specs):
        s = specs[i]
        if s["type"] == "value_unit":
            start = ci
            leaf_headers.append(mean_label)
            i += 1
            ci += 1
            end = start
            while i < len(specs) and specs[i]["type"] == "speedup":
                leaf_headers.append("Speedup")
                i += 1
                ci += 1
                end += 1
            groups.append((s["header"], start, end))
        elif s["type"] == "speedup":
            leaf_headers.append("Speedup")
            groups.append((s["header"], ci, ci))
            i += 1
            ci += 1
        else:  # raw: 用例名 / 趋势
            leaf_headers.append(leaf_map.get(s["header"], s["header"]))
            i += 1
            ci += 1
    return leaf_headers, groups


def export_df_to_image(
    df: pd.DataFrame,
    speedup_cols: list[tuple],
    improve_thresh: float,
    regress_thresh: float,
    baseline_label: str = "",
    export_sort_by=None,
    export_sort_ascending: bool = True,
) -> bytes:
    """将表格导出为 JPG 图片（matplotlib 渲染，Speedup 列着色）"""
    import matplotlib.pyplot as plt

    # 仅在有 CJK 字体时用中文表头（与界面表格一致），否则退回 ASCII，避免缺字变方框
    cjk_ok = _setup_matplotlib_cjk()
    name_label = "用例名" if cjk_ok else "Benchmark"
    mean_label = "值" if cjk_ok else "Mean"
    trend_label = "趋势" if cjk_ok else "Trend"

    export_df = _sort_df_for_export(df, export_sort_by, export_sort_ascending)
    specs = _flatten_df_for_export(export_df)
    n_cols = len(specs)

    # 解析二级表头：一级 = 分组名(baseline / 各 candidate)，二级 = 值 / Speedup。
    leaf_headers, groups = _image_header_layout(
        specs, name_label, mean_label, trend_label
    )

    n_header = 2
    # 表头两行：行0 一级分组名(留空，稍后 overlay 居中绘制)，行1 二级标签
    all_text = [[""] * n_cols, list(leaf_headers)]
    all_colors = [["#4472C4"] * n_cols, ["#4472C4"] * n_cols]

    for _, row in export_df.iterrows():
        row_text = []
        row_colors = []
        for s in specs:
            t = s["type"]
            if t == "speedup":
                val = row.get(s["col"])
                if val is not None and not (isinstance(val, float) and pd.isna(val)):
                    try:
                        v = float(val)
                        row_text.append(f"{v:.4f}")
                        if v >= improve_thresh:
                            row_colors.append("#c6efce")
                        elif v <= regress_thresh:
                            row_colors.append("#ffc7ce")
                        else:
                            row_colors.append("#ffffff")
                    except (ValueError, TypeError):
                        row_text.append(_fmt(val))
                        row_colors.append("#ffffff")
                else:
                    row_text.append("-")
                    row_colors.append("#ffffff")
            elif t == "value_unit":
                val = row.get(s["val_col"])
                unit = row.get(s["unit_col"], "")
                val_str = _fmt(val)
                if val_str != "-" and unit and str(unit) != "—":
                    row_text.append(f"{val_str} {unit}")
                else:
                    row_text.append(val_str)
                row_colors.append("#ffffff")
            else:
                raw_val = _fmt(row.get(s.get("col")))
                row_text.append(_IMAGE_TEXT_MAP.get(raw_val, raw_val))
                row_colors.append("#ffffff")
        all_text.append(row_text)
        all_colors.append(row_colors)

    total_rows = len(all_text)

    # 截断过长文本，避免单列被撑得过宽；保证文字不溢出单元格
    max_chars = 28

    def _trunc(text: str) -> str:
        return text if len(text) <= max_chars else text[: max_chars - 1] + "…"

    for r in range(n_header, total_rows):
        for c in range(n_cols):
            all_text[r][c] = _trunc(str(all_text[r][c]))
    groups = [(_trunc(label), a, b) for (label, a, b) in groups]

    # 按各列最长内容（数据 + 二级表头，含边距）分配列宽
    col_chars = [
        max(len(str(all_text[r][c])) for r in range(1, total_rows)) + 2
        for c in range(n_cols)
    ]
    # 保证每个分组跨列总宽能容纳居中的一级分组名
    for (label, a, b) in groups:
        need = len(label) + 2
        span = sum(col_chars[a : b + 1])
        if need > span:
            add = (need - span) / (b - a + 1)
            for c in range(a, b + 1):
                col_chars[c] += add
    total_chars = sum(col_chars) or 1
    col_widths = [c / total_chars for c in col_chars]

    # 图宽随内容总量伸缩，确保每列有足够绝对宽度容纳文字
    fig_w = max(8, total_chars * 0.13)
    fig_h = max(3, total_rows * 0.4 + 0.7)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.axis("off")

    if baseline_label:
        ax.set_title(f"Baseline: {baseline_label}", fontsize=10, loc="left", pad=10)

    table = ax.table(
        cellText=all_text,
        cellColours=all_colors,
        colWidths=col_widths,
        cellLoc="center",
        loc="center",
        bbox=[0, 0, 1, 1],
    )
    table.auto_set_font_size(False)

    for r in range(n_header):
        for c in range(n_cols):
            table[r, c].set_text_props(color="white", fontweight="bold", fontsize=9)
    for r in range(n_header, total_rows):
        for c in range(n_cols):
            table[r, c].set_text_props(fontsize=8)

    # 列边界(轴坐标 0..1) 与行高，用于跨列/跨行居中绘制表头文字
    xcum = [0.0]
    for w in col_widths:
        xcum.append(xcum[-1] + w)
    row_h = 1.0 / total_rows

    # 把组内边框染成与表头同色(蓝)，形成"跨列/跨行合并"观感，再居中 overlay 文字。
    # 不用 visible_edges 隐藏边（会让填充单元格出现白色三角伪影）。
    header_blue = "#4472C4"

    # 一级分组：组内竖线隐形 + 分组中点居中绘制分组名
    grouped_cols = set()
    for (label, a, b) in groups:
        grouped_cols.update(range(a, b + 1))
        for c in range(a, b + 1):
            table[0, c].set_edgecolor(header_blue)
        xc = (xcum[a] + xcum[b + 1]) / 2
        ax.text(
            xc, 1.0 - row_h / 2, label,
            ha="center", va="center", color="white", fontweight="bold",
            fontsize=9, transform=ax.transAxes, zorder=5,
        )

    # 无分组的列(用例名/趋势)：两行表头竖向合并，居中 overlay 标签
    for c in range(n_cols):
        if c in grouped_cols:
            continue
        table[0, c].set_edgecolor(header_blue)
        table[1, c].set_edgecolor(header_blue)
        table[1, c].get_text().set_text("")
        xc = (xcum[c] + xcum[c + 1]) / 2
        ax.text(
            xc, 1.0 - row_h, leaf_headers[c],
            ha="center", va="center", color="white", fontweight="bold",
            fontsize=9, transform=ax.transAxes, zorder=5,
        )

    buf = io.BytesIO()
    fig.savefig(buf, format="jpg", bbox_inches="tight", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _render_export_buttons(
    df: pd.DataFrame,
    speedup_cols: list[tuple],
    improve_thresh: float,
    regress_thresh: float,
    baseline_label: str,
    file_prefix: str,
    export_sort_by=None,
    export_sort_ascending: bool = True,
):
    """渲染导出按钮（Markdown + JPG）"""
    st.subheader("导出")
    md_text = export_df_to_markdown(
        df,
        speedup_cols,
        improve_thresh,
        regress_thresh,
        baseline_label,
        export_sort_by=export_sort_by,
        export_sort_ascending=export_sort_ascending,
    )

    try:
        img_bytes = export_df_to_image(
            df,
            speedup_cols,
            improve_thresh,
            regress_thresh,
            baseline_label,
            export_sort_by=export_sort_by,
            export_sort_ascending=export_sort_ascending,
        )
    except Exception as e:
        st.warning(f"JPG 导出失败（需要 matplotlib）: {e}")
        return

    st.iframe(
        _build_export_buttons_iframe_html(
            md_text,
            img_bytes,
            f"{file_prefix}.md",
            f"{file_prefix}.jpg",
        ),
        height=48,
    )


# =============================================================================

@st.cache_data(show_spinner=False)
def cached_load_benchmark(path: str) -> BenchmarkData:
    """带缓存的 benchmark 加载"""
    return load_benchmark(path)


@st.cache_data(show_spinner=False)
def cached_scan_json_files(directory: str) -> list[str]:
    """扫描目录下的所有 .json 文件"""
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return []
    return sorted(str(p) for p in dir_path.glob("*.json"))


# =============================================================================
# UI Layer
# =============================================================================

def init_session_state():
    """初始化 session state"""
    defaults = {
        "synthetics": {},          # label -> BenchmarkData
        "synthetic_counter": 0,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def render_sidebar() -> dict:
    """渲染侧边栏配置，返回配置字典"""
    with st.sidebar:
        st.header("配置")

        # 目录输入
        default_dir = sys.argv[1] if len(sys.argv) > 1 else "."
        json_dir = st.text_input("JSON 目录", value=default_dir)

        # 扫描文件
        json_files = cached_scan_json_files(json_dir)
        if not json_files:
            st.warning(f"目录 `{json_dir}` 中未找到 JSON 文件")
            return None

        st.caption(f"发现 {len(json_files)} 个 JSON 文件")

        # 文件多选
        file_options = {os.path.basename(p): p for p in json_files}
        # 预选钩子：便于一键演示 / 冒烟测试直接渲染对比页。
        #   PERFKANBAN_PRESELECT=all        → 选中全部文件
        #   PERFKANBAN_PRESELECT=a.json,b   → 选中指定文件
        _preselect = os.environ.get("PERFKANBAN_PRESELECT", "")
        if _preselect == "all":
            _default = list(file_options.keys())
        elif _preselect:
            _default = [f for f in _preselect.split(",") if f in file_options]
        else:
            _default = []
        selected_files = st.multiselect(
            "选择文件",
            options=list(file_options.keys()),
            default=_default,
        )

        if len(selected_files) < 2:
            st.info("请至少选择 2 个文件（1 个 baseline + 1 个 candidate）")
            return None

        # 加载选中的文件
        loaded: dict[str, BenchmarkData] = {}
        seen_labels: dict[str, int] = {}  # label -> count，用于消重
        for fname in selected_files:
            path = file_options[fname]
            try:
                data = cached_load_benchmark(path)
                # 确保标签唯一：若重复则追加文件名
                label = data.name
                if label in seen_labels:
                    data.name = f"{label} ({fname})"
                    # 也更新先前的
                    for prev_fname, prev_data in loaded.items():
                        if prev_data.name == label:
                            prev_data.name = f"{label} ({prev_fname})"
                    seen_labels[label] += 1
                else:
                    seen_labels[label] = 1
                loaded[fname] = data
            except Exception as e:
                st.error(f"加载 `{fname}` 失败: {e}")

        if len(loaded) < 2:
            return None

        # 加入合成结果
        all_data = dict(loaded)
        for label, synth in st.session_state.synthetics.items():
            all_data[f"synthetic:{label}"] = synth

        # Baseline 选择
        all_keys = list(all_data.keys())
        baseline_key = st.selectbox("Baseline", options=all_keys, index=0)

        # 候选列表
        candidate_keys = [k for k in all_keys if k != baseline_key]

        # 阈值配置
        st.subheader("阈值")
        col1, col2 = st.columns(2)
        with col1:
            improve_thresh = st.number_input(
                "提升阈值", value=1.05, min_value=1.0, max_value=5.0, step=0.01,
                help="Speedup > 此值标记为提升（绿色）",
            )
        with col2:
            regress_thresh = st.number_input(
                "回归阈值", value=0.95, min_value=0.01, max_value=1.0, step=0.01,
                help="Speedup < 此值标记为回归（红色）",
            )

        # 筛选范围
        filter_range = st.slider(
            "Speedup 筛选范围",
            min_value=0.0,
            max_value=5.0,
            value=(0.0, 5.0),
            step=0.05,
            help="只显示 Speedup 超出此范围的用例（0.0-5.0 表示不过滤）",
        )

        return {
            "all_data": all_data,
            "loaded": loaded,
            "baseline_key": baseline_key,
            "candidate_keys": candidate_keys,
            "improve_thresh": improve_thresh,
            "regress_thresh": regress_thresh,
            "filter_range": filter_range,
        }


def render_tab_comparison(config: dict):
    """Tab 1: 对比表格"""
    all_data = config["all_data"]
    baseline = all_data[config["baseline_key"]]
    candidates = [all_data[k] for k in config["candidate_keys"]]
    improve_t = config["improve_thresh"]
    regress_t = config["regress_thresh"]
    filter_low, filter_high = config["filter_range"]

    # 获取用例列表
    benchmark_names = get_all_benchmark_names([baseline] + candidates)

    # 构建对比表
    df, speedup_cols = build_comparison_df(baseline, candidates, benchmark_names)
    if df.empty:
        st.warning("没有可对比的用例")
        return

    # Speedup 阈值筛选
    filtering = filter_low > 0.0 or filter_high < 5.0
    if filtering:
        df_filtered = filter_df_by_speedup(df, filter_low, filter_high, speedup_cols)
    else:
        df_filtered = df

    # 用例选择器（表格 + 柱状图共享）
    name_col = ("", "用例名")
    available_cases = df_filtered[name_col].tolist()
    selected_cases = st.multiselect(
        "选择用例",
        options=available_cases,
        default=available_cases,
    )

    # 根据选中用例过滤
    if selected_cases:
        df_display = df_filtered[df_filtered[name_col].isin(selected_cases)]
    else:
        df_display = df_filtered

    # 统计信息
    col1, col2, col3 = st.columns(3)
    col1.metric("用例总数", len(df))
    col2.metric("候选数", len(candidates))
    col3.metric("当前显示", len(df_display))

    # 排序：用原生 Streamlit 按钮（一排），点击即常规重跑——平滑、零 JS、不依赖
    # 组件 iframe/懒加载 chunk，反代/CDN/CSP 环境下也稳。排序状态存 session_state，
    # 表格/柱状图/导出共用；几何平均行追加在末尾、不参与排序。
    # 可排序列 = 用例名 + 各候选 Speedup（值/单位是带单位格式化串、跨行单位不一，不排）。
    ss = st.session_state
    ss.setdefault("cmp_sort", "__name__")
    ss.setdefault("cmp_asc", True)

    sortable: list[tuple] = [("__name__", "用例名", name_col)]
    for cand in candidates:
        col = (cand.name, "Speedup")
        if col in df_display.columns:
            sortable.append((cand.name, f"{cand.name} Speedup", col))
    if ss.cmp_sort not in {cid for cid, _, _ in sortable}:
        ss.cmp_sort = "__name__"

    st.caption("排序")
    btn_cols = st.columns(len(sortable))
    for (cid, label, _), bc in zip(sortable, btn_cols):
        arrow = ("  ▲" if ss.cmp_asc else "  ▼") if cid == ss.cmp_sort else ""
        if bc.button(label + arrow, key=f"cmp_sort_{cid}", use_container_width=True):
            if ss.cmp_sort == cid:
                ss.cmp_asc = not ss.cmp_asc
            else:
                ss.cmp_sort = cid
                ss.cmp_asc = True
            st.rerun()

    cur_sort = ss.cmp_sort
    ascending = ss.cmp_asc
    sort_col = next(col for cid, _, col in sortable if cid == cur_sort)

    # 仅排序数据行；几何平均行在渲染/导出时再追加到末尾，故不受排序影响
    df_sorted = _sort_df_for_export(df_display, sort_col, ascending)

    # HTML 表格渲染：二级表头 + 固定列(用例名/baseline) + 一级表头居中 + 底部几何平均行
    st.html(render_comparison_table_html(df_sorted, speedup_cols, improve_t, regress_t))

    # 导出按钮（沿用同一排序结果，故导出顺序与表格一致）
    if not df_sorted.empty:
        _render_export_buttons(
            df_sorted, speedup_cols, improve_t, regress_t,
            baseline.name, "comparison",
        )

    # Speedup 柱状图（复用选中用例 + 同一排序）
    if not df_sorted.empty and speedup_cols:
        st.subheader("Speedup 柱状图")
        st.bar_chart(
            build_speedup_chart_df(df_sorted, speedup_cols, name_col),
            horizontal=True,
        )


def render_tab_replacement(config: dict):
    """Tab 2: 用例替换（基准合成）"""
    loaded = config["loaded"]
    all_data = config["all_data"]
    baseline = all_data[config["baseline_key"]]
    candidates = [all_data[k] for k in config["candidate_keys"]]

    all_names = get_all_benchmark_names([baseline] + candidates)

    st.subheader("用例来源替换")

    # 选择基础文件
    source_keys = list(loaded.keys())
    base_file = st.selectbox("选择基础文件（合成以此为基础）", options=source_keys)

    if not base_file:
        return

    base_data = loaded[base_file]

    # 构建可编辑表格数据
    edit_rows = []
    for bname in all_names:
        current_source = base_file
        current_mean = None
        if bname in base_data.benchmarks:
            current_mean = round(base_data.benchmarks[bname].mean, 6)

        # 确定该用例在哪些文件中可用
        available = [base_file]
        for key, data in loaded.items():
            if key != base_file and bname in data.benchmarks:
                available.append(key)

        edit_rows.append({
            "用例名": bname,
            "来源文件": current_source,
            "均值 (s)": current_mean,
            "可用来源": ", ".join(available),
        })

    edit_df = pd.DataFrame(edit_rows)

    st.caption("当前基础文件中每个用例的来源和均值。下方可指定替换。")

    st.dataframe(edit_df, width="stretch", hide_index=True)

    # 替换操作
    st.subheader("指定替换")
    st.caption("为要替换的用例选择新的来源文件，留空则保持基础文件的原始数据。")

    replacements: dict[str, str] = {}
    for bname in all_names:
        # 找到该用例可用的文件
        available_for_name = [base_file]
        for key, data in loaded.items():
            if key != base_file and bname in data.benchmarks:
                available_for_name.append(key)

        if len(available_for_name) <= 1:
            continue

        selected = st.selectbox(
            f"`{bname}`",
            options=["保持原值"] + available_for_name,
            key=f"replace_{bname}",
        )
        if selected != "保持原值":
            replacements[bname] = selected

    # 生成合成结果
    if st.button("生成合成结果", type="primary"):
        if not replacements:
            st.info("没有指定任何替换")
            return

        counter = st.session_state.synthetic_counter + 1
        label = f"Synthetic-{counter}"
        synthetic = merge_benchmarks(loaded, replacements, base_file, label)

        st.session_state.synthetics[label] = synthetic
        st.session_state.synthetic_counter = counter
        st.success(f"已生成 `{label}`，包含 {len(replacements)} 个用例替换。请切回 Tab 1 查看。")

    # 管理已有合成结果
    if st.session_state.synthetics:
        st.subheader("已有合成结果")
        for label, synth in list(st.session_state.synthetics.items()):
            col1, col2 = st.columns([3, 1])
            col1.text(f"{label} ({len(synth.benchmarks)} 用例)")
            if col2.button("删除", key=f"del_{label}"):
                del st.session_state.synthetics[label]
                st.rerun()


def render_tab_trend(config: dict):
    """Tab 3: 趋势观察"""
    all_data = config["all_data"]
    baseline = all_data[config["baseline_key"]]
    candidates = [all_data[k] for k in config["candidate_keys"]]
    improve_t = config["improve_thresh"]
    regress_t = config["regress_thresh"]

    benchmark_names = get_all_benchmark_names([baseline] + candidates)

    df, speedup_cols = build_trend_df(baseline, candidates, benchmark_names)
    if df.empty:
        st.warning("没有可观察趋势的用例")
        return

    # 统计
    trend_col = ("", "趋势")
    col1, col2, col3 = st.columns(3)
    col1.metric("用例总数", len(df))
    col2.metric("候选数", len(candidates))
    trend_counts = df[trend_col].value_counts()
    col3.metric("趋势分布", " | ".join(f"{k}: {v}" for k, v in trend_counts.items()))

    # 应用样式
    styled = apply_speedup_styling(df, improve_t, regress_t, speedup_cols)
    st.dataframe(styled, width="stretch", height=600)

    # 导出按钮
    if not df.empty:
        _render_export_buttons(
            df, speedup_cols, improve_t, regress_t,
            baseline.name, "trend",
        )

    # 自选用例 Speedup 趋势图
    st.subheader("单用例趋势图")
    selected_bench = st.selectbox("选择用例", options=benchmark_names)
    if selected_bench:
        name_col = ("", "用例名")
        row = df[df[name_col] == selected_bench]
        if not row.empty:
            chart_data = {}
            for sc in speedup_cols:
                su_val = row.iloc[0].get(sc)
                if su_val is not None and pd.notna(su_val):
                    chart_data[sc[0]] = float(su_val)
            if chart_data:
                chart_df = pd.DataFrame(
                    {"Speedup": chart_data}, index=list(chart_data.keys())
                )
                st.bar_chart(chart_df)
                st.caption("Speedup = 1.0 为基准线（与 baseline 持平）")


def main():
    st.set_page_config(
        page_title="Performance Kanban",
        page_icon="📊",
        layout="wide",
    )
    st.title("Performance Kanban")
    st.caption("pyperformance 测试结果对比工具")

    init_session_state()

    config = render_sidebar()
    if config is None:
        st.stop()

    tab1, tab2, tab3 = st.tabs(["对比表格", "用例替换", "趋势观察"])

    with tab1:
        render_tab_comparison(config)

    with tab2:
        render_tab_replacement(config)

    with tab3:
        render_tab_trend(config)


if __name__ == "__main__":
    main()

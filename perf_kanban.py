#!/usr/bin/env python3
"""pyperformance 测试结果观察工具 — Performance Kanban

用法:
    streamlit run perf_kanban.py [json_dir]
    streamlit run perf_kanban.py              # 默认当前目录
    streamlit run perf_kanban.py /path/to/json
"""

import sys
import os
import json
import statistics
from dataclasses import dataclass, field
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


def _derive_label(metadata: dict, filename: str) -> str:
    """从 metadata 推导可读标签"""
    parts = []
    py_ver = metadata.get("python_version", "")
    if py_ver:
        parts.append(py_ver)
    commit = metadata.get("commit_id", "")
    if commit:
        parts.append(commit[:8])
    platform = metadata.get("platform", "")
    if platform:
        parts.append(platform)
    if not parts:
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

        # 用第一个 run 的 metadata.name 作为用例名
        run_meta = runs[0].get("metadata", {})
        bench_name = run_meta.get("name", "unknown")

        # 合并所有 run 的 values
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
    每个 candidate 三列：绝对值 | 单位 | Speedup
    返回 (DataFrame, speedup列名元组列表)
    """
    # 构建 MultiIndex 列
    col_tuples: list[tuple] = [("", "用例名")]
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

        row = [name]
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

    # 列宽：用例名宽，值/单位窄，Speedup 适中
    col_widths = {}
    for col in df.columns:
        if isinstance(col, tuple):
            top, sub = col
            if sub == "用例名":
                col_widths[col] = 160
            elif sub == "值":
                col_widths[col] = 90
            elif sub == "单位":
                col_widths[col] = 50
            elif sub == "Speedup":
                col_widths[col] = 100
            elif sub == "趋势":
                col_widths[col] = 80
        else:
            col_widths[col] = 100

    # 设置列宽（px）
    styler = styler.set_properties(
        **{"text-align": "right"},
        subset=[c for c in df.columns if isinstance(c, tuple) and c[1] in ("值", "Speedup")]
    )

    return styler


# =============================================================================
# Caching Layer
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
        selected_files = st.multiselect(
            "选择文件",
            options=list(file_options.keys()),
        )

        if len(selected_files) < 2:
            st.info("请至少选择 2 个文件（1 个 baseline + 1 个 candidate）")
            return None

        # 加载选中的文件
        loaded: dict[str, BenchmarkData] = {}
        for fname in selected_files:
            path = file_options[fname]
            try:
                data = cached_load_benchmark(path)
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

    # 是否启用筛选
    filtering = filter_low > 0.0 or filter_high < 5.0
    if filtering:
        df_filtered = filter_df_by_speedup(df, filter_low, filter_high, speedup_cols)
    else:
        df_filtered = df

    # 统计信息
    col1, col2, col3 = st.columns(3)
    col1.metric("用例总数", len(df))
    col2.metric("候选数", len(candidates))
    col3.metric("筛选命中", len(df_filtered))

    # 应用样式（含列宽）
    styled = apply_speedup_styling(df_filtered, improve_t, regress_t, speedup_cols)
    st.dataframe(styled, width="stretch", height=600)

    # 自选用例 Speedup 柱状图
    if not df_filtered.empty and speedup_cols:
        st.subheader("Speedup 柱状图")
        name_col = ("", "用例名")
        available_cases = df_filtered[name_col].tolist()
        selected_cases = st.multiselect(
            "选择要展示的用例",
            options=available_cases,
            default=available_cases[:5] if len(available_cases) <= 5 else [],
        )
        if selected_cases:
            mask = df_filtered[name_col].isin(selected_cases)
            chart_df = df_filtered[mask].set_index(name_col)[speedup_cols]
            # 列名从 tuple 简化为 candidate 名
            chart_df.columns = [c[0] for c in chart_df.columns]
            st.bar_chart(chart_df, horizontal=True)
        else:
            st.caption("请在上方选择至少一个用例")


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

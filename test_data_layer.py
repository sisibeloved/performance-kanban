"""测试 Data Layer 核心函数"""
import json
import tempfile
import os

# 添加当前目录到 path
import sys
sys.path.insert(0, os.path.dirname(__file__))

import perf_kanban
from perf_kanban import (
    load_benchmark,
    compute_speedup,
    build_comparison_df,
    build_trend_df,
    merge_benchmarks,
    filter_df_by_speedup,
    get_all_benchmark_names,
    export_df_to_markdown,
    export_df_to_image,
    apply_speedup_styling,
    _geomean_speedup,
    _comparison_column_config,
    _build_export_buttons_iframe_html,
)


class _FakeExportColumn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeExportStreamlit:
    def __init__(self):
        self.iframes = []

    def subheader(self, *args, **kwargs):
        pass

    def columns(self, count):
        return [_FakeExportColumn() for _ in range(count)]

    def download_button(self, *args, **kwargs):
        raise AssertionError("download buttons should be rendered by the iframe")

    def iframe(self, src, **kwargs):
        self.iframes.append((src, kwargs))


def make_json_file(benchmarks_dict: dict, metadata: dict = None) -> str:
    """生成临时 pyperf JSON 文件"""
    bench_list = []
    for name, mean_val in benchmarks_dict.items():
        bench_list.append({
            "runs": [{
                "values": [mean_val] * 5,
                "metadata": {"name": name, "loops": 10},
            }]
        })

    data = {
        "benchmarks": bench_list,
        "metadata": metadata or {
            "python_version": "3.12.0",
            "platform": "linux-x86_64",
            "commit_id": "abc12345",
        },
    }

    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    return path


def test_load_benchmark():
    path = make_json_file({"2to3": 0.234, "json_dumps": 0.012})
    data = load_benchmark(path)
    assert "2to3" in data.benchmarks
    assert "json_dumps" in data.benchmarks
    assert abs(data.benchmarks["2to3"].mean - 0.234) < 1e-6
    assert abs(data.benchmarks["json_dumps"].mean - 0.012) < 1e-6
    os.unlink(path)
    print("  [PASS] test_load_benchmark")


def test_compute_speedup():
    # candidate 更快
    assert abs(compute_speedup(2.0, 1.0) - 2.0) < 1e-6
    # candidate 更慢
    assert abs(compute_speedup(1.0, 2.0) - 0.5) < 1e-6
    # 持平
    assert abs(compute_speedup(1.0, 1.0) - 1.0) < 1e-6
    # 边界
    assert compute_speedup(0.0, 1.0) is None
    assert compute_speedup(1.0, 0.0) is None
    print("  [PASS] test_compute_speedup")


def test_comparison_df():
    p1 = make_json_file({"2to3": 0.2, "regex": 0.1}, {"python_version": "3.12.0"})
    p2 = make_json_file({"2to3": 0.1, "regex": 0.2}, {"python_version": "3.13.0"})

    base = load_benchmark(p1)
    cand = load_benchmark(p2)
    names = get_all_benchmark_names([base, cand])

    df, speedup_cols = build_comparison_df(base, [cand], names)
    assert len(df) == 2
    assert len(speedup_cols) == 1
    speedup_col = speedup_cols[0]
    assert speedup_col in df.columns
    # baseline 绝对值列也应展示，并固定在候选列左侧
    assert (base.name, "值") in df.columns
    assert (base.name, "单位") in df.columns
    assert list(df.columns[:3]) == [("", "用例名"), (base.name, "值"), (base.name, "单位")]
    # 2to3: baseline=0.2, cand=0.1, speedup=2.0
    name_col = ("", "用例名")
    row_2to3 = df[df[name_col] == "2to3"].iloc[0]
    assert abs(row_2to3[speedup_col] - 2.0) < 1e-4
    assert row_2to3[(base.name, "值")] is not None

    os.unlink(p1)
    os.unlink(p2)
    print("  [PASS] test_comparison_df")


def test_trend_df():
    p1 = make_json_file({"2to3": 0.2}, {"python_version": "3.12.0"})
    p2 = make_json_file({"2to3": 0.19}, {"python_version": "3.13.0a1"})
    p3 = make_json_file({"2to3": 0.18}, {"python_version": "3.13.0a2"})
    p4 = make_json_file({"2to3": 0.17}, {"python_version": "3.13.0b1"})

    base = load_benchmark(p1)
    cands = [load_benchmark(p) for p in [p2, p3, p4]]
    names = get_all_benchmark_names([base] + cands)

    df, speedup_cols = build_trend_df(base, cands, names)
    assert len(df) == 1
    assert ("baseline", "值") in df.columns
    assert ("", "趋势") in df.columns
    # Speedup 序列：0.2/0.19 ≈ 1.053, 0.2/0.18 ≈ 1.111, 0.2/0.17 ≈ 1.176
    # 趋势：差值 = 1.176 - 1.053 = 0.123 > 0.02 → "↑ 提升"
    assert df.iloc[0][("", "趋势")] == "↑ 提升"

    for p in [p1, p2, p3, p4]:
        os.unlink(p)
    print("  [PASS] test_trend_df")


def test_merge_benchmarks():
    p1 = make_json_file({"2to3": 0.2, "regex": 0.1}, {"python_version": "3.12.0"})
    p2 = make_json_file({"2to3": 0.15, "regex": 0.2}, {"python_version": "3.13.0"})

    d1 = load_benchmark(p1)
    d2 = load_benchmark(p2)

    sources = {"base": d1, "other": d2}
    # 替换 2to3 用 other 的数据，regex 保持 base
    merged = merge_benchmarks(sources, {"2to3": "other"}, "base", "Synth-1")

    assert abs(merged.benchmarks["2to3"].mean - 0.15) < 1e-6  # 来自 other
    assert abs(merged.benchmarks["regex"].mean - 0.1) < 1e-6  # 来自 base

    os.unlink(p1)
    os.unlink(p2)
    print("  [PASS] test_merge_benchmarks")


def test_filter_by_speedup():
    p1 = make_json_file({"a": 1.0, "b": 1.0, "c": 1.0})
    p2 = make_json_file({"a": 0.5, "b": 1.0, "c": 2.0})

    base = load_benchmark(p1)
    cand = load_benchmark(p2)
    names = get_all_benchmark_names([base, cand])

    df, speedup_cols = build_comparison_df(base, [cand], names)

    # 筛选 Speedup > 1.1 或 < 0.9
    filtered = filter_df_by_speedup(df, 0.9, 1.1, speedup_cols)
    # a: speedup=2.0 (超), b: speedup=1.0 (内), c: speedup=0.5 (超)
    assert len(filtered) == 2
    name_col = ("", "用例名")
    assert "a" in filtered[name_col].values
    assert "c" in filtered[name_col].values

    os.unlink(p1)
    os.unlink(p2)
    print("  [PASS] test_filter_by_speedup")


def test_export_markdown_comparison():
    """测试对比表格 Markdown 导出"""
    p1 = make_json_file({"2to3": 0.2, "regex": 0.1}, {"python_version": "3.12.0"})
    p2 = make_json_file({"2to3": 0.1, "regex": 0.2}, {"python_version": "3.13.0"})

    base = load_benchmark(p1)
    cand = load_benchmark(p2)
    names = get_all_benchmark_names([base, cand])
    df, speedup_cols = build_comparison_df(base, [cand], names)

    md = export_df_to_markdown(df, speedup_cols, 1.05, 0.95, baseline_label="3.12.0")

    # 包含 baseline 信息
    assert "**Baseline:** `3.12.0`" in md
    # 包含 Geomean
    assert "Geomean Speedup" in md
    # Markdown 表格格式
    assert "| Benchmark |" in md
    assert "| --- |" in md
    # 2to3 speedup = 0.2/0.1 = 2.0 (>= 1.05, 加粗)
    assert "**2.0000**" in md
    # regex speedup = 0.1/0.2 = 0.5 (<= 0.95, 加粗)
    assert "**0.5000**" in md

    os.unlink(p1)
    os.unlink(p2)
    print("  [PASS] test_export_markdown_comparison")


def test_export_markdown_trend():
    """测试趋势表格 Markdown 导出"""
    p1 = make_json_file({"2to3": 0.2}, {"python_version": "3.12.0"})
    p2 = make_json_file({"2to3": 0.19}, {"python_version": "3.13.0a1"})
    p3 = make_json_file({"2to3": 0.17}, {"python_version": "3.13.0b1"})

    base = load_benchmark(p1)
    cands = [load_benchmark(p) for p in [p2, p3]]
    names = get_all_benchmark_names([base] + cands)
    df, speedup_cols = build_trend_df(base, cands, names)

    md = export_df_to_markdown(df, speedup_cols, 1.05, 0.95, baseline_label="3.12.0")

    # 趋势表包含 baseline 列和 Trend 列
    assert "| Benchmark |" in md
    assert "baseline |" in md
    assert "| Trend |" in md
    # 2to3 趋势为 ↑ 提升
    assert "↑ 提升" in md

    for p in [p1, p2, p3]:
        os.unlink(p)
    print("  [PASS] test_export_markdown_trend")


def test_export_markdown_sort_parameter_does_not_mutate_table_df():
    """测试导出排序仅影响导出内容，不改变表格 DataFrame"""
    p1 = make_json_file({"a": 1.0, "b": 1.0, "c": 1.0})
    p2 = make_json_file({"a": 0.5, "b": 1.0, "c": 2.0})

    base = load_benchmark(p1)
    cand = load_benchmark(p2)
    names = get_all_benchmark_names([base, cand])
    df, speedup_cols = build_comparison_df(base, [cand], names)
    name_col = ("", "用例名")

    original_order = df[name_col].tolist()
    md = export_df_to_markdown(
        df,
        speedup_cols,
        1.05,
        0.95,
        export_sort_by=speedup_cols[0],
        export_sort_ascending=True,
    )

    table_rows = [
        line.split("|")[1].strip()
        for line in md.splitlines()
        if line.startswith("| ")
        and not line.startswith("| Benchmark |")
        and not line.startswith("| --- |")
    ]
    assert table_rows == ["c", "b", "a"]
    assert df[name_col].tolist() == original_order

    os.unlink(p1)
    os.unlink(p2)
    print("  [PASS] test_export_markdown_sort_parameter_does_not_mutate_table_df")


def test_comparison_column_config_pins_name_and_baseline_columns():
    """测试对比表用位置固定用例名和 baseline 列，保留 MultiIndex 列名"""
    p1 = make_json_file({"a": 1.0})
    p2 = make_json_file({"a": 0.5})

    base = load_benchmark(p1)
    cand = load_benchmark(p2)
    df, speedup_cols = build_comparison_df(base, [cand], ["a"])

    config = _comparison_column_config(df, speedup_cols)

    assert all(getattr(col, "__class__", None) is tuple for col in df.columns)
    assert config[0]["pinned"] is True
    assert config[1]["pinned"] is True
    assert config[2]["pinned"] is True
    assert config[1]["alignment"] == "right"
    assert config[3]["pinned"] is False

    os.unlink(p1)
    os.unlink(p2)
    print("  [PASS] test_comparison_column_config_pins_name_and_baseline_columns")


def test_speedup_styling_centers_top_level_column_headers():
    """测试一级列头居中显示"""
    p1 = make_json_file({"a": 1.0})
    p2 = make_json_file({"a": 0.5})

    base = load_benchmark(p1)
    cand = load_benchmark(p2)
    df, speedup_cols = build_comparison_df(base, [cand], ["a"])

    styled = apply_speedup_styling(df, 1.05, 0.95, speedup_cols)
    styled._compute()
    styles = styled._translate(False, False)

    top_level_header_styles = [
        style
        for style in styles["table_styles"]
        if style["selector"] == ".col_heading.level0"
    ]
    assert top_level_header_styles == [
        {"selector": ".col_heading.level0", "props": [("text-align", "center")]}
    ]

    os.unlink(p1)
    os.unlink(p2)
    print("  [PASS] test_speedup_styling_centers_top_level_column_headers")


def test_export_buttons_iframe_html_renders_three_uniform_actions():
    """测试导出区只渲染三个统一样式按钮"""
    html = _build_export_buttons_iframe_html(
        'quote " and </script> marker',
        b"\xff\xd8\xffabc",
        "comparison.md",
        "comparison.jpg",
    )

    assert html.count('class="export-action-button"') == 3
    assert "导出 Markdown" in html
    assert "复制 Markdown 到剪贴板" in html
    assert "导出 JPG" in html
    assert "复制 JPG" not in html
    assert "ClipboardItem" not in html
    assert "navigator.clipboard.writeText" in html
    assert "download=\"comparison.md\"" in html
    assert "download=\"comparison.jpg\"" in html
    assert "data:text/markdown;base64," in html
    assert "data:image/jpeg;base64,/9j/YWJj" in html
    assert 'quote \\" and <\\/script> marker' in html
    assert "</script> marker" not in html
    print("  [PASS] test_export_buttons_iframe_html_renders_three_uniform_actions")


def test_render_export_buttons_uses_iframe_for_button_group():
    """测试导出区用 st.iframe 渲染统一按钮组"""
    fake_st = _FakeExportStreamlit()
    original_st = perf_kanban.st
    original_markdown = perf_kanban.export_df_to_markdown
    original_image = perf_kanban.export_df_to_image
    perf_kanban.st = fake_st
    perf_kanban.export_df_to_markdown = lambda *args, **kwargs: "markdown"
    perf_kanban.export_df_to_image = lambda *args, **kwargs: b"\xff\xd8\xffabc"

    try:
        perf_kanban._render_export_buttons(
            df=None,
            speedup_cols=[],
            improve_thresh=1.05,
            regress_thresh=0.95,
            baseline_label="",
            file_prefix="comparison",
        )
    finally:
        perf_kanban.st = original_st
        perf_kanban.export_df_to_markdown = original_markdown
        perf_kanban.export_df_to_image = original_image

    assert len(fake_st.iframes) == 1
    iframe_html, iframe_kwargs = fake_st.iframes[0]
    assert "复制 JPG" not in iframe_html
    assert iframe_kwargs["height"] > 0
    print("  [PASS] test_render_export_buttons_uses_iframe_for_button_group")


def test_geomean_speedup():
    """测试 Geomean Speedup 计算"""
    p1 = make_json_file({"a": 1.0, "b": 1.0, "c": 1.0})
    p2 = make_json_file({"a": 0.5, "b": 1.0, "c": 2.0})

    base = load_benchmark(p1)
    cand = load_benchmark(p2)
    names = get_all_benchmark_names([base, cand])
    df, speedup_cols = build_comparison_df(base, [cand], names)

    gmeans = _geomean_speedup(df, speedup_cols)
    assert len(gmeans) == 1
    # speedups: 2.0, 1.0, 0.5 → geomean = (2.0 * 1.0 * 0.5)^(1/3) = 1.0
    gm_val = list(gmeans.values())[0]
    assert abs(gm_val - 1.0) < 1e-6

    os.unlink(p1)
    os.unlink(p2)
    print("  [PASS] test_geomean_speedup")


def test_export_image():
    """测试 JPG 图片导出"""
    p1 = make_json_file({"2to3": 0.2, "regex": 0.1}, {"python_version": "3.12.0"})
    p2 = make_json_file({"2to3": 0.1, "regex": 0.2}, {"python_version": "3.13.0"})

    base = load_benchmark(p1)
    cand = load_benchmark(p2)
    names = get_all_benchmark_names([base, cand])
    df, speedup_cols = build_comparison_df(base, [cand], names)

    img_bytes = export_df_to_image(df, speedup_cols, 1.05, 0.95, baseline_label="3.12.0")

    # 返回有效的 JPG 字节
    assert isinstance(img_bytes, bytes)
    assert len(img_bytes) > 1000
    # JPEG magic bytes: FF D8 FF
    assert img_bytes[:3] == b'\xff\xd8\xff'

    os.unlink(p1)
    os.unlink(p2)
    print("  [PASS] test_export_image")


if __name__ == "__main__":
    print("Running Data Layer tests...")
    test_load_benchmark()
    test_compute_speedup()
    test_comparison_df()
    test_trend_df()
    test_merge_benchmarks()
    test_filter_by_speedup()
    test_export_markdown_comparison()
    test_export_markdown_trend()
    test_export_markdown_sort_parameter_does_not_mutate_table_df()
    test_comparison_column_config_pins_name_and_baseline_columns()
    test_speedup_styling_centers_top_level_column_headers()
    test_export_buttons_iframe_html_renders_three_uniform_actions()
    test_render_export_buttons_uses_iframe_for_button_group()
    test_geomean_speedup()
    test_export_image()
    print("\nAll tests passed!")

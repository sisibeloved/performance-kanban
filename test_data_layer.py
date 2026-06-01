"""测试 Data Layer 核心函数"""
import json
import tempfile
import os

# 添加当前目录到 path
import sys
sys.path.insert(0, os.path.dirname(__file__))

from perf_kanban import (
    load_benchmark,
    compute_speedup,
    build_comparison_df,
    build_trend_df,
    merge_benchmarks,
    filter_df_by_speedup,
    get_all_benchmark_names,
)


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
    # 2to3: baseline=0.2, cand=0.1, speedup=2.0
    name_col = ("", "用例名")
    row_2to3 = df[df[name_col] == "2to3"].iloc[0]
    assert abs(row_2to3[speedup_col] - 2.0) < 1e-4

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


if __name__ == "__main__":
    print("Running Data Layer tests...")
    test_load_benchmark()
    test_compute_speedup()
    test_comparison_df()
    test_trend_df()
    test_merge_benchmarks()
    test_filter_by_speedup()
    print("\nAll tests passed!")

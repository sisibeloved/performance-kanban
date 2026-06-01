"""生成模拟 pyperformance 测试数据用于端到端验证"""
import json
import os
import random
import tempfile

BENCHMARKS = [
    "2to3", "chameleon", "chaos", "crypto_pyaes", "delve",
    "djangocms", "dulwich_log", "fannkuch", "float", "genshi_text",
    "genshi_xml", "go", "hexiom", "html5lib", "json_dumps",
    "json_loads", "logging", "mako", "meteor_contest", "nbody",
    "nqueens", "pathlib", "pickle", "pickle_dict", "pickle_list",
    "pickle_pure_python", "pidigits", "python_startup",
    "python_startup_no_site", "regex_compile", "regex_dna",
    "richards", "scimark_lu", "scimark_monte_carlo",
    "scimark_sor", "scimark_sparse_mat_mult", "spectral_norm",
    "sqlalchemy_declarative", "sqlalchemy_imperative",
    "sqlglot_optimize", "sympy_expand", "sympy_integrate",
    "sympy_str", "sympy_sum", "telco", "tornado_http",
    "typing_extensions", "unpack_sequence", "unpickle", "unpickle_list",
]


def generate_file(
    output_dir: str,
    name: str,
    python_version: str,
    platform: str,
    commit: str,
    speed_factor: float = 1.0,
    missing: list[str] = None,
) -> str:
    """生成一个模拟的 pyperf JSON 文件"""
    missing = missing or []
    benchmarks = []
    for bench_name in BENCHMARKS:
        if bench_name in missing:
            continue
        base_time = random.uniform(0.001, 1.0)
        mean_time = base_time * speed_factor
        # 生成 5 个采样值，带轻微随机波动
        values = [mean_time * random.uniform(0.97, 1.03) for _ in range(5)]
        benchmarks.append({
            "runs": [{
                "values": values,
                "metadata": {
                    "name": bench_name,
                    "loops": 10,
                    "inner_loops": 1,
                },
            }],
        })

    data = {
        "benchmarks": benchmarks,
        "metadata": {
            "python_version": python_version,
            "platform": platform,
            "commit_id": commit,
            "commit_branch": "main",
            "hostname": "test-machine",
            "cpu_model_name": "Test CPU",
        },
    }

    path = os.path.join(output_dir, name)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def main():
    random.seed(42)
    output_dir = os.path.join(os.path.dirname(__file__), "sample_data")
    os.makedirs(output_dir, exist_ok=True)

    # Baseline: CPython 3.12
    generate_file(output_dir, "cpython_3.12_baseline.json",
                  "3.12.0", "linux-x86_64", "a" * 40, speed_factor=1.0)

    # Candidate 1: CPython 3.13 (稍快)
    generate_file(output_dir, "cpython_3.13.json",
                  "3.13.0", "linux-x86_64", "b" * 40, speed_factor=0.92)

    # Candidate 2: CPython 3.14 (更快)
    generate_file(output_dir, "cpython_3.14.json",
                  "3.14.0a1", "linux-x86_64", "c" * 40, speed_factor=0.85)

    # Candidate 3: PyPy (更快，但有些用例缺失)
    generate_file(output_dir, "pypy3.10.json",
                  "3.10.14", "linux-x86_64", "d" * 40, speed_factor=0.6,
                  missing=["dulwich_log", "sqlglot_optimize", "tornado_http"])

    print(f"Sample data generated in {output_dir}/")
    for f in sorted(os.listdir(output_dir)):
        if f.endswith(".json"):
            path = os.path.join(output_dir, f)
            size = os.path.getsize(path)
            print(f"  {f} ({size} bytes)")


if __name__ == "__main__":
    main()

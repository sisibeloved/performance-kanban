# Performance Kanban — pyperformance 测试结果观察工具

面向 pyperformance 测试结果的单文件 Streamlit 交互式对比工具。

---

## 功能

| 能力 | 说明 |
| --- | --- |
| Speedup 倍数对比 | 以 `baseline / candidate` 作为性能差距指标，支持排序和按阈值筛选 |
| 多平台 / 多配置对比 | 同时加载多个 JSON 文件，逐用例横向比较 |
| 用例数据替换合成 | 从不同 JSON 中选取单个用例结果，合成为一组新数据 |
| 固定 baseline 趋势观察 | 锁定 baseline，观察多个 candidate 的 Speedup 变化趋势 |
| 颜色编码 | 提升阈值 / 回归阈值可配，Speedup 列自动着色 |

---

## 快速开始

### 安装依赖

```bash
pip install streamlit pandas plotly matplotlib
```

### 启动

```bash
# 指定 JSON 目录
streamlit run perf_kanban.py /path/to/json

# 默认当前目录
streamlit run perf_kanban.py
```

浏览器打开 `http://localhost:8501`。

### 模拟数据

```bash
python generate_sample_data.py    # 生成 sample_data/ 下的 4 个测试 JSON
streamlit run perf_kanban.py sample_data/
```

---

## 界面说明

### Sidebar — 全局配置

| 控件 | 用途 |
| --- | --- |
| JSON 目录 | 扫描目录下所有 `.json` 文件 |
| 选择文件 | 多选要纳入分析的文件（至少 2 个） |
| Baseline | 选定一个文件作为基准 |
| 提升阈值 / 回归阈值 | Speedup 着色边界，默认 1.05 / 0.95 |
| Speedup 筛选范围 | 只显示超出此范围的用例 |

### Tab 1: 对比表格

核心视图。二级表头按 candidate 分组，每组三列：`值`（原始单位）、`单位`（us/ms/s）、`Speedup`（4 位小数）。

- Speedup 列按阈值着色：绿色 = 提升，红色 = 回归
- 底部柱状图支持自选用例，避免全量用例单位差异导致的图表失真
- 一键导出：Markdown 格式（含 Geomean Speedup 摘要，适合 PR）/ JPG 图片

### Tab 2: 用例替换

从已加载的多个 JSON 中选取单个用例结果合成为新数据集。操作流程：

1. 选择基础文件
2. 为指定用例选择替代来源
3. 点击"生成合成结果"
4. 合成结果自动出现在 Tab 1 的对比表格中

### Tab 3: 趋势观察

固定 baseline，展示所有 candidate 的 Speedup 变化。表尾"趋势"列根据首尾 Speedup 差值标记方向（↑ 提升 / ↓ 回归 / ↔ 波动）。底部支持选择单个用例绘制趋势图。

---

## 数据格式

输入为 pyperf JSON 格式（version 6+），由 `pyperformance run` 或 `python -m pyperf` 生成：

```json
{
    "benchmarks": [
        {
            "runs": [{
                "values": [0.234, 0.235, 0.233],
                "metadata": {"name": "2to3", "loops": 20}
            }]
        }
    ],
    "metadata": {
        "python_version": "3.12.0",
        "platform": "linux-x86_64",
        "commit_id": "abc12345"
    }
}
```

内部解析为 `BenchmarkData` / `BenchmarkResult` 数据结构，每个用例提取均值用于 Speedup 计算。

---

## 文件结构

```
performance-kanban/
├── perf_kanban.py              # 单文件应用（Streamlit）
├── generate_sample_data.py     # 模拟数据生成器
├── test_data_layer.py          # Data Layer 单元测试
├── start.sh                    # WSL 启动脚本
├── docs/
│   └── superpowers/specs/
│       └── 2026-05-30-perf-kanban-design.md
├── README.md
└── CHANGELOG.md
```

---

## 测试

```bash
python test_data_layer.py
```

覆盖：JSON 解析、Speedup 计算、对比表构建、趋势表构建、用例替换合成、阈值筛选。

---

## 许可

MIT

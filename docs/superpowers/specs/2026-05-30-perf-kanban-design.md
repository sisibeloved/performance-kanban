# pyperformance 测试结果观察工具 — 设计文档

> 日期: 2026-05-30
> 状态: 已确认

## 1. 概述

一个基于 Streamlit 的单文件 Web 应用，用于加载、对比和分析 pyperformance 的 JSON 测试结果。核心能力：Speedup 倍数对比、多平台/多配置横向比较、用例级别数据替换合成、固定 baseline 趋势观察。

**启动方式：** `streamlit run perf_kanban.py [json_dir]`

**依赖：** `streamlit`, `pandas`, `plotly`（可选，用于增强图表）

## 2. 架构

```
perf_kanban.py (单文件)
├── Data Layer (纯函数，无 Streamlit 依赖)
│   ├── load_benchmark(path) -> dict
│   ├── compute_speedup(baseline, candidate) -> dict
│   ├── merge_benchmarks(sources, replacements) -> dict
│   └── filter_by_threshold(data, threshold) -> dict
├── UI Layer (Streamlit 页面)
│   ├── Sidebar (配置面板)
│   ├── Tab 1: 对比表格
│   ├── Tab 2: 用例替换
│   └── Tab 3: 趋势观察
└── Session State (跨 Tab 共享数据)
    ├── loaded_data: dict[str, BenchmarkData]
    ├── synthetics: dict[str, BenchmarkData]
    └── config: dict (baseline, threshold, etc.)
```

## 3. 数据模型

### 3.1 输入格式

pyperf JSON 格式（version 6+）：

```json
{
    "benchmarks": [
        {
            "runs": [
                {
                    "values": [0.234, 0.235, 0.233],
                    "warmups": [[1, 0.456]],
                    "metadata": {
                        "name": "2to3",
                        "loops": 20,
                        "inner_loops": 3
                    }
                }
            ]
        }
    ],
    "metadata": {
        "commit_id": "abc123",
        "commit_branch": "main",
        "commit_date": "2024-01-01T12:00:00",
        "hostname": "machine-a",
        "python_version": "3.12.0",
        "platform": "linux-x86_64",
        "cpu_model_name": "AMD EPYC 7763"
    }
}
```

### 3.2 内部数据结构

每个加载的 JSON 文件统一转换为：

```python
@dataclass
class BenchmarkData:
    name: str          # 自动标签，如 "CPython 3.12 @ x86_64"
    file: str          # 原始文件名
    metadata: dict     # 原始全局 metadata
    benchmarks: dict[str, BenchmarkResult]

@dataclass
class BenchmarkResult:
    mean: float        # 均值 (秒)
    values: list[float]  # 原始采样值
    metadata: dict     # 该用例的 metadata
```

### 3.3 Speedup 计算

```python
def compute_speedup(baseline_mean: float, candidate_mean: float) -> float:
    """Speedup = baseline / candidate。> 1 表示更快，< 1 表示更慢。"""
    return baseline_mean / candidate_mean
```

## 4. 功能设计

### 4.1 Sidebar — 全局配置

| 控件 | 类型 | 说明 |
|------|------|------|
| JSON 目录 | `st.text_input` | 输入包含 JSON 文件的目录路径 |
| 已加载文件 | `st.multiselect` | 选择要纳入分析的文件 |
| Baseline 选择 | `st.selectbox` | 从已选文件中选择一个作为 baseline |
| 提升阈值 | `st.number_input` | Speedup > 此值标记为提升，默认 1.05 |
| 回归阈值 | `st.number_input` | Speedup < 此值标记为回归，默认 0.95 |
| Speedup 筛选范围 | `st.slider` | 只显示 Speedup 超出此范围的用例 |

所有配置通过 `st.session_state` 在三个 Tab 之间共享。

### 4.2 Tab 1 — 对比表格

**用途：** 选中 baseline 和多个 candidate，逐用例对比 Speedup。

**表格列：**

| 列 | 内容 | 说明 |
|----|------|------|
| 用例名 | benchmark name | 行索引 |
| baseline (s) | 绝对时间均值 | 固定在最左列 |
| candidate-1 Speedup | 倍数 | 颜色编码 |
| candidate-2 Speedup | 倍数 | 颜色编码 |
| ... | ... | ... |
| 最大差异 | max/min Speedup | 显示最大偏差 |

**颜色编码（与阈值联动）：**
- 绿色：Speedup > 提升阈值（默认 1.05）
- 红色：Speedup < 回归阈值（默认 0.95）
- 灰色/白色：中间范围

通过 pandas Styler 的 `style.map()` 实现，Streamlit 自动渲染。

**交互：**
- `st.dataframe` 原生列排序
- 侧边栏阈值和筛选范围实时过滤
- 表格上方统计：已加载文件数、用例总数、筛选命中数

**可选附加：** Speedup 柱状图（`st.bar_chart`），按用例分组。

### 4.3 Tab 2 — 用例替换（基准合成）

**用途：** 从多个 JSON 中选取不同用例的结果，合成为一组新结果。

**交互流程：**

1. 显示所有已加载文件的用例来源表
2. `st.data_editor` 可编辑表格：
   - 列：`用例名 | 当前来源文件 | 当前均值`
   - "当前来源文件" 列为下拉菜单，可切换为其他文件
   - 切换后自动加载对应用例数据
   - 某文件中不存在的用例标记为 N/A，不可选
3. 点击 "生成合成结果" 按钮
4. 合成结果存入 `st.session_state["synthetics"]`
5. 自动在 Tab 1 中作为新 candidate 出现

支持创建多个合成结果（Synthetic-1、Synthetic-2...），通过侧边栏管理。

### 4.4 Tab 3 — 趋势观察

**用途：** 固定 baseline，观察多个 candidate 的 Speedup 变化趋势。

**表格列：**

| 列 | 内容 | 说明 |
|----|------|------|
| 用例名 | benchmark name | 行索引 |
| baseline (s) | 绝对时间均值 | 固定在最左列 |
| candidate-1 Speedup | 倍数 | 颜色编码 |
| candidate-2 Speedup | 倍数 | 颜色编码 |
| ... | ... | ... |
| 趋势 | 方向标记 | ↑ ↓ ↔ |

**趋势计算：** 对每个用例的 Speedup 序列，比较最后一个与第一个 candidate 的 Speedup 差值：
- 差值 > epsilon → ↑ 持续提升
- 差值 < -epsilon → ↓ 缓慢回归
- 其余 → ↔ 波动

**可选趋势图：** 用 `st.bar_chart` 或 Plotly 画 Speedup 柱状图，X 轴为 candidate 名称/日期，Y 轴为 Speedup，1.0 基准线标注。

## 5. 文件结构

```
performance-kanban/
├── perf_kanban.py          # 单文件应用
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-05-30-perf-kanban-design.md
└── tests/                  # 可选
    └── test_data_layer.py  # Data Layer 单元测试
```

## 6. 错误处理

- JSON 解析失败：显示文件名和错误信息，跳过该文件
- 缺失用例：表格中标记 N/A，Speedup 列显示 "—"
- 空目录：提示用户指定包含 JSON 文件的目录
- 阈值异常：校验提升阈值 > 回归阈值

## 7. 性能考虑

- JSON 加载使用 `@st.cache_data` 缓存，避免重复解析
- 大文件场景（>50 个 JSON）：考虑 lazy loading 或分页
- Streamlit rerun 开销：通过 `st.fragment` 或 `st.cache_data` 控制

## 8. 未来扩展（不在本次范围）

- 导出对比结果为 CSV/HTML
- 远程 JSON 加载（URL/API）
- 多 baseline 对比
- 自定义标签和分组

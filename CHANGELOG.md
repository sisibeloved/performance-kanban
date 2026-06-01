# 更新日志

本项目的所有重要更改均记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [0.1.0] - 2026-05-30

### 新增

- **Speedup 倍数对比表格**：以 `baseline / candidate` 作为核心指标，二级表头按 candidate 分组（值 / 单位 / Speedup）
- **多文件横向对比**：同时加载多个 pyperf JSON 文件，逐用例横向比较不同 Python 版本 / 实现的性能
- **颜色编码**：Speedup 列根据可配置的提升阈值 / 回归阈值自动着色（绿色 = 提升，红色 = 回归）
- **排序与筛选**：支持按 Speedup 升序 / 降序排序，支持按阈值范围筛选用例
- **自选用例柱状图**：底部柱状图支持用户手动选择用例，避免全量用例单位差异导致的图表失真
- **用例替换合成（Tab 2）**：从不同 JSON 中选取单个用例结果，合成为一组新数据，自动参与对比
- **固定 baseline 趋势观察（Tab 3）**：锁定 baseline，展示多个 candidate 的 Speedup 变化趋势，表尾标记方向箭头（↑ ↓ ↔）
- **趋势折线图**：支持选择单个用例绘制 Speedup 趋势折线
- **数据精度**：Speedup 保留 4 位小数，用例值保留 2 位小数，单位保持 pyperformance 原始输出（us / ms / s）
- **模拟数据生成器**：`generate_sample_data.py` 生成 4 个测试 JSON（CPython 3.12 / 3.13 / 3.14 + PyPy）
- **单元测试**：覆盖 JSON 解析、Speedup 计算、对比表构建、趋势表构建、用例替换合成、阈值筛选
- **WSL 启动脚本**：`start.sh` 一键启动（kill 旧进程 + 启动 Streamlit）

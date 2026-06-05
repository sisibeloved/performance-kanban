# AGENTS.md — 给 AI 协作者的工程约定与踩坑记录

本项目是单文件 Streamlit 工具(`perf_kanban.py`)+ 纯数据层测试(`test_data_layer.py`)。
下面是改动本项目时**必须先读**的约定,大多来自真实踩坑。

---

## 0. 依赖与运行

- 运行依赖:`streamlit`、`pandas`、`plotly`、`matplotlib`(`pip install streamlit pandas plotly matplotlib`)。
- 测试**无额外依赖、无 pytest**:`__main__` 里顺序调用,直接
  `python test_data_layer.py`(用装了上述依赖的解释器即可)。
- 生成模拟数据:`python generate_sample_data.py sample_data/`。
- 注意:若机器上有多个 Python,请确认用的是**装了上述依赖的那个解释器**
  跑测试/启动(否则 `ModuleNotFoundError`);具体是哪个解释器因机器而异,自行确认。

---

## 1. Streamlit 渲染层限制(最重要,反复踩)

`st.dataframe` 底层是 **glide-data-grid(canvas 画布)**,不是 HTML 表格。这带来一串硬限制:

- **表头 CSS 不生效**:`Styler.set_table_styles([... .col_heading.level0 ...])` 这类
  表头样式(居中、对齐)会被 Streamlit 转发,但 canvas 表头**根本不应用**,是死代码。
  → 验证过:`streamlit/elements/lib/pandas_styler_utils.py` 只把 cell 级样式
  (背景色/字色)落到网格;表头 `table_styles` 等于没写。
- **固定列会丢二级表头**:`column_config(pinned=...)` 把列冻结后,MultiIndex 的
  一级分组表头(`colspan`)无法跨越冻结边界,被固定的列只剩叶子层。
- **交互式排序是纯前端**:点表头 / 三点菜单排序只改浏览器显示,**不回传 Python**,
  因此**不影响导出、不影响任何服务端逻辑**。排序若要落到数据/导出,必须在 Python 侧做。
- `column_config` 支持整数位置 key(`column_config_utils.py` 有 `_NUMERICAL_POSITION_PREFIX`),
  但能改的也就是宽度/对齐/pin,改不了上面这些表头行为。

**结论 / 选型规则:**
- 需要**二级表头 + 固定列 + 表头居中 + 精确列宽**这类视觉控制 →
  **必须用 HTML 表格渲染**:`Styler.to_html()` + `st.html(...)`,固定列用 CSS
  `position: sticky` + `left` 偏移,一级表头用 `colspan` 天然渲染。
  参考实现:`perf_kanban.py` 的 `render_comparison_table_html()`。
- 只在"能接受 glide 限制"时才用 `st.dataframe`(如趋势表)。
- 不要试图用"两个 `st.dataframe` 拼像素对齐"——做不到,别走这条死路。

HTML 表格代价:失去 glide 的交互式排序(本项目已用"导出排序"替代,影响为零)、
虚拟滚动、全屏;且**不随 Streamlit 深色主题自适应**(目前固定浅色样式)。

---

## 2. 验证纪律:渲染出来看,别信"看起来合理"

- 改动**任何前端/渲染**后,要么把最终产物渲染出来看,要么断言**最终产物字符串**。
- 优先直接 `streamlit run perf_kanban.py sample_data/` 在浏览器里看。
- 如果环境跑不起来 streamlit 服务(沙箱限制、无显示等),退路是把
  `Styler.to_html()` 写成 HTML 文件,用任意浏览器或 headless 浏览器截图肉眼检查
  (任何 Chromium/Chrome 的 `--headless --screenshot=out.png file://...html` 都行,
  具体可执行文件路径自行确定,不要写死)。
- 验证 `position:sticky` 这类滚动行为时,在页面里用脚本设置容器 `scrollLeft` 后再截图。

---

## 3. 测试约定:测真实产物,不测"中间对象含有某段 CSS"

- 测试是**纯数据层**(`test_data_layer.py`),不经过 Streamlit 前端,所以
  天然测不到表头对齐/层级/列宽这类渲染问题——这些只能靠上面的截图验证。
- **反面教材(已删除)**:曾有测试断言"Styler 对象里**含有** `text-align:center`"。
  它永远绿,但 `st.dataframe` 忽略该 CSS,界面始终是错的 → **绿测试 + 坏界面**。
- 正确做法:既然改成了 HTML 渲染,就断言 `render_comparison_table_html()` 返回的
  **最终 HTML 字符串**里确实有 `colspan="2"`(二级表头)、`text-align: center`、
  `position: sticky`、几何平均行等。参考 `test_comparison_table_html_structure`。

---

## 4. 数据/渲染分层小约定

- `build_comparison_df` 产出 **MultiIndex 二级表头**;导出层 `_flatten_df_for_export`
  依赖这个结构,改列结构时注意同步导出与测试。
- baseline 也在对比表里显示(值/单位两列,无 Speedup),并**固定在左侧**;
  若 baseline 名与某 candidate 同名,会自动加 ` (baseline)` 后缀避免重复列。
- 几何平均行(`build_gmean_row`)**受筛选/选择影响,不受排序影响**,常驻表格最底部;
  只在 Speedup 列有值,其余留空。
- 导出(Markdown/JPG)用的是筛选后的 `df_display`,**只受筛选影响**;排序通过
  独立的"导出排序"参数(`_sort_df_for_export`)实现,不依赖界面排序。

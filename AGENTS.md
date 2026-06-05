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

HTML 表格代价:虚拟滚动、全屏丢失;**不随 Streamlit 深色主题自适应**(目前固定浅色样式)。
交互式排序已**重新实现**为"点击表头"(见第 5 节)。

---

## 2. 验证纪律:渲染出来看,别信"看起来合理"

- 改动**任何前端/渲染**后,要么把最终产物渲染出来看,要么断言**最终产物字符串**。
- **静态片段**(`Styler.to_html()`):写成 HTML 文件,用 headless 浏览器截图肉眼看
  (任意 Chromium/Chrome 的 `--headless --screenshot=out.png file://...html`;
  可执行路径自行确定,不要写死)。验证 `position:sticky` 时先用脚本设容器
  `scrollLeft`/`scrollTop` 再截图。
- **整个 Streamlit 应用 / 交互行为**:可以(也应该)真跑起来验证,环境是支持的。

### ⚠️ pkill 自杀陷阱(踩过)

`pkill -f streamlit` 会**把正在执行该命令的 shell 自己杀掉**——因为这个 shell 的命令行
里就含 "streamlit" 字样,自匹配 → SIGTERM → 整条命令 exit 144。曾被这个误导成
"沙箱不让跑 streamlit",其实环境一直能跑。**别用 `pkill -f <在你命令里出现的词>`**;
要停服务就**按端口杀**:
```bash
PID=$(ss -ltnp | grep ':8501' | grep -oP 'pid=\K[0-9]+' | head -1); kill "$PID"
```

### 跑起来验证的正确姿势

1. 首次需跳过 streamlit 的邮箱交互提示:`mkdir -p ~/.streamlit &&
   printf '[general]\nemail = ""\n' > ~/.streamlit/credentials.toml`。
2. 后台启动(用 harness 的 run_in_background;`fileWatcherType none` 改代码后需手动重启):
   `python -m streamlit run perf_kanban.py sample_data/ --server.headless true
   --server.port 8501 --server.address 127.0.0.1 --browser.gatherUsageStats false
   --server.fileWatcherType none`,然后 `curl -s -o /dev/null -w "%{http_code}" :8501`
   等 200。
3. 默认侧栏未选文件 → 表格不渲染。验证表格相关交互时,可临时给"选择文件"
   multiselect 加 `default=...`(验证后**务必还原**),或用 CDP 驱动 multiselect。
4. **交互验证(浏览器扩展未连时)**:用 headless Chrome + CDP,仅需 `requests`+`websockets`:
   `chrome --headless=new --remote-debugging-port=9222 ... "http://127.0.0.1:8501/"`,
   从 `http://127.0.0.1:9222/json` 取 page 的 `webSocketDebuggerUrl`,用 `Runtime.evaluate`
   读 DOM / `.click()` / 轮询变化。验"无整页刷新"用 sentinel:先 `window.__x='ALIVE'`,
   点击后再读,若仍在 → 是平滑重跑而非 reload。

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
- 排序在**服务端**做(`_sort_df_for_export`,稳定排序),表格/柱状图/导出共用同一
  `df_sorted`;几何平均在排序后才追加 → 永远置底。可排序列 = 用例名 + 各候选 Speedup
  (值/单位是带单位的格式化字符串、跨行单位不一,不参与排序)。
- **传给 `st.bar_chart` 的 DataFrame,索引名/列名必须是普通字符串**,绝不能把
  MultiIndex 元组(如 `('', '用例名')`)当索引名——Vega-Lite 会把它当字段访问路径解析、
  抛 `Access path missing closing bracket`,**该异常会中断整次重跑渲染**,表现成
  "其它交互(如点击表头排序)莫名失效"。构建图表数据用 `build_speedup_chart_df`。
  (issue #4 的真凶;排查时务必看浏览器 Console,别只盯着出问题的那个功能。)

---

## 5. 点击表头排序:HTML 表 + JS 桥(平滑,不整页刷新)

HTML 表是静态的,没有原生点击排序。实现方式:

- 表头是带 `data-sortidx` 的锚点(`render_comparison_table_html` 的 `header_links` 注入)。
- 渲染一批**离屏隐藏的 `st.button`**(key=`cmp_sortbtn_<k>`),点击它们走常规 websocket
  重跑(**平滑,不整页刷新**)。排序状态存 `st.session_state`。
- 一个 `height=0` 的 `components.html` iframe 内 JS,用 `window.parent.document` **事件委托**
  把表头点击转发到对应隐藏按钮。

**关键坑(踩过):** 必须用**事件委托**(在常驻的 `document` 上挂一个监听),不能逐个
锚点 `addEventListener`。因为每次重跑 `st.html` 会**替换表头 DOM 节点**,逐元素绑定只对
首批节点有效 → 表现为"**只有第一次点击生效,之后失灵**"。委托挂在不变的 `document` 上
(用 `document.__cmpSortDelegated` 防重复挂),对后续新节点同样有效。

> ❌ 别用 `<a href="?sort=...">` 查询参数导航来排序——那会**整页刷新**(锚点导航
> 触发整页重载),体验差。

已用 headless Chrome + CDP 实测:连点两次(含切换升/降序)都生效,`window` sentinel
跨点击存活 → 确认是平滑重跑而非整页刷新。

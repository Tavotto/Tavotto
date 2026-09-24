# 拖动性能探针（2026-09-23，ADR 0075）

> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 完整背景与裁决在 `docs/adr/0075-drag-performance-probe.md`；给测试者 / 开发者的用法在
> `docs/perf-probe-guide.md`。

- **热路径只 import `perf/core.ts`**（叶子，不 import 任何 store）。编排、store 订阅、
  DOM 统计、报告组装在 `perf/session.ts`，只被探针自己的界面（`perf/probeStore.ts`、
  `components/PerfProbeHud.tsx`）import——反过来就绕出 import 环。
- **没在录制时零成本**：`perfSpan` / `perfInput` / `perfCount` / `perfSegmentBegin`
  的第一句都是「没在录制就直通」，不挂 rAF、不下 mark、不分配。新增插桩点照这个
  形状写，`perf/core.test.ts`「没在录制时」两条是看护。
- **插桩点是闭集**（`PerfSpan`）：加一个要同步 `scripts/perf_report.py` 的 `CODE`
  （结论要指得出代码位置）；帧行的列顺序（`FrameRow`）是报告格式，改了要升
  `REPORT_SCHEMA` 并让分析器兼容旧版。
- **片段边界只在 `interactionStore.begin / end`**：不在各个 `startXDrag` 里各切一遍。
  新增一种拖动只要照常 `interaction().begin(kind)`，探针自动覆盖。
- **「停止」对整次标准测试生效**：停止旗子只在 `runStandardTest` 开头清一次、每轮开跑前
  先看一眼；轮与轮之间那 800ms 里按停止，第二轮不开跑。看护：`perf/synthetic.test.ts`。
- **自动测试绝不改用户文档**：收尾一律 `pointercancel`（取消语义见
  `fake-realtime-preview.md`）。`perf/synthetic.test.ts` 断言文档与撤销栈原样；
  把收尾换成 `pointerup` 它必须红。e2e 另断言零后端渲染、被拖元素 transform 复原。
- **报告只有数字**：没有面板 id、文件名、图内文字、路径。新加上下文字段只许是计数或
  闭集枚举；`/api/perf/system` 只回硬件与电源事实（`tests/test_perf_probe.py` 用真实
  主机名 / 用户名 / 家目录反查）。
- **报告经后端落盘，不走 `<a download>`**：桌面壳的 WKWebView 会取消浏览器式下载
  （壳没注册下载处理器）。`saveReport` 先 `POST /api/perf/report`（写进数据目录、名字由
  后端生成），桌面版再 `revealExportedFile` 在访达里显示、失败时把完整路径说出来；
  浏览器模式另给一份下载。**桌面版后端没接住时不退回下载**（那一下会被静默取消）：
  `saveReport` 回 `null`，面板说「没保存」、报告留在 `probeStore.unsaved` 给「重试保存」
  ——回一个文件名就是谎称已保存。保存结果按**请求代数**落地：关掉 / 放弃 / 重新开始 / 再点一次
  重试都换代，过时的结果既不许把关掉的面板重新打开，也不许盖掉后来的成功。失败提示的锚点是
  `data-perf-notice="save-failed"`。看护：`perf/probeStore.test.tsx`。
- **documentStore 的通知分三类记**（`store.document.doc` / `.save` / `.other`，片段上下文带
  `doc_split: true`）：同一个 store 里住着文档本体与保存状态，只数通知会把「上一次松手 1 秒后
  的自动保存改 saveState」读成「拖动途中写文档」。`flushAutosave` 外面的 `autosave.flush` span
  量它落在拖动途中时花了多久。看护：`perf/session.test.ts`。
- **松手链路**：`renderStore` 发请求前 `perfRenderBegin(key)`、拿到响应 `perfRenderResponse`（照抄
  后端 timings 里的数字，含 `/api/engine/render` 的 `server_ms`）、写进 store 后 `perfRenderApplied`；
  `PanelView` 把那一版 SVG 换进 DOM 后 `perfRenderPainted(key)`，随后两帧记进 `swap_frames`（不依赖
  片段——换图常在尾巴之后）。**位图这一格（raster / evicted / 非编辑态的引擎位图）没有 SVG**：
  落定是**这一版自己的**位图 `<img>` 的 onLoad（变体与 rev 都对得上；暂挂的上一张不算），
  否则分析器退回拿 `applied` 当落定，取图、解码、换图整段漏掉。记录上 `painted_via` 分
  `svg` / `png`，分析器把位图那段说成「取位图 + 解码」而不是 innerHTML，上屏后那两帧说成
  「绘制新位图」而不是「绘制新 SVG」。看护：
  `canvas/panelPreviewMode.test.tsx`「性能探针」一组、`tests/test_perf_probe.py` 的位图松手链路。**渲染键只活在内存里**（它含文件名），报告里只有数字。
- **判断不在产品里**：产品只给帧率与超时比例一句话；「卡在哪、怎么改」在
  `scripts/perf_report.py`，判据随代码改，不为改一条规则发版本。
- **探针面板拖动中不重渲染**：片段数只在片段结束时通知（`perfSegmentBegin` 不通知），
  否则量到的卡顿里有一份是探针自己的。
- 看护：`web/src/perf/core.test.ts`、`web/src/perf/synthetic.test.ts`、
  `web/e2e/perf-probe.spec.ts`（真浏览器：渲染列有值、两轮跑满、零后端、报告不含文件名）、
  `tests/test_perf_probe.py`（机器事实解析 + 身份反查 + 分析器排序）。

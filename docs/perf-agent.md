# Tavotto Performance Agent：无需人工点击的性能自动测试

2026-10-14。面向 Claude / Codex / 本地 CI / GitHub Actions。

## 使用
先构建 Tavotto、安装 Playwright browser，且确保后端 Python 有渲染依赖。
```bash
python scripts/build_frontend.py
cd web && pnpm install --frozen-lockfile && pnpm exec playwright install chromium && cd ..
python scripts/perf_agent.py run --browser chromium --repeat 16 --out web/test-results/perf-agent/run.json --summary web/test-results/perf-agent/summary.json
python scripts/perf_agent.py summarize web/test-results/perf-agent/run.json --json perf-summary.json
python scripts/perf_agent.py compare web/test-results/perf-agent/run.json baseline.json --json comparison.json
```

Agent 只要调用 CLI，然后读取 JSON 的 `status`、`scenarios`、`rows` 即可自动判断。标准返回码：
- 0：成功完成运行/摘要；compare 时表示环境可比且无回归
- 2：执行失败、输入无效、覆盖不完整；**绝不当性能通过**
- 3：相对基线回归或环境/样本不可比；见 JSON `status`

本地后端默认使用执行 CLI 的同一个 Python（包括 Windows 的 `Scripts/python.exe`、conda 或自选 venv）；显式 `TAVOTTO_PYTHON` 仍优先。pnpm 按 PATH 解析实际程序，支持安装路径含中文/空格及 Windows 的 `.CMD` shim。

GitHub Actions 独立的 `Performance Agent` workflow 支持手动触发和每天自动执行，自动保存 JSON、Playwright trace 和截图。默认报告模式，不自动修改主分支，不改变现有 merge queue Gate。

## 数据真相
测试是真实后端 + Playwright Chromium/WebKit：先预热，再使用 DOM 稳定锚点点击“科研预设与符号”菜单、逐个插入符号 α、²、μm 和尺寸线预设，验证对象数量确实增加。采样的 `two_raf_ms` 是从 click capture 到**第二个 rAF 回调**的耗时，不能冒充 GPU 实际 present 或完整端到端交互延迟。

- 样本少于 10、覆盖不完整、浏览器/平台/分辨率/DPR 等变化，一律 `incomparable`，不能通过门禁。
- 两次报告的 `iterations` 也必须一致：每轮插入会增加画布对象，10 轮和 60 轮代表不同负载，不能只把它们当作样本数量不同。
- 性能回归阈值默认 `P95 > 基线 25%` **且** 绝对增加至少 15ms。需要人工在相同固定 runner 和真实稳定数据上确认阈值。
- nightly 首次尚无同机基线，只记录并上传报告，不输出“性能没退化”的假结论。后续可从同规格历史报告作为 `--baseline` 交给 Agent 比较。
- 报告只有硬件分类、受控场景名与耗时数字。没有脚本路径、用户文档文本、对象 ID 或文件名。
- 有些现象无法在后台浏览器里模拟，特别是 macOS 拖动原生窗口、GPU 合成与显示器拖影。原生档必须运行真实签名 App + macOS runner，配合 `scripts/perf_macos_window.py` 与 Instruments。
- 只有与待验 SHA 匹配的 Actions 成功 run 才是有效浏览器自动化证据；本地 pytest 不等同于浏览器实测。

## 与现有性能探针组合
本 CLI 的黑盒 UI 计时与 ADR 0075 性能探针互补。待 #1000 的 `PerfAction` 诊断成熟后可增加录制/下载原始分段报告，并把渲染耗时拆成同步提交、React、字体/layout 三类。禁止直接暴露用户内容给 Agent。

## 给 Agent 的执行契约
1. 固定被测 SHA、同规格 runner、浏览器版本及测试数据；先检查覆盖/预热。
2. 执行 `run`，读取 `summary.json`；不要将测试运行成功误判为“优于基线”。
3. 若有同机有效 baseline，执行 `compare`；status 为 `incomparable` 时汇报环境不匹配，不自主提高阈值。
4. 真机窗口症状归类为 `needs_native_mac_capture`；不要伪造 FPS、截图、性能提升幅度。
5. 生成修复 PR 时附上基线、候选的原始 JSON、commit SHA、性能差异及视觉/编辑正确性验证。


## 历史基线自动发现与可信度

GitHub Actions `Performance Agent` 在成功跑完测试后，使用只读的 Actions API 从 **main 分支最近 8 个成功的同名工作流**寻找原始 artifact `perf-agent/run.json`，自动生成 `comparison.json`。首次合入后的第一次运行尚无历史 main artifact，会明确记录 `no baseline`，不会冒充 `PASS`。

与浏览器样本不同，回归对比需要完整环境元数据（OS release、CPU 型号、核数、内存、浏览器精确版本、窗口尺寸、DPR 等）。字段缺失、重复/未知场景、样本不足、不同环境均是 `incomparable`；不可通过调整阈值掩盖。GitHub 共享 Runner 上的差异只发**提示**不阻断 CI；需要在固定原生 runner 上重复验证后才可升格为强门禁。



## 复用 ADR 0075 的自动拖动分析

`run --include-drag` 会在科学符号自动测试完成后，额外复用 `web/e2e/perf-probe.spec.ts` 内**已有的真实浏览器测试**：选择图内元素、真实拖动、按 `pointercancel` 安全收尾的 m1/m3 输入频率对照，完成报告导出。

输出与符号指标分开，均存于同一目录：
- `run.json` / `summary.json`：符号和预设交互黑盒耗时；
- `drag.json`：原有 `tavotto-perf-probe/1` 逐帧报告；
- `drag-findings.json`：`scripts/perf_report.py` 的机器可读结论和阶段归因。

任何拖动 E2E 失败、漏报、分析器失败都返回非零，不把没有测试到的拖动场景冒充绿灯。不同进程 / 窗口 GPU 呈现问题仍需要真实 Mac 原生验证。历史基线的自动对比当前仅覆盖符号 UI 指标，**拖动报告是诊断证据，不应误称为已通过拖动性能回归门禁**。


## 自动触发的热点文件

`performance-agent.yml` 使用精确 PR 路径列表：包括预设面板/缩略图、`TextView`、`ObjectView`、`MeasureChip`、`PanelView`、画布拖动交互和相关 Zustand store。改这些代码的 PR 会自动触发 Agent 的符号与画布拖动 E2E；与性能无关的文档/依赖调整不会因本工作流承担额外的浏览器测试。此为独立的非必需工作流，保留 merge queue 的原有门禁结构。

## Agent 读取整个测试的结果

使用 `run --include-drag --summary summary.json` 时，`summary.json` 除了各场景 P95，还包含顶层 `status` 与 `suites.symbol_ui`、`suites.canvas_drag`。拖动开始前整体状态为 `pending`，拖动和分析器都成功才是 `ok`；任一失败后为 `failed` / `incomplete`。**Agent 不能仅因为 `run.json` 的符号测量存在或 `symbol_ui=ok` 就认为整条流水线已通过。**

每次 `run` 开始就用本次的 `pending` 替换旧摘要；启动失败、非零退出、缺产物、超时和参数范围无效都会留下本次 `failed` 结论。`summarize` / `compare` 在输入失败时也会把指定的 `--json` 写为 `failed`，避免 Agent 读到上一轮的成功。摘要位置不可写时以退出码 2 为准。

`run --baseline` 还会将 `regression_status` 更新为实际的 `pass` / `regression` / `incomparable`；比较完成前摘要保持 `pending`，比较输入失败则为 `failed`。性能回归或不可比时，顶层 `status` 也分别为 `regression` / `incomparable`，而 `suites` 保留套件的执行结果。

独立执行历史比较时，`compare run.json baseline.json --json comparison.json --summary summary.json` 会把裁决同步到已完成的运行摘要；输入失败也会使该摘要成为 `failed`。仍在执行或不完整的摘要不能被外部比较改写为完成。

如果没有 `--include-drag`，`canvas_drag=not_requested`，该状态只说明符号套件成功；不得据此宣称拖动已经测试。


## 未启动拖动的终态语义与本地确定性

`run --include-drag` 的 `canvas_drag` 在符号阶段是 `not_started`；
仅在符号采样完整且即将启动拖动子进程时进入 `pending`。
符号阶段发生非零退出、超时或样本不完整时不会把拖动伪称为正在运行；
一旦拖动启动，其失败仍单独归到 `canvas_drag=failed`。
本地 Agent CLI 与 Actions 一样，强制传入 `TAVOTTO_NO_UPDATE_CHECK=1`，
避免在首次运行时由自动更新请求污染性能计时。


## 自动测试进行中的摘要持久化

Playwright 开始一轮测试前清理的是配置的 `outputDir`；不能让这个目录和
Agent 原始报告 / `pending` 摘要同根，否则长时间运行时摘要会突然消失。
专用 `playwright.perf.config.ts` 使用
`web/test-results-perf-agent-playwright` 保存 Playwright 的 trace 与截图，
Agent 持续可读的数字 JSON 则保留在 `web/test-results/perf-agent`；
Actions 同时归档两者。拖动 E2E 的 `--output test-results-perf-drag`
也不覆盖这两个目录。

超范围但语法合法的选项（例如 `run --repeat 2`）先删除同一路径的旧
`run.json` 再返回失败摘要，避免 Agent 因看到上一轮成功数据而误判本轮。


## 历史性能基线永远只读

`run --baseline base.json --out run.json` 会在移除旧输出之前先拒绝任何
`--out` 与 `--baseline` 指向同一真实文件的调用，包括路径正规化和已存在
硬链接别名。同文件当作当前与历史基线会给出伪造的 `pass`，所以宁可返回
失败也不覆盖用户的基线。

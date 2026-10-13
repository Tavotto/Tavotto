# Tavotto Performance Agent：无需人工点击的性能自动测试

2026-10-14。面向 Claude / Codex / 本地 CI / GitHub Actions。

## 使用
先构建 Tavotto、安装 Playwright browser，且确保后端 Python 有渲染依赖。
```bash
python scripts/build_frontend.py
cd web && pnpm install --frozen-lockfile && pnpm exec playwright install chromium && cd ..
python scripts/perf_agent.py run --browser chromium --repeat 16 --out web/test-results/perf-agent/run.json
python scripts/perf_agent.py summarize web/test-results/perf-agent/run.json --json perf-summary.json
python scripts/perf_agent.py compare web/test-results/perf-agent/run.json baseline.json --json comparison.json
```

Agent 只要调用 CLI，然后读取 JSON 的 `status`、`scenarios`、`rows` 即可自动判断。标准返回码：
- 0：成功完成运行/摘要；compare 时表示环境可比且无回归
- 2：执行失败、输入无效、覆盖不完整；**绝不当性能通过**
- 3：相对基线回归或环境/样本不可比；见 JSON `status`

GitHub Actions 独立的 `Performance Agent` workflow 支持手动触发和每天自动执行，自动保存 JSON、Playwright trace 和截图。默认报告模式，不自动修改主分支，不改变现有 merge queue Gate。

## 数据真相
测试是真实后端 + Playwright Chromium/WebKit：先预热，再使用 DOM 稳定锚点点击“科研预设与符号”菜单、逐个插入符号 α、²、μm 和尺寸线预设，验证对象数量确实增加。采样的 `two_raf_ms` 是从 click capture 到**第二个 rAF 回调**的耗时，不能冒充 GPU 实际 present 或完整端到端交互延迟。

- 样本少于 10、覆盖不完整、浏览器/平台/分辨率/DPR 等变化，一律 `incomparable`，不能通过门禁。
- 性能回归阈值默认 `P95 > 基线 25%` **且** 绝对增加至少 15ms。需要人工在相同固定 runner 和真实稳定数据上确认阈值。
- nightly 首次尚无同机基线，只记录并上传报告，不输出“性能没退化”的假结论。后续可从同规格历史报告作为 `--baseline` 交给 Agent 比较。
- 报告只有硬件分类、受控场景名与耗时数字。没有脚本路径、用户文档文本、对象 ID 或文件名。
- 有些现象无法在后台浏览器里模拟，特别是 macOS 拖动原生窗口、GPU 合成与显示器拖影。原生档必须运行真实签名 App + macOS runner，配合 `scripts/perf_macos_window.py` 与 Instruments。
- 本 workflow 的真实环境目前未运行验证；只有 Actions 中的成功 run 才是有效自动化证据。

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

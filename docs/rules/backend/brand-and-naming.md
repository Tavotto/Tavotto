# 品牌与命名

> 原文出自 `src/tavotto/AGENTS.md`「品牌与命名」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

产品名 **Tavotto**（拼写大小写固定）。品牌与格式常量唯一出处：
`web/src/lib/brand.ts`、`engine/brand.py`——界面/导出格式不得手写产品名。
对象层级 Project / Canvas / Tab / Object 见
`docs/adr/0001-project-canvas-tab-object.md`。

**2026-08-20 从 Magplot 改名为 Tavotto，选的是干净断裂**：`magplot-package` /
`.magplot` / `magplot-proof` / `magplot/objects@1` / `magplot.*` 的 localStorage 键
一律不再认，Magic Matplot 时代那一档 `LEGACY_*` 也一并删了——只认两代前的名字、
却不认上一代的，比干净断裂更难解释。`brand.py` / `brand.ts` 因此**没有
LEGACY_ 常量**，别照着旧模式再加一档。两个例外都在 mm 前缀那一族，它们指的是
**用户自己磁盘上的东西、我们改不到**：图库里的 `mm_registry.json`（读取端回退，
唯一判据 `registry.existing_registry_path()`，写出永远新名）与用户 shell 里的
`MM_WORKER_PYTHON`（读取端回退，唯一判据 `pool.worker_python_env()`）。
文档 schema 的迁移（`migrateToProject`，接受 2/3）与品牌无关，照旧。
桌面标识符换成了 `com.tavotto.tavotto`：存量 0.7.0 桌面版**不会原地升级**，
发版说明里要写明先卸载旧版。

论文 Figure 排版 + 参数化图表编辑工具。Flask 后端（`src/tavotto/app.py`）+
RenderCore（pikepdf / HarfBuzz / PDFium，**只经 `src/tavotto/pdfbackend/` 契约层**），前端 `web/`
（Vite + React 19 + TS + Tailwind v4）；旧 v1 前端已于 2026-08-15 删除（git 可找回）。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 干净断裂：旧名一律不认、不加 LEGACY_
- 唯二例外 `mm_registry.json`（`registry.existing_registry_path()`）与 `MM_WORKER_PYTHON`（`pool.worker_python_env()`）只在读取端回退
- 桌面 id `com.tavotto.tavotto`

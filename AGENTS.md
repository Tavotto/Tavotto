# Tavotto — 仓库级规则

本文件是每次会话都要读的那一份，只放四样：任务路由、跨仓库不变量、验证入口、
索引。各层规则的**全文按主题**放在 `docs/rules/`（总览与体积预算见
`docs/rules/README.md`）：改哪一带，就按那一层速查表里的路径读那一份细则，
不要求每次全读。

## 任务路由

- 用户要求安装、运行或试用 Tavotto，而未要求修改源码：这是**用户安装任务**。
  只读 README 的「在 Codex 里用 Tavotto（插件）」章节；不得构建仓库、安装
  开发依赖或运行完整测试。普通用户安装绝不需要 clone 本仓库，也绝不需要
  `pnpm`、`npm`、`cargo`、Tauri、前端构建、`run.sh`、测试套件或源码
  editable install——源码开发只留给明确说「我要贡献/开发 Tavotto」的人。
- 用户明确要求修改 Tavotto：根据改动目录读取**最近的**子目录 `AGENTS.md`
  （索引见文末），它是那一层的速查表，每一行指向 `docs/rules/` 里的细则与
  `docs/adr/` 里的架构决策——动手前先读对应的那几份。

## 不可破坏的跨仓库不变量

- 产品名 **Tavotto**（拼写大小写固定）。品牌与格式常量唯一出处
  `web/src/lib/brand.ts` / `engine/brand.py`——界面、导出格式、仓库地址
  不得手写。Magplot/旧品牌是**干净断裂**，不加 LEGACY_ 常量
  （仅有的两个 mm 前缀例外见 `docs/rules/backend/brand-and-naming.md`）。
- **单一权威原则**：每条规则/判据只有一个出处，其余侧是它的镜像或消费者；
  改动一侧必须同步另一侧。二十来对**严格同源对**与各自的看护用例、以及
  出版规范 / 导出请求 / 用户样式 / 文字词汇四条「全产品只有一份」的结构，
  全文在 `docs/rules/repo/same-origin-pairs.md`——改到表里任一侧先开那张表。
- **安全边界**：会话认证（ADR 0008）不许被任何新端点绕过；worker 沙盒与 `Path.unlink` 守卫不放松
  （cwd 切到脚本目录的条件见 ADR 0047 / 0107）。
- **渲染闭包零 PyMuPDF（ADR 0072）**：应用 / 发行 / runtime 闭包里没有 pymupdf / fitz，PDF 全经
  `pdfbackend/` 契约层走 `rendercore/`；`scripts/ci/retirement_scan.py` 看护；
  `TAVOTTO_RENDER_BACKEND=pymupdf` 是 `backend_retired` 错误，不是回退。
- **隐私**：遥测三档同意（unset ≠ 同意）、白名单结构性防线、
  `TAVOTTO_NO_TELEMETRY=1` 硬开关；用户脚本/路径/图内文字在结构上就发不出去。
  诊断包先脱敏再交出。
- **写回事务不变式**：热态所见 == 写进文件的 == 重开后重放出来的；
  prepare → verify（全量重放 + 几何比对 + 像素门）→ commit，任一环不过
  一律 409 且原文件零改动。不许为省时间跳过 verify。
- **运行时可写数据一律走 `engine/config.data_dir()`**，不往包目录、安装目录
  或仓库根写任何东西（macOS 上写 .app 会当场破坏代码签名）。
- **1.0 收敛纪律**（`docs/1.0-release-readiness.md`）：除非 correctness / safety / compatibility /
  release blocker，不扩大产品能力、不趁机重写已稳定模块；新增核心不变式测试提交前手工反证一次。
  RenderCore / 首开两条主线是登记例外（ADR 0053 / 0072）。
- 上面三条的全文（主语、看护、例外的边界）：`docs/rules/repo/invariants-detail.md`。
- 许可证 AGPL-3.0-only；`docs/support-matrix.json` 是平台支持口径的唯一出处，
  README/网站/应用内文案必须与它一致。

## 判据的主语（写判据之前）

门禁 / 断言最常错在**判据问错了主语**：量的不是你以为的对象，恒真或恒假而测试一直绿。
**动键盘之前先说出主语：谁的、哪个进程、哪个时刻、哪个维度。** 判源码结构用 AST 不用子串、
写成正面形式；判不出就别判；反证先验落点、结论用退出码。全文在 `docs/rules/repo/predicate-subject.md`。

## 第三路径审查

方案要禁用 / 置灰能力、让用户在两个有缺陷的选项间选、降保真换性能、错误降成警告或只改文档、加白
名单 / 特例、复刻外部系统的规则，或同类问题修过两轮仍冒新边界时：先写出用户要保持的不变量、找冲突
根因，提出改结构的候选并实验反证，之后才把真取舍交维护者。不放松安全、验证、审查与 1.0 纪律；
临时缓解不关根 issue。全文：`docs/rules/repo/third-path-review.md`。

## 最常用验证

```sh
ruff check . --fix && ruff format .        # 开发时：修 + 排 import + 格式化（秒级）
ruff check . && ruff format --check .      # 提交前：只检查，与 CI 跑的完全一致
.venv/bin/python -m pytest                 # 后端（tests/ 跑在 .venv）
cd web && pnpm test && pnpm build          # 前端 + 类型检查（别用 tsc --noEmit：恒假绿）
cd workerd && cargo test && cargo clippy --all-targets -- -D warnings && cargo fmt --check
python scripts/smoke_app.py --python .venv/bin/python   # 端到端冒烟
```

- **改完 Python 先过 Ruff，再跑针对性 pytest，最后才是完整验证**；Ruff 不替代任何语义门禁。
- 字体不进 git：跑用例 / 打包前先 `python scripts/fetch_fonts.py`。引擎改动后重启服务：
  `lsof -ti:5089 -sTCP:LISTEN | xargs kill; ./run.sh --no-browser`。
- 改了字体 / 字形归属、`web/src` 或引擎四模块、新增 `sys.path` 源码根，以及完整验证链与指导文档
  门禁（`python scripts/dev/agents_budget.py --check`）：`docs/rules/repo/local-verification.md`。

## 会话卫生（多会话 / 子代理并行时）

- 主工作区常被并发会话切分支、HEAD ≠ origin/main：动手前 `git fetch && git status -sb`，分支工作
  一律进独立 worktree；推送写明 refspec（`git push origin HEAD:<分支>`）。
- 会话开始先核 `git config user.name` / `user.email`（身份错了全程零提示）；提交不加 Co-Authored-By 尾注。
- vitest 大片 `localStorage is undefined` 先怀疑 Node 26：`NODE_OPTIONS=--no-experimental-webstorage` 重跑。
- 结束进程只按自己启动的 PID 杀，禁用 `pkill -f` 这类按模式匹配的杀法。

## Review guidelines

给 Codex 代码评审。每个 PR 通常只有**一次**完整评审（`docs/ci/pr-review-tiers.md`），一次报全。

- 只报正确性、回归、用户数据、Windows / macOS 差异、用户既有项目兼容；格式 / lint / 类型 /
  命名不报（CI 管）；diff 没触及也没因它变可达的旧代码不报。每条写出触发场景（操作 → 错误
  结果），纯理论的不报。P1 = 丢编辑、热态 ≠ 文件 ≠ 重放、静默错图、受支持平台走不通、门禁空转。
- 重点：画布视觉位置与逻辑坐标分叉（单选 / 多选变换、平移后缩放、倍率换算、撤销重做、序列化
  往返）；用户 Python / venv 发现、内置 runtime 回退、缺依赖说清（用户依赖不在内置 runtime）；
  数据不一定在脚本旁（相对路径基准、改 cwd、别处打开项目、盘符 / 大小写 / 非 ASCII）；与用户原
  matplotlib 输出无理由的差异（元素位置、axes 几何、文字、字体、图例、刻度、图幅）；同源对只改一侧。
- 定向复核（评论点名了上次的 commit / 意见）：只核那几条修好没有、修复有没有引入回归。

## 子系统索引（改哪里，先读哪份）

| 目录 | 规则文件 | 覆盖 |
| --- | --- | --- |
| `src/tavotto/`（含 `engine/`） | `src/tavotto/AGENTS.md` → `docs/rules/backend/` | Flask、渲染引擎、worker 协议、PDF 后端、写回、编码 Agent 桥、遥测、预检、外部交接 |
| `web/` | `web/AGENTS.md` → `docs/rules/frontend/` | 前端、渲染态、预览平面、命中几何、i18n、playground、UI 视觉纪律 |
| `src-tauri/` | `src-tauri/AGENTS.md` → `docs/rules/desktop/` | 桌面壳、ACL、更新通道、安装界面、壳内 i18n |
| `workerd/` | `workerd/AGENTS.md` | Rust supervisor |
| `packaging/` | `packaging/AGENTS.md` → `docs/rules/packaging/` | wheel/sdist、内置渲染 runtime、PyInstaller、macOS 签名 |
| `codex-plugin/` | `codex-plugin/AGENTS.md` → `docs/rules/plugin/` | Codex 插件、技能、MCP server、内嵌画布、首次使用契约 |
| `.github/` | `.github/AGENTS.md`（速查表）→ `docs/rules/ci/` | CI 分层、门禁纪律、验证链、发布链 |
| 跨仓库 | `docs/rules/repo/` | 同源对总表、判据的主语、第三路径审查、不变量展开、本地验证 |
| 架构决策 | `docs/adr/` | 改动前先读对应 ADR |

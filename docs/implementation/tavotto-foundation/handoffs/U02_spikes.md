# U02 · 两个独立技术证明 — 交接（按 [`../07_HANDOFF.md`](../07_HANDOFF.md) 模板；两个 milestone **各自一段结论**）

**阶段 / 子切片**：U02.render_spike + U02.runtime_spike（两个 milestone，一个 PR；分别验收，一个受阻不锁死另一个）。
架构决策：[`docs/adr/0055-render-spike.md`](../../../adr/0055-render-spike.md)、
[`docs/adr/0056-runtime-spike.md`](../../../adr/0056-runtime-spike.md)。

**开始 HEAD / 结束 HEAD / 用户原有工作区改动**：开始 `85047b75`（`foundation/u01-contracts` 当时的 head，即 U01 的
PR #451；`origin/main` 当时 `89a83726`）；U01 评审后重写并 rebase 到新 U00（`98a55042`，含 main `8406b361`）之后本分支
`rebase --onto a23bfef6 85047b75`，无冲突；结束 = 本 PR 的 head（合并后以 `git log origin/main` 里 PR 号为准）。
全部在 worktree `tavotto-wt/foundation-u02` 里做，用户主工作区一个字节没碰；候选包全部装在 scratchpad 的独立
spike venv，主仓库 `.venv` 与 `pyproject.toml` 零改动。

**默认路径 / 候选路径 / 本阶段拟启用能力**：默认路径不变（PyMuPDF 后端、safe worker、旧依赖解析、解释器链、
内置 runtime 的锁与构建脚本）；候选路径 = 两个 spike 各自验证过的那一条（PDFium 栅格 + pikepdf/fontTools/HarfBuzz
受限 emitter；uv + pbs CPython + 离线 wheel）；拟启用能力**无**（`plan.json` `new_default_capabilities_enabled: []`）。

**已读规则与复用的权威模块**：根 `AGENTS.md`、`src/tavotto/AGENTS.md`、`packaging/AGENTS.md`、`.github/AGENTS.md`；
`docs/rules/backend/` 的 pdf-backend-boundary / export-pipeline / runtime-figure-assets / process-boundaries /
dependency-repair-and-packages / profiles-and-preflight / preparation-and-receipts；`docs/rules/repo/same-origin-pairs.md`、
`predicate-subject.md`；`docs/legal/LICENSING.md`、`COMMERCIALIZATION_DEPENDENCY_AUDIT.md`、issue #182；ADR 0019 / 0033 /
0045 / 0046 / 0053；`packaging/runtime-lock.json` + `scripts/build_worker_runtime.py`（`download()` 的「校验失败当场失败」
是 `hashcheck.download_verified` 的同一条纪律）。复用（权威不动）：`tavotto.richtext.parse_runs` 与三个上下标常量
（spike 的上下标语义与画布同源）、`engine.config.data_dir()`（runtime_spike 的落点）、`runtime-lock.json`（macOS
CPython 来源的单一出处）、U00 夹具 `pdf_png_assets/page.pdf` 与 `dependency_declarations` 的三个包。

**实际代码与 API / 数据结构变更**（产品代码零改动）：

| 层 | 变更 |
|---|---|
| `scripts/dev/u02_spikes/`（新，spike 代码，不进产品 import 图） | `hashcheck.py`（sha256 校验 / 校验后才改名的下载 / 私有目录判据，纯标准库）、`fonts.py`（批准字体清单：URL / 每文件 sha256 / 许可 / 义务）、`pdfwrite.py`（受限 emitter）、`render_spike.py`（写 + 三把独立读取器 + report）、`render_child.py`（串行 render child + 纯标准库客户端）、`freeze_spike.py`（PyInstaller 最小候选冻结）、`runtime_spike.py`（provisioner 路径，纯标准库）、`requirements.txt`（候选包版本钉死） |
| `tests/`（新，快、不依赖候选包） | `test_foundation_u02_render.py`（纯标准库读 evidence：结构 / ToUnicode 解码 / 嵌入子集 cmap 交叉核对 / 独立重算的导入矩阵 / PNG 像素）、`test_foundation_u02_render_child.py`（假 child 的控制流 + 真 child skip-with-reason）、`test_foundation_u02_runtime.py`（hashcheck 负例 / 钉法单一出处 / 假归档的 provisioning 控制流 / report 与锁一致） |
| `tests/test_source_hygiene.py` | `stdout=PIPE` 判据多一个文件的例外（`render_child.py`，行协议客户端，专门读线程），前提由 chatty 用例动态看住；搬进 `src/` 时删掉 |
| `.github/workflows/foundation-u02-spikes.yml`（新） | `workflow_dispatch` + **只在 spike 自己的文件变动时**的 `pull_request`（paths 过滤；`gh workflow run` 只认默认分支上已登记的 workflow，合入前派发不了）、托管 runner 三平台矩阵、非 required、不进任何 Gate 闭集；跑全套 spike 并上传 evidence 工件。**寿命**：U02 的证据工作流，不长期存在——render 那一半由 U06 / U07 在真实用例进 src/ + tests/ 后删除或收编，runtime 那一半由 U05 在 provisioner 进产品后同样处理（连同 `scripts/dev/u02_spikes/` 与 hygiene 的单文件例外）；evidence 与 ADR 留作记录。已被 `tests/test_merge_queue_workflows.py` 的 workflow 合同纳入：事件闭集（`workflow_dispatch` / `pull_request` 都在 `_KNOWN_EVENTS`）、信任区（监听 PR 事件的 workflow 全部 job 在托管 runner 枚举内）、每 job `timeout-minutes`（`test_source_hygiene`）；取消规则与 ci.yml 同一形状（`cancel-in-progress` 只对 `pull_request`，组名带 event_name + ref，与 ci.yml 不同名、互不挤占）——`TestConcurrency` 只枚举 ci.yml / codeql.yml 两个文件，本 workflow 不在它的枚举里（豁免的形状是「不在枚举内」，不是白名单），所以这一条靠人读、不靠用例 |
| `docs/adr/0055-render-spike.md`、`0056-runtime-spike.md` | 两段独立结论（版本 / 平台 / 字体来源与许可 / 实测 / 失败路线 / 选择原因 / 仍缺的目标） |
| `docs/implementation/tavotto-foundation/evidence/u02/` | `render/`（truth.json 手写规格、spike.pdf 18 KB、spike_pdfium.png 30 KB、report.json）、`freeze/`（report + 冻结 exe 渲染的 PNG + PyInstaller 日志 `pyinstaller-log.txt`）、`runtime/`（report-macos-arm64.json） |
| `plan.json` / `README.md` / `PACKAGE_CONTENTS.json` | U02 `implementation_status: done`、产品资格仍 `not_run`；README 加 U02 段；清单重算 |

**关联旧要求 ID / 场景 ID**：R01（RC-021 ~ RC-037 的技术前提：批准字体离线可用 / 身份 / fallback 分层 / HarfBuzz
cluster / 子集 GID / ToUnicode / 字体格式边界——本阶段只给**证据与边界**，实现归 U06）、R14（RC-095 写入 / 读取不同源、
RC-097 定点反证、RC-101 native 库过 PyInstaller、RC-108 许可与来源分别核验）、CP03（FO-022 ~ FO-028 的技术前提：
provisioner / 来源 hash / 不改 PATH / 离线 / 坏 hash 不发布——实现归 U05）。registry 220 条 `execution_status`
一条没动；enrollment 台账没有 U02 的 case（本阶段没有产品用例，见下）。

## render_spike 的结论（通过 → 可推进 U06）

| 命令 | 目标平台 / 环境 / 产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| `python -m dev.u02_spikes.fonts --dest <fonts>` | macOS arm64，spike venv；13 个字体文件 + 2 份 LICENSE | 0 | 每个文件 sha256 与清单一致（tarball 整体与逐文件各一次） |
| `python -m dev.u02_spikes.render_spike --fonts … --out evidence/u02/render` | 同上；`spike.pdf` / `spike_pdfium.png` / `report.json` | 0 | **52/52**：页面尺寸、4 处导入的 13 个像素采样、透明组 D vs 逐对象 E 像素不同、导入页 A/B 内部矩形包围盒（经 PDFium 的 form 矩阵回算）、写入矩阵 == 读取侧独立重算、17 个真文字对象、PDFium 与 pdfminer 两把文字尺子、pypdf 四个字体结构；两次运行产物**字节相同** |
| `pytest tests/test_foundation_u02_render.py`（主仓库 `.venv`，纯标准库） | 同上 | 0 | 20 条：hash 一致、页盒、4 处导入矩阵 + clip + 组包装、像素、透明组、字体结构两条路径、文字层 4 行解码回原文、组合序列一个字形回两个码位、嵌入子集 cmap 与 code 一致（RC-034）、墨、版本钉死 |
| `pytest tests/test_foundation_u02_render_child.py`（主仓库 `.venv`） | 同上 | 0 | 8 条假 child 用例过；真 child 1 条 **skip**（理由写明：没有 pypdfium2）——skip 是 not_run |
| 同上（spike venv） | 同上 | 0 | 9/9，真 child 用例真跑：渲染 / child 侧预算拒绝 / 坏 PDF 结构化错误 / 超时 kill + reap + 恢复 |
| `python -m dev.u02_spikes.freeze_spike …`（PyInstaller 6.19.0） | macOS arm64；冻结产物 120 文件 69 MB（不进 git） | 0 | 7/7：冻结 exe 在干净环境跑、`_MEIPASS` 下有 `libpdfium.dylib` + 13 字体 + 2 LICENSE、exe 以 `--render-child` 自起 child、渲染 400×320（3.6 ms） |
| 变异反证（写入侧 6 条 + 客户端 5 条） | 同上 | 每条非零 | 见 ADR 0055 §3；其中「旋转反向」第一版被 spike 自己的读取器漏掉（采样点经写入侧矩阵映射 = 自证），已改成读取侧独立重算 |
| Linux / Windows 腿 | `foundation-u02-spikes.yml` dispatch | 见「其它目标」 | — |

结论：**通过**。成熟写入器适配 + 受限自有 emitter 这条路能写出真实的、独立读取器认可的 PDF（非对称源页导入 / 变换 /
clip / 整体 opacity 的矢量透明组 / 中英 Greek 上下标可检索文字，两种字体程序路径），PDFium 在串行 child 里栅格、错误 /
超时 / 崩溃 / 预算路径各有闭环，最小 freeze 过。默认字体来源合法（OFL 1.1 ×2）且分发义务写清。会变的旧字体名 /
布局基线已列表（ADR 0055 §4）。

## runtime_spike 的结论（通过 → 可推进 U05；Windows / Linux 运行时证据待 dispatch）

| 命令 | 目标平台 / 环境 / 产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| `python -m dev.u02_spikes.runtime_spike --out evidence/u02/runtime --keep`（主仓库 `.venv` 解释器，纯标准库；首轮 `TAVOTTO_DATA_DIR` 指临时目录，进 git 的那份是评审处置后用 `--data-dir` 复用 hash 校验过的下载缓存重跑的） | macOS arm64；`report-macos-arm64.json` | 0 | **15/15**：uv 0.12.17 从钉死的 wheel 取出并 `--version`；pbs CPython 3.13.15（来源 = 锁文件）staging → 真起 → 原子改名到按内容命名的目录 `cpython-3.13.15-<sha12>` → 原子切 `active.json`；三个 wheel 按 hash 下到 wheelhouse；`uv venv --python <私有>`；死代理 + `--offline --no-index --require-hashes` 安装成功 0.09 s；空 wheelhouse 失败；不带 offline 的联网被代理拒（os error 61）；venv 里 `six/tabulate/sortedcontainers` 版本对、prefix / base_prefix / executable / 全部 sys.path 在 data_dir 下；篡改归档与错期望值各拒一次且执行计数 / 目录不变；embeddable 静态检查（有 `._pth`，无 venv / ensurepip / tkinter）；HOME 零新文件、PATH 不变；顶层目录全在 data_dir |
| `pytest tests/test_foundation_u02_runtime.py`（主仓库 `.venv`，不联网） | 同上 | 0 | 15 条：hashcheck 五条（含 LF 归一化内容 hash）、钉法单一出处（macOS 来源逐字等于锁；spike 表不覆盖锁里的目标；wheel 版本 == 夹具）、假归档 provisioning 控制流五条（staging → 原子改名 → 指针；换版本不删旧目录只切指针；指针原子；坏 hash 不执行；起不来不发布）、report 与锁一致 |
| 变异反证 | 同上 | 非零 | 去 `verify_sha256` → 两条坏 hash 用例红；去原子改名 → 目录存在性断言红；退回「同名 rmtree 再 replace」→ 换版本用例红；指针直接 write_text → 原子指针用例红 |
| Windows（embeddable 上 `-m venv` 真失败、pbs Windows 起 venv、注册表不动）/ Linux | `foundation-u02-spikes.yml` dispatch | 见「其它目标」 | 本机 **not_run** |

结论：**通过（macOS arm64）**。一条固定 provisioner + 私有完整 Python + 最小 venv + 离线 wheel 的路径成立，四条负例
（坏 hash ×2、空来源、死代理）都红在该红的地方。**Windows / Linux 没有本机证据**：在 dispatch 腿给出结论之前，
这两个平台上不允许默认启用私有 Python 准备（06 §2）；这不阻塞 U05 的纯模型开发。

**本切片的正例、负例、旧行为回归**：正例 = 上面两张表的 ALL OK；负例 = 写入侧 6 条 + 客户端 5 条 + runtime 4 条
（见两份 ADR §3）；旧行为回归 = 产品源码零改动，`pyproject.toml` / `runtime-lock.json` / 默认后端 / 解释器链 /
`aggregate_gate.py` / ruleset / LICENSE 一个都没动；`tests/test_source_hygiene.py` 只多了一个文件的例外。

**本次是否改变 case enrollment（planned / observing / enforced / later）及理由**：**没有**。U02 没有产品用例——两个
spike 都不经产品入口，evidence 是技术证明不是 FO / RC 场景的资格；台账 32 条 FO 原样（31 planned + FO14 later），
`U01-S1` 仍是唯一 enforced。

**未运行 / 基础设施问题 / 真正产品失败，分别说明**：

* 未运行（not_run）：Linux / Windows 上的 render_spike、freeze、runtime_spike（dispatch 腿的结论写在下面「其它目标」，
  没有的就是没有）；macOS x86_64 一切；`RLIMIT_AS` 在 macOS 上不生效（不是没测，是内核不强制——像素预算是唯一护栏）；
  科学栈大 wheel 的离线安装体积 / 时间；签名 / 公证；registry 220 条产品实例。
* 基础设施问题：files.pythonhosted.org 一次 TLS EOF（`download_verified` 传输层有界重试 3 次；hash 不符不重试）。
* 真正产品失败：无（本阶段不碰产品）。顺带发现的**事实**：① Liberation 2.1.5 没有 `liga`（fi/fl 不连字）；
  ② 外来页作为 Form XObject 导入时它的文字层随之保留（PDFium / pdfminer 都抽得到 `U00 fixture: y = 3x + 1` ×4）；
  ③ fontTools 子集 `save()` 默认写当前时间进 `head.modified`，不钉 `recalcTimestamp=False` 产物不可复现；
  ④ pypdfium2 嵌套对象的 `get_bounds()` 是 form 局部坐标，要用 form 的矩阵逐层乘回页面空间。

**批准的字体 / 视觉差异，及未授权变更检查**：本阶段没有改任何产品输出；ADR 0055 §4 列出换脸后**将**改变的旧字体名 /
asc-desc / 覆盖表 / provenance 判据，等 U06 按 D07 一次批准迁移。`git diff --stat` 只含上表的文件。

**当前可合并依据（不等于可以默认启用 / 发行）**：中高风险档（`scripts/`、`tests/`、`.github/` 新文件 + hygiene 例外）
→ `full-ci` + `@codex review`；ruff 两条 0；针对性 pytest 0；变异反证逐条红；全量 pytest 见 PR 正文；新 workflow 只有
`workflow_dispatch` 与 paths 过滤的 `pull_request`、托管 runner、每 job 有 `timeout-minutes`、顶层 `TAVOTTO_NO_TELEMETRY=1`，
`test_merge_queue_workflows` 的事件闭集 / 信任区 / 超时判据全过。

**仍缺哪些默认启用 / 精确安装物资格**：全部。本阶段不产生任何产品资格；render_spike 的 pass 是「候选栈 + 一个平台
（+ dispatch 腿）」的技术证明，runtime_spike 同理。

**下一个无阻塞阶段 / 子切片**：U06（依赖 U01.contracts + U02.render_spike）与 U05（依赖 U04 + U02.runtime_spike）。给它们的输入：

* U06：`fonts.py` 的清单与 hash（进包方式待定：PyInstaller datas 已验证可行）；`pdfwrite.FontFace` 的两条字体程序路径与
  ToUnicode 的 cluster 写法可以搬进 `src/`（那时 `test_source_hygiene` 的例外要删，`importgraph` 基线要登记新边）；
  D07 迁移表（ADR 0055 §4）；ActualText / y_offset / 多 glyph cluster 三条边界；pikepdf 的 Pillow + lxml 传递依赖要么接受
  要么换 pypdf 做对象模型（ADR 0055 §1）。
* U07：透明组 / 导入页 / clip 的 emitter 形状；render child 的四条路径与 `last_exit` / `restarts` 记账；freeze 结论。
* U05：uv 的钉法与三条子命令、staging / 原子发布 / `active.json`、四条负例、embeddable 静态事实 + 「Windows base 用 pbs」
  的建议、Linux / Windows-pbs 的 sha256（进锁文件前重新从 SHA256SUMS 核）。
* 三条腿：PR 上随 spike 文件变动自动跑；合入 main 后也可 `gh workflow run foundation-u02-spikes.yml --ref <分支>`（合入前 gh 找不到它：404 workflow not found on the default branch，实测）；工件 `u02-evidence-<os>`。

**回退方式、不能假装可回滚的外部副作用**：revert 本 PR 即回退（全是新文件 + 一条 hygiene 例外 + 台账状态）；没有设置写入、
没有发布。外部副作用只有本机 scratchpad 里的 spike venv / 字体 / 下载缓存与 `--keep` 留下的临时目录，删掉即可。

## 其它目标（三条腿；`foundation-u02-spikes.yml` run [35507598899](https://github.com/Tavotto/Tavotto/actions/runs/35507598899)，head c5f13db4——之后的 rebase 没动 spike 文件与用例）

首轮 run 35507170229（head e04b9747）ubuntu / macos 全过、windows 在 `test_foundation_u02_runtime.py` 的锁文件 hash 用例红：
`packaging/runtime-lock.json` 没钉 `eol=lf`，Windows 检出成 CRLF，字节 hash ≠ 报告值；改成 LF 归一化的内容 hash 后重跑（c5f13db4）三腿全过。

| 腿 | fonts | render_spike | U02 用例（spike venv） | freeze | runtime_spike | 备注 |
|---|---|---|---|---|---|---|
| ubuntu-latest（x86_64，Python 3.13.15） | 13 文件 hash 全对 | **52/52**；`spike.pdf` 与 git **逐字节相同**（12966287d846），PNG 与 git 不同（PDFium 栅格按平台不同，属 03 §6「需要校准」，spike 自己的 13 个采样点仍全部命中） | 43 passed / 0 skipped（真 child 用例真跑） | 7/7；`libpdfium.so` 在 `_internal/`；`RLIMIT_AS` = **set**（2 GiB 下 PDFium 渲染正常；没做超限触发试验）；产物 112 MB、14.9 s | **15/15**；pbs `x86_64-unknown-linux-gnu`；uv Linux wheel；死代理 4.8 s 拒 | Linux 的私有 Python 来源在 spike 表里，不在锁文件（U05 决定要不要抬进锁） |
| windows-latest（AMD64，Python 3.13.15） | 同上 | **52/52**；`spike.pdf` 与 git **逐字节相同**；PNG 按平台不同 | 43 passed / 3 skipped（三条假解释器是 sh 脚本的用例，理由写明；真 child 用例在 Windows 上真跑并过） | 7/7；`pdfium.dll` 在 `_internal/`；`RLIMIT_AS` = unsupported（Windows 没有 resource 模块，像素预算是唯一护栏）；产物 57 MB、14.9 s | **16/16**：pbs `x86_64-pc-windows-msvc` install_only 作私有 Python 起得来；uv Windows wheel 建 venv + 离线装 + 死代理 20.5 s 拒；篡改 / 错 hash 拒；**embeddable 真跑 `python.exe -m venv` 退出 1：`No module named venv`**（静态检查的结论在运行时成立） | `%USERPROFILE%` 指空目录跑完仍空、PATH 不变；**注册表没量**（spike 不读不写注册表，但没有「注册表前后快照」这一步——U05 若要这条证据得加） |
| macos-latest（arm64，Python 3.13.15） | 同上 | **52/52**；`spike.pdf` 与 git 逐字节相同；**PNG 也与 git 相同**（422b77b9408e——同平台同 PDFium 版本栅格可复现） | 43 passed / 0 skipped | 7/7；`libpdfium.dylib`；`RLIMIT_AS` = failed（内核不强制，与本机一致）；75 MB、13.1 s | **15/15**；与本机同一份钉法 | 与本机 macOS arm64 结论一致 |

结论修订：runtime_spike 的 Windows / Linux **运行时证据已取得**（不再是 not_run）；仍缺的是注册表快照、大 wheel 体积 / 时间、去重 / 租约 / GC / 取消 / 配额（U05）与签名（不在此 gate）。render_spike 三平台都过；PDFium 栅格跨平台像素不同但同平台可复现——U07 的像素门要按平台分基线或按「需要校准」档记阈值，不能拿 macOS 的 PNG 当 Linux / Windows 的真值。

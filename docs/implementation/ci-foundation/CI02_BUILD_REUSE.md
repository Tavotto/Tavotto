# CI02 · 精确产物复用和工具准备去重——用数字写成的决定（2026-09-16）

- 改动对象：`tests/test_merge_queue_workflows.py`（新增 `TestBuildReuseAndCaches` 八条 + 三个 helper）、本文档、
  `evidence/ci02/`、`acceptance.json`（CIP-011…015）、`.github/AGENTS.md`（门禁纪律加一段）、`evidence/README.md`（登记 ci02/）。
  `coverage_ledger.json` 没动：没有任何 job 换执行位置。叠在 CI03b `1f7f13e8`（PR #376）之上；栈：#372 CI00 → #373 CI01 →
  #374 CI03a → #375 CI03c → #376 CI03b → 本 PR。
- **改了什么（可执行的部分）**：合同测试；**ci.yml 只改一行**——`windows-exe-smoke` 两片的
  `pnpm exec playwright install --with-deps ${{ matrix.browsers }}` 去掉 `--with-deps`（§2 的实验，由本 PR 的 full-ci run 判；`posix-e2e` 的保留）。
  `web/tsconfig*.json` / `web/package.json` / `scripts/plugin_stage.py` / 任何构建脚本、任何 needs / if / timeout / Gate 闭集都没动。
- **没做什么，以及为什么**（每一条都在下面用 CI 日志里的秒数说理由）：
  1. **不跨 job 抽取任何构建产物**（§1）：9 种 recipe 里 8 种「同 job 重建保留」，1 种（插件候选）本来就是唯一的数据边。
  2. **不加 Playwright 浏览器缓存**（§2）：派工时的前提是「Windows 装依赖 + 浏览器 245s」，日志拆开后浏览器下载只占 17–27s，
     **203–226s 是 `--with-deps` 在装 Windows Server 的 Media Foundation**；再叠上 §4 的发现——本仓库的缓存在合并组上 0% 命中——
     这条缓存是净负收益。
  3. **不改缓存作用域、不给 `package` 加 pnpm 缓存**：前者写成可执行的设计（§4.1）归 CI05 拍板，后者在前者修好之前是净负。
- **本轮没有任何真实 run**（不能 push）。CI 侧全部数字取自**已有** run 的 jobs API 与日志：PR #374 `35007730894`、PR #375 `35011613925`
  （两个都是 `full-ci` PR，attempt 1，全绿）、合并组 `35015416419`。本机数字只作数量级。
- 回退：ci.yml 那一行加回 `--with-deps`（并把 `TestBuildReuseAndCaches.PLAYWRIGHT_INSTALL` 的 Windows 那条改回）；删掉 `TestBuildReuseAndCaches`
  与三个 helper（`_jsonc` / `_with_block` / `_uses_steps`）。

## 1. Recipe 表与逐行决定（A + E「不混同目标」）

机器可读版 [`evidence/ci02/recipe_table.json`](evidence/ci02/recipe_table.json)；秒数来自 run `35011613925`（CI03c 之后的 DAG，
[`evidence/ci02/ci_step_seconds.json`](evidence/ci02/ci_step_seconds.json)）；尺寸来自本机（[`local_builds.json`](evidence/ci02/local_builds.json)）
或 CI 日志打印的数字。「关键路径」按该 run 实测：**frontend → windows-exe-smoke → CI integration gate**（两个 run 都是；CI00 的
29 个合并组里 28 个经 backend-fast → windows-exe-smoke，CI01 删边后换成这条）。

| # | 目标 | 构建命令 | 输入（锁 / 源码 / 平台） | 输出 | 每个合并组建几次（在哪、各几秒） | 关键路径上？ | 决定 |
|---|---|---|---|---|---|---|---|
| 1 | **web 应用** `web/dist → src/tavotto/web` | frontend：`pnpm build`（= `tsc -b && vite build && node scripts/tailwind-scan-check.mjs`）；其余 8 处：`scripts/build_frontend.py`（= `pnpm install --frozen-lockfile` + `i18n:check` + `pnpm build` + 原子拷进包） | `web/src`、`pnpm-lock.yaml`、四份 tsconfig、`vite.config.ts`、`publication.json` / `canvas_coverage.json` / `playground-runtime.json`（路径别名）；Node 22 / pnpm 11；**平台中性** | 8 个文件 **1.9 MiB**（`index-*.js` 1.91 MB / gzip 596 kB）；本机冷 7.0s（vite 本体 321 ms，其余是 `tsc -b`） | **9 次**：frontend 15；package ×4 32/31/36/40；windows ×2 42/45；macos 35；posix 26 → **302 runner 秒 ≈ 5 分钟** | 是（windows-exe-smoke (1) 的 42s） | **同 job 重建保留**。抽取只省 `i18n:check` + `pnpm build`（≈ 20–25s/腿；`pnpm install` 省不掉——widget 构建与 Playwright 都要 `node_modules`），关键路径净省 ≈ 25s / 1485s；代价是一套新的产物身份 + 丢掉「wheel 里的前端由**本平台**的 `build_frontend.py` 造出」这条覆盖（#322 的 `.cmd` shim 缺陷正是 Windows 腿这一步抓到的）。与 CI00 §14 第 3 条一致。 |
| 2 | **MCP 画布 widget** `canvas.html` | `scripts/build_mcp_widget.py [--out …]`（= `pnpm exec vite build --config vite.mcp.config.ts` → 单文件内联 + 指纹戳） | `web/src`（不含测试）、`mcp.html`、`vite.mcp.config.ts`、锁、三份 tsconfig、脚本自身、两份 JSON；**平台中性** | **1273 KiB**，指纹 `24f78154bb52d971`（本机与 CI Windows 日志同尺寸同指纹）；本机 0.6s | **4 次**：frontend 3（写到 `RUNNER_TEMP/plugin`，进候选）；windows ×2 各 3；posix <3 | 是（3s） | **同 job 重建保留**。3s；e2e 要的是默认位置 `codex-plugin/mcp/widget/canvas.html`，下载 + 核验不比 3s 快。frontend → plugin-candidate 那份**已经**是抽取（下一行）。**与 #1 不同 recipe**：不同 vite config、不同入口、单文件内联 vs 多文件 dist。 |
| 3 | **完整插件候选** zip + `plugin-build.json` | frontend：`plugin_stage.py stage --widget … --source-sha $(git rev-parse HEAD)` → `verify` → `archive` → `verify --content-digest`；plugin-candidate：download → `unpack` → `verify --source-sha HEAD --content-digest <清单里的> --serve` | `git ls-files -s -- codex-plugin`（索引里的模式）、#2 的产物、LICENSE、`pnpm-lock.yaml` 的 sha256、toolchain 版本；**平台中性** | 确定性 zip（含 dotfiles）+ 清单 | 1 次；消费 1 次（download 2s / unpack + verify + serve ≈ 5s） | 否（fast gate 闭集，不在关键路径） | **保持——全图唯一的数据边**，形状正是 04 §1 要的（§5）。 |
| 4 | **浏览器 playground** `web/dist-playground` | `scripts/build_browser_playground.py`（= `vite build --config vite.playground.config.ts` + 确定性 `engine.zip` + 指纹 manifest） | `web/src`、`playground.html`、`vite.playground.config.ts`、engine 四模块、`playground-runtime.json`；**平台中性** | 8 个文件 **1.6 MiB**（`engine.zip` 227 KB），指纹 `d1b9f461b86f28b0`；本机 0.7s | 1 次（frontend，<3s，未单列） | 否 | **同 job 重建保留**（没有 CI 消费者——产物由网站仓库提交）。**第三个 recipe**：三者互不能替代（CIP-013）。 |
| 5 | **wheel** | package ×4：`python -m build`（先 #1） | `src/tavotto/**`（含刚拷进去的 `web/`）、`pyproject.toml`；**平台中性**（`py3-none-any`） | whl **1.6 MiB** + sdist 5.4 MiB；本机 3.9s | **4 次**：7 / 9 / 7 / 26（Windows 里一半是 pip） → 49 秒 | 否（package 腿 66–128s） | **同 job 重建保留**。可以只建一次，但 49 runner 秒不值一条新数据边；「在本平台造出来再装进干净 venv」本身是被验的东西。 |
| 6 | **workerd** 可执行 | `cargo build --release --manifest-path workerd/Cargo.toml`（`workerd` job 只 fmt / clippy / test，不建 release） | `workerd/src`、`Cargo.lock`、rustc stable、目标三元组；**平台绑定** | **779 KiB**（Windows .exe 780 KiB）；本机冷 12.1s | **3 次**：windows ×2 26/35；macos 12 → 73 秒 | 是（26–35s） | **同 job 重建保留（平台绑定）**。两片 Windows 之间共享一份 .exe 要再串一个 Windows producer job——本 run 的 Windows runner 领取等待是 **205s / 321s**（`created → started`），比 35s 的编译贵得多。 |
| 7 | **内置渲染 runtime** `runtime/` | `scripts/build_worker_runtime.py --clean`（下 CPython 归档 → 按锁装 wheel → 逐个 import + 画一张图） | `packaging/runtime-lock.json`（sha256 钉 CPython + wheel + `pip.platforms`）、runner 平台 / 架构；**平台绑定** | Windows **249 MiB** / macOS **288 MiB**（`du` 304M） | **3 次**：windows ×2 64/76；macos 47 → 187 秒 | 是（64–76s） | **同 job 重建保留（平台绑定）**。249 MiB 经 artifact 上传 + 下载 + 解包估 1–2 分钟 ≥ 64–76s；macOS 那份含符号链接与可执行位，upload-artifact 不保留（.app 会坏）；CPython 归档的**下载**已由 `actions/cache` 缓存（第一类），wheel 刻意每次真从 PyPI 装（发现 yank）。 |
| 8 | **PyInstaller 桌面产物** `dist/Tavotto` | `pyinstaller packaging/tavotto.spec --noconfirm`（`TAVOTTO_REQUIRE_RUNTIME=1`；datas = #1 + #7，binaries = #6） | 上面三行 + flask / pymupdf + PyInstaller；**平台绑定** | ≥ #7 的大小（日志没打印总量；macOS 318 个 Mach-O） | **3 次**：windows ×2 28/29；macos 58 → 115 秒 | 是（28–29s） | **同 job 重建保留（平台绑定）**。两片 Windows 共享 dist/Tavotto（≥ 249 MiB）：producer 串在前 + 上传 1–2 分钟 + 各自下载 + **再付一次 Windows 领取等待**，链路只会变长；省下的只是片 2 的 ~205s 构建段（runner 分钟），CI03c §9 已把它记作已知代价。 |
| 9 | **macOS .app**（Tauri 壳） | `desktop-tauri.yml`（tag / dispatch）；ci.yml 不建 | — | — | 0 次 | 否 | 不在本轮范围（`desktop-shell` 只 fmt / clippy / test，`bundle.resources` 指向空目录）。 |

**结论：0 行值得新抽取。** 8 行保留（4 行平台绑定；4 行平台中性但收益 < 30s 或传输 + 核验 ≥ 构建），1 行本来就是数据边。
lead 的两条预判（web build 保留、Windows .exe 保留）都成立，理由里多了一条派工时没有的数字：**Windows runner 的领取等待
（本 run 205s / 321s）让任何「多串一个 Windows job」的方案在关键路径上必然为负**，不管传输多快。

## 2. Playwright 浏览器缓存：决定「不做」，与它的数字（B）

派工时 lead 的预判是「`windows-exe-smoke (1)` 的『装 web 依赖与本片的浏览器』245s、`(2)` 231s ≈ 浏览器下载，缓存它是一处真收益」。
**这个预判被日志推翻**：按日志行首时间戳拆开
（[`evidence/ci02/playwright_install_split.json`](evidence/ci02/playwright_install_split.json)，run `35011613925`）：

| 腿 | 步总时长 | `pnpm install` | `--with-deps` 的系统依赖 | 浏览器下载（含解包） |
|---|---:|---:|---:|---:|
| windows-exe-smoke (1)（chromium） | 244.8s | 1.0s（`Already up to date`——`build_frontend.py` 早装过） | **226.4s** | 17.4s（chromium 191.8 MiB + headless-shell 114.5 MiB + ffmpeg + winldd） |
| windows-exe-smoke (2)（chromium + webkit） | 231.6s | 1.2s | **203.8s** | 26.7s（多一个 webkit） |
| posix-e2e（chromium） | 35.9s | 0.4s | 23.8s（apt：字体与 chromium 依赖） | 11.7s |

Windows 那 200 多秒是什么：已装的 `playwright-core@1.62.1` 的 `installDependenciesWindows` 在 targets 含 chromium 时跑
`bin/install_media_pack.ps1`，脚本只有一件事——Windows Server（`ProductType -eq 3`）上 `Install-WindowsFeature Server-Media-Foundation`。
日志里 19:17:28 → 19:21:14 之间只有那张 `Success / No / Success / {Media Foundation}` 表。webkit **不触发**任何 Windows 依赖安装。

于是「缓存浏览器目录」能省的上限是 17 / 27 / 12 秒，而它的代价：

- **合并组上必冷**（§4：候选 ref 每个都是新的，main 上没有 ci.yml 的任何缓存）→ 每次合并资格都是 miss + save：两片各在 job 尾部
  多传一份浏览器目录（压缩包就有 306 MiB：chromium 191.8 + headless-shell 114.5，片 2 再加 webkit；解包目录更大，本轮没量）——Post 步骤在**关键路径 job 的末尾**。
- 仓库缓存已 **10.3–10.6 GB / 60–62 条**，超过 10 GB 上限，正在按最近访问淘汰；再放三条几百 MiB 的键（每个 PR / 候选 ref 各一份）只会把 cpython / pnpm 的小条目挤出去。
- Playwright 官方 CI 文档原话：“Caching browser binaries is not recommended, since the amount of time it takes to restore the cache is comparable
  to the time it takes to download the binaries. Especially under Linux, operating system dependencies need to be installed, which are not cacheable.”
  （playwright.dev/docs/ci「Caching browsers」）。浏览器目录按官方（playwright.dev/docs/browsers）是 `%USERPROFILE%\AppData\Local\ms-playwright` /
  `~/Library/Caches/ms-playwright` / `~/.cache/ms-playwright`，日志里的落点与此一致——路径是核实过的，只是没有用它的理由。

**决定：不加**（lead 已拍板同意）。合同测试把它做成结构：`actions/cache` 的清单是枚举（只有两处 CPython 归档），谁加第三条就红，得先回到这里改数字
（`test_actions_cache_steps_are_exactly_the_cpython_archive_downloads`，变异 M11）。

### 2.1 真正的 200 秒：Windows 两片去掉 `--with-deps`（实验，本轮已改）

- **改动**：ci.yml `windows-exe-smoke` 的安装步 `pnpm exec playwright install ${{ matrix.browsers }}`（原来带 `--with-deps`）；`posix-e2e` 的
  `pnpm exec playwright install --with-deps chromium` **保留**（那边的 `--with-deps` 是 apt 装 chromium 的真依赖与字体，23.8s）。步骤名、matrix、
  `pnpm install --frozen-lockfile`、分片自验、e2e 命令都没动。
- **理由**：每片 −204…226s，而这条腿正是关键路径（run `35011613925`：frontend 175 + Windows 领取 205 + 片 1 1076 → gate 1485s）；模型上
  资格时长 1485 → ≈ 1280s，之后关键路径回到 `backend-platforms (windows)`（1187s）。信号可靠：两片 128 条 e2e 会当场说明 Chromium / WebKit
  起不起得来。回退一行。
- **由本 PR 的 full-ci run 判**（本机 macOS 证不了 windows-latest 镜像有没有 Media Foundation、Chromium 要不要它；Playwright 把它放进 `--with-deps`
  的理由正是「Chromium 在 Windows Server 上需要」）。**红了怎么判**：看 `windows-smoke-logs-shard<K>` 里的 `playwright-report` / `test-results`——
  `browserType.launch` 失败（`Failed to launch chromium/webkit`、`STATUS_DLL_NOT_FOUND` / `0xc0000135`、`mfplat.dll`）= 需要 Media Foundation，
  加回 `--with-deps` 并把这一节改成「实验失败，数字如下」；若红在具体用例而两片的浏览器都启动了、且同一用例在 attempt 2 或 CI00 样本里也红过，
  那是 flaky / 别的缺陷，与本实验无关（CI03c §9 的 `retries: 1` 仍在）。绿了：**片 1 / 片 2 的这一步时长**（预期 ≈ 20s / 30s）与资格总时长写进
  PR，作为 CI05 的对照样本。
- **合同**：`test_windows_installs_browsers_without_with_deps_and_posix_keeps_it`——主语是**整条命令**（Windows == `pnpm exec playwright install
  ${{ matrix.browsers }}`、posix == `pnpm exec playwright install --with-deps chromium`），不是「含不含某个 flag」；变异 M22（Windows 加回）/
  M23（posix 去掉）/ M18（posix 不装）打红。CI03c 的 `test_the_e2e_command_and_the_install_take_their_arguments_from_the_matrix` 原来钉着
  `--with-deps`，改成 `(?:--with-deps )?` 只管「从 matrix 取」。

## 3. TypeScript 真检查引用（C）

- `web/package.json` 的 `build` = `tsc -b && vite build && node scripts/tailwind-scan-check.mjs`：**第一条**命令是 `tsc -b`；vite 8（rolldown）
  本身不做类型检查（本机 vite 本体 321 ms，整条 build 7s 几乎全是 tsc）。ci.yml 的 `frontend` 只有 `- run: pnpm build` 这一个类型检查位置，
  没有 if / continue-on-error，也没有第二条 tsc 步骤（曾经的 `pnpm tsc --noEmit` 在 `files: []` 的方案文件下恒绿，注释与
  `.github/AGENTS.md` 都记着）。
- `web/tsconfig.json` 是 `files: []` + references 三份：`tsconfig.app.json`（include `src`）、`tsconfig.node.json`（`vite.config.ts`）、
  **`tsconfig.e2e.json`（`e2e` + `playwright.config.ts`）**——派工时问的「e2e 在不在 references 里」答案是**在**，e2e 的类型错误合并前的执行位置
  就是 `frontend` 的 `pnpm build`。三份都 `noEmit: true`，include 并集恰好是仓库里的四个 TS 根。
- **反证（本机，退出码判，[`evidence/ci02/tsc_mutations.json`](evidence/ci02/tsc_mutations.json)）**：

| # | 植入 | `cd web && pnpm build` | 说明 |
|---|---|---:|---|
| T1 | `web/src/lib/brand.ts` 末尾 `export const __ci02_mutation: number = "not a number";` | **2**（`src/lib/brand.ts(31,14): error TS2322`） | src 的类型错误挡得住 |
| T2 | 同一行放进 `web/e2e/fixtures.ts` | **2**（`e2e/fixtures.ts(238,14): error TS2322`） | e2e 的类型错误也挡得住 |
| T3 | T2 + 把 `./tsconfig.e2e.json` 从 references 里拿掉 | **0** | **同一个错误，references 少一份就没人看见**——所以合同钉的是 references 的**集合**，不是「有 references」 |
| T4 | 全部 `git checkout --` 还原 | **0**（6.6s） | 还原后绿；每次变异后 `git status` 归零 |

  T3 是这一节唯一新增的事实：references 漏一份没有任何红灯，只有一类错误从此没有执行位置。`test_the_project_references_cover_every_typescript_root`
  钉住三份的集合 + `noEmit` + include 并集（变异 M03 / M04 / M05 打红）。**不需要改 `web/tsconfig.json`**——它已经覆盖 e2e，`pnpm build`
  的时长里已经含着它（`tsconfig.e2e.tsbuildinfo` 本机每次都生成）。

## 4. 缓存四类审计（D）

ci.yml 里的全部缓存，按 04 §5 归类；机器可读清单 [`evidence/ci02/cache_inventory.json`](evidence/ci02/cache_inventory.json)（`gh cache list`
2026-09-16 的 60 条原样）。

| 缓存 | 在哪些 job | 类别（04 §5） | key 的维度 | path | 判定 |
|---|---|---|---|---|---|
| `actions/setup-node@v4` `cache: pnpm` + `cache-dependency-path: web/pnpm-lock.yaml` | frontend / plugin-candidate / windows-exe-smoke / macos-app-smoke / posix-e2e（**package 四条腿没开**） | 第一类（下载 bytes：pnpm store） | action 自动 `node-cache-<OS>-<arch>-pnpm-<hash(锁)>`（实测键 `node-cache-Linux-x64-pnpm-6e5eef4c…`）：**OS / arch / 锁** 都在 | pnpm store（不是 `node_modules`） | 合格。`pnpm install --frozen-lockfile` 照跑，命中只省下载。 |
| `Swatinem/rust-cache@v2` `workspaces: workerd` | workerd / windows-exe-smoke / macos-app-smoke | 第二类（编译缓存） | action 自动 `v0-rust-<key>-<job>-<OS>-<arch>-<环境 hash: rustc 版本 + RUSTFLAGS 等>-<hash(Cargo.lock/Cargo.toml/rust-toolchain)>`（实测 `v0-rust-workerd-Linux-x64-6ff13d87-439a7567`）：**OS / arch / toolchain / 锁 / job** 都在；README：workspace 自己的 crate 不缓存、`~/.cargo/registry/src` 不缓存 | `~/.cargo` + `workerd/target` | 合格。没有 `shared-key`（无关 job 不共享可写 target）、没有 `cache-on-failure`。 |
| `Swatinem/rust-cache@v2` `workspaces: src-tauri` + `key: ${{ matrix.os }}` | desktop-shell ×2 | 第二类 | 同上，`key` 再并入 `ubuntu-latest` / `macos-latest`（实测 `v0-rust-ubuntu-latest-desktop-shell-Linux-x64-…`）。ci.yml 注释说「两个平台的 target/ 不通用，缓存键必须分开」——rust-cache 自己已经把 `Linux-x64` / `Darwin-arm64` 放进键里，显式 `key` 是冗余但无害 | `~/.cargo` + `src-tauri/target` | 合格。**这是最大的条目**：Linux 945 MiB × 5 份 + macOS 597 MiB × 6 份 = 8.1 GB，占仓库缓存的 80%（下面「作用域」）。 |
| `actions/cache@v4` `cpython-${{ runner.os }}-${{ runner.arch }}-${{ hashFiles('packaging/runtime-lock.json') }}` | windows-exe-smoke / macos-app-smoke | 第一类（下载 bytes：CPython 归档，脚本自己按锁里的 sha256 校验） | **OS / arch / 锁** 显式 | `build/runtime-cache`（只有归档 zip；wheel 刻意不缓存） | 合格。恢复步在「构建内置渲染 runtime」之前（合同钉住，M13）。 |
| Playwright 浏览器 | — | （本轮决定不加，§2） | — | — | — |
| 第三类「构建 artifact」 | `codex-plugin-candidate`（upload/download-artifact，不是 cache） | 第三类 | source_sha + content_digest（§5） | — | 合格：消费前验，不等价于测试通过。 |
| 第四类「运行状态 / 用户配置 / venv / 测试结果」 | **无** | — | — | — | 逐条核过：两处 `actions/cache` 的 path 都是 `build/runtime-cache`；`package` 的 venv 在 `runner.temp`（CI03b）不缓存；`TAVOTTO_DATA_DIR` / config 由脚本按实例建；`playwright-report` / `test-results` 只走 `if: failure()` 的 upload-artifact。合同：path 里不许出现 venv / site-packages / .tavotto / test-results / playwright-report / ms-playwright（M12）。 |

**发现一（作用域，本轮最重要的一条）**：GitHub 的规则是「run 只能恢复**当前分支或默认分支**创建的缓存；PR 还能读 base 分支」
（docs.github.com「Restrictions for accessing a cache」）。ci.yml 在 push main 上只跑 `main-landing-audit`——**没有任何一个产缓存的 job 在 main 上跑过**，
`cache_inventory.json` 里 main 上仅有的 3 条来自 nightly / release（`windows-install` / `cpython-embed` / `updater-consumer-fidelity`）。
于是：每个 PR 的**第一次** run 全冷（第二次 push 到同一 PR 才暖）；**每个合并组候选 ref（`gh-readonly-queue/main/pr-N-<sha>`）都是新的，永远冷**。
核实：合并组 run `35015416419` 的四个 job 日志（[`evidence/ci02/merge_group_cache_misses.json`](evidence/ci02/merge_group_cache_misses.json)）——
rust-cache「No cache found.」×2、cpython「Cache not found for input keys」×2、pnpm 只有「Cache saved」（没有「Cache restored」）。
**合并资格这条唯一的常规执行点上，现有三类缓存 0% 命中，且每个候选各 save 一份**——这就是 10.6 GB / 62 条的来源（PR ×4–5 份 + 合并组 ×1–2 份
同一把键）。cold 的代价看得见：`desktop-shell (ubuntu-latest)` 在 #374 的第二次 run `35007730894`（同 PR ref，上一次 run `35004450721` 存过，日志「Restored from cache key v0-rust-ubuntu-latest-desktop-shell-…」）91s，在 #375 的首跑 `35011613925`（冷）233s；macOS 41s vs 237s。

**发现二**：`package` 四条腿的 `setup-node` 没开 `cache: pnpm`——不是漏，此刻也不该加：合并组上必冷，加了只是多四份 57 MiB 的 save
（枚举 `PNPM_UNCACHED = {"package"}`，M15）。发现一修好之后再回来开。

**发现三**：`desktop-shell` 的两份 rust-cache 合计 8.1 GB，是把 10 GB 顶穿的主因；它们在合并组上也从不命中。淘汰顺序按最近访问，先被挤掉的是
小而常用的 cpython（24 MiB）与 pnpm（55 MiB）条目。

风险表：

| 风险 | 在哪 | 后果 | 处置 |
|---|---|---|---|
| 缓存只在 PR 第二次 push 起作用，合并组永远冷 | 全部三类 | 合并资格里 rust 冷编译（desktop-shell +140s、workerd +20s）、CPython 每次重下 25 MiB、pnpm 每次重下 55 MiB；save 白传 | §8 第 2 条（种子 job），本轮不改 |
| 10 GB 上限已被顶穿，淘汰按最近访问 | desktop-shell 8.1 GB | 小条目先被挤出；即便修好作用域，main 上的种子也会被 PR 的副本挤出 | 同上；副本是作用域问题的症状，不单独治 |
| rust-cache 的 `key: ${{ matrix.os }}` 与自动键重复 | desktop-shell | 无（多一段字面量） | 记录，不改 |

### 4.1 作用域：现状与可执行的修法（记录，不改；归 CI05「需要拍板的下一步」）

**每类缓存在合并组上的实际状态**（run `35015416419`，候选 ref `gh-readonly-queue/main/pr-362-5b6debb9…`，
[`merge_group_cache_misses.json`](evidence/ci02/merge_group_cache_misses.json)；每个候选写入的 MiB 取自 [`cache_inventory.json`](evidence/ci02/cache_inventory.json)
里同一把键的条目大小）：

| 缓存 | 合并组上的日志行 | 结果 | 每个候选各写入 |
|---|---|---|---|
| rust-cache `desktop-shell`（ubuntu / macos） | `desktop-shell (ubuntu-latest)`：`No cache found.` | miss → cold 编译（clippy 87s + test 101s，暖时 12 + 3）→ Post 步 save | **945 MiB + 597 MiB** |
| rust-cache `workerd`（windows / macos / ubuntu） | `windows-exe-smoke`：`No cache found.` | miss → `cargo build --release` 26–35s（暖时不会短很多，crate 少）→ save | 10–17 MiB × 3 |
| `actions/cache` cpython（windows / macos） | `Cache not found for input keys: cpython-Windows-X64-1ed851e2…` / `…macOS-ARM64-525774d4…` → `Cache saved with key: …` | miss → 重下 10 / 24 MiB 归档（脚本按 sha256 校验）→ save | 10 + 24 MiB |
| setup-node pnpm store（linux / windows / macos） | `frontend`：只有 `Cache saved with the key: node-cache-Linux-x64-pnpm-6e5eef4c…`（没有 `Cache restored`）；windows / macos 同形 | miss → 重下 55–59 MiB store → save | 55–59 MiB × 3 |
| **合计** | 12 个 job 里没有一行 `Cache restored` | **0% 命中** | **≈ 1.8 GB / 候选**（实测：三个候选 ref 上 14 条 1885.5 MiB） |

**淘汰在发生**：两次 `gh api …/actions/cache/usage` 之间（同一小时内）从 10.6 GB / 62 条降到 10.3 GB / 60 条；上限 10 GB，淘汰按最近访问从旧到新——
先走的是小而常用的 cpython（10–24 MiB）与 pnpm（55 MiB）条目，留下的是 945 MiB 的 desktop-shell 副本（PR ×4–5 份 + 候选 ×1 份同一把键，
`cache_inventory.json` 的 `families`）。

**候选 ref 的条目谁来清**（[`queue_ref_cache_persistence.json`](evidence/ci02/queue_ref_cache_persistence.json)）：PR #361 / #362 已于 09-15 合入，
它们的 `gh-readonly-queue` ref 已不在远端（`git ls-remote` 只剩 pr-357 一条），但 **13 条缓存还挂在这两个已删除的 ref 上**。GitHub 文档只说
「未访问超过 7 天自动删除」，没说随 ref 删除；候选 ref 只被自己那一次 run 访问，之后永不再读——所以答案是**没人清，7 天到期或被淘汰**。
这也意味着：合并组每天合入几个 PR，仓库缓存就每天新增几个 1.8 GB 的死重，10 GB 上限永远被顶穿。

**修法（两种形状，都动 push main 的 job 集合或缓存合同，归 CI05 由用户拍板）**：

- **(a) push main 上的种子 job（推荐的形状）——已拍板（2026-09-16）：做，栈合完后作为后续小 PR**：在 `push: main` 上加一个矩阵 job（ubuntu / windows / macos 各一条腿），每条腿只做
  「restore → 让对应工具真跑一次 → save」：`setup-node cache: pnpm` + `pnpm install --frozen-lockfile`（种 pnpm store）、
  `actions/cache` 同一把 cpython key + `build_worker_runtime.py --download-only`（**脚本今天没有这个开关**，要么加、要么整跑一遍 47–76s）、
  rust-cache + `cargo build --release`（workerd；desktop-shell 那份还要 `cargo clippy --all-targets` 才能把 dev-deps 编进 target）。
  **key 对齐是这条的成败**：cpython 与 pnpm 的 key 只含 os / arch / 锁 hash，种子与消费者天然同键，可直接种；**rust-cache 的自动键含 job id**
  （实测 `v0-rust-<key>-<job>-<os>-<arch>-…`），种子 job 与消费者 job 的 id 不同就**种了也命不中**——两边都要改成同一个
  `shared-key`（README：「用来代替自动的 job 键、在多个 job 之间稳定」），例如 `shared-key: workerd-${{ runner.os }}` /
  `shared-key: src-tauri-${{ matrix.os }}`，并把 `desktop-shell` 现在那条冗余的 `key: ${{ matrix.os }}` 一起收掉。代价：每次 push main
  多 3 条腿 ≈ 3–6 runner 分钟（rust 冷编译那一次贵，之后命中就只剩 restore + 空 save）；与 CI01 定的「push main 只跑 landing audit、不重复打包」
  要重新说清——种子 job 不是门禁、不产生结论，只是暖缓存；但它是 push main 上一个会失败的 job，失败要不要红要定。预期收益：合并组上
  desktop-shell −140s（不在关键路径）、windows-exe-smoke 的 rust −20s / cpython −几秒 / pnpm −几秒（在关键路径）、每个候选少写 1.8 GB。
  验法：**看合并组 run 的日志有没有 `Cache restored from key`**，不能看 PR 的第二次 run（那本来就暖）。
- **(b) 消费者加 `restore-keys` 前缀退回**：`actions/cache` 与 setup-node 都支持前缀匹配「最近创建的一条」。它解决的是**锁 hash 变了之后
  退回上一版**（例如 `runtime-lock.json` 改了一个 wheel，退回旧归档目录里还有别的文件可用），**解决不了作用域**：restore-keys 仍只在
  「当前 ref + main」里找，main 上没有条目它一样 miss。所以 (b) 是 (a) 的补充而不是替代；单独做 (b) 在本仓库是零收益。
- 两条都不做时的替代：把 `desktop-shell` 的 rust-cache 去掉（它在合并组上从不命中，只在 PR 第二次 push 起作用，却占 8.1 GB 把别的挤出去）——
  这是「少一个坏缓存」而不是「修好」，也要拍板。

## 5. 产物身份与不混同目标（E）

唯一的数据边 `frontend ══▶ plugin-candidate`：

- **生产者写什么**（`pluginmanifest.write_build_manifest`，`plugin-build.json`）：`schema`、`plugin` / `plugin_version`（取自 `.codex-plugin/plugin.json`）、
  `min_tavotto_version`、**`source_sha`**（`stage()` 拒绝与 checkout 的 HEAD 不同的值，也拒绝插件源码目录有未提交改动）、
  `build_inputs_fingerprint`（画布源码指纹）、`lockfile_sha256`（`web/pnpm-lock.yaml`）、`toolchain`（python / node / pnpm 版本）、
  `files[]`（每个文件的 path / **git 模式** / sha256，模式从索引读——Windows 上 `st_mode` 恒 0o666）、`pinnable`、**`content_digest`**
  （files 三元组的摘要；`audit` 段（event / run_id / kind）**不参与**身份）。
- **消费者怎么核**（ci.yml `plugin-candidate`）：`SHA="$(git rev-parse HEAD)"`（本次 checkout，不是 artifact 说它是什么）；`DIGEST` 从 artifact 里的
  `plugin-build.json` 读；`plugin_stage.py unpack`（拒绝越界 / 错根的条目）→ `verify "$PLUGIN" --source-sha "$SHA" --content-digest "$DIGEST" --serve python`：
  清单必须在、每个文件 sha256 一致、不多不少、REQUIRED 齐、画布合格（大小 / 指纹戳）、command 是裸名字、`content_digest` 重算一致且等于期望、
  再真起 MCP server 走 stdio 读画布与磁盘逐字比。合同：`test_the_plugin_candidate_consumer_verifies_head_sha_and_manifest_digest`（M19 / M20 / M21）。
  负例在 `tests/test_plugin_stage.py`：错 SHA（`test_stage_refuses_a_source_sha_that_is_not_head`、`verify_dir(source_sha="f"*40)`）、
  改一个字节（`test_one_changed_byte_changes_the_content_digest`）、少 / 多文件与改过的清单（`test_verify_catches_every_kind_of_drift`、
  `test_a_tampered_manifest_is_caught_by_recomputation`）、dotfile 进 zip（`test_zip_is_deterministic_and_round_trips` 断言 `.codex-plugin/plugin.json` 在）、
  可执行位（`test_zip_keeps_executable_mode_from_the_manifest`）。
- **recipe / target 在哪**：不在清单字段里，由 job 定义隐含——artifact 名 `codex-plugin-candidate` 只有 `frontend` 上传（`TestHeavyLaneDependencies::
  test_heavy_consumers_that_download_an_artifact_must_need_its_producer` 断言 `uploads["codex-plugin-candidate"] == {"frontend"}`），target 是平台中性的
  单文件 HTML。这条边只有一个 recipe、一个 target，所以「绑定 recipe/target」这一半没有第二个值可混。
- **生成物不进索引**：`frontend` 在 `pnpm build` **之后**跑 `scripts/ci/check_generated_untracked.py`（两条判据：`git ls-files` 里没有 + `git check-ignore`
  命中；`GENERATED` = canvas.html / `web/dist-mcp` / `web/dist-playground`），`main-landing-audit` 再查一次；顺序由合同钉住（M09）。
- **三个 web 目标是三个 recipe**：#1 `vite.config.ts` → 多文件 `dist`；#2 `vite.mcp.config.ts` → 单文件内联 + 指纹戳；#4 `vite.playground.config.ts` → 多文件 +
  `engine.zip` + manifest。入口、config、输出形态都不同，任何一个都不能替代另一个（CIP-013）。
- **发行链不被 PR 污染**：候选只在 `frontend` / `plugin-candidate` 里验，不向源码分支回写、不发布；release.yml 在固定发行 SHA 上重新造，`plugin_stable`
  只投影 Release 资产里的那份（`docs/ci/plugin-stable-channel.md`，ADR 0043）。本轮没碰它。

## 6. 合同测试与变异反证

`tests/test_merge_queue_workflows.py::TestBuildReuseAndCaches` 八条（与前几轮同一条纪律：不用 PyYAML，只认本仓库缩进形状，切不出当场抛）：

| 用例 | 主语 | 打红它的变异 |
|---|---|---|
| `test_the_web_build_script_starts_with_a_real_project_build` | `build` 脚本的**第一条**命令 | M01 `tsc --noEmit`、M02 去掉 tsc |
| `test_the_project_references_cover_every_typescript_root` | references 的**集合** + 三份 `noEmit` + include 并集 | M03 去掉 e2e 引用、M04 e2e 少 `playwright.config.ts`、M05 `noEmit: false` |
| `test_the_frontend_job_type_checks_through_pnpm_build_without_a_safety_net` | `frontend` 的 `- run: pnpm build`：恰好一条、无 if / continue-on-error、无第二条 tsc、生成物检查在其后 | M06 加 continue-on-error、M07 删步、M08 加 `pnpm tsc --noEmit`、M09 顺序对调 |
| `test_actions_cache_steps_are_exactly_the_cpython_archive_downloads` | `actions/cache` 的 (job, path, key) **枚举**；key 含 os / arch / 锁；恢复在使用前；path 无第四类 | M10 去掉 arch、M11 加 Playwright 缓存、M12 path 改成 venv、M13 挪到构建之后 |
| `test_every_setup_node_pnpm_cache_is_keyed_by_the_lockfile` | 开了 pnpm 缓存的 job 集合 == 五个且都带 `cache-dependency-path`；没开的 == {package} | M14 去掉 dependency-path、M15 package 开缓存 |
| `test_every_rust_cache_names_its_own_workspace_and_shares_nothing` | rust-cache 的 (job, workspaces) 枚举；无 shared-key / cache-on-failure | M16 加 shared-key、M17 去掉 workspaces |
| `test_windows_installs_browsers_without_with_deps_and_posix_keeps_it` | 两条 Playwright 腿各恰好一条 `playwright install`，主语是整条命令：Windows 不带 `--with-deps`、posix 带 | M18 posix 不装、M22 Windows 加回 `--with-deps`、M23 posix 去掉 |
| `test_the_plugin_candidate_consumer_verifies_head_sha_and_manifest_digest` | 消费者 SHA = HEAD、digest 来自清单、verify 三个参数；生产者同形 | M19 不核 digest、M20 SHA 改成 `github.sha`、M21 zip 复验不带 digest |

变异反证 [`evidence/ci02/mutations_ci.json`](evidence/ci02/mutations_ci.json)：**23/23 KILLED**，每条记着目标串次数、退出码、红的是哪条用例、
还原后 md5 与变异前一致。顺序：树干净且基线绿 → 断言目标串恰好出现期望次数 → 写入 → 清 `__pycache__` + `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`
→ pytest 退出码判 → `git checkout --` 还原 → 核 md5；跑完 `git status` 只剩本目录的新文件。

## 7. 验证命令与退出码（2026-09-16，worktree，本 PR 的树）

| 命令 | 退出码 |
|---|---:|
| `/opt/homebrew/bin/actionlint .github/workflows/ci.yml` | 0（ci.yml 改了一行 + 注释） |
| `.venv/bin/ruff check . && .venv/bin/ruff format --check .` | 0 |
| `cd web && pnpm build`（冷：先删 `node_modules/.tmp`）| 0，**7.0s**（暖 6.6s） |
| `.venv/bin/python -m pytest tests/test_merge_queue_workflows.py tests/test_ci_tooling.py tests/test_docs_references.py tests/test_source_hygiene.py tests/test_generated_untracked.py` | 0（138 passed, 1 skipped：test_ci_tooling 的「非 Linux 无 /proc」） |
| `python scripts/build_mcp_widget.py --out <scratch>/canvas.html --json` / `build_browser_playground.py` / `python -m build --outdir <scratch>` / `cargo build --release`（本机，量尺寸与时长） | 0 / 0 / 0 / 0 |
| 本机 tsc 反证 T1 / T2 / T3 / T4（§3） | 2 / 2 / 0 / 0 |
| 变异反证 23 条（§6） | 23/23 KILLED |
| 实机：本 PR 的 full-ci run 上 `windows-exe-smoke (1)` / `(2)` 不带 `--with-deps` 的 e2e 结论与安装步时长 | **not_run**（不能 push；lead 盯 run 并写进 PR，§2.1 写了红了怎么判） |

## 8. 已知边界与下一步（都有数字，都没做）

1. **Windows 两片不带 `--with-deps` 是实验，本机（macOS）证不了**：PR 的 full-ci run 是唯一样本；红了怎么判、绿了记什么在 §2.1。若实验失败，
   备选是把 `Install-WindowsFeature` 挪到 job 开头后台跑、装浏览器前等它完成（环境不变，只与 4 分钟的构建链重叠）——跨 step 的后台进程与 DISM
   并发锁同样只有 Windows 机器才证得了。
2. **缓存作用域（§4.1）**：main 上没有 ci.yml 的任何缓存 → 合并组永远冷、每个候选写 1.8 GB 死重、10 GB 上限永远被顶穿。两种修法与各自代价写在
   §4.1，**用户已拍板做 (a)，栈合入后另开小 PR**；改了以后用合并组 run 的日志（`Cache restored from key`）验，不能看 PR 的第二次 run。
3. **`package` 的 pnpm 缓存**：第 2 条修好之后再开（四条腿各 −5s 下载）；现在开是四份白传。
4. **本轮没有真实 run**：§1 的秒数是两次已有 run 的实测，不是本 PR 的；本 PR 在 CI 上会变的只有两处——backend-fast 里多八条合同用例、
   `windows-exe-smoke` 两片的安装步不带 `--with-deps`。
5. **`dist/Tavotto` 的总大小日志没打印**：表里写的是下界（runtime 249 / 288 MiB）；要精确值得在冒烟腿加一行 `du`（不在本轮范围）。
6. **Windows 那两条腿的 `runner_wait`（205s / 321s）是 pull_request 事件上的观察**，CI00 §4.3 说合并组上 Windows 领取中位 3s；§1 里「多串一个
   Windows job 必然为负」在合并组上的量级会小一些（一次 dispatch + 领取 ≈ 5–15s + 上传下载 1–2 分钟），结论不变。

## 9. 证据索引（[`evidence/ci02/README.md`](evidence/ci02/README.md)）

`ci_step_seconds.json`（两个 run 的 57 个 job 逐步秒数）· `playwright_install_split.json`（三条腿安装步的时间戳拆分）· `cache_inventory.json`
（仓库缓存 60 条 + 按家族 / 作用域汇总）· `merge_group_cache_misses.json`（合并组 run 的命中 / 未命中行）· `recipe_table.json`（§1 表）·
`queue_ref_cache_persistence.json`（已合入候选的 ref 没了、缓存还在）· `local_builds.json`（本机五种产物的时长与尺寸）· `tsc_mutations.json`（T1–T4）·
`mutations_ci.json`（M01–M23）。

# 构建产物不跨 job 抽取、缓存枚举与 push main 上的缓存种子（CI02）

> 原文出自 `.github/AGENTS.md`「门禁纪律」（2026-09-18 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **构建产物不跨 job 抽取、缓存只有两类（CI02，2026-09-16）**：九种 recipe（web 应用 / MCP 画布 / 插件候选 / playground /
  wheel / workerd / 内置 runtime / PyInstaller / .app）逐行量过——0 行值得新抽取，唯一的数据边仍是 `frontend → plugin-candidate`
  （消费者用 **checkout 的 HEAD** + **清单里的 content_digest** 核，不信 artifact 的名字）。ci.yml 里的缓存是**枚举**：
  `actions/cache` 只有 CPython 归档（两处消费者 + `cache-seed` 的种子步，key 含 `runner.os` / `runner.arch` / 锁 hash，恢复在
  `build_worker_runtime.py` 之前）、setup-node 的 pnpm store 按 `web/pnpm-lock.yaml`、rust-cache 各自点名 `workspaces` **并带一个
  以 (workspace, profile) 命名的 `shared-key`**（`workerd` / `desktop-shell` / `workerd-release`，见下一段）；venv / site-packages /
  用户目录 / 测试结果 / **Playwright 浏览器目录**一律不缓存（`tests/test_merge_queue_workflows.py::TestBuildReuseAndCaches`，
  多一条 `actions/cache` 就红——先回文档改数字）。两条派工时的前提被日志推翻，改之前先读：Windows 腿「装浏览器」的 245s 里
  **203–226s 是 `--with-deps` 装 Media Foundation**，浏览器下载只 17–27s——所以 `windows-exe-smoke` 两片的
  `pnpm exec playwright install ${{ matrix.browsers }}` **不带 `--with-deps`**（实验，由 CI02 那个 PR 的 full-ci run 判，红则一行加回；
  `posix-e2e` 的 `--with-deps chromium` 是 apt 真依赖，保留；两条整条命令都被合同钉着）。TypeScript 的类型检查只在
  `frontend` 的 `pnpm build`（第一条命令 `tsc -b`）里，`web/tsconfig.json` 的 references **集合**（app / node / e2e）就是它的
  覆盖面——少一份没有红灯，只是那一类错误从此没有执行位置。数字、反证与下一步：
  `docs/implementation/ci-foundation/CI02_BUILD_REUSE.md`。
- **push main 上的缓存种子（CI02 §4.1 (a)，2026-09-16 用户拍板）**：GitHub 缓存的作用域是「当前 ref + 默认分支」，合并组候选 ref
  （`gh-readonly-queue/main/pr-N-<sha>`）与 PR 首跑都读不到别人的缓存，而 push main 上原先没有任何产缓存的 job——合并资格这条唯一的
  常规执行点上三类缓存 **0% 命中**（合并组 run `35015416419` 的 12 个 job 没有一行 `Cache restored`），**每个候选各 save ≈ 1.8 GB**
  死重（已合入候选的 ref 没了、条目还挂着、没人清），仓库 10 GB 配额被顶穿后按最近访问淘汰，先走的是小而常用的 cpython / pnpm。
  所以 CI01「push main 只跑落地审计、不重复打包」的合同改成「落地审计 **+ 缓存种子**」：`cache-seed` 五条腿，每条 = 一个
  (os, rust-cache `shared-key`)——ubuntu·`workerd`、ubuntu·`desktop-shell`、macos·`desktop-shell`、macos·`workerd-release`、
  windows·`workerd-release`——各做「restore → 让消费者的准备步骤**真跑一次** → save」：pnpm store 每个 os 一次（`pnpm install
  --frozen-lockfile`）、CPython 归档挂在 `workerd-release` 两条腿（`build_worker_runtime.py --clean` 整跑，脚本没有「只下载」开关且
  产品脚本不动）、rust-cache 跑消费者那一组 cargo 命令（dev = `clippy --all-targets` + `test`；release = `build --release`）。
  **它不是门禁**：不在任何 Gate 的 needs / --required 里，红了不影响合并，也不加 `continue-on-error`（红着可见）。事件条件
  `push || (pull_request && full-ci)`：push main 是种子；带 `full-ci` 的 PR 上跑的是**首验**（PR 作用域的缓存 main 读不到），让五条腿
  的每一步先在 PR 自己的 run 上执行过，而不是第一次执行就落在合入 main 那一刻；merge_group 上不跑。
  **key 对齐是成败所在**：pnpm / CPython 的 key 只含 os / arch / 锁 hash，种子与消费者写逐字相同的 with 块即可；rust-cache 的
  自动键含 **job id**，所以四处消费者都加了 `shared-key`（代替 job id 那一段；os / arch / rustc / `CARGO*` `RUST*` 环境变量 /
  Cargo.toml + Cargo.lock 仍由 action 并入），同一把键只对应**一种** cargo profile（dev 与 release 的 target/ 不是一份，
  所以 `workerd` ≠ `workerd-release`），`desktop-shell` 原先冗余的 `key: ${{ matrix.os }}` 一并收掉。一条腿一把键而不是一个 os
  一条腿：rust-cache 的 Post 步会把整机共用的 `~/.cargo/registry` 修剪到自己 workspace 的依赖集再 save，同一个 job 里两个实例会
  互相修剪，先声明的那份缓存里没有自己的 `.crate`。合同 `tests/test_merge_queue_workflows.py::TestCacheSeed` 六条 +
  `TestLandingAudit`（push 上的 job 集合 == {landing audit, cache-seed}）+ `TestBuildReuseAndCaches` 的三张枚举。**验法**：合入后
  第一次 push main 才有种子，之前入队的候选仍冷；看**下一个** merge_group run 的 `workerd` / `desktop-shell` / `windows-exe-smoke` /
  `macos-app-smoke` 日志有没有 `Restored from cache key "v0-rust-…" full match: true`（rust-cache）、`Cache restored from key:
  cpython-…`（actions/cache）、`Cache restored from key: node-cache-…`（setup-node）——不能看 PR 的第二次 run，那本来就暖。
  数字、变异反证与已知边界：`docs/implementation/ci-foundation/CI02_BUILD_REUSE.md` §4.1。

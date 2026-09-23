# desktop-shell 这一格与「在 Gate 的闭集里 ≠ 在 PR 上会跑」

> 原文出自 `.github/AGENTS.md`「发布链」（2026-09-18 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **`desktop-shell`（2026-09-04，issue #275）**：`src-tauri` 的
  `cargo fmt --check` / `clippy -D warnings` / `cargo test`，与 `workerd` 同一条
  纪律（都不做 paths 过滤）。它原先只在 `desktop-tauri.yml` 里跑，而那个工作流
  只在打 tag / dispatch 时跑、`cargo test` 还收在 build 矩阵的 macOS 那条腿上
  ——改了壳的 PR 因此一路全绿，Rust 侧判据合并前一次都不执行。
  **`tauri.conf.json` 的 `bundle.resources` 指向 `../dist/Tavotto`，空目录就够**
  （`mkdir -p dist/Tavotto`），所以这一格不必挂在完整打包之后，几十秒回来。
  **两条腿：`ubuntu-latest` + `macos-latest`（2026-09-07，issue #282）。**
  clippy 只看得见参与编译的那一支，而 `main.rs` 的应用菜单有
  `#[cfg(target_os = "macos")]` 分支——只跑 Linux 腿时，那一支的 lint 在任何
  工作流里都没有执行位置（`desktop-tauri.yml` 的 Apple Silicon 腿只跑 `cargo test`，
  不 deny warnings：编译错误抓得到、lint 抓不到），又是一条「登记了但从不执行」
  的判据。这条边界现在**已经关掉**，不再是已知边界。`cargo fmt` 不吃 cfg
  （rustfmt 解析整个文件），本来就两支都看得见。
  matrix 一变（少一类 runner，或把某条 cargo 命令用 `if:` 收窄到一条腿上）
  由 `tests/test_merge_queue_workflows.py::TestGates::test_desktop_shell_lints_both_sides_of_the_macos_cfg`
  与同类 `::test_the_rust_gates_run_on_every_desktop_shell_leg` 当场判红。
  **job 名字变了**：显示名现在是 `desktop-shell (ubuntu-latest)` /
  `desktop-shell (macos-latest)`，但 job **id** 仍是 `desktop-shell`，
  `needs:` 与 `--required` 读的都是 id，required contexts 也只有三个 Gate 名字
  （`scripts/ci/merge_queue_ruleset.py` 的 `GATE_CONTEXTS`）——所以仓库设置里
  **不需要重新登记任何必需检查**。
- **「在 Gate 的闭集里」≠「在 PR 上会跑」**：重型那几档接在 integration gate 里，
  普通 PR 上整体 skipped 而 Gate 判 deferred（绿）。把一个 fast 档的 job 改成
  重型条件，Gate 依旧全绿而它守的东西合并前一次都不验——
  `tests/test_merge_queue_workflows.py::test_every_fast_lane_job_actually_runs_on_a_plain_pull_request`
  逐个比死条件看住这一位。

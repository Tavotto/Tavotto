# evidence/ci02/ · CI02 的证据

CI 侧全部取自**已有** run（本轮不能 push，没有新 run）：PR #374 `35007730894`、PR #375 `35011613925`（都是 `full-ci` PR，attempt 1）、
合并组 `35015416419`；取数日期 2026-09-16。本机部分产自 worktree（macOS 26 / Apple Silicon 12 核，Python 3.13.11 / Node 26.7.0 / pnpm 11.0.7 /
rustc 1.95.0；同一台机器上另有会话并行，CPU 非独占），树是本 PR 的工作树（`1f7f13e8` + 合同测试；源码与 ci.yml 未动）。
解读在上一级的 [`CI02_BUILD_REUSE.md`](../../CI02_BUILD_REUSE.md)。

| 路径 | 内容 | 产出方式 |
|---|---|---|
| `ci_step_seconds.json` | 两个 run 的 57 个非 skipped job：job 秒数 + ≥1s 的逐步秒数（`completed_at − started_at`） | `gh api repos/Tavotto/Tavotto/actions/runs/<id>/jobs?filter=latest&per_page=100` |
| `playwright_install_split.json` | 三条 Playwright 腿「装依赖与浏览器」那一步按日志行首时间戳拆成 `pnpm install` / `--with-deps` 系统依赖 / 浏览器下载三段，附下载清单与依赖安装的证据行 | `gh run view 35011613925 --job <id> --log`，正则取时间戳相减 |
| `cache_inventory.json` | 仓库 Actions 缓存用量（10.3 GB / 60 条）+ 60 条原样（key / MiB / ref / 时间）+ 按家族与按 ref 作用域（pull_request / merge_group / main）的汇总 | `gh api repos/Tavotto/Tavotto/actions/cache/usage`、`gh cache list --limit 200 --json …`（两次查询之间从 10.6 GB / 62 条降到 10.3 GB / 60 条——淘汰在发生） |
| `queue_ref_cache_persistence.json` | 已合入的 PR #361 / #362：候选 ref 已从远端删除，13 条缓存（1.8 GB 量级）仍挂在那两个 ref 上——「谁来清」的答案 | `gh pr view`、`git ls-remote origin 'refs/heads/gh-readonly-queue/*'`、`gh cache list` |
| `merge_group_cache_misses.json` | 合并组 run `35015416419` 四个 job 日志里的缓存命中 / 未命中行（全部 miss + save） | `gh run view … --log \| grep -E 'Cache not found\|Cache restored\|No cache found\|Cache saved'` |
| `recipe_table.json` | §1 的 recipe 表（9 行：命令 / 输入 / 输出 / 在哪建几次各几秒 / 是否关键路径 / 决定 / 理由）+ 两个 run 的关键路径 | 手写 + 从 `ci_step_seconds.json` 抄数 |
| `local_builds.json` | 本机五种产物的命令、时长、尺寸（web/dist、canvas.html、dist-playground、wheel + sdist、workerd），runtime / PyInstaller 引用 CI 日志里的 MiB | `/usr/bin/time -p …`（产物写到 scratchpad 或 gitignore 的位置；`git status` 归零） |
| `tsc_mutations.json` | TypeScript 反证 T1–T4：src 植错 rc 2 / e2e 植错 rc 2 / **references 去掉 e2e 再植错 rc 0** / 还原 rc 0，附 `error TS` 行 | `cd web && pnpm build`，退出码判，`git checkout --` 还原 |
| `mutations_ci.json` | 合同测试的变异反证 M01–M23（ci.yml / package.json / tsconfig），**23/23 KILLED**，每条记目标串次数、退出码、红的用例、还原 md5 | `mutate_ci02.py`（会话 scratchpad，不进仓库；顺序：树干净 + 基线绿 → 目标串次数 == 期望 → 变异 → 清 `__pycache__` + `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider` → pytest 退出码 → `git checkout --` → 核 md5） |

**不进仓库的东西**（会话 scratchpad）：三个 job 的完整日志（各 1.9k 行）、两个 run 的 jobs 原始 JSON、`gh cache list` 原始输出、变异脚本、本机构建日志与产物。

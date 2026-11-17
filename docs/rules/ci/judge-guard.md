# 判据路径：改动不能悄悄改评判它自己的尺子

> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 名单 `.github/judge-paths.json`（唯一出处）；分类器 `scripts/ci/judge_guard.py`。
> 看护用例：`tests/test_judge_guard.py`

<!-- agents-route
sheet: .github/AGENTS.md
order: 25
改到: `.github/judge-paths.json` 里的任一路径（workflow、`scripts/ci/`、Gate 看护用例、Guardian 夹具、根 `AGENTS.md`……）
主题: 判据路径 → `judge-guard.md`
必守要点: 不自动入队、先报维护者；新门禁 / 看护用例 / 豁免表同 PR 登记进名单；observe 不判红
-->

**为什么。** PR 若能同时改代码和评判它的检查，检查就失效了。两个 Gate 的 `aggregate_gate.py`、
docs-scope / release-note / CLA 判定已经取自默认分支，PR 改不了对自己的判定；但下面这些天然跟着
PR 走，结构上堵不死：workflow 本身（「去默认分支取判定器」那一步、Gate 的 `--required` 与 `needs`
都写在 PR 的 `ci.yml` 里）、树内看护用例与棘轮、Guardian 夹具与期望。根 `AGENTS.md` 的 Review
guidelines（Codex 的评审口径）Codex 读的是 PR head 还是默认分支**未实测**，按跟着 PR 走处理。
对它们能做的是**看见并交人**。

**规则。**

- **改到名单里的路径，不自动入队**：PR 正文写明改了哪条判据、是收紧还是放宽、为什么；推上去后停下，
  报维护者，回「#N 可以排」才入队。这条覆盖「入队不再问用户」类授权（`docs/ci/pr-review-tiers.md`
  「入队的机械判据」）。风险档照旧按改动性质定，不因此自动升档。
- **CI 只负责标出来**：`docs-scope` job 里的「标出改到的判据路径（只观察）」一步从默认分支取分类器与
  名单，命中就打 notice 并写进 step summary。`mode: observe` 永远不判红，且该步 `continue-on-error`；
  配置坏了只 warning。这一步是**绊线不是锁**——所有会话以同一个账号推送、打标签、入队，GitHub
  分辨不出哪一步是维护者本人做的；也挡不住一个故意删掉这一步的 `ci.yml` 改动（但 `ci.yml` 自己在
  名单里，改它照样被标出——那次 run 跑的是改后的 workflow，所以入队前读 diff 才是兜底）。
- **入队前本地自查**（自动入队的流程也照这条判，不靠读 CI 注解）：
  `git diff --name-only --no-renames origin/main...HEAD > /tmp/changed.txt && git show origin/main:.github/judge-paths.json > /tmp/jp.json && git show origin/main:scripts/ci/judge_guard.py > /tmp/jg.py && python3 -I /tmp/jg.py --paths-file /tmp/changed.txt --config /tmp/jp.json`，
  输出 `"touched": true` 就停下报维护者。
- **新增门禁、看护用例、豁免表、基线，同一个 PR 登记进名单**。条目以 `/` 结尾是目录前缀，以 `*` 结尾是
  同一目录下的文件名前缀，否则是精确路径，不支持别的通配。每条写 `why`；匹配不到任何已跟踪文件的条目会让看护用例红（空条目 = 空转门禁）。
- **CODEOWNERS 不是评审边界**：ruleset 里 `require_code_owner_review` 关着、批准数 0，它只发评审请求；
  也不能打开（唯一维护者无法批准自己账号开的 PR，所有 PR 会卡死）。

**档位。** `off` / `observe`。要升成判红的档，先在 observe 下攒够两周命中记录、核过误报，另开 PR
（改档本身就是改判据，按本规则交维护者）。

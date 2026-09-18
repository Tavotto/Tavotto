# 发布链：两段链、插件版本清单、更新清单与遥测部署顺序

> 原文出自 `.github/AGENTS.md`「发布链」（2026-09-18 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **两段链（F.2，2026-09-16 起）**：实验室 runner 注册在私有仓库 `Tavotto/ci-infra`，
  公开仓库不能等私有仓库（本仓库禁止轮询：lab 的排队时间没有上界），所以发布链在 lab
  门禁处切成两段。**第一段 `release.yml`**（tag push / workflow_dispatch）：`trust`（精确
  SHA / origin/main 祖先或 tag / release:blocker 签字，一字未动）→ `build` / `desktop` →
  `dispatch_lab`（hosted，`needs: [build, desktop, trust]`，形状与 `lab-ci.yml::dispatch` 同：
  `TAVOTTO_CI_INFRA_TOKEN` 为空 `::error::` + exit 1；`-f mode=release -f sha=<trust 的 SHA>
  -f use_prebuilt_dist=true -f source_run_id=<本 run> -f publish=<trust 算出的>
  -f ack_open_blockers=<原样> -f pypi_target=<trust 折算的>`），**到此结束、不等结果**——第一段 run 的 success 只表示
  「产物造出来了、lab 派出去了」。ci-infra 的 `report` 给 SHA 打 commit status `lab/release`，
  **release 且绿**时再 `gh workflow run release-publish.yml -R Tavotto/Tavotto`。
  **第二段 `release-publish.yml`**（只有 `workflow_dispatch`，inputs `sha` / `source_run_id` /
  `lab_run_id` / `publish` / `ack_open_blockers` / `pypi_target` 全 string）：`trust2` **不信任载荷**——重跑
  `trust` 同一段 ancestry + tag 判断（照抄）、对**此刻** open 的 release:blocker 用第一段的
  ack 再跑一次 `release_blockers.py`（第二段不新增签字入口）、publish **按同一规则重算**（有
  `v<源码版本>` tag 指向该 SHA → true；载荷的 publish 只能把 true 压成 false）、读**一次**
  `lab/release` status（`gh api …/commits/<sha>/status`，要求 state == success 且 target_url
  指向 `lab_run_id` 那个 ci-infra run）；然后 `validate_artifacts`（download-artifact 带
  `run-id: inputs.source_run_id` 从第一段 run 取全部产物，job 加 `actions: read`）→
  `github_release` / `n1_update_windows` / `pypi` / `plugin_stable`——从 release.yml **逐字搬来**，
  只把 `needs.trust.*` 改成 `needs.trust2.*`。**失败形状**：lab 红 → `lab/release` = failure、
  第二段从未开始；读结论看两处（status + 第二段有没有该 SHA 的 run），
  `docs/ci/release-qualification.md`「发行链上的 gate」。**pypi_target**：从前 `pypi` job 那条 `if`
  的两支（tag 触发看 `vars.PYPI_PUBLISH_ENABLED` / dispatch 看 `inputs.pypi`）到不了第二段，由
  第一段 `trust` 折成一个值随两跳载荷传过去（ci-infra 接口 +1），`trust2` 只收窄（闭集、publish
  不是 true → none），第二段不再读那个仓库变量。合同：`tests/test_release_workflow_contract.py`（`_REUSABLE_CALLERS` 空集、
  `_DISPATCHERS` 两个、发布判据主语扩到第二段、七条两段链专属用例，变异 49 + 15 条打红）、
  `test_merge_queue_workflows.py::TestRunnerTrustZones`（`uses` reusable 的 workflow 集合 == ∅、
  派发的 == {lab-ci, release}）、`test_update_chain_gates.py`（pattern 点名的 universe 学会跨 run）。
- release.yml 的插件版本清单（`codex-plugin.json`）由 `build` job 生成（不再在没有 Node 的
  `validate_artifacts`——现在在第二段——里从源码目录打包），**不能挪进 desktop-tauri.yml 的 updater-manifest**
  （那个 job 没配 minisign 私钥就整个跳过，插件更新通道会悄悄停而且全绿）。
- 桌面更新清单 `latest.json` 由 `scripts/make_updater_manifest.py` 在两条
  matrix 腿都跑完后合成；macOS 更新包必须在签名/公证之后重做
  （见 `src-tauri/AGENTS.md`）。
- 遥测部署顺序：先发代理 → 验 PostHog 收得到 → 配采集器 → 再发客户端
  （反过来新事件被静默 400 而且全绿）。发行量采集器失败必须让 workflow 红。

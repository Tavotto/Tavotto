# 完整 Codex 插件候选（ADR 0043）：构建、验证、投影到 plugin-stable

> 原文出自 `.github/AGENTS.md`「发布链」（2026-09-18 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **完整 Codex 插件（ADR 0043，2026-09-05）**：ci.yml 的 `frontend` job 从本次 checkout 真构建
  画布 → `scripts/plugin_stage.py` 按 git 清单 + 显式构建物组装、验证、确定性 zip → artifact
  `codex-plugin-candidate`；`plugin-candidate` job 脱离源码树解包、真起 MCP server 读画布、执行
  `tests/test_plugin_candidate.py`（有产物时**不许 skip**）。两者都在 `CI fast gate` 的闭集里。
  候选只作验证，**不向源码分支回写、不发布**。release.yml 的 `build` job 在固定发行 SHA 上
  同样造一次（`--serve` 用发出去的 wheel），三样进 `dist/`（zip / `codex-plugin.json` /
  `codex-plugin-build.json`）与产物清单；`release-publish.yml` 的 `validate_artifacts` 成对验证；
  `plugin_stable` job（同在第二段）在 Release 与 PyPI 之后把**同一份** zip 投影到发行分支
  `plugin-stable`（publish=false 时对临时 bare 仓库演练全部发布行为 + 对真实远端只读 plan）。
  手动入口 `plugin-stable.yml`
  （bootstrap / promote / rollback，从 Release 资产取内容）。手册：`docs/ci/plugin-stable-channel.md`。
  **发行分支不触发任何源码 CI**——没有 workflow 监听它，GITHUB_TOKEN 的推送也不触发。

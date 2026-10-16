# Agent 统一验证入口：白名单场景与诚实结果（ADR 0131）

> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 决策与取舍见 `docs/adr/0131-agent-verification-entry.md`；代码事实表见
> `docs/implementation/agent-verification/code-facts.md`。

## 怎么用

```sh
python scripts/ci/agent_verify.py list
python scripts/ci/agent_verify.py run <场景ID>... --python .venv/bin/python [--out DIR] [--json]
```

- 证据在 `--out` 下；不给就新建临时目录，并打印路径。`report.json` 是结论，`<id>/evidence/` 是原始证据。
- 权威是 `report.json` 的 `overall`。退出码 0 / 1 / 2 / 3 / 4 只对本地有意义，CI 的 pwsh 会把非 0/1 折成 1。
- 字体不进 git：新 worktree 先 `python scripts/fetch_fonts.py`，或设 `TAVOTTO_FONTS_DIR`。
  性能场景缺字体会判 `environment_unavailable`。

## 规则

- **只认登记过的场景**。新增场景就是在 `scripts/ci/agent_verify.py` 的 `SCENARIOS` 加一条，
  argv 只调用仓库里**已有**的验证器（脚本或 pytest 文件），只用 `{python}` `{evidence}`
  `{private}` `{work}` 四个占位符。不提供任何透传任意命令或参数的开关。
- 每条场景必须写：
  - `claim`：passed 到底证明了什么，例如性能场景的 passed 是「数据完整」，不是「性能达标」；
  - `unverified`：明确没覆盖的东西。只有模拟证据的场景，要把真机条件写在这里；
  - `owner_doc`：对应的规则或 ADR。
- **结果是闭集，任何非 passed 都不能聚合成 passed**：
  - 有 skip 就是 `skipped`，没收集到用例是 `not_run`，没有 junit 是 `error`；
  - 前置条件不满足是 `environment_unavailable`；登记的测试文件不存在是 `not_run`。
  - 不许为了让场景变绿去改基线、削弱断言、扩大豁免、把失败转成 skip。
- **隔离**：
  - 每个场景的 `TAVOTTO_DATA_DIR`、`MPLCONFIGDIR`、`TAVOTTO_CI_STATE_ROOT` 都在自己的私有目录下；
  - 固定 `TAVOTTO_NO_TELEMETRY=1`，`PYTHONPATH` 用绝对路径；
  - 调用方环境只放行白名单变量。
  - 新场景如果要读别的环境变量，加进 `extra_env` 并写明理由，**不要扩大放行白名单**。
- **隐私**：`report.json` 不含本机绝对路径。原始日志可能含路径，只留在本机，不贴进公开 PR。
  含用户路径或文件名的中间产物，比如 bench 原始 JSON，放 `{private}`，跑完即删。
- **不进 CI、不上 self-hosted**：P0 只供本地 Agent 使用。要让 CI 消费就另开 PR：只用 hosted runner，
  遵守 `runner-trust-zones.md` 的事件闭集，只上传 `report.json`。
- **不更新基线**：性能场景透传 `comparison_verdict`，基线状态是 `not_checked`。
  将来接入同机基线时，机器指纹不同只出 `incomparable`。

## 看护

`tests/test_agent_verify.py`：
- 聚合永不把非 passed 判成 passed；
- junit 缺失、有 skip、0 条用例这几种情况各自的判定；
- 超时杀进程树；
- 调用方环境不泄漏、隔离变量到位；
- 未登记 ID 和非空 `--out` 是用法错误；
- 报告不含绝对路径；
- 登记的脚本、用例、文档都真实存在。

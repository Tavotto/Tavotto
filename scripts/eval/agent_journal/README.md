# Agent 单独 vs Agent + Tavotto：把图改到期刊合规

这个评测比较两种做法，看谁能把 agent 生成的 matplotlib 图改到期刊合规：agent 单独改（A 组），agent 用 Tavotto MCP 改（B 组）。
不进 CI，也不在 PR 里跑。被测模型要额度，评测时间以小时计。

- **语料**：`corpus.json` 有 20 张图，是 agent 按 `briefs.json` 写的，每张跑 3 本期刊（Nature、JACS、JMST），共 60 组。
- **判分**：只用 Tavotto 预检。无头起 `tavotto mcp`，对改后的图跑 `tavotto_preflight(journal_id)`，没有 error 和 warn 才算合规（`pass`）；`pass_all` 另外要求质量提示也全过。
- **条件**：
  - baseline：不改，只判原图。
  - A：agent 只能编辑 `fig.py`、运行 `python fig.py`。
  - B：在 A 的基础上再给 Tavotto MCP。判分恢复 agent 最后的 Tavotto 会话；会话没了，才判 `fig.py`。
- **被测 agent**：`opencode run`，模型 `opencode-go/deepseek-v4-pro`。权限配置只放开编辑文件和 `python fig.py`，其余 shell、联网、外部目录都拒绝，每组跑在独立临时目录里。
- 模型额度用完的那组不记结果，下次 `run` 会补跑。B 组的 `prompt_rev` 字段从 2 起加了「做完别关 Tavotto 会话」，旧记录没有这个字段，按 1 算。

## 跑法

```sh
uv build --wheel -o /tmp/dist                       # 判分与 B 组用的 Tavotto
export TAVOTTO_WHEEL=/tmp/dist/tavotto-*.whl
export AGENT_PYTHON=/path/to/python                 # 给 agent 用，要装 numpy / matplotlib / scipy
export EVAL_RUNS=/tmp/tavotto-eval-runs             # 运行目录（会被清空重建）
python3 scripts/eval/agent_journal/harness.py run baseline
python3 scripts/eval/agent_journal/harness.py run A --jobs 3
python3 scripts/eval/agent_journal/harness.py run B --jobs 3
python3 scripts/eval/agent_journal/harness.py rejudge A   # 判据变了：不重跑 agent，只重判
python3 scripts/eval/agent_journal/harness.py report
```

结果追加在 `results/<cond>.jsonl`，每行一组，字段有 before / after 判分、agent 用时、token、工具调用数。

## 首轮结果（2026-11，DeepSeek v4-pro，20 张图 × 3 本刊，Tavotto 0.21.1）

| 组 | 期刊合规 | 连通用提示也全过 | 平均工具调用 | 平均用时 |
|---|---|---|---|---|
| 原图 | 4/60 | 1/60 | – | – |
| A：agent 单独 | 60/60 | 24/60 | 9.6 | 101 s |
| B：agent + Tavotto | 51/60 | 27/60 | 21.6 | 406 s |

B 组按提示词版本分开：`prompt_rev` 1 是 27/32（5 组都是改完关了会话，修改没导出就丢了，后来由 #1903 修）；`prompt_rev` 2 是 24/28。
没合规的 9 组剩下的都是页宽。`prompt_rev` 2 里 18-inset 三本刊全挂：两组是 `tavotto_fix_to_journal` 已经改到合规，agent 又回脚本修文字重叠、重新打开图，会话里的改动没导出就没了；JMST 那组 `fix_to_journal` 只回 `constraint_conflict` / `unsupported`，没说哪条约束冲突，agent 只好去猜 override 的属性名。

## 第二轮结果（2026-11，DeepSeek v4.1-flash，同样 20 张图 × 3 本刊）

被测模型改为 `opencode-go/deepseek-v4.1-flash`；B 组全部是 `prompt_rev` 2，Tavotto 用的是 #1963 分支打出的 wheel（含 #1903）。结果在 `results/flash/`。

| 组 | 期刊合规 | 连通用提示也全过 | 平均工具调用 | 平均用时 |
|---|---|---|---|---|
| 原图 | 4/60 | 1/60 | – | – |
| A：agent 单独 | 49/60 | 28/60 | 22.2 | 178 s |
| B：agent + Tavotto | 60/60 | 38/60 | 31.1 | 329 s |

表里的判分是用 main（含 #1985）+ #2003 的 wheel 重判的（`rejudge`，没有重跑 agent）。当初跑完时用的旧 wheel 判 A 组只有 33/60：27 组 Tavotto 打不开改后的图，原因是 Flash 改完脚本习惯先 `savefig` 再用 PIL 读回转成 RGB，旧版安全模式拒绝这类脚本。#1985 让它们回退到真实 Python，#2003 补上真实 Python 那一路的 Helvetica 小字兜底；重判后 A 组有 16 组结论变了，B 组不变。

- A 组还剩 1 组打不开（`02-bar-errorbars` × JMST）：agent 把输出改成先存 `_fig_tmp.*` 再转存 `fig.tif` / `fig.png`，脚本里已经没有叫 `fig` 的图，注册表的 stem 对不上，属于判分口径，不是 Tavotto 的问题。
- A 组能判但没合规的 10 组剩下的都是页宽（Nature 3、JMST 7）；B 组 60 组全部合规。
- B 组代价：工具调用多 40%、用时多 85%。`tavotto_open_figure` 247 次（每次运行约 4 次）、`tavotto_close_session` 119 次，`tavotto_health` / `tavotto_guide` / `tavotto_list_journals` 61 / 56 / 51 次——ADR 0189 针对的就是这些。

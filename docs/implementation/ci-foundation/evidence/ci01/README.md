# evidence/ci01/ · CI01 的静态证据

全部产自 worktree 里的改后 ci.yml（commit `7d77494c`，叠在 CI00 基线 `37fb89a1` / 源码 `8b95256c` 之上），日期 2026-09-16。
**没有一条真实 run**：本轮不能 push，调度层面的一切（CIP-008…010）仍是 `not_run`。上一级 CI00 的 `evidence/dag.json` /
`dag_edge_kinds.json` 是 `8b95256c` 的事实，原样不动。

| 路径 | 内容 | 产出方式 |
|---|---|---|
| `dag_edge_kinds_after.json` | 改后 19 条 needs 边的分类 + 行号（指向 `7d77494c` 的 ci.yml）。删掉的 4 条不在表里；frontend → 四个重型 的 4 条从 `verdict-only` 改标 `短预筛`，理由写在各条 `note` | 人工读改后 ci.yml，从 CI00 那份派生 |
| `dag_after.json` | 改后 ci.yml 的 17 个 job、19 条边（含分类） | `python scripts/ci/ci_baseline.py dag --workflow .github/workflows/ci.yml --edge-kinds evidence/ci01/dag_edge_kinds_after.json --out evidence/ci01/dag_after.json`（退出码 0） |
| `dag_diff.json` | CI00 `dag.json` ↔ `dag_after.json` 的机器比对：删了哪 4 条、改标了哪 4 条、除 `needs` 外每个 job 每个字段有没有别的变化（**空**） | 两份 JSON 的集合差（算法写在 `_doc`） |
| `analyze_after_check.json` | `ci_baseline.py analyze` 对改后 ci.yml 的解析检查（退出码 0、86 run、19 边）；**输出本身不进仓库、也不是证据**——用新 DAG 去分解旧 run 是归因错误，理由写在文件里 | 见文件的 `command` |

改后 DAG 的画法、事件 × 层级表、取消 / 并发真值表在上一级的 [`CI01_EVENTS_AND_DAG.md`](../../CI01_EVENTS_AND_DAG.md)。

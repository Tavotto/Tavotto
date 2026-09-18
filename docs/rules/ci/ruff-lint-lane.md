# Ruff（python-lint）这一格的边界

> 原文出自 `.github/AGENTS.md`「门禁纪律」（2026-09-18 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **Ruff（`python-lint`）是快线里最便宜的一格，不是别的门禁的替代品**：
  它只回答「这份 Python 在语法与名字层面立得住吗」（F821/F401/F841 那一类），
  语义问题仍归不变式 / 等价性矩阵 / CompatBench。它经 `CI fast gate` 参与合并
  资格（`needs` 与 `--required` 闭集里都有它，红了或 skipped 都会让 Gate 红），
  **没有新增 required context**。规则集与豁免的唯一出处是 `pyproject.toml` 的
  `[tool.ruff]`——workflow 命令行上不许再写一份，CI 也不许 `--fix`。
  当前状态：**lint、import 排序（`I`）、formatter 三项均已启用**。
  `python-lint` 这一格里 `ruff check` 与 `ruff format --check` 是**两个独立结论**
  （format 那步带 `if: always()`），一次 push 就把要修的都告诉你；CI 只检查、
  永不写回。**没有因此新增第四个 required context**——两条命令在同一个 job 里。
  first-party 靠 `[tool.ruff]` 的 `src` **按目录**判（那几个目录就是运行时真的
  被注入 sys.path 的），不靠一张会漂的名字清单——**但新增一处 sys.path 源码根
  时必须回来审查 `src`**，在已有源码根下加模块则不用动。
  理由与后续见 `docs/ci/ruff.md`。

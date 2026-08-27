# Ruff — Python 快速静态检查

`ruff check .` 是本仓库 Python 改动的**第一层反馈**：全仓 295 个文件、
96993 行，本机实测冷缓存 29 ms、热缓存 12~13 ms（formatter 迁移后重测；
迁移前是 290 个文件 85762 行、20~30 ms / ~10 ms——**行数涨了 12%**，
因为 `ruff format` 把多元素的集合/参数列表一行一个地拆开，
见下面「formatter」一节）。它挡的是「一个拼错的名字、一个删了引用却忘了
删的 import」这类在编辑器里一秒可判、却要消耗一整轮十分钟 CI 才被发现的问题。

**它加速的是开发反馈，不是产品。** Ruff 不在渲染路径上，不参与 worker 协议，
不影响 `script_build_ms` / `canvas_draw_ms` / `manifest_ms` 里的任何一个数字。
渲染性能是另一件事，入口在 `scripts/bench_render.py` 与 `docs/perf-baseline.md`。

## 日常怎么用

```sh
# 开发时：让它替你修
ruff check . --fix && ruff format .

# 提交前：只检查，**与 CI 那一格逐字相同**
ruff check . && ruff format --check .
```

本地跑过后一对再 push，就不会在 CI 上因为格式又红一轮。
`--unsafe-fixes` 会动语义，要逐条看过再用，不是迁移手段。

改完 Python 的顺序是 **Ruff → 针对性 pytest → 完整验证**，别把顺序倒过来：
一个 F821 不值得先跑十分钟矩阵。

`--fix` 只在本地用。CI 那一格只检查、不改树——门禁替你把红的改绿了，就不再是
门禁（`tests/test_merge_queue_workflows.py::TestPythonLint` 钉着这条）。

**`--unsafe-fixes` 不是迁移手段。** 它会动语义（比如直接删掉一个赋值，而右侧
表达式可能有副作用）。接入时那 4 条 hidden fix 全是手工逐个看过再改的，其中
`tests/test_release_workflow_contract.py` 那处正好是反例：`desk = _wf(DESKTOP)`
的绑定没人用，但 `_wf()` 构造时会自检 workflow 形状、切不出预期的 job/step 就
抛——连调用一起删掉，会静默地少验一件事。所以那里保留调用、只去掉绑定。

## 规则集

配置全在 `pyproject.toml` 的 `[tool.ruff]`，**命令行上不再写第二份**
（写了，本地跑的就不是 CI 跑的那一套）。

`select = ["E4", "E7", "E9", "F", "I"]`，逐族的理由写在 pyproject 的注释里。
两件容易踩空的事：

* **`select` 必须显式钉住。** ruff 的默认规则集会随版本变宽——接入时实测
  0.16.4 的默认集在本仓库报出 1000+ 条诊断（RUF100/PLW1510/UP037/FURB167…），
  而更早的默认只有 E4/E7/E9/F。不钉住，一次例行升版就能让门禁一夜变红。
* **`target-version = "py310"` 是下界不是开发机。** 元数据承诺 3.10–3.13，
  静态分析就得按 3.10 做，否则「在 3.10 上根本 import 不了」这类问题永远看不见。

`ignore` 只有 `E701` / `E702` 两条，都是**排版**规则（`if x: y` 一行、分号连写），
属于 formatter 的管辖范围。见下。

## import 排序（`I`）：first-party 靠**目录**判，不靠名字清单

接入那轮推迟 `I`，真正的原因不是它改的文件多，而是**它判错了归属**：
`pathgeom` / `overrides` / `manifest` 这些是 worker 里 `sys.path.insert(0, HERE)`
之后平铺 import 的兄弟模块，ruff 默认当第三方，于是 `import pathgeom` 被排进
matplotlib 中间——而那条空行分隔的正是「外面的世界」与「我们自己的模块」。

解法**不是** `known-first-party` 名字清单。实测那条路要列 39 个名字
（`_common`、`manifest`、`overrides`、`pixelcompare`、`aggregate_gate`、
`compat_corpus`、`tavotto_mcp`……），而且每新增一个平铺模块都得记得回来补一行
——一张一定会漂的清单。改用 `src`：

```toml
[tool.ruff]
src = [".", "src", "src/tavotto/engine", "scripts", "scripts/ci", "tests",
       "codex-plugin/mcp", "codex-plugin/skills/tavotto-figure/scripts",
       "services/telemetry_proxy"]
```

这几个目录**就是运行时真的被注入 sys.path 的那几个**，ruff 按路径解析。判据与
现实是同一个东西，不是它的一份拷贝。

**但它把维护降到了少数几个源码根，不是取消维护**，这条边界要说清楚：

| 情况 | 要不要动 `src` |
| --- | --- |
| 在**已有**源码根下新增模块（如又往 `scripts/ci/` 加一个脚本） | 不用，ruff 按路径自然认出来 |
| 新写一处 `sys.path.insert(0, NEW_ROOT)` 并从那里平铺 import | **必须回来审查这张表**，否则那些模块会被排进第三方组 |

漏了第二种的表现很轻但很烦：那个目录的模块被排到 matplotlib 那一组里，
`ruff check` 照样绿（它只管排得对不对，不管归属判得对不对），只有人读 diff
时才觉得别扭。

### `combine-as-imports = true` 是必须的

ruff 的 isort 默认 `false`，会把带 `as` 别名的成员**拆成一条条独立语句**：

```python
from tavotto.engine import (config as engine_config, handoff as engine_handoff,
                            patchspec, pool as engine_pool, ...)
```

会变成七条各自带括号的 `from tavotto.engine import (...)`，比原样难读得多。
`codex-plugin/mcp/tavotto_mcp/bridge.py` 是实测出这条的地方——**先看 diff 再
提交**，`--fix` 全绿不等于结果可读。

### 合并 import 会让挂在成员行上的 `# noqa` 失效

`src/tavotto/app.py` 里两条 `from . import x  # noqa: E402` 被合并之后，诊断落在
`from . import (` 这一**语句首行**上，而 noqa 还留在成员行——E402 当场复活。
正确形态是把 noqa 挂到语句首行：

```python
from . import (  # noqa: E402 —— 必须在 app 实例创建之后
    desktop as desktop_mode,
    security,          # 需要 app 实例存在后立即挂钩
)
```

接入时用「noqa 规则码逐个计数、比对前后」核过一遍：除了这一处 90 → 89
（两条合成一条）之外，其余 8 个规则码的数量一个没变。

## formatter：**已启用**（2026-08-27）

当前状态一句话：**lint、import 排序（`I`）、formatter 三项均已启用**
（AGENTS.md / .github/AGENTS.md / CONTRIBUTING.md 三处与此处必须一致）。

### 覆盖面是算得出来的

```
迁移那一刻仓库 .py 294 − 三个内容目录 86 = 208   ← 覆盖数，与 ruff 报的逐位相同
```

`[tool.ruff.format]` 的 `exclude` 与 lint 的 per-file-ignores **是同一批目录**，
理由同一条：那些 .py 的排版是给人看的**内容**，不是我们的代码风格。
两处清单必须一起改——漏一处的表现是 `ruff check` 放过而 `ruff format --check`
报红，而两条门禁说的是同一件事。`TestPythonLint` 对拍这两张清单。

**glob 是实测确定的**：用钉住的 ruff 0.16.4 配 `--force-exclude` 逐个验过，
目录形式（`"examples/"`）**一个文件都排不掉**，必须写成 `**/*.py`。

### `*.md` 也排除了

`ruff format` 会重排 Markdown 里 ```` ```python ```` 代码块，而 `ruff check`
对同一个文件说「No Python files found」——门禁的两半对「什么算 Python」判断不
一致，本身就该消除。更要紧的是那些文件是什么：`docs/release-notes/**` 是**已经
发出去的历史记录**；`docs/adr/0014` 里 dataclass 的行尾注释对齐成一列，那份对齐
就是它想表达的东西；`docs/audit/**` 记的是当时那份复现配方的原样；
codex-plugin 的 `figure-contract.md` 随插件发给用户。

### `docstring-code-format = false` 是显式写出的

即使当前版本默认就是关的。docstring 里的代码片段不该因为将来某次 ruff 升版
自动触发第二轮全仓迁移；真想开的话单独一个 PR 评估。

### 它没有动语义，这一条是证明出来的

205 个文件、约 32000 行的 diff（净增约 9600 行——ruff 把多元素的集合与参数
列表一行一个地拆开，这是它的风格，不是有东西被加进来），靠「测试过了」背书不够。落地前对**每一个**改动
文件做了 AST 比对，并对有差异的逐个审计：

```
AST 逐节点完全相同 201 ／ 仅 docstring 文本有差异 4 ／ 其它 0
```

那 4 个差异都在 docstring 上：两处是 ruff 在 `"""` 后补空格（docstring 以 `"`
开头时），两处是正文缩进被规范化。逐条查过谁在读 `__doc__`——全仓 28 个文件读它，
且**清一色**是 `argparse.ArgumentParser(description=__doc__…)`，读的都是**模块**
docstring；改到的这四处一处都不是模块 docstring（两个函数、一个类、一个函数），
也没有任何测试或产物断言这四段文本。另外全仓 294 个 .py 在 Python
3.10 / 3.11 / 3.13 上逐个 `compile()` 成功；本机没有 3.12，另用
`ruff --isolated --target-version py312 --select E9` 做语法层面的补充（CI 矩阵
同样没有 3.12）。

### `ignore` 整个删掉了

接入那轮豁免过 `E701` / `E702`，理由写明是「formatter 的活，它落地那天就该删」。
formatter 落地了，那 8 处紧凑写法被机械地拆开，豁免的理由随之消失——**豁免要
跟着它的理由一起消失**。没有留 `ignore = []`：一条不说明任何事的配置，只会让
读的人去想「为什么要显式写个空的」。

### git blame

这次动了 205 个文件，纯格式化提交的 SHA 记在 `.git-blame-ignore-revs` 里。
GitHub 网页版自动读它；本地要生效各自配一次：

```sh
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

往那个文件里只加**确实只有工具产出、没有一行人工改动**的提交——夹带了别的东西，
blame 会连那些一起忽略掉，而那正是你将来最想查到的部分。

## 后续（各自独立 PR）

按「先证明有价值，再开」的顺序，不要打包一次做完：

1. ~~`I`（import 排序）~~ —— **2026-08-27 已完成**（用 `src` 而不是
   `known-first-party`，理由见上）。
2. ~~`ruff format` 全仓迁移~~ —— **2026-08-27 已完成**，`ignore` 也随之整个删掉。
3. `B` / `UP` / `SIM` / `RUF` —— 每族先单独跑一遍看噪声比，值得再开。
   `RUF100`（unused noqa）尤其要注意：本仓库有 295 条 `# noqa`，其中
   BLE001 / SLF001 / PLC0415 / N802 这些规则**当前没有启用**，冒然开 RUF100
   会把这批有意义的标注全判成「多余」。
4. pre-commit —— 本仓库目前**没有** `.pre-commit-config.yaml`，为一个工具
   引入一整套 developer lifecycle 不划算。Ruff CLI + CI 这一格已经够用。

## CI

`python-lint`（显示名 **Python quality (Ruff)**）是快线里最便宜的一格：只装一个
wheel，不碰科学栈 / 前端 / Rust，在同一个 job 里跑 `ruff check` 与
`ruff format --check` **两步**（format 那步带 `if: always()`，两条独立出结论）。
**因此没有多出第四个 required context。** 它在 `pull_request` 与 `merge_group`
上都跑，经 **CI fast gate** 参与合并资格：

```
PR / merge_group
   ├─ python-lint   ← Ruff
   ├─ invariants
   ├─ backend-fast
   ├─ frontend
   ├─ workerd
   └─ compat-smoke
            ▼
      CI fast gate        ← ruleset 的 required context（三个稳定 Gate 之一）
            ▼
       Merge Queue
```

**没有新增 required context**：ruleset 认的仍然只有
`CI fast gate` / `CI integration gate` / `CodeQL gate` 三个。python-lint 挂在
fast gate 的 `needs` + `--required` 闭集里，红了 / 没跑（skipped）都会让
fast gate 红——`scripts/ci/aggregate_gate.py` 对两种情况都显式判失败。

CI 里 ruff 的版本**从 `pyproject.toml` 的 dev extra 读**，workflow 里不抄字面量：
两边漂开的表现是「本地绿、CI 红」，那是让人不再信任 lint 门禁最快的方式。
`tests/test_merge_queue_workflows.py::TestPythonLint` 看着这条，以及「这一格
不许变重」「不许在命令行覆盖规则集」「CI 不许 `--fix`」。

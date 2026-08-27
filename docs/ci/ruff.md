# Ruff — Python 快速静态检查

`ruff check .` 是本仓库 Python 改动的**第一层反馈**：全仓 290 个文件、
85762 行，本机实测冷缓存 20~30 ms、热缓存 ~10 ms。它挡的是「一个拼错的名字、一个删了引用却忘了
删的 import」这类在编辑器里一秒可判、却要消耗一整轮十分钟 CI 才被发现的问题。

**它加速的是开发反馈，不是产品。** Ruff 不在渲染路径上，不参与 worker 协议，
不影响 `script_build_ms` / `canvas_draw_ms` / `manifest_ms` 里的任何一个数字。
渲染性能是另一件事，入口在 `scripts/bench_render.py` 与 `docs/perf-baseline.md`。

## 日常怎么用

```sh
ruff check .              # 检查（CI 跑的就是这一条）
ruff check . --fix        # 只应用安全修复
ruff check path/to/x.py   # 只看一个文件
```

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

`select = ["E4", "E7", "E9", "F"]`，逐族的理由写在 pyproject 的注释里。两件
容易踩空的事：

* **`select` 必须显式钉住。** ruff 的默认规则集会随版本变宽——接入时实测
  0.16.4 的默认集在本仓库报出 1000+ 条诊断（RUF100/PLW1510/UP037/FURB167…），
  而更早的默认只有 E4/E7/E9/F。不钉住，一次例行升版就能让门禁一夜变红。
* **`target-version = "py310"` 是下界不是开发机。** 元数据承诺 3.10–3.13，
  静态分析就得按 3.10 做，否则「在 3.10 上根本 import 不了」这类问题永远看不见。

`ignore` 只有 `E701` / `E702` 两条，都是**排版**规则（`if x: y` 一行、分号连写），
属于 formatter 的管辖范围。见下。

## formatter：本轮**有意推迟**

实测过再决定的，不是没做：

```
ruff format --check .   →  293 files would be reformatted, 87 already formatted
```

380 个文件里 293 个会被重写，而且 `line-length` 88 与 100 的结果只差 1 个文件
（292 / 293）——说明差异不在行宽，在括号换行风格、引号、空行这些**结构**上。
把它塞进一个开发工具 PR 会得到几千行与 Ruff 配置本身无关的改动：review 淹掉、
合并冲突暴涨、真正该被看的那几十行配置反而没人看。1.0 收敛阶段更不该这么干。

同样理由推迟的还有 **`I`（import 排序）**：实测会改 53 个文件、约 470 行，
而且它会把多行 import 的括号风格一并重排。更要紧的是它**判错了归属**——
`pathgeom` / `overrides` / `manifest` 这些是 worker 里 `sys.path.insert` 之后
平铺 import 的兄弟模块，ruff 默认把它们当第三方，会把 `import pathgeom` 挪到
matplotlib 那一组里去。真要开 `I`，得先把 `known-first-party` 配对，那是一次
需要单独判断的改动。

`line-length = 100` 已经按实测写进配置（全仓超过 100 列的只有 33 行，0.04%），
所以**将来做 formatter 迁移时不必再重新测一遍行宽**；当前没有任何被选中的规则
消费它，它只作用于 `ruff format`。

## 后续（各自独立 PR）

按「先证明有价值，再开」的顺序，不要打包一次做完：

1. `I`（import 排序）+ `known-first-party` 配对 —— 上面那个归属问题得先解决。
2. `ruff format` 全仓迁移 —— 一次性格式化 + `ruff format --check .` 进门禁，
   落地后 `ignore` 里的 `E701` / `E702` 就该删掉（那两条本来就是它的活）。
3. `B` / `UP` / `SIM` / `RUF` —— 每族先单独跑一遍看噪声比，值得再开。
   `RUF100`（unused noqa）尤其要注意：本仓库有 295 条 `# noqa`，其中
   BLE001 / SLF001 / PLC0415 / N802 这些规则**当前没有启用**，冒然开 RUF100
   会把这批有意义的标注全判成「多余」。
4. pre-commit —— 本仓库目前**没有** `.pre-commit-config.yaml`，为一个工具
   引入一整套 developer lifecycle 不划算。Ruff CLI + CI 这一格已经够用。

## CI

`python-lint` 是快线里最便宜的一格（只装一个 wheel，不碰科学栈 / 前端 / Rust），
在 `pull_request` 与 `merge_group` 上都跑，经 **CI fast gate** 参与合并资格：

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

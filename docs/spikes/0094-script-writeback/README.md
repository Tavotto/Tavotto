# ADR 0094 spike：把 override 写回原脚本（savefig 钩子块）

本目录是 [ADR 0094](../../adr/0094-script-writeback.md) 的可行性证据：一个**原型**生成器与四份测量脚本，外加跑出来的结果。
它不在任何产品代码路径上，也不被产品 import。

## 文件

| 文件 | 作用 |
| --- | --- |
| `emit.py` | 原型生成器：一组 override → 「Tavotto 调整」代码块（只用 matplotlib 公开 API）；按文件编码 / 换行 / 缩进插入、删除 |
| `run_spike.py` | 主测量：热态（一次性 worker 应用 override）vs 写回后的新脚本（另一个一次性 worker、只带留作 override 的条目），用写回事务 verify 的同一把尺（`app._compare_manifests` + `pdfbackend.compare_png` + `app.REPLAY_PIXEL_TOL`）比 |
| `analyze_literals.py` | 方案 B（就地改字面量）对同一批 override 能表达多少：真跑脚本、记下每个 artist 的创建调用与执行次数，按 A/B/C/D 四类分 |
| `legend_ast.py` | 覆盖率路线 2(b)：就地改 / 新增脚本里唯一那处 `legend(...)` 的 `fontsize=`，按同一把尺比 |
| `bytes_checks.py` | 编码 / 换行 / BOM / 缩进矩阵；在不 import Tavotto 的纯 matplotlib 进程里跑写回后的脚本；目标身份守卫（重排 / 删除 / 无 label 插入） |
| `evidence/results_round1.json` | 第一轮主结果（8 份脚本、82 条单条 + 8 组组合；两条覆盖率路线都没开，`--no-routes`） |
| `evidence/results_routes.json` | 第二轮：开了路线 1（标题拖动）与 2(a)（公开 API 重建图例） |
| `evidence/results_legend_ast.json` | 第二轮：路线 2(b)（源码层改 `legend(fontsize=)`）的 6 条 |
| `evidence/results_nofix.json` | 反证：未修正的 worker 上跑三份 `paper_style` 用例（钩子不触发） |
| `evidence/results_naive.json` | 反证：图例整体字号按「逐条改字号」翻译（verify 必须拒） |
| `evidence/literals.json` | 方案 B 的逐条分类 |
| `evidence/bytes_checks.json` | 字节与守卫检查 |
| `evidence/worker_paper_style_fix.diff` | spike 用来模拟「PR 1 前置修正」的那一行 worker 补丁（只打在 scratch 的源码副本上） |

## 用例

| 用例 | 脚本 | 形状 |
| --- | --- | --- |
| `fig1_kinetics` / `fig2_correlation` / `fig2_yield` | `examples/figures/`（`paper_style.save` 保存） | 函数封装；savefig 在另一个模块里；一个脚本两张图 |
| `pg_spectrum` / `pg_kinetics` / `pg_calibration` | `web/src/playground/examples/` | 顶层脚本、注释箭头、`tight_layout` |
| `user_a` | 用户真实脚本甲的副本（**未入库**） | 五个 axes 的 GridSpec、两个色条、循环里给两个子图画同样的线、`canvas.draw()` 后按最终位置摆色条、savefig 后 `plt.close` |
| `user_b` | 用户真实脚本乙的副本（**未入库**） | 循环按文件画 2×N 条线、f-string label、`rcParams.update`、`tight_layout` + `bbox_inches='tight'`；原脚本用 `input()` 交互选输入、依赖一个第三方分析库——副本把 `__main__` 段换成固定选择、给那个库配了一个出合成数据的替身，**import 之后到 `__main__` 之前逐字未改** |

用户脚本只读、复制到 scratch 后使用，原目录零写入；证据文件里用户用例的 stem 与代码片段已替换成占位。

## 结果（`evidence/*.json` 的汇总）

### 第一轮（`results_round1.json`）

**单条（82 条，5 类）**：通过 69（84%），按表不写 13（标题拖动 7、图例整体字号 6），**被 verify 拒 0，出错 0**。

| 类别 | 通过 | 按表不写 |
| --- | --- | --- |
| 颜色（曲线、文字） | 9 | 0 |
| 线宽 | 7 | 0 |
| 字号（标题、轴标签、文字、刻度、单条图例文字） | 31 | 6（图例整体字号，T3） |
| 文字位置（注释文字、轴标签拖动） | 10 | 7（标题拖动，T3） |
| 图例位置（预设位置、拖动） | 12 | 0 |

**组合（每份脚本一组全部 override）**：8/8 通过，写回后的脚本 + 留作 override 的 1–3 条，与热态的探针 PNG **逐字节相同**。

**方案 B（同 82 条）**：唯一字面量 18（22%）、共享字面量 2、表达式 27、不在调用里 35。

**反证**：
- 未修正的 worker（`paper_style` 捷径不经 `Figure.savefig`）：三份用例 25/25 条全部被 verify 拒——钩子在 Tavotto 里不触发，零改动。
- 图例整体字号按逐条翻译：两处单条 + 两组组合全部被拒（几何门：图例 bbox 偏 0.014–0.025；像素门：0.84%–1.95%）。
- spike 过程中的两处真失败也都被 verify 拦下过：曲线改色 / 改线宽时图例示意线没跟着变（像素门，max diff 97–224）、部分写回打乱广播与窄条目的先后（几何门）；生成器修正后通过。

**字节**：UTF-8 LF / CRLF / BOM+CRLF / GBK 声明含中文 / 制表符缩进 / latin-1 声明（自动换 ASCII 块）6/6：编码、换行、BOM 不变，删块逐字节还原，插两次 == 插一次，纯 matplotlib 进程里调整生效且没有 import Tavotto。另测 9 个源文件（全部用例脚本、`paper_style.py` 与用户脚本乙的原版）的混合换行（前三行 CRLF），删块同样逐字节还原。

**守卫**（第一轮）：写回后把两条带 label 的曲线换序 → 调整仍在原曲线上；删掉那条 → 一条 `Tavotto 调整未应用` 告警、脚本照常跑完；无 label 的曲线前面插一条新线 → 调整落到新线上（已知边界，与 ADR 0083 一致）。

### 第二轮：覆盖率路线（ADR §十四）

| 路线 | 条数 | 通过 | 失败形态 |
| --- | --- | --- | --- |
| 1 标题拖动：`set_title(..., y=)` 关自动定位（3.8.4 / 3.11.2 源码同一段） | 7 | 7 | — |
| 2(a) 钩子块里公开 API 重建图例 | 6 | 5 | 用户脚本甲把代理 handle 显式传给 `legend()`，取不回 → 告警跳过 → verify 拒 |
| 2(b) 源码层改 `legend(fontsize=)` | 6 | 5 | 用户脚本乙的图例在子图外、参与 `tight_layout`：字号提前变大改了布局 → 几何 + 像素门拒 |

开路线 1 + 2(a) 后单条 **81/82**；2(a) 与 2(b) 的失败互不重叠，先 (a) 后 (b) 为 82/82。组合 7/8（用户脚本甲那组因为那条图例整组 409）。
2(a) 第一版用 `Text.update_from` 搬文字样子，连 transform 一起抄了，5 条全被几何门拒；改成只搬字体 / 颜色 / 透明度后通过。

## 复现

```sh
W=<本仓库 worktree 的绝对路径>
S=<一个 scratch 目录>
mkdir -p $S/examples && cp docs/spikes/0094-script-writeback/*.py $S/
cp $W/examples/figures/*.py $W/web/src/playground/examples/*.py $S/examples/
# 模拟 PR 1 的前置修正：源码副本打上 evidence/worker_paper_style_fix.diff
mkdir -p $S/src_fix && rsync -a --exclude __pycache__ $W/src/tavotto $S/src_fix/ \
  && (cd $S/src_fix && patch -p1 < $W/docs/spikes/0094-script-writeback/evidence/worker_paper_style_fix.diff)
# 数据目录、配置目录、HOME 三者都隔离到 scratch（只设数据目录时应用仍会写用户真实的配置）
cd $S && export TAVOTTO_DATA_DIR=$S/datadir TAVOTTO_CONFIG_DIR=$S/configdir HOME=$S/home TAVOTTO_NO_TELEMETRY=1
PYTHONPATH=$S/src_fix $W/.venv/bin/python run_spike.py                    # 第二轮（路线全开）→ results.json
PYTHONPATH=$S/src_fix $W/.venv/bin/python run_spike.py --no-routes        # 第一轮口径 → results_round1.json
PYTHONPATH=$S/src_fix $W/.venv/bin/python legend_ast.py                   # 路线 2(b)
PYTHONPATH=$W/src     $W/.venv/bin/python run_spike.py --no-routes fig1_kinetics fig2_correlation fig2_yield   # 反证：未修正
PYTHONPATH=$S/src_fix $W/.venv/bin/python run_spike.py --no-routes --naive pg_kinetics                        # 反证：逐条翻译
$W/.venv/bin/python bytes_checks.py
```

`user_a` / `user_b` 两个用例需要用户脚本的副本，仓库里没有：把副本放进 `$S` 下的目录，再写一个不入库的本地 JSON（`{"user_a": [目录, 脚本, entry, stem], "user_b": [...]}`），用环境变量 `TAVOTTO_SPIKE_USER_CASES` 指向它。解释器是装了 matplotlib 3.11 的 `.venv`；
路径一律绝对路径（worktree 里相对 `PYTHONPATH` 会让子进程跑到主工作区的旧代码）。

## 原型没做、产品必须做的

块里不带机读来源（二次写回合并）、步骤不按 `_apply_rank` 排序、没有 `TavottoAdjustmentWarning` 类、verify 把新脚本写成同目录的临时副本
（产品改为 staging 装载、用户目录零写入）、写法表只覆盖 spike 的 5 类与两条覆盖率路线；路线 2(a) 在生成时不判「条目是不是按 label 自动收集的」，要跑到 verify 才发现。见 ADR §三、§五、§十。

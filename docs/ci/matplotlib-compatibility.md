# Matplotlib CompatBench —— 兼容性资格验证

回答一个此前没有被量化过的问题：

> 一份来自 ChatGPT / Claude / Gemini / Copilot / 普通科研用户、写法不可预测的
> matplotlib 脚本，Tavotto 到底有多大概率能够正确发现、执行、捕获、打开、
> 识别、编辑、撤销、重放并导出？

它是 Tavotto 1.0 发行资格验证的正式组成部分（在整体流程里的位置见
`docs/ci/release-qualification.md`）。

---

## 1. 与 `tests/acceptance/` 的区别

两套 corpus 回答的**不是同一个问题**，共享工具但语义必须分开：

| | 问什么 | 参照物 | 红了意味着 |
|---|---|---|---|
| `tests/acceptance/`（golden 回归） | 已支持的行为有没有退化？ | **Tavotto 昨天** | 我们弄坏了什么 |
| `tests/compat/`（CompatBench） | 外部 matplotlib 世界我们兼容多少？ | **原生 matplotlib** | 我们从来就没支持 / 刚刚支持得更差了 |

合并之后就再也分不清「我们退步了」和「我们本来就不支持」——那正是这套东西
存在的理由。golden 回归抓不到「Tavotto 从第一版起就一直错误地修改某个
artist」，因为它比的两侧都是 Tavotto。

---

## 2. 目录

```
tests/compat/
├── README.md          怎么加一个 case（给写 case 的人）
├── manifest.json      **意图**：这个 case 为什么存在、期望是什么
├── matrix.json        **版本目标**：在哪几套 Python/matplotlib 上验
├── baseline.json      **观测**：今天实际是什么样（committed，人工 review）
├── assets/            确定性数据文件（csv / png / npy / json）
└── cases/
    ├── script_shapes/     脚本形态（入口方言、savefig 写法、相对路径读盘…）
    ├── core_artists/      matplotlib 的绘图 API 与 artist
    ├── axes_layout/       坐标轴、刻度、布局引擎
    ├── scientific_stack/  numpy / pandas / scipy / seaborn / Pillow / 排版
    └── metamorphic/       同一张视觉结果 × 不同代码组织方式
```

三个 JSON 各回答一个问题，**谁都不许兼任**。

---

## 3. 漏斗，不是一个百分比

一个「92%」会把「产品刻意不支持」「环境缺字体」「我们的 bug」揉成同一个数字，
而这三件事的处理方式完全相反。所以报告输出**九级漏斗**：

```
discover → execute → capture → open → semantic → edit → replay → export → fidelity
```

| 阶段 | 判据 | 走哪条路 |
|---|---|---|
| discover | `discover.merge` →（必要时）`probe.probe_and_register` → 注册表里查得到这个 stem | 产品自己的发现链 |
| execute | 脚本跑通 | 真 worker（`pool.one_shot`） |
| capture | 捕获到的 figure 数与 stem 与清单一致 | 真 worker |
| open | manifest 有元素、无 warning | 真 worker |
| semantic | 清单声明的角色与可编辑字段都在 | manifest |
| edit | 应用 → manifest 反映 → 撤销 → 回到原值，全程无 warning | 真 worker |
| replay | 热态 == 清空重放 == 全新 worker 重放 | `app._compare_manifests`（与写回同一把尺） |
| export | PDF/PNG 真导出、体积合理、**解得开**、无 warning | 真 worker + PyMuPDF / Pillow |
| fidelity | **原生 matplotlib** vs **Tavotto 零 override** | 见 §5 |

**分母是「本该跑到这一级的 case 数」**，不是全部 case：execute 就崩了的 case
不进 export 的分母，否则后面几级互相污染，读报告的人分不清「没跑」和
「跑了没过」。

---

## 4. 分类（六选一，闭集）

| 分类 | 含义 | 要求 |
|---|---|---|
| `full_support` | 全绿 | — |
| `partial_support` | 跑得起来、认得出来，但**有缺口**，将来可能补 | 必须写 `reason` |
| `unsupported_by_design` | 缺口是**产品决定**，不打算补（3D 数据、改数据/改结构 → 回代码） | 必须写 `reason` |
| `environment_dependency` | 缺字体 / 缺可选包，不是引擎问题 | 必须写 `reason` |
| `product_bug` | Tavotto 自己的缺陷，**待修** | 必须写 `reason` **和** `follow_up` |
| `invalid_fixture` | case 自己写错了 | 必须写 `reason` |

`partial_support` 与 `unsupported_by_design` 的区别很要紧：前者是路线图上的
候选，后者是产品边界。混成一个数字的话，「值得补的缺口有多少」就再也答不了。

`product_bug` 另记**阶段**（`product_bug:capture` 与
`partial_support:unsupported_artist` 是两回事）。

### 只有被声明过的失败才不算缺陷

这是整套东西的核心纪律：

* 清单里没有声明过的失败 → **一律 `product_bug`**；
* 想声明某一级不该过，要具体到阶段：`expected.<stage> = false` +
  `expected_false_reasons.<stage>`；
* 光写 `classification: unsupported_by_design` 不够——那样这个 case 从此
  无论哪里坏掉都是绿的；
* **`execute` / `capture` / `open` 任何档位都不许声明成 false**。跑不起来 /
  捕获不到 / 打不开就是不兼容，理由再充分也得记成 classification，
  让它出现在报告里。

---

## 5. 零 patch 原生保真度（native control）

> **没有任何 override 时，Tavotto 不应该偷偷改变用户的 Figure。**

对照组走 `scripts/ci/compat_driver.py --mode native`：普通 matplotlib
（`MPLBACKEND=Agg`、cwd = 脚本目录，就是 `python figure.py` 的语义），
`savefig` 只**记录**、随后**照常调用真实实现**——这一点与 Tavotto 的拦截
正相反：拦截是为了不写用户的文件，对照是为了拿到「没有 Tavotto 时这张图长
什么样」。

比较复用 `scripts/ci/pixelcompare.py`，与 golden 视觉回归**同一份算法**
（变化像素占比 / 平均绝对差 / 最大绝对差 / 噪声底噪 3）。阈值在
`compat_matrix.FIDELITY_TOLERANCE`，比 golden 松一档——两侧的 PNG 由两个进程
分别编码，字体 hinting 与光栅化的舍入有极小的系统性差异。实测绝大多数 case
是**逐像素相同**（changed_ratio = 0.0），所以这一档并没有因为放宽而失去意义。

保真度在**任何编辑之前**量：零 patch 的定义就是「还没动过」。放在
edit/还原之后的话，一个还原不干净的 case 会把自己的污染算成
「Tavotto 偷偷改了用户的图」。

### 什么时候可以关掉它

只有两种正当理由，各有一个现存的例子：

**① 参照物本身不稳定** —— `ax_constrained_layout`。实测（本机 mpl 3.10.8，640px）：
**原生 matplotlib 把同一个 Figure 连存两次，第二张与第一张的 changed_ratio
就有 0.099**——constrained_layout 的布局引擎每帧迭代逼近，还没收敛。
Tavotto 与原生的差是 0.035，比原生自己两次之间的差还小。这里没有一个稳定的
参照物可比，不是「Tavotto 改了用户的图」。

**② 参照物随与产品无关的东西变** —— `sci_cjk`。CJK 字形与度量随字体包版本
变化，像素基线会在一次 fonts-noto-cjk 升级里整片变红。结构 / 编辑 / 重放 /
导出照验——中文必须能画出来；只是不拿它做像素判据。（与 `tests/acceptance`
的 `c03_cjk` 同一条取舍。）

这两条之外一律不许关。要关就得在 `expected_false_reasons` 里写清楚
**为什么这个 case 没有可比的参照物**，而不是「它老是红」。

---

## 6. 版本矩阵

`tests/compat/matrix.json` **只写「去哪读版本」，绝不复制版本号**——版本锁是
唯一输入（见 CLAUDE.md），复制一份必然在某次升级里悄悄漂开，而漂开的表现是
「CI 上验的 matplotlib 和用户拿到的不是同一个」。

| target | 来源 | required | 说明 |
|---|---|---|---|
| `current` | 当前环境 | ✗ | 本地开发用，不做版本断言，**不能当发行判据** |
| `minimum` | 写死精确版本 | ✓ | pyproject 宣称 `matplotlib>=3.8`，那条下界必须自己有一档在验。`pip install matplotlib>=3.8` 在 CI 上等于装 latest，那样什么都没验 |
| `bundled` | `packaging/runtime-lock.json` | ✓ | 桌面版内置 runtime（用户真正拿到的） |
| `browser` | `packaging/playground-runtime.json` | ✓ | 浏览器 playground，只跑 `browser_eligible` 子集 |

**minimum 这一档不是摆设**：它第一次跑就抓到一个 Tier 1 缺陷——matplotlib
3.8 的 `PolyCollection.get_window_extent()` 回 `-inf`（3.10+ 换成
`FillBetweenPolyCollection` 才自带可用的框），于是 `fill_between` /
`fill_betweenx` / `stackplot` 的**整片填充区在界面上不存在**，而 pyproject
宣称的下界正是 3.8。同一轮还抓到 corpus 自己的一个 fixture bug
（`boxplot(tick_labels=…)` 是 3.9 才有的关键字）。两件事都只有在真的跑一遍
下界版本时才会出现。

**browser 这一档如实记账**：CompatBench 跑的是 `engine/browser.py` 这**同一份
代码**在 CPython 上的行为（与 `tests/test_browser_session.py` 同一条纪律：
语义与解释器无关）。真 Pyodide + 真 CDN 的那一步在
`web/e2e/playground.spec.ts`，不在这里。别把它当成「Pyodide 上跑过了」。

---

## 7. Browser / Desktop 语义对拍

只比语义，不比像素：字体栈、matplotlib 版本、WASM 后端都会造成合理的像素
差异；**语义随入口改变才是事故**。比四样：捕获到哪些 stem、有哪些角色、
**完整的可编辑属性集合**、以及同一组 patch 的规范化哈希（父进程与浏览器侧
各算一遍，必须逐字相同——`engine/patchspec.py` 只有一份实现）。

**分叉一律让门禁红，不分档位。** 早期版本把对拍结果只写进报告的一节
「Browser / Desktop semantic divergence」，而 `evaluate_gate()` 只看 stages 与
classification——于是它把分叉打印出来、然后说「通过」。一个这样的门禁比不
检查更坏：它让人以为这件事有人看着。

钉死的一条：`plt.plot(...) + plt.show()`（无 savefig）两个入口都必须捕获，
fallback stem 逐字相同。看护在 `tests/test_compat_capture_parity.py`。

**刻意保留的一条差异**：桌面的 `entry` 机制是超集，浏览器按
`python figure.py` 跑。只有 `def main():` 而没人调用的脚本在原生 Python 下
也什么都不画，浏览器捕获不到是对的。因此 `browser_eligible` 是**推出来的**
（脚本作为独立文件跑就出图 + 不需要数据文件/本地 helper），不是随手勾的。

---

## 8. artist 普查（诊断，不是门禁）

`compat_driver.py --mode census` 在 instrument 之后走一遍 artist 树，统计每个
类出现了几次、其中几个拿到了 gid。它的产出是 **Tavotto 的产品路线图**——
「哪个 matplotlib artist 是最大的兼容缺口」——不是 pass/fail 依据。真正的
兼容判定一律走生产路径的 worker：**诊断探针替代不了门禁**，那会变成
「我们自己写的尺子量自己」。

普查**剪枝**坐标轴零件（XAxis/YAxis/Tick/Spine/Legend 内部、offsetbox 排版盒）
与空文字。第一版没剪，Top-N 是 `Line2D 7964 / Text 6553`（每条刻度线与每个
刻度标签），真正的缺口被整个挤出了视野。

---

## 9. 基线

`tests/compat/baseline.json` 是 **committed 资产**，纪律与视觉基线一样：

* **缺失 = FAIL**，绝不当成空基线放行（「没有基线 → 生成一份 → 报绿」是
  第一次跑永远通过、什么都没验证）；
* CI **绝不**自动更新。只能显式：
  ```bash
  python scripts/ci/compat_matrix.py --all --update-baseline
  ```
  且 `CI=true` 时该参数被硬拒（退出码 2）；
* 任何基线更新必须进 code review；
* 基线里**不许有时间戳**——它每次都变，会把真正的分类变化淹没在 diff 噪音里。

### 基线不是豁免名单

禁止「case 红了 → 加进 expected_failures → CI 变绿」。所以：

* 任何非 `full_support` 的分类都必须有**非空 reason**（schema 强制）；
* `product_bug` 还必须有 `follow_up`——它是待修缺陷，不是可以长期接受的状态；
* **Tier 1 的 product_bug 一律不许进基线**（schema 强制）；
* 分类比基线**退步**（`full_support` → `unsupported_by_design` 这种）直接红。

---

## 10. Tier 与发行门禁

初期不用一个武断的「overall ≥ 95%」卡版本，改用档位：

| Tier | 是什么 | 要求 |
|---|---|---|
| **must** | 标准 matplotlib 的高频路径（line / scatter / bar / hist / imshow / legend / colorbar / text / subplots / 常见布局 / `plt.show`-only） | execute / capture / open / export / 已知编辑目标 100%，**product_bug 0** |
| **expected** | 常见长尾 | 允许 partial；execute/capture/open 原则上必须成功 |
| **exploratory** | 罕见 artist / 别扭 API | 可以 partial 或 unsupported_by_design，**但不能让 worker 崩** |

门禁按运行档位收紧（`compat_matrix.GATES`，pr ⊆ main ⊆ nightly ⊆ release）：

| gate | Tier 1 必须 100% 的阶段 | 已知 product_bug |
|---|---|---|
| `pr` | execute / capture / open | 容忍（基线里的） |
| `main` | + export / edit | 容忍 |
| `nightly` | + replay | 容忍 |
| `release` | + fidelity | **一个都不容忍** |

任何档位下，**新出现的** product_bug 一律红。

### 1.0 exit rule

```
P0 compatibility bugs = 0
```

P0 包括：常见标准脚本无法执行 / 常见 Figure 无法捕获 / 打开后画面明显错误且
无 warning / 零 patch 改变 Figure / edit 后 replay 与热态不一致 / 导出损坏 /
Browser 与 Desktop 同语义脚本表现完全不同 / 用户文件可能被 sandbox 处理破坏。

P1 可以存在，但必须 **known / classified / documented / non-silent**。

---

## 11. CI 编排

| 场次 | 跑什么 | 目标耗时 |
|---|---|---|
| PR / 常规 CI | `--smoke --no-fidelity --gate pr` | 2~4 分钟 |
| push main | `--tier must,expected --gate main` | 十几分钟 |
| nightly | `--all --fidelity --browser --gate nightly` + 版本矩阵 | 不限 |
| release | 全量必需矩阵 + `--gate release` | 不限 |

失败时上传诊断 artifact（`compat-report.json` + 失败 case 的
`control.png` / `tavotto.png` / `diff.png`）。**只对失败 case 上传重文件**，
否则成功的 100+ case 会把 artifact 撑爆。

### 把 `compat-smoke` 设成必需检查：**顺序不能反**

它跑而不拦的话就是空转，所以最终要进 main 的 ruleset 必需检查清单。但那一步
必须**在这个 job 已经落到 `main` 之后**做——

> 分支保护要求的检查，是按**名字**匹配的。名字对应的 job 只存在于某个还没
> 合并的分支时，`main` 上开出来的每一个 PR 都永远等不到它上报，
> `mergeStateStatus` 恒为 `BLOCKED`。**不是某个 PR 有问题，是门禁指向了一个
> 还不存在的 job。**

这不是假想：2026-08-21 就是这么把仓库里当时全部三个 open PR 一起锁死的，
而唯一能满足那条规则的恰恰是**引入这个 job 的那个 PR 自己**——于是别人的
PR 被迫排在它后面，而这个依赖是凭空造出来的。

正确顺序：**先合带 job 的 PR，再把检查名加进 ruleset。** 与遥测那条
「先发代理、再发客户端」（代理会拒绝不认识的事件，反过来新事件会被静默 400）
是同一个形状：**消费方先就位，再让生产方指过去。**

改 ruleset 之前先 `gh api repos/<owner>/<repo>/rulesets/<id> > backup.json`，
改完逐项对账只有该变的变了——它是仓库级设置，影响的是所有人。

---

## 12. 常用命令

```bash
python scripts/ci/compat_matrix.py --smoke              # PR 档
python scripts/ci/compat_matrix.py --all                # 全量（默认带保真度）
python scripts/ci/compat_matrix.py --target bundled     # 指定版本目标
python scripts/ci/compat_matrix.py --case shape_pyplot_show_only
python scripts/ci/compat_matrix.py --tier must --gate main
python scripts/ci/compat_matrix.py --browser            # 加上桌面/浏览器对拍
python scripts/ci/compat_matrix.py --smoke --list       # 只列出选中的 case
python scripts/ci/compat_matrix.py --all --update-baseline   # 本地，人来读
```

单元看护：

```bash
.venv/bin/python -m pytest tests/test_compat_manifest.py \
                           tests/test_compat_runner.py \
                           tests/test_compat_capture_parity.py
```

---

## 13. 怎么加一个 case

1. 在 `tests/compat/cases/<category>/` 里写脚本。设计原则见
   `tests/compat/README.md`——短、可读、数据确定、不联网、不看时间、
   不依赖 HOME、不写用户目录、**不使用 Tavotto 私有 API 构造 Figure**。
   它应该看起来像真正用户写的 matplotlib 脚本。
2. 在 `manifest.json` 里加一条，写清楚 `notes`（为什么存在）、
   `semantic_expectations`（必须认出什么）、`mutations`（1~5 个代表性编辑）。
3. `python scripts/ci/compat_matrix.py --case <id>` 跑一遍。
4. **红了先想是不是产品 bug**，不要先改期望。真是产品边界的，写
   `classification` + `reason`（必要时 `expected.<stage>=false` +
   `expected_false_reasons`）。
5. `--all --update-baseline`，**逐条读 diff**，再提交。

## 14. 怎么给一次失败定性

```
跑不起来 / 捕获不到 / 打不开        → 一律先当 product_bug 查
某个 artist 认不出来               → partial_support（记进 artist 普查）
产品明确不做（改数据 / 改结构 / 3D 盒内属性）→ unsupported_by_design
缺字体 / 缺可选包                  → environment_dependency（写清环境前提）
清单写错了（属性名不存在等）        → 改清单，不是改产品
```

**最不能接受的是「看起来成功，但 silently wrong」。** 当某个 case 不支持时，
正确的结果可能就是 `partial_support` 或 `unsupported_by_design`——

> Do not optimize Tavotto to pass the benchmark.
> Build the benchmark so Tavotto is forced to tell the truth.

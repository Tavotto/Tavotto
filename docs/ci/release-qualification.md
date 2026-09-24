# 发行资格验证（Release Qualification）

这一层回答的问题与 PR 门禁不同。

`ci.yml` 回答的是：**这次改动有没有破坏已知行为？**（跨平台、快、每个 PR 都跑）

这一层回答的是：**这个包发出去，用户装上会怎样？**——那些需要长时间、需要
持久磁盘、需要固定机器才能问出口的问题：

- 跑几百次会不会漏句柄、漏进程、越跑越慢？
- 上一版的用户升上来，他的项目还打得开吗？
- 图和上次发的那版长得一样吗？
- 比上个版本慢了吗？
- 装出来的**这一份** wheel 真的能用吗？

实现全在 `scripts/ci/`，步骤定义只有 `.github/workflows/_lab-qualification.yml`
一份。**2026-09-16 起（F 组）实验室 runner 注册在私有仓库 `Tavotto/ci-infra` 上**：
`lab-ci.yml` 的 `trust-check` 验完 SHA 后由托管机 `dispatch` 到 ci-infra 的
`lab-qualification.yml`，那边再 `uses` 这份 reusable；结论以 commit status
`lab/<mode>` 回到被验的那个 SHA 上，`lab-ci.yml` 自己**不等结果**。发布链同样
不等：它在 lab 门禁处**切成两段**（PR B，`ADMIN_HANDOFF_RUNNER_POOL.md` F.2）——
`release.yml` 到派发 lab 为止，`release-publish.yml` 由 ci-infra 在 lab 绿后派发，
先重验再发布，见下面「发行链上的 gate」。机器的准备与两枚 secret 见
[`self-hosted-runner.md`](self-hosted-runner.md)。

---

## 总体位置

```
                  Public PR
                     │
                     ▼
          GitHub 托管 CI（ci.yml）
          Linux/macOS/Windows · 前端 · workerd
          wheel 冒烟 · 真产物冒烟 · CodeQL
                     │
                  merge main
                     │
          ┌──────────┴───────────┐
          ▼                      ▼
   GitHub 托管检查          Lab Qualification
                            16C / 32G Linux
                                 │
                     ┌───────────┼────────────┬────────────┐
                     ▼           ▼            ▼            ▼
                   slow        golden        soak       compat
                     │           │            │            │
                   upgrade     visual       leaks     native fidelity
                     │           │            │       browser parity
                     └──────┬────┴──────┬─────┴─────┬──────┘
                            ▼           ▼           ▼
                         benchmark    reports   compat-report.json
                            │
                            ▼
                        Release Gate
                            │
                  ┌─────────┴──────────┐
                  ▼                    ▼
             GitHub Release           PyPI
                  │
                  ▼
          Windows/macOS 桌面
          （desktop-tauri.yml，原样保留）
```

**Linux runner 不替代任何现有门禁。**`windows-latest` 与 `macos-latest` 上的
真产物冒烟、NSIS 安装器、`.app` 签名公证、updater 清单——全部原样留在
`ci.yml`、`nightly.yml` 与 `desktop-tauri.yml` 里。本层只做加法。

---

## 四个档位

| 档位 | 触发 | 内容 | 目标墙钟 |
|---|---|---|---|
| `main` | push 到 main | 常规套件 + slow + 小 golden + 100 轮 soak + 基础泄漏 + **CompatBench（must+expected，无保真度）** | ≤ 15~20 min |
| `nightly` | 每日 19:00 UTC | 上面全部 + 前端/Rust + 完整 golden + 视觉回归 + 500 轮 soak + benchmark + **CompatBench 全量（保真度 + 浏览器对拍）** | ≤ 30~45 min |
| `release` | 打 tag / 演练 dispatch（`release.yml::dispatch_lab` 派发） | 候选包验收（取第一段 run 的 `dist`） + slow + 升级 + 完整 golden + 800 轮 soak + 性能（不写基线） + **CompatBench `--gate release`** | — |
| `weekly` | 周日 20:00 UTC | 上面全部 + mutation | — |

PR / 常规 CI（`ci.yml`）另跑 **CompatBench 的 smoke 子集**（`--gate pr`，
2~4 分钟）——nightly 那份 150 case × 多运行时的东西绝不塞进每个 PR。

档位由 `trust-check` 根据触发方式判定，手动触发时可显式指定。

---

## 各环节

### 开跑前体检 `lab_preflight.py`

长期 runner 会累积状态。这些东西不会让 job 立刻失败，只会让后面的数字失去
意义：上一轮崩掉留下的 worker、快满的磁盘、被改掉的 locale、太低的 FD 上限。
**在源头报出来，比在十分钟后拿着一份可疑报告猜要便宜得多。**

每一项失败都附带可执行的处置建议——「preflight failed」本身不解决任何问题。

遗留进程的归属判定**只认 CI 持久化根与 runner 工作目录出现在命令行里**，
不按进程名。`pgrep tavotto` 会误伤维护者自己开着的实例，而假报一次之后，
这条提示下次就会被无视。

### 候选包验收 `lab_acceptance.py`

全部价值在 **exact bytes** 四个字上：它装的是 `build` job 产出的**那一份**
wheel，**不重新 build**。重新 build 测的是「同一个 commit 能造出一个能用的包」，
而发出去的是另一次构建的产物。

- 结构断言（快）：`import tavotto`、版本号、包内 `web/index.html`、
  `engine/worker.py`、`profiles/publication.json`、console script、
  `doctor --json`
- 行为验收（慢）：直接调用既有的 `scripts/smoke_app.py`，走完整用户路径

**不另写一套启动/渲染协议**——smoke_app 就是用户真实路径的那一份，再造一份
只会让两边慢慢跑偏，而跑偏那天没人会发现。

### 常规测试套件（同机 N 片并行）

全集（`pytest.ini` 的 `-m "not slow"`，约 4900 条）单进程在 lab 的 16 核 VM 上要
**46 分钟**（2026-09-19 实测），15 个核闲着；main 档 57 分钟里它占 47，单 runner
一天 8 次合入，13:11 派发的 run 14:01 才起跑。2026-09-19 起改成同一台机器上起 N 个
`--shard="$k/$N"` 进程（CI03a 的按文件分片，`docs/rules/ci/pytest-shards.md`）：

- **覆盖面不变**——每个进程在自己的 collection 之后算出全部 N 片，自验并集 == 全集，
  漏片 rc 4；四片的并集就是原来那一整个集合，被切开的只有时间。
- **判据 = 每一片的退出码**：逐个 `wait`，任一片非零整步红，并把那一片的日志尾巴
  打出来；每片各自的 basetemp / junit / manifest 带片号，失败时进诊断包。
- 不用 xdist：按用例分发会把跨用例耦合一次性摊开，按文件分已经够。
- 同机并行的隔离**靠实跑证明**：本机 12 核四片并行一次、lab 首跑一次（结果见下）。

实测：

- 本机（12 核 Mac mini，把 YAML 里的 step 脚本原样抽出来跑）：4994 条四片 252 / 258 / 354 / 291 秒，
  整步 **354 秒**，0 失败——同一台机器单进程约 20 分钟。
- lab（main 档，ci-infra run）三次：首跑 `35486968045`（`62eb7f7a`）四片 803 / 412 / 950 / 734 秒、
  整步 **15 分 51 秒**，片 1 红 1 条——`test_cli_passes_on_a_healthy_root` 真跑 `lab_preflight.main()`，
  「遗留进程」那格扫整台机器的 `/proc`，别的片的 worker 被当成遗留（#437 把那格也桩掉）；第二次
  `35490778605`（`75483a45`）四片全绿 16 分 00 秒；权重表按第二次的 junit 重算（#441）后第三次
  `35494827553`（`d071dc26`）四片 727 / 744 / 743 / 735 秒、整步 **12 分 26 秒**，四片相差不到 20 秒。
  main 档 qualify 整条 57 → **28 分钟**。
- 本机第一轮抓到两件与并发无关的事，都在合入前处理：`--basetemp` 指到路径含 "tavotto" 的目录
  会让两条「输出里不许出现 tavotto」的用例撞词（所以不指定 basetemp）；`&` 起的片继承
  SIG_IGN 的 SIGINT 会让 Ctrl-C 用例 90 秒超时（所以先 `set -m`，见 workflow 注释）。
- 权重表先按本机四份 junit 重算（2026-09-19，四片估计各 286 秒），lab 首跑仍 412 / 950 秒不平衡；
  2026-09-20 再按 lab 自己的 `lab-pytest-shards-main-35490778605` 重算（#441），才有上面 727 / 744 /
  743 / 735 的结果。以后重平衡就从最近一次 lab run 的这个 artifact 算。

### slow / 集成用例

`pytest.ini` 的 `addopts` 是 `-m "not slow"`，所以这里显式 `-m slow`。

workflow 会先 `--collect-only` 数一遍，**一条都没选中就直接失败**：那说明
标记被删/改名，或 `pytest.ini` 的 markers 变了，而门禁会安静地报绿。

> 当前仓库只有 1 条 slow 用例（`test_bootstrap.py::test_real_install_end_to_end`，
> 真建 venv 真装 matplotlib）。这一层的价值目前主要在其它环节；随着 slow 用例
> 增加，这条会自然变重。**没有为了凑数把普通用例标成 slow。**

### 操作序列 harness 放宽种子（nightly 及以上）

`tests/test_override_sequences.py`（§4.2 后端序列 harness）默认 8 条种子 × 2 张图，
PR / merge_group / 上面的常规套件都是这个档。首批三族（#412–#414）8 条就抓到，第四族
（#423）放到 24 条才碰上——所以 nightly 及以上再以 `TAVOTTO_SEQ_SEEDS=32` 单独跑一遍
这个文件。随机负责发现：红了就按它报的最短复现最小化，钉进 `FIXED`（修好的）或
`KNOWN`（开 issue 的），PR 上的默认档不加时长。

首次执行（ci-infra run 35448006749，2026-09-19，手动派的 nightly 档）实测：73 条
（64 条随机序列 + 9 条固定，含 `KNOWN` 空集那条 skip；8 条种子的档是 25 条），**12 分 43 秒**（本机 3.5 分钟；
runner 比本机慢三到四倍，与常规套件 46 分钟对本机的比例一致）。nightly 的 qualify
整条由此从 63 分钟涨到 78 分钟；上表「≤ 30~45 min」在此之前就已经只是目标不是实测。

2026-09-20 起这一步也同机 4 片并行：种子按 `TAVOTTO_SEQ_SHARD=K/N` 取模切片（第 K 片 =
`seed % N == K-1`，文件自己守着两两不交、并集是全集、写错或空片当场抛，
`test_seed_slice_semantics`），固定用例（`FIXED` / 候选池）每片各跑一遍——冗余几十秒，换来
不用再发明「谁跑固定用例」这条规则。种子本身不变：第 5 条种子在哪一片都是同一条序列，
红了报出的复现命令不带片号也能复现。起片 / 收片 / 退出码与常规套件那一步逐字同形，
同一份合同（静态 + 行为，参数化到两个 step）看着。本机 8 条种子四片 46 秒（单进程 57 秒；
8 条时固定用例占大头，32 条时每片 16 条随机 + 10 条固定）；lab 首跑（合入后第一次 nightly）实测待填。

### 升级验收 `upgrade_acceptance.py`

这是临时 runner 最难做、也最有价值的一项——它需要**同一块持久化磁盘上先后跑
两个版本**。

```
上一版 wheel → venv A
    └─ 全新用户根：打开项目 → 渲染 → 改参数 → 存布局 → 导出 → 自动保存 → 干净退出
候选 wheel   → venv B
    └─ 指向完全相同的 TAVOTTO_DATA_DIR / TAVOTTO_CONFIG_DIR / 项目目录
    └─ 再启动一次，第二次也必须正常
```

逐条核对：老项目可打开、老面板还在、老 patches 仍可渲染、元素数量一致、
渲染无 warning、老布局可列出、老自动保存可解析、仍可导出、`app.log` 无
traceback、**用户配置未被静默重置**、无孤儿 worker。

几条刻意的设计：

- **两版之间不删任何用户状态。**「升级后重开」就是用户的真实处境。
- **项目路径带中文与空格**，且在主路径上而非单开一个 case。
- **配置被静默重置算失败。**用户在设置里改过的东西升级后回到默认，比崩溃
  更难被发现——崩溃至少有人报。
- **不凭空发明 state。**写进去的都是产品自己会写的东西（`config.json` 的
  `recent_projects`/`projects`、`layouts/` 下的画布与自动保存、
  `baked_overrides/`、项目内 `tavottofile/`），审计自 `app.py` 与
  `engine/config.py`。
- N-1 的选取排除预发布：用户不会从一个 rc 升上来。
- 上一版的 wheel 走 `api.github.com` 的 assets 端点下载（`releases/download`
  的第一跳是不可达的 `github.com`）。

代价要认：每次跑要装两个 venv 并完整跑两遍应用。这是正确性优先的自觉取舍。

### Golden corpus 与视觉回归 `visual_regression.py`

corpus 在 `tests/acceptance/corpus/`，13 个 stem，每个都对应一类真实用户会遇到
的图形，且都能对上产品里记录过的坑：

| 脚本 | stem | 针对 |
|---|---|---|
| `c01_lines_scatter_bars.py` | line / scatter / bar / errorbar | 最常见形态；散点刻意不给 geometry；bar/errorbar 是 manifest 伪元素 |
| `c02_axes_and_scales.py` | subplots / twinx / loglog / constrained | 子图几何、`set_[xy]scale` 换 locator、色条就地改造 |
| `c03_text_legend_images.py` | legend / annotations / scinotation / image / cjk | 图例重建、有字的 annotate 不出端点、位图 alpha、mathtext |

corpus 脚本有两条与普通示例不同的纪律：**一切数值写死**（不用随机数，
免得 numpy 换代时整片变红）、**不 import `paper_style`**（那是图库方言，
corpus 要验的是对任意 matplotlib 脚本的处理能力）。

比较方式：

- **不比 SHA256，比像素。**PNG 元数据会让字节比对整片变红；逐字节相同这个
  条件又过强。
- 三个指标同时看：变化像素占比、平均绝对差、最大绝对差。任一越界即回归——
  单看比例会漏掉「一小块彻底变了」，单看最大值会被一个抗锯齿像素带偏。
- 噪声底噪 3：抗锯齿与 PNG 量化会让**完全相同的图形**出现 ±1~2 抖动，
  三个指标都先把它扣掉。（这条曾经真的红过：`mean_abs_diff` 当时在全图上算，
  遍布全图的底噪就足以顶穿阈值，而画面一模一样。）

实测的敏感度：

| 情况 | 判定 |
|---|---|
| 完全相同 | 通过 |
| ±2 全图噪声 | 通过 |
| 边缘 ±3 | 通过 |
| 元素挪 6 像素 | **回归** |
| 字号变化 | **回归** |
| 整体亮度 +10 | **回归** |

#### 基线纪律

> **基线缺失 = 失败。绝不自动创建。**

「没有基线 → 生成一份 → 报绿」是典型的假绿：第一次跑永远通过，而它什么都
没验证。基线只能由人显式跑 `--update-baselines` 产生，并在 code review 里被
眼睛看过。脚本还在入口硬拦了 `CI=true` 时传该参数的情况。

基线放在**仓库里**（`tests/acceptance/baselines/`）而不是持久化根——它是需要
review 的资产。放进持久化根的话，谁改了基线、为什么改，全都无从追溯。

#### 例外必须写明理由

`tests/acceptance/manifest.json` 里每一处放宽或跳过都要有理由字段，
`test_ci_qualification.py` 会检查这一点。当前两处：

- `c02_constrained` 放宽阈值：constrained_layout 的落位由 matplotlib 每帧
  重算，色条厚度有亚像素抖动。
- `c03_cjk` 跳过像素比对：CJK 字形随 `fonts-noto-cjk` 版本变化，会在一次与
  产品完全无关的字体包升级里整片变红。**结构与导出照验**——中文必须能画出来。

### Matplotlib 兼容性资格 `compat_matrix.py`

完整说明在 [`matplotlib-compatibility.md`](matplotlib-compatibility.md)。
放在这里的理由：它与 golden 回归**问的不是同一个问题**。

* golden 比的是「Tavotto 今天 vs Tavotto 昨天」——它抓不到「我们从第一版起
  就一直错误地修改某个 artist」；
* CompatBench 比的是「**原生 matplotlib** vs Tavotto 零 override」，并且沿着
  九级漏斗（discover → execute → capture → open → semantic → edit → replay →
  export → fidelity）回答「外部 matplotlib 世界我们兼容多少」。

它同时是 1.0 的 exit rule 载体：

```
P0 compatibility bugs = 0
Tier 1 的 execute / capture / open / export / 已知编辑目标 = 100%
Tier 1 的 product_bug = 0（schema 层面就不许进基线）
```

基线 `tests/compat/baseline.json` 的纪律与视觉基线**逐条相同**：缺失 = FAIL、
CI 绝不自动更新、`CI=true` 时 `--update-baseline` 被硬拒、任何更新进 review。
另外多两条：非 `full_support` 必须写 reason，`product_bug` 还必须写
`follow_up`——**基线不是豁免名单**。

### Soak 与泄漏检测 `soak.py`

**不是** `for 1000: GET /api/version`——那只能证明 HTTP 服务器还活着。真正会
泄漏的是渲染路径。每一轮做用户真会做的事：渲染 → 改参数再渲染 → 每 5 轮导出
一次 → 换面板 → 再来。

启动、请求、孤儿判定全部复用 `scripts/smoke_app.py`；patch 靶子的挑选复用
`scripts/bench_render.py`。**不另写 fake protocol。**

判据分两类，严格程度不同：

- **孤儿进程 —— 硬失败。**跑完之后属于本次运行的 worker / workerd 一个都不该
  剩。归属靠本次隔离数据目录出现在命令行里判定。
- **FD / RSS —— 看斜率，不看终值。**Python 分配器有高水位，要求「结束 RSS ==
  初始 RSS」只会得到一条恒红的门禁。丢掉 warmup 之后做线性拟合，只在**持续
  单向增长**且幅度可观时判定为泄漏。

实测行为：每轮 +1 FD 判泄漏；RSS 线性涨判泄漏；**头几轮涨完就走平判通过**；
样本不足时如实报 `inconclusive` 而不是悄悄算通过。

产出 `soak-metrics.json`，含逐轮的 iteration / rss / fds / processes / latency。

### 性能回归 `benchmark.py`

**测量本身复用 `scripts/bench_render.py`**——那份脚本已经解决过「怎么量才有
意义」（真实 HTTP 链路、冷热分开、取中位数、数据目录每次全新）。这里只补它
没有的那一半：**把数字存下来，并和上一次比**。

固定机器是这条门禁成立的前提，也是它最脆弱的地方：

- **先查污染再测量。**load average 按**每核**判（16 核上 load=4 是空闲，
  4 核上已经满了）。查出来就明确报 `environment_contaminated`，
  **不接受一份没意义的 benchmark**——把「机器忙」当成「Tavotto 变慢了」，
  会让人去优化一个不存在的回归。
- **候选版永不写基线。**release 只比不写；只有 main 模式跑绿之后才滚动更新。
  否则「和基线比」会退化成「和自己比」。
- 基线原子写，保留上一代，并带完整元数据（SHA / CPU / Python / 时间戳）。
  换过机器或解释器之后的数字与上一版不可比，没有元数据就无法事后判断。

阈值第一阶段是**中位数劣化 > 25%**，刻意宽松。`LAB_PERF_GATE=true` 之后才
阻断。

### Mutation `mutation.py`

weekly 专属，默认 report-only。

回答覆盖率答不了的问题：**这些行被执行过，但如果它们算错了，有测试会发现吗？**

scope 圈在 5 个纯逻辑模块（`patchspec` / `registry` / `locate` / `preflight` /
`profiles`），逐个审计过——纯标准库、逻辑密集、算错了会安静地产生错误结果。
刻意排除 `app.py`（庞大集成层）、`worker.py`（要科学栈）、`desktop.py` 与
`runtime.py`（平台分支会产出大量「另一个平台才走到」的 survived，纯噪声）。

配置在 `pyproject.toml` 的 `[tool.mutmut]`——**mutmut 3.x 只从 cwd 的
pyproject.toml / setup.cfg 读配置**，没有命令行或环境变量能覆盖。脚本开跑前
会断言 `only_mutate` 存在且指向的文件都在：缺了它 mutmut 会变异整个
`source_paths`，产出几千个 mutant 与一份没人会读的报告。

survived 的 mutant **会显式列进 Step Summary**，不只给一个数字——「有 12 个
存活变异」没人会去查，贴出来才有人看。

### 汇总 `summarize.py`

一张总表，且**正确性与性能分开判**：

```
正确性 ✅ PASS　·　性能 ❌ FAIL
```

「渲染成功但慢了 40%」应该这样读，而不是一句含糊的「lab failed」——两者的
处置完全不同：前者要回滚，后者要先确认机器状态。

每一项给一句具体的话（哪张图变了、哪个指标回归了、孤儿几个），不是光秃秃的
PASS/FAIL。

---

## 发行链上的 gate（两段，2026-09-16 起）

公开仓库不能等私有仓库（lab 的排队时间没有上界，本仓库禁止轮询），所以发布链
在 lab 门禁处切成两段，中间由 ci-infra 接力：

```
release.yml（第一段：tag push / workflow_dispatch）
    trust（GitHub 托管）——ref → 精确 SHA、可达 origin/main、tag 与源码版本一致、release:blocker 签字
      ├── build（造 wheel + sdist + Codex 插件，上传 artifact "dist"）
      ├── desktop（workflow_call desktop-tauri.yml：NSIS / dmg / latest.json）
      └── dispatch_lab（GitHub 托管，needs build + desktop + trust）
            gh workflow run lab-qualification.yml -R Tavotto/ci-infra -r main
              -f mode=release -f sha=<trust 的 SHA> -f use_prebuilt_dist=true
              -f source_run_id=<本 run> -f publish=<trust 算出的> -f ack_open_blockers=<原样>
              -f pypi_target=<trust 折算的 none / testpypi / pypi>
    ── 第一段到此结束，**不等结果**。run 的 success 只表示「产物造出来了、lab 派出去了」。

ci-infra lab-qualification.yml
    trust-check（同一段 ancestry 判断，重新验）
    qualify（self-hosted：uses 本仓库的 _lab-qualification.yml@main，按 source_run_id 取第一段的 dist）
      ├── 候选包验收（**下载第一段 build 的同一份 dist**）
      ├── slow 用例 / 升级 N-1 → 候选 / 完整 golden + 视觉回归
      ├── **CompatBench 全量（--gate release：任何 product_bug 都红）**
      ├── 800 轮 soak + 泄漏检测
      └── 性能回归（不写基线）
    report（GitHub 托管）——给 SHA 打 commit status `lab/release`；
      **release 且绿** → gh workflow run release-publish.yml -R Tavotto/Tavotto -r main
                          -f sha -f source_run_id -f lab_run_id=<本 run> -f publish -f ack_open_blockers -f pypi_target
      **红** → 只打 failure，**不派发**

release-publish.yml（第二段：只有 workflow_dispatch）
    trust2（GitHub 托管）——**不信任载荷**：重跑 ancestry + tag 判断（照抄 trust）；对**此刻** open 的
      release:blocker 用第一段的 ack 再跑一次 release_blockers.py（lab 期间新开的 blocker 停在这里，
      第二段不新增签字入口）；publish **按同一规则重算**（有 `v<源码版本>` tag 指向该 SHA → true，
      载荷的 publish 只能把 true 压成 false）；pypi_target **只收窄**（∉ {none, testpypi, pypi} → 红，
      publish 不是 true → none）；读**一次** `lab/release` status（state == success 且
      target_url 指向 lab_run_id 那个 ci-infra run）
      ├── validate_artifacts（按 source_run_id 从第一段 run 取全部产物；演练也跑）
      ├── github_release / n1_update_windows（publish=true）
      ├── pypi（publish=true 且 pypi_target != none；testpypi / pypi 按它选）
      └── plugin_stable（演练对临时 bare 仓库跑发布器；真推只在 publish=true）
```

**失败形状变了，读结论的人要看两处。** 从前「一个 run 红」= 没发；现在 lab 红时
第一段的 run 早已 success 结束，`lab/release` status 是 failure，而第二段**从未开始**
——「没有发布」靠的是第二段不存在。判一次发版的结论：

1. 第一段 run（`release.yml`）success，summary「第一段结束」里有本 run 的 id；
2. 该 SHA 的 commit status `lab/release`（`gh api repos/Tavotto/Tavotto/commits/<sha>/status
   --jq '.statuses[] | select(.context == "lab/release")'`）：success 才会有第 3 步；
3. `release-publish.yml` 有该 SHA 的 run 且 success；publish=false 时 summary 有
   「演练（publish=false）到此为止」，publish=true 时有 Release / PyPI / plugin-stable 的结论。

**不给 fallback。**「runner 离线 → 跳过 gate → 照发」等于这条门禁在最需要它
的时候自动消失。runner 不可用时 ci-infra 的 run 会排队或失败，第二段不会被派发，
那正是期望行为。手工派发第二段而 lab 没绿，停在 trust2（status 不是 success）。

lab 侧只有 `contents: read`；签发能力（Release 写权限、PyPI OIDC、`environment`
保护）全在第二段的对应 job 上，一个字未动。从前 `pypi` job 那条 `if` 的两支
（tag 触发看 `vars.PYPI_PUBLISH_ENABLED` / dispatch 看 `inputs.pypi`）由第一段 `trust`
折成一个值 `pypi_target` 随载荷传过去——第二段**不再读**那个仓库变量（再读一次就是
第二份权威），`trust2` 只能把它收窄成 none。

### Release-blocker 显式签字（trust 阶段，第二段 trust2 再核一次）

发布编排在 trust 里查 open 且带 `release:blocker` label 的 issue
（`scripts/ci/release_blockers.py`）。清单非空时必须在 `workflow_dispatch`
的 `ack_open_blockers` 输入里**逐条**签字（编号必须与当前 open 清单双向
对得上——防止一次 ack 永久生效），tag 触发带不了输入，有 blocker 时直接红，
提示改用 dispatch 签字后发布。不是禁止发（0.x 需要灵活），是把「明知有洞
还发」从默认无声变成显式决定——issue #35 带着未验证的 N-1 更新连发四个
版本，就是没有这道门的代价。

切两段之后 ack 随载荷经 ci-infra 原样传回第二段，`trust2` 对着**此刻** open 的
清单再跑一次同一个脚本：lab 跑的那一两个小时里新开的 blocker 会让第二段停在
trust2，而不是无声发出去。第二段**不新增签字入口**——要签新 blocker，从第一段
用 `workflow_dispatch(ref=<同一 SHA>, ack_open_blockers=…)` 重来。

---

## 真实 N-1 应用内更新（Windows）

**问的问题**：上一版的用户点「更新」，真的能更到这一版吗？这条链
（下载 → minisign 验签 → 解包 → NSIS 安装 → 重启）从 v0.7.0 起坏了四个
版本而全链绿灯——每条绿灯量的都是生产者侧的替身指标，没有一步以真实消费者
（更新器插件）的身份消费过产物。两层修补：

- **nightly 的 `updater-consumer-fidelity`**：对**线上已发布**的
  latest.json 与全部平台更新包做验签 + 插件同形态解包
  （`tools/updater-extract-probe`，zip crate `default-features = false`）。
- **release-publish.yml 的 `n1_update_windows`**（发布后自动跑；F.2 之前在 release.yml）：Windows runner 装
  N-1 官方安装包，用 `TAVOTTO_E2E_RUN_UPDATE=1`（壳的仅测试触发口，默认
  关死）驱动真实应用内更新，断言注册表 DisplayVersion 与重启后新进程的
  ProductVersion 都换成了新版本，再对更新后的 sidecar 跑
  `smoke_app --expect-source bundled --expect-runtime`。

  **「重启后的新进程」这个主语按壳的映像路径认，不按进程名认。** 安装目录里
  有两个都叫 `Tavotto.exe` 的二进制——壳在安装根，sidecar 在
  `sidecar/Tavotto/Tavotto.exe`——`Get-Process Tavotto` 两个都收，而 sidecar
  是 PyInstaller 产物、**没有版本资源**（对 v0.12.0 官方安装包实测：壳
  `ProductVersion=0.12.0`，sidecar 连 `StringFileInfo` 都没有）。选中 sidecar
  就必然读到空 ProductVersion，且等多久都不会变；「映像在安装目录下」也放它
  过去。所以判据用 `Win32_Process.ExecutablePath` 与 `$inst\Tavotto.exe` 全等
  匹配，版本资源再从那个映像路径读（轮询 15s，兜安装器仍在替换 exe 的中间态），
  空值走显式 fail 并打印 pid / 映像路径 / 句柄读值。见 issue #147。

**只能发布后测**：已发布应用的 endpoint 烤死指向 `releases/latest`，发布前
它还指着上一版。**N-1 二进制没有触发口的那一轮**（N-1 ≤ 0.10.0）自动化
驱动不了它的 UI，job 会转成手动提示——按下面的 checklist 手工执行一遍，
证据记进 issue #35（「已写进文档」不等于关闭）：

1. 干净的 Windows 10/11（或 runner）上安装 N-1 的官方
   `Tavotto-<N-1>-Windows-Setup.exe`；
2. 启动，建一个项目、渲染一张图、存一次布局（升级后要核对它们还在）；
3. 打开设置 → 检查更新 → 应该看到新版本 → 点更新，观察：进度条走满 →
   passive 安装界面 → 应用自动重启；
4. 重启后核对：关于页/`/api/version` 是新版本号，注册表
   `HKCU\...\Uninstall\Tavotto` 的 `DisplayVersion` 是新版本；
5. 项目、布局、设置还在；再渲染 + 导出一次；
6. 证据（截图/录屏 + 版本断言输出）贴进 issue #35。

macOS 的同等验证（`.app.tar.gz` 通道）尚未自动化——后做，见 issue #35。

---

## 排障

失败时看 Step Summary 的总表定位到环节，再从 artifact 里取对应报告：

| 产物 | 内容 |
|---|---|
| `reports/preflight.json` | 逐项体检与处置建议 |
| `reports/acceptance.json` | wheel 名、sha256、逐条结构断言 |
| `reports/upgrade.json` | N-1 tag、逐条升级核对 |
| `reports/visual.json` + `visual/*.png` | 每张的三个指标 + baseline/candidate/diff |
| `reports/soak.json` / `soak-metrics.json` | 逐轮资源时间序列、孤儿清单 |
| `reports/benchmark.json` | 逐指标对比、基线元数据、环境状态 |
| `reports/mutation.json` | 各类计数与 survived 清单 |

视觉回归失败时**只有变化的那几张**会留下 baseline/candidate/diff 三图——
通过的不留，免得 artifact 里全是噪音。

---

## 已知限制

- **升级验收当前跨在产品改名边界上，因此会跳过。**
  v0.7.0 的分发包是 `magplot`，v0.8.0 是 `tavotto`；2026-08-20 改名时选的是
  **干净断裂**——包名、数据目录、配置目录、格式标识全部更换且刻意不做兼容
  读取（理由记在 `src/tavotto/engine/brand.py` 的模块 docstring 里）。
  这意味着跨越那条边界的「升级」在产品语义上不存在：用户不是
  `pip install --upgrade`，而是装了另一个包。
  脚本识别出这种情况后**如实标注为跳过**（`reason: rename_boundary`），
  既不伪装成通过，也不报成失败——报失败会让人去修一条产品刻意不支持的路径。
  Step Summary 的**结果列**会显示 `⏭️ 跳过`，不是 `✅ PASS`。
  等出现同代的上一版（`tavotto` 的前一个 release）后，这项验收自动恢复。
  届时建议手工跑一次 `--baseline-tag v0.8.0` 确认整条链路真的能跑通，
  因为在此之前它的完整路径没有被端到端执行过。
- **slow 用例目前只有 1 条。**这一层的价值现在主要在别的环节；没有为了凑数
  把普通用例标成 slow。
- **视觉基线尚未生成。**首次启用需要在一台配好内置 runtime 的机器上跑
  `--update-baselines` 并把结果提交 review（见上面的基线纪律）。在此之前
  视觉回归会以 `baseline_missing` 失败——**这是设计如此**，不是 bug。
- **corpus 不含 pandas / seaborn / scipy 的 case。**引入它们会让 CI 依赖显著
  膨胀，而它们的绘图最终仍落到 matplotlib artist 上；当前 corpus 用 numpy
  构造等价的数据形态。要加的话需要同时决定这些库的版本锁策略。
- **性能与 mutation 默认不阻断。**等积累几周数据后再打开对应变量。
- **corpus 的 stem 数（13）在任务书建议的 15~30 的下沿。**刻意没有为了数量
  造几乎一样的 case；扩充时优先补真正未覆盖的形态。

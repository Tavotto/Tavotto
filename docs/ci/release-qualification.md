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

实现全在 `scripts/ci/`，由 `.github/workflows/lab-ci.yml` 与 `release.yml` 的
`lab_release_gate` 驱动。机器的准备见
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
                     ┌───────────┼────────────┐
                     ▼           ▼            ▼
                   slow        golden        soak
                     │           │            │
                   upgrade     visual       leaks
                     │           │            │
                     └──────┬────┴──────┬─────┘
                            ▼           ▼
                         benchmark    reports
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
| `main` | push 到 main | 常规套件 + slow + 小 golden + 100 轮 soak + 基础泄漏 | ≤ 15~20 min |
| `nightly` | 每日 19:00 UTC | 上面全部 + 前端/Rust + 完整 golden + 视觉回归 + 500 轮 soak + benchmark | ≤ 30~45 min |
| `release` | 打 tag（`release.yml`） | 候选包验收 + slow + 升级 + 完整 golden + 800 轮 soak + 性能（不写基线） | — |
| `weekly` | 周日 20:00 UTC | 上面全部 + mutation | — |

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

### slow / 集成用例

`pytest.ini` 的 `addopts` 是 `-m "not slow"`，所以这里显式 `-m slow`。

workflow 会先 `--collect-only` 数一遍，**一条都没选中就直接失败**：那说明
标记被删/改名，或 `pytest.ini` 的 markers 变了，而门禁会安静地报绿。

> 当前仓库只有 1 条 slow 用例（`test_bootstrap.py::test_real_install_end_to_end`，
> 真建 venv 真装 matplotlib）。这一层的价值目前主要在其它环节；随着 slow 用例
> 增加，这条会自然变重。**没有为了凑数把普通用例标成 slow。**

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
| `c03_text_legend_images.py` | legend / annotations / scinotation / image / cjk | 图例重建、annotate 不出端点、位图 alpha、mathtext |

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

## 发行链上的 gate

```
push v*
    │
    ▼
build（GitHub 托管）——造 wheel + sdist，上传 artifact "dist"
    │
    ▼
lab_gate_trust（GitHub 托管）——tag → 精确 SHA，验明正身
    │
    ▼
lab_release_gate（self-hosted）
    ├── 候选包验收（**下载 build 的同一份 dist**）
    ├── slow 用例
    ├── 升级 N-1 → 候选
    ├── 完整 golden + 视觉回归
    ├── 800 轮 soak + 泄漏检测
    └── 性能回归（不写基线）
    │
    ▼
  PASS
    ├── github_release   (needs: build, lab_release_gate)
    └── pypi             (needs: build, lab_release_gate)
```

**不给 fallback。**「runner 离线 → 跳过 gate → 照发」等于这条门禁在最需要它
的时候自动消失。runner 不可用时那个 job 会排队或失败，那正是期望行为。

gate 只有 `contents: read`。PyPI 的 OIDC 发布模型与 `environment` 保护一个字
未动。

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

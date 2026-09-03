# 渲染性能基线（Phase E）

日期：2026-08-18 ｜ 复现命令：`python scripts/bench_render.py --python .venv/bin/python --repeat 9`

这份文档是**测出来的**，不是估出来的。它存在的理由只有一个：Tavotto 之后的任何
「优化」都必须先在这里指出一个具体的数字，改完再回到这里给出同一张表的前后对照。
没有数字支撑的改动一律不做——那不是优化，是赌。

## 机器与版本

| 项 | 值 |
|---|---|
| 机器 | Apple M4 Pro（12 核）/ macOS 26.6.1 / `macOS-26.6.1-arm64-arm-64bit-Mach-O` |
| Flask 侧 | Python 3.13.11 + Flask 3.1.3 + PyMuPDF 1.28.2（`.venv`，无 matplotlib） |
| 渲染 worker | Python 3.13.11（Homebrew，来源 `system`）+ matplotlib 3.10.8 + numpy 2.4.3 |
| supervisor | `tavotto-workerd` release 构建（cargo 1.95.0） |
| 图库 | `examples/figures`（3 个面板 / 2 个脚本，全部 `cost=light`、**纯矢量**） |

## 方法（以及几个刻意的选择）

* 走**真实的 HTTP 端点**（`/api/engine/render`、`/api/engine/svg`、`/api/export`），
  不直接 import 引擎：用户等的是那条链路的总时间，绕过 Flask 量出来的数字好看但没用。
* 热态每个面板发 **9 次** override（每次都真的改一个 `fontsize`，绝不让 worker
  走「什么都没变」的捷径），取**中位数**——偶发的一次 GC / 磁盘抖动会把均值拉走。
* 每条控制面各起一次服务，**数据目录都是全新的临时目录**；否则「冷启动」量到的
  是上一轮留下的热态。
* **HOME 不隔离**（与 `smoke_app.py` 刻意不同）。matplotlib 的字体缓存在用户目录里，
  重置 HOME 等于每次冷启动都要重建一次字体缓存——实测在这台机器上是 **9 秒**，
  它会盖过所有别的数字，而真实用户一台机器只付一次。要量「新机器上的第一次」用
  `--fresh-home`，那是首次体验问题，见下面的观察 4。

## 基线

### 控制面：Python 池（`TAVOTTO_WORKERD=0`）

| 面板 | cost | 冷 wall | 冷 worker_get | 冷 build 往返 | 冷 script_build | 热 wall(中位) | queue_wait | patch_apply | canvas_draw | manifest | worker total | SVG | 导出 wall |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `Fig1_kinetics.pdf` | light | 458.3 | 160.8 | 267.9 | 87.8 | 28.1 | 0.0 | 0.0 | 11.6 | 14.7 | 27.0 | 38.4KB | 64.6 |
| `Fig2_correlation.pdf` | light | 322.8 | 2.1 | 294.8 | 116.0 | 25.0 | 0.0 | 0.0 | 8.8 | 14.5 | 24.0 | 36.4KB | 53.6 |
| `Fig2_yield.pdf` | light | 18.2（warm） | 0.1 | — | — | 17.4 | 0.0 | 0.0 | 7.4 | 8.4 | 16.4 | 28.0KB | 20.6 |

### 控制面：workerd（release）

| 面板 | cost | 冷 wall | 冷 worker_get | 冷 build 往返 | 冷 script_build | 热 wall(中位) | queue_wait | patch_apply | canvas_draw | manifest | worker total | SVG | 导出 wall |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `Fig1_kinetics.pdf` | light | 453.2 | 335.7 | 87.9 | 87.7 | 28.4 | 0.0 | 0.0 | 11.6 | 14.7 | 27.2 | 38.4KB | 64.4 |
| `Fig2_correlation.pdf` | light | 322.6 | 181.8 | 114.9 | 114.7 | 25.7 | 0.0 | 0.0 | 8.8 | 14.6 | 24.7 | 36.4KB | 52.8 |
| `Fig2_yield.pdf` | light | 18.3（warm） | 0.1 | — | — | 19.2 | 0.0 | 0.0 | 8.0 | 8.4 | 17.9 | 28.0KB | 21.4 |

单位全部是毫秒（SVG 列除外）。列的含义：

| 列 | 谁量的 | 含义 |
|---|---|---|
| `wall` | bench 客户端 | 整次 HTTP 往返 |
| `worker_get` | `app.py` | 取（必要时 spawn）会话——**既不属于 worker 也不属于 build** |
| `build 往返` | `pool` | 整条 build 命令（含子解释器启动与 `import matplotlib`） |
| `script_build` | worker | build 里 worker 自己那一段（跑脚本 + instrument + 首次预览） |
| `queue_wait` | `pool` | 请求发出去之前排了多久（Python 池 = 抢 `w.lock`；workerd 侧口径见下） |
| `patch_apply` / `canvas_draw` / `manifest` | worker | 应用 override / `savefig(svg)` / 重建 manifest |
| `worker total` | `pool` | 那一次 render 的 worker 往返 |
| `SVG` | bench 客户端 | `/api/engine/svg` 的响应体大小（前端每次渲染都要下载 + 解析） |

「warm」= 那一次并没有真的跑脚本：一脚本多产物时第二个 stem 的「第一次」已经是热的。

**`svg_ms` 为什么没有单列**：SVG 序列化与 draw 在 matplotlib 里是同一趟
（`print_svg` 边画边写），分不开，合并在 `canvas_draw_ms` 里（ADR 0003 §9）。

## 补测：含 imshow 的面板 + 预览 dpi

`examples/figures` 全是纯矢量图，量不出预览 dpi 的任何影响。另建一个只有一张
imshow 面板（600×800 随机矩阵 + 色条）的图库跑同一条链路，Python 池，repeat=9：

| 预览 dpi | 热 wall(中位) | canvas_draw | manifest | worker total | SVG |
|---|---|---|---|---|---|
| 200（默认） | 55.8 | 27.8 | 25.8 | 54.5 | 662.2KB |
| 100 | 46.6 | 18.7 | 25.7 | 45.4 | 165.6KB |
| 72 | 45.7 | 17.5 | 26.0 | 44.4 | 93.0KB |

同一个旋钮在纯矢量图上**完全无效**（同一次会话内背靠背对照，`examples/figures`）：

| 预览 dpi | Fig1 热 wall | Fig2_corr 热 wall | Fig2_yield 热 wall | SVG |
|---|---|---|---|---|
| 200（默认） | 28.1 | 25.0 | 17.4 | 38.4 / 36.4 / 28.0KB |
| 72 | 28.0 | 25.6 | 17.6 | 38.4 / 36.4 / 28.0KB（**字节数完全相同**） |

## 观察

1. **热态一次 override ≈ 17–28ms，其中 manifest 与 canvas_draw 各占一半左右。**
   `patch_apply` 恒为 0.0ms——override 的应用本身不是成本，**画**才是。
   manifest 内部再拆一层（探针，Fig1）：`fig.canvas.draw()` 6.0ms + 逐元素
   `get_window_extent` 8.5ms；`savefig(svg)` 另有 12ms（它自己又画了一遍）。
   **一次 render 画了两遍图**，这是热态最大的一块结构性开销（见「值得做的优化」1）。

2. **`queue_wait` 恒为 0.0ms。** 两条控制面都是——bench 是串行发的，没有并发。
   这条数据只能说明「串行场景下不排队」，**不能**说明真实用户不会排队（用户在慢图上
   连拖十几下正是 workerd 合并队列存在的理由）。要量排队必须先有并发压测，
   那是另一件事，本阶段没做。

3. **两条控制面的热态没有差别**（27.0 vs 27.2ms，差异在噪声内），冷启动总时间
   也一样（~455ms），但**成本落点不同**：Python 池把子解释器启动 + `import
   matplotlib` 记在第一条 build 上（`build 往返` 267.9ms vs `script_build` 87.8ms），
   workerd 把它记在 `open_session` 的握手里（`worker_get` 335.7ms，随后 build 只剩
   87.9ms）。这符合 ADR 0004 的设计（握手在会话建立时完成），也说明
   **workerd 的价值不在单请求快慢上**——它在队列合并、超时强杀、代序隔离上，
   本基线的串行场景根本触不到。

4. **首次体验是另一回事**：`--fresh-home`（模拟一台没跑过 matplotlib 的新机器）下
   Fig1 的冷启动 wall 从 458ms 涨到 **9867ms**，多出来的 9.4 秒全是 matplotlib
   重建字体缓存。它一台机器只付一次，但**用户第一次点开图看到的就是这九秒**。
   Windows 桌面版把 `MPLCONFIGDIR` 改道到数据目录（不往安装目录写），所以这九秒
   在那里同样会发生一次。

5. **导出（`/api/export`）21–65ms**，含 worker 全质量重渲染 + PyMuPDF 合成；
   imshow 面板 163ms。相对热渲染贵，但它不是交互路径，本阶段不动。

6. **run-to-run 噪声约 ±5%**（同一台机器、同一命令、无其它负载）。任何小于
   10% 的「改进」在这台机器上都读不出来，不要拿它当结论。

## Phase E 做了什么、没做什么

### 做了：缓存键诚实化 + 原子写入（E1）

`/api/render` 的磁盘缓存键从 `sha1(id|mtime|w)` 换成
`sha1(id|内容 sha1|w|后端-版本)`：

* mtime 回答的是「什么时候被碰过」，不是「里面是什么」。**可观察差异**：
  内容没变而 mtime 变了（touch、从备份还原、同步工具、重跑脚本出了同一张图）
  现在**照常命中**，以前会白重渲染一张 3200px 的预览。
* 反过来，换了 PyMuPDF 版本、同一个 PDF 渲出来的像素可能已经不一样，以前照旧命中，
  现在必然失效。
* 内容哈希用进程内 `{路径: (mtime, size, sha1)}` memo 免掉每次全文件哈希——
  **mtime/size 只是「要不要重算」的信号，身份永远是内容**。
* 缓存写入改临时文件 + `os.replace`；读到零字节（上一次写到一半被杀）当场删掉重建。
  看护：`tests/test_render_cache.py`（16 线程同键并发必须每个都拿到能解码的完整 PNG；
  把实现换回直写会立刻红）。

这一条的动机不是性能，是**正确性**：并发同键请求以前真的可能读到半个 PNG。

### 做了：端到端计时管道（E2）

worker（`build`/`render`/`export` 的 v1 响应）→ pool（`queue_wait_ms` /
`total_ms` / 冷启动折叠进来的 `build_total_ms`）→ `app.py`（`worker_get_ms`）→
`/api/engine/render` 响应体 + 一行结构化 INFO 日志 → 前端 `renderStore.timings`
（只存不显示，UI 归 Phase F）。全部是**加字段**，协议版本不升（ADR 0003 §1）。

计时管道自己踩过一次坑，值得记下来：第一版只量了 worker 内部，冷启动的
`script_build_ms` 根本不在 render 的响应里（build 是**另一条命令**），
`worker_get` 更是完全没人量——于是一次用户等了 10 秒的渲染，数据里只有 30ms。
**一个漏项就足以让整份性能数据说谎**，所以现在这三段全部显式量出来。

### 做了：预览 dpi 变成请求可带（E3-2，唯一过阈值的一条）

`/api/engine/render` 与 worker 协议 `render` 的 payload 新增可选 `preview_dpi`
（不给就是 worker 的 `--preview-dpi`，信封形状对既有调用方一字不变）。
数据见上面的补测表：含 imshow 的面板 200→100 让 `canvas_draw` 从 27.8 降到
18.7ms（−33%）、热 wall 从 55.8 降到 46.6ms（−16%）、**SVG 从 662KB 降到 166KB
（−75%）**；纯矢量图上一分钱都不值（字节数完全相同）。

**前端不接**——「编辑期降质换快显」是交互取舍，归 Phase F 判断。后端先把旋钮
留出来，Phase F 只需决定要不要发这个字段、什么时候发。

### 没做：manifest 的重复 draw（E3-1，被数据否掉）

嫌疑最大的一条：一次 render 画了两遍图（manifest 一遍 6.0ms，`savefig(svg)`
一遍 12ms）。两种实现都试了，**两种都被实测否决**：

* **把 SVG 挪到前面、manifest 复用 savefig 之后的布局**（省 7.4ms，占热态 26%）：
  bbox 直接错了。图例文字的 bbox 偏差 **0.18–0.32 figure 分数**——SVG 是按
  `preview_dpi=200` 画的，offsetbox 的落位于是留在 200dpi 的坐标里，而 manifest
  按 figure 自己的 dpi 换算。写回自检的容差是 0.5%，这个改动会让**每一次写回
  都报 replay_divergence**。数据损坏级，不可能通过。
* **`fig.canvas.draw()` 换成 `draw_without_rendering()`**（bbox 逐元素完全一致，
  最大偏差 0.000000）：只省 2.3ms（15.7 → 13.4ms），占热态 **8%，低于 15% 阈值**；
  而且在 3D + constrained_layout 的图上会多打一条
  `constrained_layout not applied because axes sizes collapsed to zero` 警告。
  收益不够，噪音是真的，不做。

### 没做：队列（E3-3）

`queue_wait` 恒为 0.0ms（观察 2），没有任何数据支撑去动它。workerd 已经有合并
队列，Python 池是参考实现，不加复杂度。

## 假实时预览（Phase G）：客户端那一半的账

日期：2026-08-18 ｜ 复现：`cd web && npx playwright test e2e/fake-realtime.spec.ts`

后端的 `timings` 回答的是「matplotlib 那边花了多久」。它看不见另一半——**用户从
按下鼠标到画面动起来等了多久**。Phase G 把这半边也量出来了，出处是
`web/src/lib/previewTrace.ts` 维护的计时环（`window.__MM_PREVIEW_TIMINGS__`）。

### 真浏览器（Chromium，Playwright，`examples/figures` 的 Fig1_kinetics）

| 项 | 实测 | 说明 |
|---|---|---|
| `preview_first_frame` | **12–30ms** | pointerdown → 第一帧预览落进 DOM。含 2px 起拖阈值与一次 rAF 等待，所以下限就是一两帧 |
| `preview_frame_count / move_count` | 40 / 40 | **这条脚本里合并率为 0**：Playwright 的 `mouse.move` 是一次一等，每一步都赶得上自己那一帧 |
| `commit_to_authority_ms` | **36–37ms** | pointerup → 权威 SVG 换上画布，整条 HTTP 链路 |
| 拖动期间 `/api/engine/render` | **0 次**（40 次 pointermove） | 用例直接数 network request，不是数 mock |

对照同一台机器同一个面板的后端热态中位 **28–32ms**（上面的基线表）：
commit→权威 的 36ms 里，matplotlib 那段占了八成以上，**剩下的都在网络与 JSON**。
这也说明「继续压前端」没有意义——要更快只能压 worker 那一段。

### DOM 写入本身的代价（jsdom，非浏览器）

浏览器里量不到「单纯写一帧要多久」（一帧里还有布局与绘制）。这张表是 jsdom 里
纯 DOM 写入的成本，**只用来回答「预览本身会不会成为瓶颈」**，不是帧预算：

| 操作 | 中位 | p95 |
|---|---|---|
| transform 首帧（含采 base） | 0.37ms | 0.51ms |
| transform 后续帧 | 0.24ms | 0.27ms |
| 样式首帧 `line.color` | 0.27ms | 0.36ms |
| 样式帧 `scatter.facecolor`（`<defs>` + 5 个 `<use>`） | 0.04–0.07ms | 0.16ms |
| **100 次 pointermove → 1 帧（总）** | **0.25ms** | 0.26ms |

最后一行是 rAF 合并的证据：一百次 `previewTransform` 加起来与**一次**落地写入
同一量级——被合并掉的 99 次几乎不要钱。（SVG fixture 18.8KB；真实
Fig1_kinetics 的 SVG 是 38.4KB，同一量级。）

### 这几个数字**不**是什么

* 不是「拖动一定 30ms 内跟手」的保证。首帧含一次 rAF 等待，掉帧的页面上会更久。
* 不是重图的数字。`examples/figures` 全是 `cost=light` 的纯矢量小图；
  heavy 脚本的 commit→权威 仍然是**秒到分钟**级——预览的价值恰恰在那里最大，
  但没有样本就不写数。
* jsdom 那张表不能当浏览器帧预算用：jsdom 没有布局也没有绘制。
## 界面动效（Phase H）：加了动画之后还剩多少余量

日期：2026-08-18 ｜ 复现：`cd web && npx playwright test e2e/motion.spec.ts`
（数字由 CDP `Performance.getMetrics` 前后取差得到，除以帧数）

动效的问题从来不是「加一个 180ms 的过渡贵不贵」，而是**它跟正在跟手的东西
抢不抢主线程**。所以先量的是没有动效时的余量。

### 余量：拖动画布对象时主线程占用多少

| 场景（140 帧，Chromium） | 主线程/帧 | 其中脚本 | 样式+布局 | 掉帧 |
|---|---|---|---|---|
| 空转（12 个对象） | 0.11ms | 0.02 | 0.00 | 0 |
| 拖 1 个对象（12 个对象） | **1.96ms** | 1.38 | 0.13 | 0 |
| 拖 1 个对象（48 个对象） | **1.77ms** | 1.22 | 0.11 | 0 |

两条结论：60Hz 预算 16.7ms **只用掉约一成**；而且 12 → 48 个对象代价不变——
immer 的结构共享 + `ObjectView` 的 memo 是有效的，**代价随选区大小走，
不随画布规模走**。动效的开销落在这段余量里。

### 侧边抽屉：动 width 到底贵多少

停靠态的抽屉动宽度会连锁触发「画布列重排 → `CanvasStage` 的 ResizeObserver →
两次 setState → OverlaySvg 重渲染」。这条链是真的，但代价可接受：

| 逐帧改动（120 帧） | 主线程/帧 | 其中脚本 | 样式+布局 | 掉帧 |
|---|---|---|---|---|
| `transform`（覆盖态走这条） | 0.51ms | 0.02 | 0.04 | 0 |
| `width`（停靠态走这条） | **1.60ms** | 0.38 | 0.30 | 0 |

所以停靠态就用 width（画布本来就该跟着让位），覆盖态用 transform。
抽屉内容包在**定宽的内层**里、外层 `overflow: hidden`——这不是样式偏好：
不包的话那 180ms 里抽屉子树每帧重排，文字按钮跟着挤；包起来之后每帧重排的
只剩画布列。e2e 里直接断言「内容层宽度全程不变」。

### 这几个数字**不**是什么

* 不是低端机的数字。同一台 M 系 Mac、60Hz、Playwright 的 Chromium；
  Windows 的 WebView2 与集显机器没有样本，**没测就不写**。
* 不是「动效永远免费」。上面两张表是**同一时刻只有一件事在动**。抽屉开合
  与面板拖动同时发生的情况没有量过（实际也几乎不会同时发生）。
* 拖动那张表里的「48 个对象」指画布上的对象总数，拖的始终是 1 个。
  多选拖动的代价随选区线性增长，那才是这条链的真实上界。

## 路径几何（manifest 的 `geometry`）：加了多少账

选中轮廓与命中判据从 bbox 换成**真实路径**之后，`build_manifest` 每次要多
取一遍路径并抽稀（`engine/pathgeom.py`）。这是加在**每一次渲染**上的固定
成本，所以单独量了一遍：同一个进程里把 `pathgeom.element_geometry`
monkeypatch 成返回 None 做开/关对照（`manifest_ms` 的中位，各 9 次）。

| 图 | 关 | 开 | 增量 | manifest JSON | geometry 元素 / 点数 |
|---|---|---|---|---|---|
| `examples/figures` Fig1_kinetics | 17.19 | 18.01 | **+0.82ms** | 26.9→27.8KB | 2 / 33 |
| `examples/figures` Fig2_correlation | 16.41 | 17.39 | **+0.98ms** | 26.3→26.4KB | 1 / 2 |
| `examples/figures` Fig2_yield | 9.35 | 9.58 | +0.23ms | 17.4→17.4KB | 0 / 0 |
| 合成「重」图：8×20000 点带噪谱线 + 2 块 fill_between | 235.2 | 261.6 | **+26.3ms**（+11%） | 25.6→130.3KB | 11 / 5352 |

真实图库上是 **+0.2～1.0ms**（占 manifest 的 2～6%、占热渲染往返的 3～4%），
JSON 只多几百字节。合成的重图上是 +26ms —— 但那张图本身的 manifest 就要
235ms，比例仍是一位数末尾。

### 两条被数据逼出来的实现选择

1. **取路径必须一次拿 numpy 数组，不能逐段迭代。**
   `Path.iter_segments()` 在两万点的谱线上要跑两万次 Python 循环：八条线
   **+550ms**，比整次渲染还慢。换成 `Path.cleaned()`（一次 C 调用返回
   vertices/codes 两个数组）之后同一段是 **2.9ms**。
2. **超长路径先按段取极值，再交给 RDP。**
   噪声让几乎每个点都成为 RDP 的转折点，栈递归退化成上万次 numpy 调用
   （单条 20000 点 26ms、八条 360ms）。改成先切 150 段、每段留 x/y 的上下沿
   （`_block_extremes`，1.8ms/条）之后，点数直接落到 600 的上限之内，而且
   **每个留下的点都是曲线上的真实点**——纵向包络逐段精确，测试拿 bbox 当尺子
   看住这一点。这一档之后不再跑 RDP：它只能再省一半点，却要多花五倍时间。

### 还没量的

* geometry 对**前端**的账（多几百个点的 `<path>` 描边、命中时的距离计算）
  没有单独量过。命中是 pointerdown/move 时的一次 O(点数) 扫描，点数有 600
  的硬上限，量级上应当远低于一帧；但**没测就不写**。
* 总点数预算（`TOTAL_BUDGET = 8000`）用完之后的降级路径没有性能样本——
  它本来就是安全阀，不是常态。

## 值得做的优化（按数据支撑排序，标注归属阶段）

1. **首次启动的 9.4 秒字体缓存**（观察 4）——目前只在 `--fresh-home` 下暴露，
   但每个新用户都会付一次，而且发生在他第一次点开图的时候。可做的方向：安装/首启
   时后台预热一次字体缓存，或在冷启动 SSE 里如实告诉用户「首次准备渲染环境」。
   **归属：产品体验，不属于本阶段的引擎优化**，先记在这里。
2. **冷启动里 ~180ms 的子解释器启动 + import**（`build 往返 − script_build`，
   两条控制面各自的形态见观察 3）。真正能砍它的是**预热一条空闲 worker**，
   而不是让 import 变快。**归属：Phase F 之后**，且必须先回答「预热几条、
   按什么策略」——预热的内存代价是每条会话一整个 matplotlib。
3. **编辑期降质快显**：数据只支持一种情况——**含 imshow 的面板**。那里
   `preview_dpi` 200→100 省 16% 的往返和 75% 的传输；纯矢量面板上做这件事是
   零收益（见补测第二张表）。**归属：Phase F，已做**：前端只对
   manifest 里有 `role=="image"` 元素的面板、且只在防抖那一路带
   `preview_dpi: 100`，松手/结束事务由 `flushRender` 按默认 dpi 定稿
   （`web/src/hooks/useEngineSync.ts`，vitest 看护）。纯矢量面板一律不带。
4. **拖动期间的 matplotlib 往返（Phase G，已做）**：以前拖一个图内元素只有
   松手才渲染（这条本来就对），但**属性页的 scrub 与取色每改一个值就发一次**。
   现在这两类走 `render:'none'` + SVG 局部预览，整轮只在收尾发一次
   （`web/src/store/svgPreviewStore.ts` 的能力表 + `useEngineSync` 的渲染策略）。
   收益按上面的表算：一次三十步的取色从「最多 30 次 × 28ms 的往返」降到 1 次。
5. **并发下的排队行为完全没有数据**（观察 2）。「用户连拖十几下」是 workerd
   合并队列的立项理由，却从来没被量过。**归属：需要一个并发压测脚本**，
   本阶段没做，也不该靠猜。
6. **manifest 的逐元素 `get_window_extent`（8.5ms / 26 元素）**：目前占热态约
   30%，但它是「量每个元素在哪」这件事本身的成本，没有明显的重复计算可砍。
   真要动得先有更大的图库样本（本基线只有 25–68 个元素的小图）。**归属：待定，
   证据不足。**

## 大图预览基线（issue #181，修复前）

日期：2026-08-28 ｜ 提交：`b23f8d9` + 本分支的合成 fixture ｜ **这是 before-fix
基线**，任何针对 [ADR 0022](adr/0022-complexity-aware-editor-preview.md) 的改动
都要回到这张表给出前后对照。

上面那份 Phase E 基线量的是**普通科研图**（25–68 个元素、纯矢量、SVG 几十到
几百 KB）。issue #181 是另一个量级的问题：多面板 `pcolormesh`，预览 SVG 里
每个 cell 一个 `<path>`。这一节把那个量级测出来。

### 复现对象

合成 fixture `tests/fixtures/large_figures/issue_181_large_pcolormesh.py`
（**不含任何用户数据**，`np.random.default_rng(181)` 现生成）：2×2 图，三格
`pcolormesh`（默认 n=470 → 每格 220 900 个 quad）+ 一格普通曲线 + 色条 +
标题/轴标签/图例。规模旋钮 `TAVOTTO_ISSUE181_MESH_N`。

摊成可用图库并跑基线：

```bash
python tests/support/large_figures.py /tmp/issue181-lib --python "$(command -v python3)"
TAVOTTO_ISSUE181_MESH_N=470 python scripts/bench_render.py \
    --python .venv/bin/python --figures /tmp/issue181-lib --repeat 3 --plane python
```

### 机器与版本

| 项 | 值 |
|---|---|
| 机器 | Apple M4 Pro（12 核）/ macOS 26.6.2 / `macOS-26.6.2-arm64-arm-64bit-Mach-O` |
| Flask 侧 | Python 3.13.11 + Flask 3.1.3 + PyMuPDF 1.28.2（`.venv`，无 matplotlib） |
| 渲染 worker | Python 3.13.11（Homebrew，来源 `system`）+ matplotlib 3.10.8 + numpy 2.4.3 |
| 控制面 | Python 池（`TAVOTTO_WORKERD=0`）；**workerd 这一轮没测** |
| 热态样本 | 3 次取中位 |

### 数据（before fix）

| 指标 | 值 | 出处 |
|---|---|---|
| `svg_bytes` | **126 132 735**（120.3 MiB） | `bench_render.py` |
| `svg_path_count` | **662 772** | 同上（= 3 × 470² 个 quad + 72） |
| `svg_image_count` | 1 | 同上（色条渐变） |
| SVG 里的元素总数（= 插进 DOM 后的节点数） | **663 533** | 逐 tag 数了一遍 |

（`svg_path_count` 2026-08-29 从 662 773 更正为 **662 772**：`bench_render.py`
的分块计数漏了「减去重叠区里数得完整的那些」，每有一个 `<path` 恰好整个落在
5 字节重叠区里就多报一个。判据已修，`tests/support/preview_hybrid_probe.py`
的 `_svg_file_stats` 与它同一条，并有一条三种数法互比的用例
（`test_the_two_ways_of_counting_the_same_svg_agree`）。那 72 个正是 hybrid
之后剩下的全部矢量 path——两条独立路径上数出来的同一个数。）
| `manifest_ms`（热，中位） | 206.3 | worker 自报 |
| `canvas_draw_ms`（热，中位） | 11 789.1 | worker 自报（`savefig(svg)`，见 ADR 0003 §9） |
| `total_ms`（热，中位） | 11 992.7 | worker 自报 |
| 热 `wall_ms`（客户端整次往返，中位） | 11 995.7 | `bench_render.py` |
| 冷 `wall_ms` | 24 334.2 | 同上 |
| 冷 `script_build_ms` | 11 969.1 | worker 自报 |
| 冷 `script_exec_ms`（**用户脚本自己那一段**） | **74.6** | worker 自报 |
| 导出 `wall_ms`（单面板 PDF，dpi 600 + PyMuPDF 合成） | 22 503.2 | `bench_render.py` |

`inline_svg=True` 那条**真实前端链路**单独量了一次（前端恒发它，见
`web/src/lib/api.ts`）：

| 指标 | 值 |
|---|---|
| worker 响应里的 `svg` | 126 132 735 字节 |
| Flask 交给浏览器的 JSON | **134 187 191 字节（128.0 MiB）** |
| 一次 render 之后 Flask 进程的峰值 RSS | **1 245 MB** |
| worker 进程峰值 RSS | not measured（`ru_maxrss` 只统计被 `wait()` 过的子进程，这条路径上它已被丢弃） |
| 浏览器 DOM 节点数 / WebView2 内存 | not measured —— 见下 |

### 这几个数字说明什么

1. **成本几乎全是 Tavotto 自己的预览 SVG，不是用户的脚本。**
   `script_exec_ms = 74.6`，而 `script_build_ms = 11 969.1`：用户的
   `pcolormesh` 调用 75 毫秒就返回了，剩下的 11.9 秒全花在**我们**把它
   序列化成矢量 SVG 上。「让用户自己 `set_rasterized(True)`」这条出路因此
   在数据上就站不住——慢的不是他的图，是我们的表示法。
2. **`canvas_draw_ms` 占热态的 98%。** 不是 manifest（206ms，1.7%）、不是
   patch apply（0.006ms）、不是排队（0ms）。任何不动预览表示法的优化
   （更快的 manifest、更好的队列、缓存）在这张图上最多省下 2%。
3. **JSON 比 SVG 还大 8 MB**：`ensure_ascii=False` 之后仍要转义，加上 manifest。
   issue #181 报的「134MB」正是这个数——它是 **worker → Flask → 浏览器**
   三跳里每一跳都要完整持有一遍的那个 payload。
4. **1.2 GB 峰值 RSS 出现在 Flask 侧，而 Flask 侧连 matplotlib 都没装。**
   放大发生在「读回 → 解析 JSON → 再编码 JSON」这三步的中间副本上，与
   渲染无关。**这正是「先 read 再判断太大」为什么不算保护**。
5. **663 533 个节点**是浏览器那一半的账。Phase G 量过 DOM 写入本身的代价
   （见上文），那里的量级是几十到几百个节点。

### 没测的，以及为什么

* **浏览器 / WebView2 的实际内存与冻结时长**：`dangerouslySetInnerHTML`
  一份 126 MB 的 SVG 正是 issue #181 的症状本身，在本机跑它只会得到一次
  无响应，量不出可比的数字。这项要在 ADR 0022 的安全闸落地**之后**，
  用受控规模（临近阈值）在真浏览器里测，属于后续 Session。
* **workerd 控制面**：本轮只测 Python 池。两条控制面在这条路径上走的是同一份
  `figsession.do_render`，差异在信封传输——大 payload 下它可能不一样，但那是
  另一个问题，没有数据就不写进来。
* **多面板并发**：#181 的用户环境是多个大 mesh 面板同时在画布上。本基线只测
  一个面板；并发下的排队行为在 Phase E 就已经标注为「完全没有数据」。

## 大图预览：Session 01 安全闸之后（issue #181）

日期：2026-08-28 ｜ 同一台机器、同一个 fixture（n=470）、同一条链路
（`pool.one_shot` + `override(inline_svg=True)`，前端恒发 `inline_svg`）。

| 指标 | 修复前 | 修复后 | |
|---|---|---|---|
| worker 响应里的 `svg` | 126 132 735 字节 | **不存在** | `preview.mode = raster` |
| `read_text()` 调用次数 | 1 | **0** | 判定在读之前（`stat().st_size`） |
| 交给浏览器的 JSON | 134 187 191 字节 | **≈ 97 400 字节** | **1378×** |
| 服务进程峰值 RSS | 1 245 MB | **≈ 25 MB** | **50×** |
| manifest 元素数 | 95 | 95 | 语义保真（不变量 1） |

（后两行取整：JSON 里带着 `timings`，那几个浮点数的位数每次不同，峰值 RSS
同样有百分之几的抖动。两次独立测量分别是 97 392 / 97 391 字节、
24.8 / 25.7 MB——量级是结论，末位不是。）

raster 档下用户实际看到的那张图（`preview_png`，宽度钉死
`previewbudget.RASTER_PREVIEW_WIDTH_PX = 1200`）：

| 指标 | 矢量预览 SVG | 位图预览 PNG | |
|---|---|---|---|
| 出图耗时 | 11 789 ms | **210 ms** | **56×** |
| 字节数 | 126 MB | **1.1 MB** | **112×** |
| base64 之后（MCP 那条路） | — | 1.5 MB | 受控，不是百 MB 级 |

### 还没解决的（这一轮刻意没碰）

* **`canvas_draw_ms` 一分钱没省**：12.3 秒的 `savefig(svg)` 照旧要跑一次
  ——安全闸只是不让那份产物进内存与 DOM，没有让它不产生。真正砍掉这一段是
  Session 02/03（复杂度分析器 + hybrid：mesh 层直接 rasterize，根本不生成
  那 66 万个 `<path>`）。**Session 03 已兑现，见下文。**
* **软闸（8–16 MiB）今天不改变任何行为**，见 ADR 0022 §4。**Session 03 起
  它生效**（越过它且名单没收满 ⇒ 全收、重画一遍）。
* **浏览器侧仍未实测**：闸落地之后可以用「临近阈值」的受控规模在真浏览器里
  量 DOM 节点数与 WebView2 内存了，但那是 Session 05 的事。

## 大图预览：hybrid 之后（issue #181，Session 03）

日期：2026-08-29 ｜ 同一台机器、同一个 fixture（n=470）｜出处
`python tests/support/preview_hybrid_probe.py --n 470 --bench`。

**A/B 在同一进程同一次运行里交替**：同一张 Figure、同一个会话，一次按正常
预算渲（hybrid），紧接着把六个闸全抬走再渲一次（纯矢量对照）。**不同时刻的
两次跑是两个样本，不是对照**——机器负载、热缓存、别的进程都会偏向其中一侧。
热态取 3 次中位。

| 指标 | 纯矢量（= 修复前） | hybrid | |
|---|---:|---:|---|
| 预览 SVG `svg_bytes` | 126 132 735 | **1 838 682** | **68.6×** |
| `<path>` | 662 772 | **72** | **9205×** |
| `<image>` | 1 | 4 | 三块 mesh 各一张 + 色条色带 |
| 热 `canvas_draw_ms`（中位） | 11 118.0 | **320.0** | **34.7×** |
| 热 `manifest_ms`（中位） | 185.6 | 185.7 | 不变（语义那一步没动） |
| 热 `preview_plan_ms`（中位） | 0.039 | 0.041 | 1 : 271 000 |
| 热 `total_ms`（中位） | 11 302.4 | **505.8** | **22.3×** |
| **冷 build**（`instrument_all`，含首次预览） | 11 420.5 | **537.4** | **21.3×** |
| `preview.mode` | vector | hybrid | |
| `preview.rasterized_artist_count` | 0 | 3 | |

纯矢量那一列**逐字节复现了修复前基线**（126 132 735 字节、662 772 个
`<path>`）——同一个 fixture、同一台机器，两个月前用另一条链路（HTTP +
`bench_render.py`）量的。对照组不是重新叙述一遍旧数字，是当场量出来的。

三次独立测量的 `canvas_draw_ms`：hybrid 339.2 / 331.6 / 320.0，vector
11 369.9 / 11 257.6 / 11 118.0；冷 build hybrid 564.5 / 556.2 / 537.4，
vector 11 371.8 / 11 433.6 / 11 420.5。**量级是结论，末位不是。**
（`preview_plan_ms` 含计时包装自身的开销；分析器的裸开销 0.0165 ms 见下一节。）

### 这几个数字说明什么

1. **省下来的正是「我们自己的表示法」那一段。** `canvas_draw_ms` 从 11.1 秒
   降到 0.32 秒，而 `manifest_ms` 一动没动（185.6 → 185.7 ms）——后者是语义
   那一步，它本来就不该变，不变就是不变量 1 的一个旁证。
2. **冷 build 与热 render 同步下降**（21.3× / 34.7×）。接线点只有
   `figsession.render()` 一处，两条路走的是同一段代码。只在 render request 上
   rasterize 的实现会让这张表的「冷 build」一行原地不动——用户第一次打开图
   仍然要等 11 秒，而那正是 issue #181 报的症状。
3. **预览 dpi 这个旋钮终于有用了。** 修复前它在纯矢量 mesh 上一分钱不值
   （dpi 72→300 耗时与体积一模一样）；hybrid 之后 mesh 层是 `<image>`，同一张
   n=200 的图 dpi 72 是 310 KB、dpi 200 是 600 KB。手势中降 dpi 这条既有策略
   从此在大图上真的省钱。
4. **1.8 MB 仍然不算小**，但它比 8 MiB 软闸低一个数量级，也比 16 MiB 硬闸低
   一个数量级——这张图从此走的是正常的内联 SVG 那条路，不再触发 raster 降级。
5. **72 个 `<path>`** 就是这张图上「语义编辑层」的全部：坐标轴、刻度、图例、
   第四格那两条普通曲线。**它们一个都没被 rasterize**——hybrid 的契约在数字上
   是这一行。

### 还没解决的

* **浏览器侧仍未实测**（DOM 节点数、WebView2 内存、首帧时间）。现在有了
  受控规模的产物（1.8 MB / 76 个节点），这一测终于跑得起来——Session 05。
* **`manifest_ms` 现在是热态里最大的一段**（185.7 ms，占 37%）。它里面有一次
  `fig.canvas.draw()`（量包围盒必须有 renderer）。Phase E 量过一次「去掉重复
  draw」，被数据否掉；在 hybrid 之后它的占比变了，值得重新量一次。
* **导出仍然是 22.5 秒**（单面板 PDF，dpi 600 + PyMuPDF 合成）。那是不变量 2
  要求的：导出走的是用户原来的矢量语义，一个 `<path>` 都不少。

## 前端 SVG payload 的驻留字节（issue #181，Session 04）

日期：2026-08-29 ｜ 出处 `web/src/store/svgMemoryBudget.test.ts` 的
「真实巨型 payload」一组（跑 `cd web && pnpm test`）。**四版各 12 MiB 的真
字符串**过一遍真的 `renderStore.render()`，不是声明出来的字节数。

**A/B 在同一次运行里交替**（同一进程、同一条 `render()` 代码路径，紧挨着的
两趟，只改输入）：对照那一趟让四个变体**全都挂在画布上**，于是每一份 payload
都被 pin 住、一份都驱逐不掉——那正是 Session 04 之前的驻留形状。

| 指标 | 全被 pin（= 本 Session 之前） | 预算可以动手 | |
|---|---:|---:|---|
| 驻留 SVG payload（`residentSvgBytes`） | 48 MiB | **12 MiB** | **4×** |
| `byKey` 条目 | 4 | 4 | 不变 |
| 带 manifest 的条目 | 4 | 4 | 不变（不变量 4） |
| 还挂着 `svg` 的条目 | 4 | 1 | |

把两个预算常量临时改成 `Number.MAX_SAFE_INTEGER` 再跑一遍，得到的是同一组
数（48 / 144 MiB）——两种「关掉预算」的方式互相印证。

**这几个数是用两把独立的尺子量的。** 一把是 `residentSvgBytes`（我们自己的
记账），另一把是 V8 的 `process.memoryUsage().heapUsed`（`v8.setFlagsFromString
('--expose-gc')` + `vm.runInNewContext('gc')` 取到真 gc，连跑四次再读）：

| 场景 | 记账 | V8 heapUsed 增量 |
|---|---:|---:|
| 1 文件 × 4 变体 × 12 MiB，预算关 | 48 MiB | 48.1 MiB |
| 1 文件 × 4 变体 × 12 MiB，预算开 | 12 MiB | 12.1 MiB |
| 3 文件 × 4 变体 × 12 MiB，预算关 | 144 MiB | 144.2 MiB |
| 3 文件 × 4 变体 × 12 MiB，预算开 | 36 MiB | 36.2 MiB |

**第一次量的时候两侧的 heapUsed 一模一样**（42.5 MiB vs 42.5 MiB），看起来像
「预算没省下任何内存」。真正的原因是 `NODE_OPTIONS=--expose-gc` **传不到
vitest 的 worker 线程**，`globalThis.gc` 是 undefined，量到的其实是「这一趟总共
分配了多少」——两侧当然相同（都渲了 4 × 12 MiB），与留下多少无关。尺子量不了
那个维度时，它会恒等成立，而恒等成立看起来就像「没有差别」。

### 这几个数字说明什么

1. **省的是 payload，不是语义。** 两侧的 `byKey` 条目数与带 manifest 的条目数
   **逐个相同**——撤销落点、版本恢复、几何权威一个都没少。这是本节唯一重要
   的一行：「budget exceeded → delete PanelRender」能省下同样的字节，代价却是
   把这一列一起清零。
2. **3 文件那一档触发的是每文件预算，不是全局预算**（36 MiB < 64 MiB）。两条
   闸各管一维：一个大文件的老档不该把别的文件的近期档挤掉。
3. **12 MiB 这个量级不是随手编的。** Session 03 之后 #181 那张图的预览 SVG 是
   1.8 MB；12 MiB 对应的是更大的数据集，或者矢量层本身就巨大、hybrid 收不动
   的那一类。普通科研图是几十到几百 KB，4 档合起来 1 MB 上下，**这套预算在
   它们身上一次都不会触发**（`预算之内的普通图一份都不丢` 那条用例钉着）。

### 还没量的

* **浏览器里的真实堆**（WebView2 / Chromium 渲染进程）。上面量的是 Node 的
  V8 堆，与浏览器同一个引擎但不是同一个进程；DOM 那一侧的开销更是另一回事
  ——Session 05 一起测。
* **驱逐之后重画一次的往返成本**。撤销回到被清掉的那一档时同步器会重排一次
  渲染；在 heavy 脚本上那是分钟级，而画面在此期间由引擎位图顶着（内嵌画布里
  没有位图可顶）。这条取舍值得在真机上量一次。

## 复杂度分析器开销（issue #181，Session 02）

日期：2026-08-29 ｜ 同一台机器、同一个 fixture ｜ 判据实现
`src/tavotto/engine/preview_complexity.py`，阈值 `engine/previewbudget.py`。

分析器进 render 热路径，所以它要回答的问题不是「快不快」，而是**「与它要省下
的那件事相比够不够便宜」**。分母因此取 `savefig(svg)`——那正是 complexity-aware
hybrid 要砍掉的那一段。

复现：

```bash
python tests/support/preview_complexity_probe.py \
    --issue181-n 470 --bench --bench-repeat 7
```

| 图 | 分析器（7 次取中位） | `savefig(svg)` | 比 |
|---|---|---|---|
| 普通科研图（两条曲线 + 图例，SVG 20 KB） | **0.0055 ms** | 11.6 ms | **1 : 2 109** |
| #181 fixture（n=470，SVG 126 MB） | **0.0165 ms** | 10 540 ms | **1 : 638 782** |

抖动（min/max）：普通图 0.0051 / 0.0144，fixture 0.0160 / 0.0406——**两张图的
分析耗时是同一个量级**，因为判据吃的是 artist 数量与 shape 元数据，不是数据量。
一块 220 900 个 cell 的网格与一块 576 个 cell 的网格，读的都是
`get_coordinates().shape` 那一个元组。

### 分析器算出来的账 vs 后端真的画出来的

| | 模型（`savefig` 之前） | 后端实测（`savefig` 之后） |
|---|---|---|
| primitive | **662 704** | 662 772 个 `<path>` + 1 个 `<image>` |
| vertex | 3 314 300 | —— |

差的 69 个是坐标轴、刻度、图例边框那些结构件——分析器**有意不数它们**（一个
是一个节点，撑不爆 DOM），所以这不是误差而是口径。落在数据层上的 99.99%
它都算到了。

逐族的精确度另有对拍看护（`tests/test_preview_complexity.py::test_model_matches_what_the_svg_backend_actually_emits`）：
同一张图带 / 不带那个 artist 各 `savefig` 一次，差分出它自己摊出来的节点数与
顶点数。**十二格里 primitive 全部逐个相等**；vertex 七格精确相等、
`poly_tail_heavy` 0.948、contour 0.916，另三格（已是位图 / 两格不可见）两侧
都是 0——那三格比的是「都为 0」，不是比值，0/0 的判据挡不住任何东西。

### 这几个数说明什么

1. **判定可以无条件地做。** 0.0055 ms 对普通图是白送的——不需要「只在大图上
   才分析」这种会自己制造分类错误的开关。
2. **它指向的节省是 10.5 秒里的绝大部分**，而不是 12 秒里的 2%（对比
   Session 01：安全闸让产物不进内存与 DOM，`canvas_draw_ms` 一分钱没省）。
   真正兑现这笔节省是 Session 03——Session 02 只产出「该 rasterize 谁」的名单。
   **兑现了**：`canvas_draw_ms` 11 118.0 → 320.0 ms，见上一节。
3. **分析器不看数据量**：n 从 24 涨到 470（数据量 383 倍）它只从 0.013 ms 涨到
   0.0165 ms。会随规模涨的实现基本都在某处遍历了 path 或复制了数组，
   `test_quadmesh_paths_are_never_built` 与那条 50 ms 的粗闸盯的就是它。

## 浏览器侧首次实测（issue #181，Session 06）

日期：2026-08-29 ｜ 出处 `tests/support/browser_dom_probe.mjs` ｜
**这是 01–04 每一节都写着「还没量」的那一项**：产物挂进真 Chromium 之后，
DOM 有多少节点、渲染进程吃多少内存、开关几次会不会涨。

### 方法（以及一个先量错了的对象）

* 挂法与 `PanelView` 一致：一次 `innerHTML` 塞进去，等两帧再读数。
* 节点数与 JS 堆取自 **Chromium 自己的** CDP `Performance.getMetrics`
  （`Nodes` / `JSHeapUsedSize`），不是我们数标签数出来的。
* 卸载后 `HeapProfiler.collectGarbage` 强制回收再读一次，量的才是
  「留下了多少」而不是「分配了多少」。

**渲染进程 RSS 第一版量错了对象**：按 `ps | grep -- '--type=renderer'` 全局
求和，得到 5.7 GB——那是这台机器上**所有** Chromium（含用户自己开着的浏览器）。
它稳定、可复现、量纲也对，唯独主语不是被测对象。正确取法是启动前快照已有的
renderer pid、启动后取差集，只认新出现的那个。见
`docs/adr/0022-complexity-aware-editor-preview.md` §9。

### 两把尺子不能互相相除

| 尺子 | #181 修复前 | hybrid 之后 |
|---|---:|---:|
| SVG 元素总数（数标签，与 before 基线同一把） | 663 533 | **818** |
| Chromium `Nodes`（浏览器自己数） | not measured | **2 887** |

同一批产物上两者差 **2.0–3.5 倍**，且比值不是常数（四种形状实测：
paths ×2.0、lines ×2.5、dense ×2.6、mesh ×3.5——越是「元素少但每个都带
包裹 `<g>`」的图，倍数越高）。**修复前那一列的
`Nodes` 永远不会有真值**——把 126 MB 挂进 DOM 就是 #181 的症状本身，量它
只会得到一次无响应。所以「663 533 → 2 887」这种写法是错的，两个数出自两把尺。

### hybrid 产物（n=470，1 838 682 字节 / 818 个元素）

| 指标 | 值 |
|---|---:|
| DOM 节点（CDP `Nodes`） | **2 887** |
| JS 堆（挂着时） | **2.47 MB** |
| 渲染进程 RSS | **107–125 MB**（空白页基线 73 MB） |
| 解析 + 插入（`parseMs`） | 70–84 ms |
| **到画出来（`renderedMs`）** | **80–92 ms** |

**两个计时数，谁都不冒充谁。** `parseMs` 只到 `innerHTML` 赋值返回；用户卡住
的是 `renderedMs`（再等两帧，布局与首次栅格化都算进去）。上一版只报前者、
却把它叫「挂载耗时」——那个数漏掉了一段真实的停顿，而漏掉的比例随规模涨：
mesh 上 +16%，40 000 条折线上 261 → 399 ms（**+53%**）。

### 生命周期：开关 5 次

| 轮次 | 挂载 nodes / heap / RSS | 卸载 + GC 后 nodes / heap / RSS |
|---|---|---|
| 1 | 2 897 / 6.25 MB / 107 MB | 6 / 0.62 MB / 102 MB |
| 2 | 2 887 / 2.47 MB / 112 MB | 6 / 0.62 MB / 107 MB |
| 3 | 2 887 / 2.47 MB / 117 MB | 6 / 0.63 MB / 112 MB |
| 4 | 2 887 / 2.47 MB / 121 MB | 6 / 0.63 MB / 116 MB |
| 5 | 2 887 / 2.47 MB / 125 MB | 6 / 0.63 MB / 119 MB |

**DOM 与 JS 堆每一轮都完全归位**（6 个节点 / 0.63 MB）。RSS 五轮共涨
17–18 MB——allocator retention，不是 0.8 / 1.4 / 2.0 / 2.6 / 3.2 GB 那种
线性增长。**不要把 Chromium 正常的 retention 叫成 Tavotto 泄漏。**

### 字节闸看不见节点数

四条复杂度预算量 primitive 数，两条字节闸量产物字节数。**没有一条量节点数**
——而渲染进程的代价按节点走。

下表每一行都由 `tests/support/large_preview_svg.py --shape <名字>` 生成、由
`tests/support/browser_dom_probe.mjs` 实测（命令见下面「复现」）。**shape 是
参数不是描述**：换一台机器重跑同一条命令得到同一张图（数据全部出自
`rng(181)`）。

| shape | 判据裁决 | `svg_bytes` | SVG 元素 | DOM 节点 | 解析 | **到画出来** | RSS |
|---|---|---:|---:|---:|---:|---:|---:|
| `mesh --n 470` | `hybrid` / `complexity_budget` | 1 838 682 | 818 | 2 887 | 70–84 ms | 80–92 ms | 107–125 MB |
| **`lines --n 40000`** | **加节点闸之前：`vector` / `normal`——无闸触发**<br>今天：`raster` / `complexity_budget` | 9 334 177 | 80 156 | **200 531** | 256–265 ms | **396–402 ms** | 317–359 MB |
| `dense --n 560` | `raster` / `svg_hard_limit` | 22 469 114 | 1 278 | 3 336 | 894–933 ms | 936–970 ms | 216–235 MB |
| `paths --n 40000 --vector`（形状对照） | 抬闸 | 25 580 012 | 40 157 | 80 537 | 532–548 ms | 636–654 ms | 348–363 MB |
| 上一行 `--copies 4`（形状对照） | 每面板都合规 | 102 320 048 | 160 628 | **渲染进程被打死** | — | — | — |

空白页基线：13 个节点 / 0.61 MB 堆 / 73–74 MB RSS。

**第二行曾经是可达的，本轮把它堵上了**：`estimated_primitives = 40 000` 在
`TOTAL_VECTOR_PRIMITIVE_BUDGET`（50 000）之内；8.90 MiB 越过了 8 MiB 软闸，
但软闸调 `escalate_plan` 时 `line` 按契约不在 `RASTERIZABLE_FAMILIES` 里，
返回 `None`；16 MiB 硬闸也没到。**四条复杂度预算加两条字节闸，一条都没拦住
它**，而它在浏览器里是 20 万个 DOM 节点、约 375 ms 的首帧、350 MB 渲染进程。
表里那一行写的是**加节点闸之前**的裁决；今天它落 `raster` /
`complexity_budget`（见下）。
40 000 次 `plot()` 是普通 matplotlib 写法。多面板画布把它乘上面板数，而
**每个 live 面板都被 pin 住、按设计永不驱逐**（Session 04 §7）。

**最后一行不是「慢」，是死**：102 MB / 16 万个元素挂进去，渲染进程直接没了
（CDP 报 `Target page, context or browser has been closed`）。那正是 issue
#181 报告的那个结局。

**字节数不是节点数的代理，两个方向都成立**：

* `dense` 22.5 MiB 只有 3 336 个节点——字节全在 path 的 `d` 属性里。这一条
  **字节闸看见了**（越过 16 MiB 硬闸 → `raster`）。
* `lines` 8.90 MiB 却有 20 万个节点——**字节闸看不见**，因为它根本没超。

贵的是**大量细小的、不可 rasterize 的 primitive**，不是大量字节。按字节推断
「密集曲线是风险」只对了一半：密集曲线确实贵，但拦住它的是字节那把尺子；
折线海便宜得多（字节上），却曾经是唯一一个没有任何一把尺子拦得住的。

### 缺口补上了：阈值锚在哪

`TOTAL_VECTOR_NODE_BUDGET = 24 000` 判「收完之后矢量层还剩多少个 SVG 元素」。
阈值不是拍的，锚在**100 ms 这条人机边界**上——下面三档是同一条命令
（`--shape lines --n <N>`）扫出来的，浏览器读数取自各自的 `--vector` 对照：

| 条线 | 模型 `estimated_nodes` | SVG 元素 | DOM 节点 | 解析 | **到画出来** | 单面板 RSS | 今天的裁决 |
|---:|---:|---:|---:|---:|---:|---:|---|
| 10 000 | 20 000 | 20 156 | 50 532 | 58–63 ms | **98–102 ms** | 142–156 MB | `vector` |
| 20 000 | 40 000 | 40 156 | 100 533 | 120–129 ms | 187–195 ms | 203–223 MB | **`raster`** |
| 40 000 | 80 000 | 80 156 | 200 534 | 235–252 ms | 363–386 ms | 317–356 MB | **`raster`** |

阈值 24 000 正好落在第一行（20 000）与第二行（40 000）之间，而**那两行的
「到画出来」正好跨过 100 ms**。用的是 `renderedMs` 不是 `parseMs`：按解析口径
第一行才 60 ms，看起来还能再放宽一倍——那正是口径错了会做出的错决定。

`dense`（560 × 2000 点）**故意留着不拦**：它只有 1 278 个元素，代价在**字节**
而不是节点，那是字节闸的辖区，节点闸看不见也不该看见。它今天由 16 MiB 硬闸
接住，落 `raster` / `svg_hard_limit`。

`Line2D` 每条摊 **2.01** 个元素（一个 `<path>` + 一个 `<g>`，对拍实测 400 条 =
400 path + 400 g），逐实例着色且几何共享的散点同样翻倍（500 个 `<use>` +
501 个 `<g>`）。**primitive 数与节点数在 mesh 上恰好相等，在这两族上差一倍**
——`primitive_count` 那条对拍证明不了节点口径，所以补了第二条。

### 回归看护落在哪儿（Session 05）

上面这些数字要一直成立，靠的是三层，各管一维：

| 层 | 在哪 | 判据 | 是不是门禁 |
|---|---|---|---|
| 结构（DOM 节点 / `<path>` 数） | `web/e2e/large-figure.spec.ts` | 落到 hybrid/raster、整页节点 < 20 000、`<path>` 比纯矢量低两个数量级 | **是**（随 `pnpm e2e` 进 `windows-exe-smoke`） |
| 可观测（这一版是哪一档） | 诊断快照 `schema_version: 2` | 每个面板报 `preview_mode` / `preview_reason` / `svg_resident` / `svg_evicted`，顶层报 `preview_memory` | 不适用 |
| 绝对内存（渲染进程） | `large_preview_svg.py` + `browser_dom_probe.mjs --browser msedge` | 渲染进程 RSS / JS 堆 / DOM 节点，5 轮 open/close | **否**——出 JSON 产物 |

**为什么内存那条不做门禁**：绝对内存在托管 runner 上会飘，按字节做一条闸只会
得到一个随机红的检查，而随机红的检查最后一定会被人忽略掉——比没有检查更坏。
结构性的那条不飘：同一台机器上节点数是确定的。

### 还没量的

* **Windows WebView2 的 renderer private bytes**：本机没有 Windows 环境，
  **not locally measured**。issue 原始报告是 6.47 GB；上面所有数字来自
  macOS 上的 headless Chromium，**不是同一个渲染引擎、不是同一个平台**，
  不能拿来宣称 §8 的内存验收项已满足。CI 接线已经就位
  （`windows-exe-smoke` 里那两条命令，`--browser msedge`），**但那一档只在
  merge_group / `full-ci` 上跑，本地无从复现**。Edge 与 WebView2 同一个
  Chromium 引擎、同一个平台，比 macOS 上的 headless Chromium 近得多，
  **但它不是 WebView2 本身**——产物里 `browserChannel` 如实写着量的是谁。
* **完整编辑器里的读数**：上面挂的是裸页面里的一份预览 SVG，量的是这一份
  payload 的 DOM 代价本身（也就是 #181 归咎的那一步）。真实画布还有工具栏、
  命中层、检查器，绝对值会更高。

## 复现

```bash
# 基线（两条控制面）
python scripts/bench_render.py --python .venv/bin/python --repeat 9

# 只测一条控制面 / 换图库 / 量预览 dpi 这个旋钮
python scripts/bench_render.py --python .venv/bin/python --plane python \
    --figures ~/papers/figures --preview-dpi 100

# 「新机器上的第一次」（含字体缓存重建，不是稳态）
python scripts/bench_render.py --python .venv/bin/python --fresh-home
```

`--json` 会把每次测量的原始数字（含被中位数吃掉的那些）落盘，方便事后做前后
对比。**这一句说的是 `scripts/bench_render.py`**——下面那个浏览器探针不认识
`--json`，传给它会当场退出（上一版会把它 `Number()` 成 `NaN`、一轮都不跑，
再输出一份空结果）。

### 浏览器侧（issue #181，Session 06）

上面「字节闸看不见节点数」那张表，每一行的两条命令：

```bash
# 1. 走真链路出一版预览 SVG（**worker 解释器**，要 matplotlib）
python tests/support/large_preview_svg.py /tmp/mesh.svg  --shape mesh  --n 470
python tests/support/large_preview_svg.py /tmp/lines.svg --shape lines --n 40000
python tests/support/large_preview_svg.py /tmp/dense.svg --shape dense --n 560
python tests/support/large_preview_svg.py /tmp/paths.svg --shape paths --n 40000 --vector

# 2. 挂进真 Chromium，量节点数 / JS 堆 / 渲染进程 RSS，开关 5 次
node tests/support/browser_dom_probe.mjs /tmp/mesh.svg --cycles 5

# 多面板那一行：同一份 payload 并排挂 4 次（渲染进程会死，探针如实记一行）
node tests/support/browser_dom_probe.mjs /tmp/paths.svg --cycles 2 --copies 4
```

第 1 步的解释器**不能用 `.venv`**：按依赖边界它刻意不装 matplotlib
（`tests/conftest.py` 头一句写着这件事），要的是 worker 那一侧的那个。
第 2 步要 `web/node_modules`（`cd web && pnpm install`）与 Playwright 的
Chromium（`pnpm exec playwright install chromium`）。

## 发布终审（Session 23，2026-09-02）：main vs 产品体验分支的交错 A/B/C

机器：Apple M4 Pro / 24 GB / macOS 26.6.2；解释器 `.venv`（Python 3.13.11）+ worker
`/opt/homebrew/opt/python@3.13`（matplotlib 3.10.8）；图库 `examples/figures`；
`scripts/bench_render.py --plane python --repeat 7`，A / B / C **交错**各跑 3 轮取中位
（不同时刻的两次跑是样本不是对照）。A = `origin/main`（`c12c229c`），B = 分支 `9d8dadc5`，
C = 分支去掉 fonttype 42 那一笔（`5d35686e`）。

| 面板 | 指标 | A main | B 分支 | C 分支-42 |
|---|---|---|---|---|
| Fig1_kinetics | 冷 wall | 452.0 | 472.9 | 470.7 |
| | 冷 script_build | 95.0 | 108.6 | 110.0 |
| | 热 wall（中位） | 31.3 | 36.2 | 36.0 |
| | 热 manifest | 17.7 | 22.4 | 22.1 |
| | 热 canvas_draw | 11.4 | 11.5 | 11.4 |
| | 导出 wall | 63.8 | **98.2** | 63.7 |
| Fig2_correlation | 热 wall / manifest | 28.0 / 17.2 | 31.2 / 20.0 | 30.9 / 19.8 |
| | 导出 wall | 55.2 | **74.9** | 54.6 |
| Fig2_yield | 热 wall / manifest | 18.9 / 10.0 | 21.0 / 12.0 | 21.2 / 12.2 |
| | 导出 wall | 20.4 | **54.6** | 20.9 |

结论（单位 ms）：

* **热渲染慢 ~15%，全部落在 manifest 步骤（+27%，约 5 ms/次）**，`canvas_draw` 持平。是 13–22
  十轮的累计代价（B ≈ C）。`manifest._glyph_scan` 每次 7 µs，不是它。记 P2（T-127）：
  **阈值**——manifest 超过 main 的 1.3× 算回归、2× 升 P1；复现：上面那条命令在两棵树上交错跑。
* **导出多出的 20–35 ms 全部来自 fonttype 42**（C 与 A 持平）：TrueType 子集化的固定代价，换来
  完整的 PDF 文本层与 Type 0 嵌入（T-122）。微基准：单张简单图 `savefig(pdf)` 10 → 28 ms，
  字节 12.5 KB → 9.9 KB。
* 冷启动 +4%（script_build +14%，绝对 13 ms）：worker 端 import 面变大，同一族。

### 文档层（`scripts/bench_document.py`，新增）

| 对象数 | 字节 | validate | write_json | read | revision | autosave PUT | autosave GET | 版本追加（已满 120） |
|---|---|---|---|---|---|---|---|---|
| 100 | 23 KB | 0.0 | 0.29 | 0.15 | 0.03 | 0.74 | 0.10 | 9.3 |
| 1 000 | 230 KB | 0.0 | 1.4 | 1.3 | 0.1 | 4.25 | 0.17 | 102.5 |
| 5 000 | 1.16 MB | 0.0 | 7.3 | 7.2 | 0.39 | 20.8 | 0.47 | **547** |

自动保存本身是线性的（1 MB 文档 21 ms 往返）。**版本时间线每条塞整份文档**：上限是条数（120）
不是字节，1 MB 的文档塞满后单文件约 140 MB，每次追加要「读 → 追加 → 裁 → 整写」547 ms。
记 P2（issue 见 STATUS 遗留表）；阈值：1 000 对象的追加超过 250 ms 升 P1。

### watcher 空闲（真进程，`examples/figures`，渲染一次后）

6 个 5 秒采样：app + worker 合计 CPU **0.0–0.1%**，RSS 147 MB，主进程 3 线程 / 56 fd；
`touch` 三个脚本后三次采样 CPU 仍 0.0%（批次合并成一次刷新，日志 3 行）；`kill` 后无孤儿子进程。

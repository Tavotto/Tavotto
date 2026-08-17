# 渲染性能基线（Phase E）

日期：2026-08-18 ｜ 复现命令：`python scripts/bench_render.py --python .venv/bin/python --repeat 9`

这份文档是**测出来的**，不是估出来的。它存在的理由只有一个：Magplot 之后的任何
「优化」都必须先在这里指出一个具体的数字，改完再回到这里给出同一张表的前后对照。
没有数字支撑的改动一律不做——那不是优化，是赌。

## 机器与版本

| 项 | 值 |
|---|---|
| 机器 | Apple M4 Pro（12 核）/ macOS 26.6.1 / `macOS-26.6.1-arm64-arm-64bit-Mach-O` |
| Flask 侧 | Python 3.13.11 + Flask 3.1.3 + PyMuPDF 1.28.2（`.venv`，无 matplotlib） |
| 渲染 worker | Python 3.13.11（Homebrew，来源 `system`）+ matplotlib 3.10.8 + numpy 2.4.3 |
| supervisor | `magplot-workerd` release 构建（cargo 1.95.0） |
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

### 控制面：Python 池（`MAGPLOT_WORKERD=0`）

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
4. **并发下的排队行为完全没有数据**（观察 2）。「用户连拖十几下」是 workerd
   合并队列的立项理由，却从来没被量过。**归属：需要一个并发压测脚本**，
   本阶段没做，也不该靠猜。
5. **manifest 的逐元素 `get_window_extent`（8.5ms / 26 元素）**：目前占热态约
   30%，但它是「量每个元素在哪」这件事本身的成本，没有明显的重复计算可砍。
   真要动得先有更大的图库样本（本基线只有 25–68 个元素的小图）。**归属：待定，
   证据不足。**

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

`--json` 会把每次测量的原始数字（含被中位数吃掉的那些）落盘，方便事后做前后对比。

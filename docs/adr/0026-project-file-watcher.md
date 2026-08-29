# ADR 0026：项目级文件 watcher —— 快照、批次与自写循环

状态：**Accepted**
日期：2026-08-29
相关：[0025 项目刷新的唯一后端入口](0025-unified-project-refresh.md)（watcher 是它的第四个调用方，也是最后一个）、
[0001 项目/画布/标签/对象层级](0001-project-canvas-tab-object.md)（项目隔离的层级出处）、
[0024 保存生命周期与外部修改](0024-save-lifecycle-and-external-change.md)（`.tavotto` 文档的外部修改走那条路，**不走这里**）、
本轨道文档 [`docs/implementation/product-ux-reliability/`](../implementation/product-ux-reliability/STATUS.md)。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| watcher 住哪 | 新模块 `engine/project_watch.py`；`pool.py` 里的旧实现**删除**，不留兼容代理 |
| 判据 | **整个项目目录的轻量快照**：文件集合 + 每个文件的 `(size, mtime_ns)` |
| 为什么两维 | mtime 抓就地改写；size 抓粗粒度时间戳文件系统上的同秒两次保存 |
| 遍历规则 | 脚本用 `discover.iter_all_scripts()`，素材用 `project_refresh.iter_assets()`——**都不新写第三份判据** |
| 实现方式 | 纯标准库轮询（默认 2 s）；不引入 watchdog / FSEvents / Tauri 监听 |
| 它做什么 | **只发现**。发现之后调 `app.refresh_project(reason="watcher")` |
| 它不做什么 | 不 `discover.merge`、不 reload 注册表、不发 `registry.changed` / `assets.changed`、不 probe、不跑用户脚本 |
| 批次合并 | 防抖 0.5 s（新变化把批次结束往后推）+ **批次年龄上限 5 s** |
| 快照何时更新 | **在结算之前**——刷新执行期间到达的写入进下一批，不丢 |
| 自写识别 | `project_refresh.is_self_written()`（内容修订号），**不是时间窗口**；摘掉的只是注册表那几个路径，不是整批 |
| 目录暂时不可用 | 这一轮什么都不做（快照返回 `None`），**不当成"全删了"** |
| 生命周期 | 每项目一个；同路径重复 `start()` 停旧的；`close_project()` / `reset_projects()` 收 |
| watcher 自己发的事件 | 只有 `panel.file_changed` 与 `project.error` |

---

## 1. 背景：老 watcher 守住的只有一种形状

`pool.start_watcher(figures_dir, scripts, on_change)` 的循环体是这样的：

```python
for s in tracked:                      # tracked = 注册表里那张脚本清单
    try:
        mt = (fig_dir / s).stat().st_mtime
    except OSError:
        continue                       # ← 删除在这里被吞掉
    if tracked[s] is not None and mt != tracked[s]:
        changed.append(s)
```

它能发现的只有**一件事**：清单上的某个脚本被就地改写了。逐条看：

| 用户做的事 | 老 watcher | 为什么 |
|---|---|---|
| 新建 `fig3.py` | 发现不了 | 不在 `tracked` 里，而 `tracked` 只在重挂时才更新 |
| 删除 `fig1.py` | 发现不了 | `stat()` 的 `OSError` 被 `continue` 吞掉 |
| 重命名 | 发现不了 | 一次删除 + 一次新增，两头都看不见 |
| 原子替换（编辑器的标准保存法） | **不可靠** | 判据的主语是"那个文件"，而 rename 覆盖之后它已经是另一个 inode |
| 在编辑器里改 `tavotto_registry.json` | 发现不了 | 不是 `.py`，不在清单里 |
| 往图库里丢一张新 PDF | 发现不了 | 同上 |

前四条里，**原子替换那一条最值得说**：`write tmp → fsync → os.replace(tmp, target)`
是 VS Code、Vim（`backupcopy=no`）、大多数编辑器与我们自己的 `atomicio` 的
保存方式。老判据问的是"**这个文件**的 mtime 变了没有"，而它拿到的
`Path` 在替换之后指向的是一个新对象——旧的那个已经不存在了。判据量错了
主语，于是"能不能发现"取决于 `stat()` 恰好在替换前还是替换后执行。

## 2. 裁决：判据换成"这条路径现在是什么"

新判据是**整棵树的快照**：

```
scripts   : rel_key(POSIX) → (size, mtime_ns)     # discover 的剪枝与深度
registry  : 文件名          → (size, mtime_ns)     # 新名 + 旧名
assets    : 素材 id         → (size, mtime_ns)     # iter_assets 的口径
```

集合变了 = 新增 / 删除 / 改名；签名变了 = 就地改写 / 原子替换。主语从
"那个文件对象"换成"这条路径当前的状态"，上表七行于是全部落进同一个机制。

### 2a. 为什么签名是两维

`mtime_ns` 一维不够：FAT32、部分网络盘、老 HFS+ 的时间戳精度只到 1~2 秒，
一秒内的两次保存 mtime 完全相同。`size` 一维也不够：改一个数字、换一个
颜色名，长度常常一字节不差。**两维都要，而且各自都有用例看着**
（`test_signature_notices_a_same_mtime_rewrite` /
`test_signature_notices_a_same_size_rewrite`）——只留一维的变异会被打红。

刻意**不做内容哈希**：空闲轮询要对整个图库的 PDF/PNG 逐个读全文，大项目上
是秒级开销，而这里要回答的只是"动过没有"。真正需要"内容一样不一样"的地方
（自写识别、渲染缓存键）各自按需算一个文件的哈希。

### 2b. 遍历规则不新写第三份

脚本走 `discover.iter_all_scripts()`（`PRUNE_DIRS` / `MAX_DEPTH=4` / 隐藏项），
素材走 `project_refresh.iter_assets()`。这是刻意的：

* watcher 盯得**比 discover 宽** → 为一个永远进不了注册表的文件反复刷新；
* 盯得**比它窄** → 用户新建的脚本发现不了；
* 素材那把尺与 `/api/panels` 不一致 → "用户看得见的图改了却不刷新"，
  或者反过来，刷新报一张列表里根本没有的图。

代价是每轮两次遍历（脚本一次、素材一次）。合并成一次要写第三份分类判据，
而三份判据里迟早有一份漂移——那比两次 `os.walk` 贵得多。`iter_all_scripts`
用的是含基础设施脚本的那个视图：`paper_style.py` 被 `SKIP_PREFIXES` 挡在
自动起草之外，而它恰恰是最需要盯的一个。

### 2c. 为什么还是轮询

原生事件（inotify / FSEvents / ReadDirectoryChangesW）省 CPU，但：

* 每个平台的语义都不一样（重命名报几条、原子替换报什么、网络盘上报不报），
  于是"测试里绿的"和"用户机器上跑的"会是两个东西；
* 我们真正要的判据是"**现在**这棵树长什么样"，轮询能直接回答，而事件流要
  自己维护一份状态去逼近它；
* 桌面、浏览器和测试必须共享同一个后端 watcher（共享规则 §6）。

空闲开销是每 2 秒一次剪枝遍历 + `stat()`，不读任何内容。

## 3. 批次：一次保存最多一次刷新

```
poll:  拍快照 → 与上一张比 → 有差异就并进 pending，并把"最后一次变化"推到现在
       pending 非空 且 (安静够久 或 批次够老) → 结算这一批
```

* **防抖 0.5 s**：一次编辑器保存常常是"写临时文件 → rename → 稍后生成图片"
  几步，跨过轮询边界时会被拆成两轮，防抖把它们并回一批。
* **年龄上限 5 s**：防抖等的是"安静"，而目录**可能永远不安静**（脚本正在跑、
  正在拷一个大目录）。没有封顶的话刷新会被无限期推迟——"不允许无限等待
  目录永远安静"。
* 两个参数都可注入，用例用假时钟**逐轮**驱动 `poll()`，于是"这两次保存被
  并成了一批"是一句确定的断言，不是 `time.sleep(2.5)` 之后碰运气。

**快照在结算之前就换掉**（`self._snapshot = snap` 排在 `_dispatch()` 之前）。
反过来写的话，刷新执行期间落盘的文件会被算进已经结算的那一批，下一轮 diff
为空——用户看到的是"保存了没反应"，而这类缺陷极难查，因为它只在刷新耗时
跨过一个轮询周期时才出现。

同一项目的刷新本来就串行（`RefreshState.lock`），加上循环本身是单线程，
"不允许并发两个同项目 refresh"是结构性的。

## 4. 自写循环：认内容，不认时间

统一刷新自己会写 `tavotto_registry.json`，watcher 下一轮必然看到它。认不
出来的话，每一次刷新都会触发下一次刷新。

判据是 `project_refresh.is_self_written()`——**磁盘上这份的内容修订号，
是不是我们上一次写下/读到的那一份**。它优于"写完之后忽略两秒"的两个理由：

* 时间窗在慢磁盘上不够长（我们的写入还没落地，窗口已经过了）；
* 在快机器上又会吞掉用户**紧接着**做出的真实修改。

内容比较两头都不会错，连"用户把文件改回原样"都判得对（内容没变 = 确实不用
刷新）。

**摘掉的只是注册表那几个路径，不是整批。** 一次保存完全可能同时改了脚本、
生成了图片，并让刷新回写了注册表——那时仍然要刷新，否则用户新加的那张图
会被自写防护顺手吞掉（`test_a_self_written_registry_does_not_swallow_the_rest_of_the_batch`）。

## 5. 目录不可用 ≠ 目录空了

网盘掉线、外接盘没挂上、用户临时把目录改了名——遍历会得到一张空表，而
**空表与"用户删光了所有文件"在 diff 里长得一模一样**。照它行事的后果是
一次网盘抖动打掉整个项目的渲染会话、并触发一次全量"删除"刷新。

所以 `take_snapshot()` 在目录不可用时返回 `None`，这一轮什么都不做，也不动
上一张快照。目录回来之后，下一次 diff 仍然会把这段时间里真正发生的变化算
出来——快照比的是两个**状态**，不是重放事件流，所以中间漏看几轮不丢信息。

"不高 CPU"是结构性的：每轮之间有 `stop_event.wait(interval)` 挡着，而目录
不在时连遍历都不进（用例把遍历入口打成"一碰就炸"来量这一条，不靠计时）。

## 6. 事件的归属

| 事件 | 谁发 | 什么时候 |
|---|---|---|
| `registry.changed` | **统一刷新** | 注册表结构真的变了（ADR 0025） |
| `assets.changed` | **统一刷新** | 素材清单真的变了（ADR 0025） |
| `panel.file_changed` | **watcher** | 已登记脚本的**内容**变了，且文件还在 |
| `project.error` | **watcher** | 后台刷新失败（可恢复；新增，前端本阶段只有类型） |

`panel.file_changed` 归 watcher 是有理由的：它不是"派生数据变了"，而是
"这张图的源码变了，请重渲染"——刷新看不见这件事（脚本内容改变常常不改变
注册表结构）。反过来，watcher **不许**自己再发一份 registry/assets 事件：
前端会收到两条互相矛盾的 diff。

**已删除的已登记脚本不发 `panel.file_changed`**：对一个源文件已经不在的
面板发"内容变了"，前端照做只会得到一个渲染错误。"源文件不见了"是就绪度的
事实（Prompt 07），由刷新的 `registry.changed` 与后续的就绪度模型表达。

### 6a. worker 失效

刷新只作废"注册表关系变了"的那些（ADR 0025），而**脚本内容变了不改变
注册表关系**——所以这一件仍归 watcher：

* 已登记脚本内容变了 / 被删了 → `pool.invalidate(script, dir)`；
* `paper_style*` 变了 → `pool.invalidate_project(dir)`（本项目全部，别的项目
  一个不动）；
* 新增一个还没登记的 `.py`、新增一张不相干的图片 → **一个都不作废**。
  那是几十秒的冷启动，用户会以为是自己点坏了什么。

## 7. 与 Tavotto 自己文件的隔离

| 目标 | 怎么隔离 |
|---|---|
| `tavottofile/`（画布、导出、版本历史） | 在 `discover.PRUNE_DIRS` 与 `project_refresh.EXCLUDE_DIRS` 里，两条遍历都剪掉 |
| autosave | 在数据目录，根本不在项目树内（R-07 仍开着） |
| `.tavotto` 文档的外部修改 | 走 ADR 0024 的修订/冲突服务，**不作为素材事件**——`.tavotto` 不在 `PDF_EXT`/`IMG_EXT` 里 |
| 刷新自己写的注册表 | `is_self_written()`（§4） |
| 导出目录 | 就是 `tavottofile/export/`，已被上面第一行剪掉；项目设置改到别处时它仍在项目树内可见，此时与用户手放的图同等对待（这是既有 `/api/panels` 口径，不在本 ADR 改动范围） |

## 8. 生命周期

每个项目一个 watcher，一份 stop event、一份快照、一份 pending 批次、一份
sink（sink 里的三个回调都闭包着这个 `ctx`，事件因此必然带对 `pj`）。

* 同一路径重复 `start()` **替换**旧的，且旧的真的 `stop()`——只从注册表里
  摘掉的话，那个线程还在跑、还在拍快照、还会继续调刷新；
* `close_project()` 停对应的那一个，`reset_projects()` 全停；
* `stop()` 同时**丢掉 pending 批次**：项目关掉之后再发事件是错的，那个 pj
  对前端已经不存在；
* 线程是 daemon，`desktop.py` 的退出路径显式全停一次；
* 注册表键与池键同一把尺（`pool.norm_dir`），三处分头判断的话会出现"一个
  认为是同一个项目、另一个认为是两个"。

## 9. 为什么不留兼容代理

Prompt 允许 `pool.py` 保留 `start_watcher/stop_watcher` 代理以减少一次性
改动。**这里选了全量迁移**，理由是一个具体的失败模式：

`start_watcher(dir, scripts, on_change)` 这个签名表达不了项目 watcher 需要的
东西（ctx、刷新回调）。留一个降级版代理的话，任何一条老路径（比如
`RefreshSink.watch` 那个钩子）调它，就会**把功能完整的项目 watcher 替换成
一个只盯清单的**——两个 watcher 不会同时跑，但活下来的是残缺的那个，而且
一点征兆都没有。迁移成本是 12 处一行改动（10 个测试夹具 + `desktop.py`），
比这个风险便宜。

`RefreshSink.watch` 一并删除：它的动作（按新清单重挂 mtime 跟踪）在整棵树的
watcher 下没有对应物。留着一个没人调的钩子，下一个人会以为它还在守什么。

> ADR 0025 摘要表里的「watcher 重挂时机」那一行随之作废，见该文件的更新注记。

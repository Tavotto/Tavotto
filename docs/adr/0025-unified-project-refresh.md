# ADR 0025：项目刷新的唯一后端入口

状态：**Accepted**
日期：2026-08-29
相关：[0023 文档落盘的唯一权威](0023-document-persistence-authority.md)（注册表落盘并进 `atomicio` 是它的收口）、
[0013 RuntimeFigureAsset](0013-runtime-figure-assets.md)（probe 成功后要物化的那批）、
[0001 项目/画布/标签/对象层级](0001-project-canvas-tab-object.md)（"项目隔离"的层级出处）、
本轨道文档 [`docs/implementation/product-ux-reliability/`](../implementation/product-ux-reliability/STATUS.md)。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 刷新逻辑放哪 | `engine/project_refresh.py` 一处；`app.refresh_project()` 只接两个副作用出口 |
| 谁调它 | `/api/project/refresh`、`/api/registry/scan`、probe 成功、手工登记；**Prompt 05 的 watcher 也调它** |
| 锁的粒度 | **每项目一把**，挂在 `ProjectCtx` 上随项目消亡；不是全局大锁 |
| 素材 diff 跟谁比 | **上一轮的清单**（跨轮），不是同一次调用里的前后（那个恒等于空） |
| 素材身份 | 文件签名（kind + size + mtime_ns），**不做内容哈希** |
| 「哪些文件算素材」 | 判据只有 `iter_assets()` 一处，`/api/panels` 与刷新共用 |
| 没有基线时 | 报 `assets.baseline=true`（"这一轮在建基线"），**不报"没变化"** |
| 没跑静态扫描时的冲突 | `conflicts=null`（不知道），**不是** `{}`（确认没有） |
| registry diff 的维度 | 脚本增删 + entry/cost/notes/stems + stem 归属迁移；stems 按**集合**比 |
| worker 失效范围 | 只作废 `removed + changed` 的脚本，且**限本项目**；无差异一个都不动 |
| watcher 重挂时机 | 只在**被盯的脚本集合**变了时；entry/cost 变了不重挂 |
| 事件 | 一次刷新至多一条 `registry.changed` + 一条 `assets.changed`；**无差异一条不发** |
| 老客户端兼容 | 恰好一个脚本变时照旧给 `{script, stems}` |
| watcher 认自己写的那一下 | **内容修订号**比较，不是"写完忽略两秒"的时间窗口 |
| 失败 | 抛 `RefreshError`（稳定 code）；注册表不半更新、不清空，事件不发 |

---

## 1. 背景：同一件事有三份实现

`9f67f56` 上，"项目里的东西变了"在后端有三条各自为政的路径：

| 入口 | 它做了什么 |
|---|---|
| `POST /api/registry/scan` | `discover.merge` + 无条件 `write_config` + `reload_registry` |
| probe 成功 | `reload_registry` + `_materialize_runtime` + 一条 `registry.changed` |
| `PUT /api/registry` | `discover.register` + `reload_registry` + 一条 `registry.changed` |

而 `reload_registry()` 自己是这样的：

```python
try:
    ctx.registry.load(ctx.path)
except (FileNotFoundError, RuntimeError):
    return                      # 失败静默
engine_pool.start_watcher(...)  # 每次都重挂，无论盯的对象变没变
```

三条路径合起来的问题不是"重复代码"，是**它们的答案不一样**：

1. **没有一条作废过 worker。** 用户把 `fig_a.py` 的 entry 从 `main` 改成
   `render`、或者把 stem `FigA` 从 A 脚本挪到 B 脚本，热 worker 手里那份
   还是老的——界面上表现为"我改了注册表，图还是老样子"。
2. **没有一条知道"什么变了"。** 事件里只有 `{script, stems}`，而这两个字段
   在批量场景（一次扫描发现四个新脚本）根本表达不了，于是要么发四条，要么
   丢三个。
3. **素材完全不在刷新的视野里。** 前端的素材列表靠自己重取。
4. **无差异也照发照写。** 一次什么都没发现的扫描同样重写注册表（mtime 变了）
   并重挂 watcher（mtime 基线被重置）。这两件事单独看无害，但 Prompt 05 要
   引入的项目 watcher 会把它们读成"外部改动"——**刷新自己触发下一次刷新**。

第四条路径（watcher）如果照着任意一条再抄一遍，分叉就有四份。

## 2. 裁决：一个服务模块 + 两个注入的出口

```text
engine/project_refresh.py     纯标准库、不 import Flask、不 import documents
    refresh_project_index(ctx, *, reason, changed_paths, allow_static_merge, publish, sink)

app.py
    refresh_project(ctx, ...)   ← app 层唯一入口，注入 sink（sse_publish / start_watcher）
```

`sink` 是注入而不是回头 import：`engine/project_refresh.py` 要能被 Flask 父
进程安全 import（`.venv` 里没有 matplotlib，见 `src/tavotto/AGENTS.md` 的进程
边界），而 SSE 与 watcher 回调都是 app 层的东西。

**边界靠依赖方向守着，不靠注释**：这个模块连 `engine/documents` 都不 import，
于是"派生元数据刷新不碰文档"在结构上就不可能被违反。

### 刷新流程

```text
取项目锁
  → registry 刷新前快照
  → （allow_static_merge）discover.merge → 内容变了才写盘
  → ctx.registry.load()                 ← 失败即抛，内存里那份原封不动
  → registry 刷新后快照 → 结构化 diff
  → 素材清单（与**上一轮**比）
  → 作废关系真的变了的 worker（限本项目）
  → 被盯的脚本集合变了才重挂 watcher
释放锁
  → 有差异才发事件
```

## 3. 为什么素材 diff 必须跨轮比

Prompt 04 的流程写的是"刷新前素材快照 → …… → 刷新后素材快照 → 计算 diff"。
照着实现，这条 diff **永远是空的**：刷新会改注册表，所以 registry 的"前/后"
有内容；素材它一个字节都不碰，同一次调用里的两张快照必然逐项相同。

这正是"对拍的尺子量不了那个维度"那一族——判据没错，它只是恒等成立。而恒等
成立的 diff 看起来和"什么都没变"一模一样，**没有任何信号提醒你它坏了**。

所以基线存在 `RefreshState.assets` 里，项目打开时 `seed_state()` 落一份。
没有基线的那一轮报 `baseline=true`，不报"没变化"——第一次刷新报空比不报还糟，
它是一句**错的断言**，不是一句"我还不知道"（同 T-12：不知道要自己占一档）。

## 4. 为什么签名而不是内容哈希

素材身份 = `(kind, size, mtime_ns)`。做内容哈希意味着每次刷新把整个图库里的
PDF/PNG 读一遍——大项目上是秒级开销，而这里要回答的只是"有没有动过"。

内容哈希在本仓库仍然有它的位置，但那是**按需、按单个文件**的：`/api/render`
的缓存键是内容哈希（ADR 见 `src/tavotto/AGENTS.md`），因为它回答的是"这张图
的像素还能不能复用"，mtime 变了而内容没变时不该丢缓存。两个问题不同，两把尺
就该不同——把刷新也拉去做内容哈希，是拿贵的尺量便宜的维度。

## 5. 为什么 watcher 靠内容修订号认自己

`§五 自写 registry` 要求刷新为下一阶段准备"可观测的写入结果或 fingerprint"。
三种候选：

| 方案 | 为什么不选 |
|---|---|
| 写完之后忽略 registry 事件 N 秒 | 慢磁盘上 N 不够、快机器上 N 会吞掉用户真实的外部修改。**两头都错** |
| 只在自己写的时候设一个标志位 | 并发刷新会互相顶掉；崩溃后标志位丢了 |
| **内容修订号比较** ✔ | 两头都对：用户把文件改回原样 = 内容没变 = 确实不用刷新 |

`RefreshState.registry_revision` 在**装载成功之后**更新，所以它同时回答"我们
写的"和"我们读过的"——watcher 要的正是"这份内容我已经消化过"，而不是"这个
字节序列出自我们的笔"。

顺带的一条：无变化的刷新**不回写**注册表（按字节比）。老名字
（`mm_registry.json`）那份天然与目标路径不同，因此搬迁照旧发生。

## 6. 失败语义

| 阶段失败 | code | 结果 |
|---|---|---|
| 静态扫描 / 合并 / 写盘 | `scan_failed`（沿用，前端已有文案） | 注册表未动，事件不发 |
| 装载（文件缺失 / 不合法 JSON / 结构不对） | `registry_reload_failed`（新） | **内存里那份原封不动**，项目照常能用 |

"内存里那份原封不动"不是靠 try/except 兜的，是结构性的：`Registry.load_data()`
先把 `cleaned` / `index` 建在局部变量里，全部校验通过才赋值给
`self._scripts`——半更新状态在这个类里不存在。

两个 code 都走 `app.py` 的 `_refresh_error` 漏斗（400，不是 500：成因是用户
图库里的东西，重试一百次也是同样的结果）。`tests/test_error_codes.py` 的扫描
范围因此把 `engine/project_refresh.py` 也算进去——码表看不见的模块 = 没有门禁。

## 7. 顺带清掉的一处手写原子写（R-05）

`discover.write_config()` 是九处手写 `tmp + replace` 中的一处。它的临时文件名
每次不同（并发写不会互相搬走对方），但**没有 fsync**：`os.replace` 只保证
"要么旧要么新"，不保证新内容已经离开页缓存，掉电时 replace 出来的是一个空
文件——而注册表**随图库走**，坏掉的是用户目录里的文件，重装应用也修不回来。

现在它走 `atomicio.write_json()`。看护它的两条用例（故障注入 / 并发临时文件）
原本钉在 `Path.replace` 上，而 atomicio 调的是 `os.replace`——**桩挂不上就会
恒绿**，所以这次一并把注入点挪到 `os.replace`。

## 8. 明确不在本次范围

- **项目 watcher 本身**（Prompt 05）。本次只给它准备入口与自写识别。
- **前端消费**（Prompt 06）。本次只把事件与响应的**类型**补齐（`web/src/lib/api.ts`），
  没有加任何 handler。
- **`/api/panels` 的语义**。它照旧全量 probe 出尺寸；刷新只是与它共用了
  "哪些文件算素材"这一条判据。

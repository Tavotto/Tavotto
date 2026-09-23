# ADR 0078：MCP 编辑会话落盘，server 进程换了按 `session_id` 恢复

日期：2026-09-23 · 状态：**Accepted**
相关：[0006 Codex 里的 MCP server / MCP App 画布](0006-codex-mcp-app-and-publication-profile.md)、
[0009 工作区根的可信传递](0009-codex-workspace-root-authority.md)、
[0051 保留式规范化](0051-preserving-normalization.md)、
[0069 画布负载不走工具结果](0069-canvas-payload-under-host-event-cap.md)、
验收记录 [`docs/acceptance/workbuddy-mcp-app.md`](../acceptance/workbuddy-mcp-app.md)。

## 问题

MCP 编辑会话（项目、stem、规范、已提交的 patches、规范化合同）一直只活在 server 进程
的内存里（`bridge._SESSIONS`）。这隐含一个前提：**画布发来的每一次 `tools/call` 都落在
开图的那个进程里**。

* Codex 一个任务一个长连接进程，前提基本成立；但改插件配置时 Codex 会重启 server，画布
  随后的 apply 得到 `unknown_session`（`codex-desktop-canvas.md` 末节记过）。
* WorkBuddy 5.6.2 实测（2026-09-23，L3）：一轮对话结束后对话的 Agent CLI 被 SIGTERM，它
  启动的 stdio server 跟着退出；画布的反向调用被投递到预热池里的新 CLI，新 CLI 再起一个
  **新的** Tavotto 进程。画布手里的 `session_id` 在那里不存在。

宿主怎么托管进程不归我们管，而且会越来越多样。画布发的本来就是**全量** patches
（override 语义，ADR 0003），重建一个会话所需的一切都是可序列化的小数据——没有理由把
「进程别换」当成正确性的前提。

## 裁决

1. **提交点落盘。** 会话已提交的状态在三个时刻写一条记录：`open_figure` 结束（新建或
   沿用）、`apply_overrides` 结束（合同解除之后）、`normalize_figure` 事务收尾（提交、
   回退、异常回退都算——`normalize_figure` 包一层 `finally`）。规范化事务**中途**的候选
   状态不落盘，`_render` 本身不写盘。
2. **记录只说「是哪张图、改到了哪一步」，不给任何权限。** 字段：`id / project / stem /
   profile / patches / contract / normalized / cost / created / saved_at / v`。**不存脚本
   与入口**：恢复时从项目注册表重新读；stem 不在了就拒（`session_restore_failed`，删记录）。
3. **恢复在内存未命中时发生**（`get_session` → `_restore_session`）：
   * 项目路径重新规范化，必须落在**当前连接**的 `RootAuthority` 允许根内，否则
     `workspace_root_changed`——**拒在渲染之前**，一个脚本都不执行，记录保留（换一个
     根对的连接还能恢复）；
   * 先 `_render(已提交 patches)` 成功再登记（与 open 同一条纪律），渲染失败原样报错、
     记录保留；
   * 恢复出来的会话在**这一次**调用的结果里带 `restored: true` 与一句说明（读一次即清）——
     不假装它一直开着：渲染修订号从头计、上一进程的缓存（预检等）重算。
4. **明确释放的不复活。** `close_session` 与 `_evict_if_needed` 连记录一起删。淘汰在 open
   的文字里本来就要点名，恢复不能让它变成一个静默的内存细节。
5. **落盘不拖垮编辑。** 写失败时这次工具调用照常成功，stderr 说一句「进程换了之后恢复
   不了」——stdout 归协议。
6. **存放与清理。** `engine/config.data_dir()/mcp-sessions/<session_id>.json`；目录 0700、
   文件 0600；临时文件 + `os.replace` 原子写；`session_id` 先过正则
   `^s-[0-9a-f]{12}$` 再拼路径；形状不对 / 版本不认识 / id 与文件名不符 / 过期（7 天）一律
   当作不存在；每次保存顺手清过期与超额（64 条，最旧的先走）。
7. **只用标准库**（`tavotto_mcp/sessionjournal.py`）：不新增引擎 import，`_BRIDGE_IMPORT` /
   `BRIDGE_IMPORTS_AT_MIN` / `MIN_TAVOTTO_VERSION` 这组三处同源不动。

## 后果

* 画布在「进程换了」之后的下一次调用多一次重渲染（与冷开一张图相同的代价），之后照常。
* 两个进程同时持有同一个 `session_id`（宿主同时起了两份）时各自一份内存状态，记录
  last-writer-wins。画布每次发全量列表，下一次 apply 就收敛；不做跨进程锁。
* 这**不能**修 WorkBuddy 5.6.2 的反向调用问题：那里调用在到达我们之前就被出口审查拦下，
  或卡在新 CLI 的审批队列里（验收记录 P0-1 / P0-2）。它修的是「调用到了、但到的是另一个
  进程」这一半。
* 隐私：记录只在本机数据目录（含项目路径），不经任何网络路径发出；遥测是白名单结构，
  这里没有新增任何可发送的字段。

## 看护

* `tests/test_mcp_session_journal.py`：换进程恢复（apply / 取件两条路）、合同随会话回来、
  越界记录拒在渲染之前、记录不含脚本 / 入口、stem 不在了不恢复、非法 id 不碰文件系统、
  关闭 / 淘汰的不复活、落盘失败不拖垮编辑、权限位、过期与超额清理、坏形状当作不存在。
  五条变异（去范围校验、关闭不删、淘汰不删、不恢复、丢合同）各自打红。
* `tests/test_mcp_roundtrip.py`：真 stdio 两个 server 进程接力——进程 A 改完退出，进程 B
  用同一个 `session_id` 取件，manifest 与 A 的热态逐元素一致，接着改、`verify_replay` 无
  偏差；关闭过的会话在 B 里是 `unknown_session`。

# TEST_MATRIX — 现有可复用的测试与本轨道要补的场景

> 状态：`已有` = 起始 commit 上已存在并跑绿；`部分` = 有测试但覆盖不到合同；
> `未开始` = 本轨道要新建。**未开始的一律不预先建空文件**
> （空门禁比没有门禁更坏，见根 `AGENTS.md`）。
>
> 命令：
> ```sh
> PYTHONPATH=<worktree>/src <repo>/.venv/bin/python -m pytest    # 后端
> cd web && pnpm test && pnpm build && pnpm i18n:check           # 前端
> ```

---

## DOCUMENT / MIGRATION / SAVE / RECOVERY / HISTORY

| 场景 | 状态 | 位置 / 备注 | 归属 |
| --- | --- | --- | --- |
| autosave PUT/GET 往返、非法载荷、删除 | 已有 | `tests/test_autosave.py` | — |
| 跨标签页乐观并发（409 stale_write） | 已有 | `tests/test_autosave.py` | — |
| 版本时间线 CRUD / 去重 / 裁剪 | 已有 | `tests/test_versions.py` | — |
| 前端文档模型 / 迁移 / 画布操作 | 已有 | `web/src/types/document.test.ts`、`store/documentStore.test.ts` | — |
| schema 2 → 3 迁移 | 已有 | `document.test.ts`（`migrateToProject`） | — |
| 磁盘写入是**原子**的（含 `/api/layouts` 另存为） | 已有 | `tests/test_document_persistence.py::test_layout_save_is_atomic`、`::test_replace_failure_keeps_the_old_file_and_cleans_up` | 02 ✅ |
| 写入拒绝 NaN / Infinity | 已有 | `test_write_json_rejects_non_finite_before_touching_disk`、`test_autosave_put_rejects_non_finite_and_keeps_disk`、`test_layout_save_rejects_non_finite` | 02 ✅ |
| 写入失败时清理临时文件 / 结构化错误 | 已有 | `test_replace_failure_keeps_the_old_file_and_cleans_up`（含 `code`） | 02 ✅ |
| 落盘前 fsync 文件本身（不只 fsync 目录） | 已有 | `test_write_json_fsyncs_the_file_before_replacing` | 02 ✅ |
| revision / fingerprint（供外部修改检测） | 已有 | `test_content_revision_tracks_content_not_mtime`、`test_autosave_exposes_revision_on_write_and_read` | 02 ✅ |
| 文档发现：空项目 / 多文档 / 旧扁平布局 | 部分 | 三目录合并列出有实现，`tests/test_paths_and_baked.py` 覆盖旧位置兼容；**现状没有 index 文件**，「损坏 index 能重建」无从谈起 | 03 |
| 收纳目录里 Tavotto 自己的文件不是用户文档 | 已有 | `test_styles_file_is_not_listed_as_a_document`、`test_reserved_name_is_not_reachable_through_the_document_api`、`test_reserved_stems_are_derived_from_the_real_filenames` | 02 ✅ |
| 未知扩展字段一次 load-save 不丢 | 未开始 | **有意不做**，条件见 ADR 0023 §5(b)：更高 schema 一律拒绝，今天不存在「未知字段」 | — |
| 未来 schema 版本的拒绝（独立 code） | 已有 | `test_validate_rejects_future_schema_with_its_own_code`、`test_autosave_put_reports_future_schema` | 02 ✅ |
| 保存状态机 clean/dirty/saving/saved/error/conflict | 未开始 | — | 03 |
| 关闭前提醒 / `Ctrl+S` 手动保存 | 未开始 | — | 03 |
| 崩溃恢复：本机副本转正 | 部分 | `readAutosaveDoc` 有实现，无端到端用例 | 03 |
| 外部修改冲突检测（不静默覆盖） | 未开始 | — | 03 |
| **版本检查点带画布身份** | 未开始 | 见 `STATUS.md` R-03（跨画布恢复会串） | 03 |

## REFRESH / WATCHER / SSE / FRONTEND SYNC / READINESS

| 场景 | 状态 | 位置 | 归属 |
| --- | --- | --- | --- |
| registry 扫描 / 写入 / stem 映射 | 已有 | `tests/test_registry.py`、`test_discover.py` | — |
| 脚本探测（显式动作） | 已有 | `tests/test_script_probe.py` | — |
| 多项目隔离（watcher / worker / baked） | 已有 | `tests/test_projects.py`、`test_paths_and_baked.py`、`test_project_watch.py::TestLifecycle` | — |
| 素材竞态 / 派生 metadata 不污染 undo | 部分 | `web/src/store/assetStore` 无专门用例 | 06 |
| 统一 refresh 服务 | **已有（04）** | `tests/test_project_refresh.py`（39 条） | — |
| **项目 watcher：整棵树的快照** | **已有（05）** | `tests/test_project_watch.py`（42 条） | — |
| ├ 新增 / 删除 / 重命名 / 原子替换 `.py` | 已有（05） | `TestChangeKinds`（四条各一） | — |
| ├ 就地改写：同长度（量 mtime）/ 同 mtime（量 size） | 已有（05） | `TestSnapshot` 两条——**两维各有一条看着** | — |
| ├ 注册表：新名 / 旧名 / 删除 / 非法 JSON 后修复 | 已有（05） | `TestChangeKinds` ×3 + `TestErrors::test_broken_registry_reports_and_recovers` | — |
| ├ 素材：新增 PDF / 改 PNG / 删 JPG / 重命名 | 已有（05） | `TestChangeKinds` ×4 | — |
| ├ `paper_style*` 变更作废整项目（且只作废本项目） | 已有（05） | `TestChangeKinds` + `TestLifecycle::test_style_change_only_invalidates_its_own_project` | — |
| watcher 事件批次合并 | **已有（05）** | `TestBatching`：一批连续写入一次刷新、永不安静的目录有年龄上限、刷新期间的变化不丢、stop 取消 pending | — |
| watcher 自写不循环 / 自写后外部再改仍触发 | **已有（05）** | `TestSelfWriteLoop` ×3（含"自写不吞掉同批里的别的变化"） | — |
| watcher 不 probe、不执行用户脚本 | **已有（05）** | `TestNoSideEffects`（桩 + 磁盘 CANARY 两层证据） | — |
| watcher 不监控 Tavotto 自己的 autosave/history | **已有（05）** | `TestSnapshot::test_script_scope_matches_discover`（`tavottofile/` 被剪）；autosave 不在项目树内 | — |
| watcher 只发 `panel.file_changed`，不发第二套 registry/assets 事件 | **已有（05）** | `TestEndToEnd` ×2（含 `pj` 与 `reason`） | — |
| Readiness 事实模型 / capability API | 未开始 | — | 07 |
| 左栏常驻 / 折叠 / 素材状态 | 未开始 | — | 08 |

## FAST EDIT / LAYOUT / STYLE / SPEC / VALIDATION / EXPORT

| 场景 | 状态 | 位置 | 归属 |
| --- | --- | --- | --- |
| 出版规范预检（两侧同一份向量） | 已有 | `tests/test_preflight.py` + `web/src/lib/preflight.golden.test.ts` + `tests/golden/preflight_vectors.json` | — |
| 导出端点 | 已有 | `tests/test_export_endpoint.py` | — |
| 打包（图 + 脚本 + 证明） | 已有 | `tests/test_package.py` | — |
| 写回事务（prepare/verify/commit、像素门） | 已有 | `tests/test_write_back.py`、`test_pixel_compare.py` | — |
| 几何权威 / 面板渲染 | 已有 | `web/src/store/geometryAuthority.test.ts`、`tests/test_manifest_geometry.py` | — |
| 快速编辑模式（不经画布） | 未开始 | — | 09 |
| 原图规格导出合同 | 未开始 | — | 09/12 |
| Style/Spec 分层与项目快照 | 未开始 | — | 10 |
| 统一检查引擎 + 左侧问题面板 | 未开始 | — | 11 |
| 问题项 → 真实对象定位（含跨画布） | 部分 | preflight 已带 objectIds/gids，无 canvasId | 11 |

## PROPERTIES / TEXT / LEGEND / TICKS / MULTISELECT / QUICKEDIT

| 场景 | 状态 | 位置 | 归属 |
| --- | --- | --- | --- |
| 富文本 / 上下标（两侧同源） | 已有 | `tests/`（真 PDF 几何看护）+ `web/src/lib/richText.test.ts` | — |
| 图例文本 | 已有 | `tests/test_legend_text.py` | — |
| 刻度 / 轴遍历权威 | 已有 | `tests/test_axes_ticks_scale.py`、`test_axes_traversal_authority.py` | — |
| 标注合成（文字 / 箭头 / 形状） | 已有 | `tests/test_compose_*.py` | — |
| ContextBar / QuickEdit 交互 | 已有 | `web/src/canvas/contextBar.test.tsx` 等 13 个 canvas 用例 | — |
| 统一属性系统 / 标注字体 | 未开始 | — | 13 |
| Unicode / 数学文本 / 字体回退与导出一致 | 部分 | `tests/test_font_family_options.py` | 14 |
| 图例绑定与同步 | 未开始 | — | 15 |
| 刻度线内外命中 | 未开始 | — | 16 |
| 多选浮动栏 / 主选对象 / 排列 | 部分 | `alignAction.test.ts`、`alignUndoConvergence.test.tsx` | 17 |
| 右键菜单按对象与选择状态 | 未开始 | — | 18 |

## SETTINGS / AGENTS / PACKAGES / TUTORIAL / MCP / AI

| 场景 | 状态 | 位置 | 归属 |
| --- | --- | --- | --- |
| Coding Agent 注册表与设置 | 已有 | `tests/test_ai_agents.py`、`test_ai_capabilities.py` | — |
| 项目环境解析 | 已有 | `tests/test_project_env.py` | — |
| 受控依赖修复 | 已有 | `tests/test_dependency_repair.py`、`test_dependency_repair_e2e.py` | — |
| MCP server / stdio / roundtrip | 已有 | `tests/test_mcp_*.py`（6 个） | — |
| 内置 AI 桥 / 历史 | 已有 | `tests/test_ai_bridge.py`、`test_ai_history.py` | — |
| 设置外壳尺寸稳定 | 未开始 | — | 19 |
| 安全包管理（只碰受管环境） | 部分 | 受管环境有用例，包管理 UI 无 | 19 |
| 离线教程资源 / 版本化复制 / API | 未开始 | **全仓无 tutorial/onboarding 实现** | 20 |
| 交互式 onboarding 与一次性提示 | 未开始 | — | 21 |

## I18N / A11Y / PRIVACY / PERFORMANCE / PACKAGING / E2E

| 场景 | 状态 | 位置 | 归属 |
| --- | --- | --- | --- |
| i18n 资源完整性 / 死 key | 已有 | `pnpm i18n:check`、`tests/test_i18n_dead_keys.py` | — |
| 桌面壳 i18n | 已有 | `tests/test_desktop_i18n.py` | — |
| 遥测白名单结构性防线 | 已有 | `tests/test_telemetry_invariants.py`、`test_telemetry_proxy.py` | — |
| 打包（wheel / sdist / runtime / NSIS） | 已有 | `tests/test_package.py`、`test_bundled_runtime.py`、`test_nsis_template.py`、`test_workerd_packaging.py` | — |
| 平台支持口径一致 | 已有 | `tests/test_support_matrix.py` | — |
| a11y（axe）扫描 | 部分 | 见 issue #130：`incomplete` 不进 violations，门禁半盲 | 22 |
| E2E（POSIX 腿） | 部分 | 见 issue #30：Playwright 只有 Windows 腿 | 23 |
| 性能基线 | 已有 | `docs/perf-baseline.md` + nightly | 23 |

---

## 变异验证记录（Session 02）

新增判据提交前逐条反证过，**每一条都能被它守的那个缺陷打红**：

| 变异 | 结果 |
| --- | --- |
| `allow_nan=False` → `True` | 红 ✔ |
| 去掉 `os.fsync(f.fileno())` | 红 ✔ —— **第一版判据是空的**：只断言「有人被 fsync 了」，而写完还会 fsync 目录，所以删掉文件那次照样非零。改成「被 fsync 的里面有一个是普通文件」才红 |
| replace 失败不清 tmp | 红 ✔ |
| 退回非原子写（直接写目标路径） | 红 ✔ |
| `is_user_document_stem` 恒 True | 红 ✔ |
| `require_user_document_stem` 空转 | 红 ✔ |
| 未来版本按普通非法处理 | 红 ✔ |
| 修订号掺进 mtime | 红 ✔ |
| autosave GET 不带修订号 | 红 ✔ |
| 空画布的项目文档放行 | 红 ✔ |

---

## Session 03 新增用例

| 场景 | 位置 |
| --- | --- |
| 状态机全链路（dirty→saving→saved→clean，`saved` 自己回落） | `web/src/store/saveStateMachine.test.ts` |
| 保存期间继续编辑 → 完成后仍是 dirty | 同上 |
| 写盘失败 → `save_error`，重试救回来 | 同上 |
| `hasUnsavedWork` 只对四个状态为真 | 同上 |
| `saveNow` 等到磁盘真的写完才返回 | 同上 |
| 连按多次手动保存合并成一次写入 | 同上 |
| 空文档不产生 PUT | 同上 |
| 409 `external_change` → conflict，磁盘不被覆盖，摘要带上"那边是什么" | 同上 |
| 冲突期间的自动保存只写本机副本，一次都不再撞磁盘 | 同上 |
| 重新加载：磁盘那份进内存，内存那份变成可恢复副本 | 同上 |
| 明确覆盖拿 409 回的 hash 当基线，**校验仍然生效** | 同上 |
| 从没读过磁盘就写 → 先确认 → 发现没读过的内容 → 冲突 | 同上 |
| 读到了却没有修订号 → 不带基线，**不猜成"磁盘上没有"** | 同上 |
| 崩溃恢复：主文档照常打开 + 提供恢复；主文档一个字节不动 | 同上 |
| 恢复只进内存并置 dirty（两根轴都置），确认保存后才覆盖主文档 | 同上 |
| 保留主版本只删自己那一个恢复键 | 同上 |
| 未裁决的恢复副本跨会话仍在 | 同上 |
| 损坏的恢复副本不拦住打开文档 | 同上 |
| 未来 schema：不打开、不覆盖、明确说出来 | 同上 |
| 关闭保护：clean 不拦，dirty / save_error 拦，未决恢复副本不算未保存工作 | 同上 |
| 本机副本更新时打开磁盘那份 + 挪进恢复槽位 | `web/src/store/documentStore.test.ts` |
| 本机副本不比磁盘新 = 陈旧残留，直接清掉 | 同上 |
| 冲突后队列不再撞磁盘、状态是 conflict、编辑继续进本机副本 | 同上 |
| 首次写带 `base_revision=absent` | 同上 |
| 恢复落点四条分支（same / other / missing / unknown） | `web/src/lib/versionTarget.test.ts` |
| 自动检查点带上激活画布的 id 与当下的名字 | `web/src/hooks/useVersionCheckpoints.test.ts` |
| 排队中的写入带走**排队那一刻**的 pj | `web/src/store/projectStore.test.ts`（既有用例，本次补 await） |
| 外部修改两条边（`absent` 有文件 → 冲突；hash 无文件 → 放行） | `tests/test_document_persistence.py` |
| `base_revision` 优先于 `base`；不发修订号的调用方仍走 `stale_write` | 同上 |
| 摘要的两个时间维度；读不出来回 `None` 而不是空壳 | 同上 |
| 检查点记画布身份，**缺席不补默认值** | 同上 |
| 自动检查点去重按画布分 | 同上 |
| 前后端 `SCHEMA_CURRENT` 同源 | 同上 |

## 变异验证记录（Session 03）

24 条变异逐一反证，**全部被打红**。有价值的三条：

| 变异 | 结果 |
| --- | --- |
| `absent` 哨兵不再挡（判据只剩一条边） | 红 ✔ |
| 被外部删掉的文件不许重建（另一侧过严） | 红 ✔ —— 判据两侧都钉住了 |
| `beforeunload` 先冲刷再读状态 | 红 ✔ —— 这条**先是变异发现的真缺陷**：flush 会把状态推成 `saving`，于是干净文档每次刷新都拦 |
| 写成功后一律报 `saved` | 红 ✔ |
| 恢复不置 store 的 `dirty` 标志 | 红 ✔ —— **第一版判据是空的**：只断言 `saveState==='dirty'`，而 `recoverLocalCopy` 里那句 `setSaveState('dirty')` 是显式的，改 `applyProject` 的参数照样绿。补上 `expect(s().dirty)` 才红 |
| 读到了但没修订号 → 当成「磁盘上没有」 | 红 ✔ |
| 读到了但没修订号 → 当成「没确认过」 | 红 ✔ —— 这两条**证伪了原实现**：两条捷径都会把文档锁成永远存不上，第三档 `null` 是补出来的 |
| 没有画布身份 = 当成当前画布（R-03 原形态） | 红 ✔ |
| 排队写入读全局 pj 而不是排队那一刻的 | 红 ✔ |

> 变异脚本本身也有一处教训：`git checkout -- <file>` 还原会**吃掉未提交的
> 修复**。中途按变异结论改好的 `rememberRevision` 被下一轮还原掉了，靠
> 「锚点找不到」才发现。**跑变异前先提交**。

## Session 04 新增用例

全部在 `tests/test_project_refresh.py`（36 条），除最后两行。

| 场景 | 覆盖的是 |
| --- | --- |
| 无变化刷新 = 空 diff + 零事件 | 无差异不产生事件风暴 |
| 新增静态可识别脚本 → `added_scripts` + 磁盘那份也更新 | 静态合并 |
| 注册表里删掉脚本 → `removed_scripts` / `removed_stems` | 外部改动 |
| 同一脚本 stems 增 / 删 | 「只比脚本名不够」的第一维 |
| entry / cost / notes 变了（脚本清单一个字没变） | 第二维——这三条任一变了，热 worker 手里那份就不对了 |
| stem 换主人 → `moved_stems`，两边都进 `changed_scripts` | 第三维；且它不是"新增/删除" |
| stems 纯重排 **不算**变化 | 顺序无语义，按列表比会白白作废一批 worker |
| 手工条目优先于静态草稿 | 现有条目永远优先 |
| 冲突报出来、谁都不给、也不写进注册表 | 冲突不被自动裁决 |
| 没跑静态扫描时 `conflicts is None`，跑了没冲突才是 `{}` | T-15：不知道自己占一档 |
| 素材新增 / 内容变 / 删除 | 跨轮比（T-14） |
| `/api/panels` 与刷新 inventory 的 id 集合逐字相同 | 同一把尺（不变式 D） |
| **刷新不执行用户脚本**：桩住 probe/worker 入口 **+** 脚本真跑会留下的文件不存在 | 双份证据，缺一不可 |
| `/api/project/refresh` 返回结构化 diff | 新端点 |
| 未知 `reason` 归成 `manual`（含非字符串、空串、注入串） | 不变式 C |
| HTTP 上的 `changed_paths` 被忽略 | 不接受客户端传路径 |
| 没打开项目 → 409 `no_project` | 既有守卫 |
| 指名项目 B 刷新：事件 pj 是 B，A 的注册表一个字没动 | 项目隔离 |
| `/api/registry/scan` 旧响应三个字段逐字保留 + 新 diff 另给 | 存量前端不坏 |
| 扫描失败仍是 `scan_failed`；刷新失败是 400 + 稳定 code | 稳定错误码 |
| 手工登记 / probe 成功后的事件 `reason` = `registry` / `probe` | 它们走了统一入口的**结构性证据** |
| 一次四个新脚本 = **一条** `registry.changed`，且不带单脚本字段 | 不为十几个脚本发十几条 |
| 只有素材变时不发 `registry.changed` | 两类事件各管各的 |
| `publish=False` 仍返回完整 diff | 调用方可以只要事实不要广播 |
| 两个项目里同名 `shared.py`：刷新 A 只作废 A 的 worker | worker 失效的项目隔离 |
| 新增一张不相干的图片不作废任何 worker | 不为无关变化打掉热会话 |
| 两个项目并行刷新（A 的锁被别人拿着时 B 照常刷完） | 锁不是全局的 |
| 同一项目并发刷新串行且注册表不损坏（barrier 判据，不靠 sleep） | 项目级串行 |
| 刷新失败：注册表内容与事件都原封不动 | 失败语义 |
| 注册表文件被删：内存里那份不清空 | 同上 |
| 无变化的刷新**不回写**注册表（mtime 不动） | 不给 watcher 制造假的外部修改 |
| 自写识别按内容修订号，用户改一个字就认得出来 | T-16 |
| watcher 只在被盯的脚本集合变了时重挂 | entry 变了不重挂 |
| 快照不是活视图（`reg.load()` 之后仍代表刷新前） | 共享 list 会让 diff 永远是空的 |
| `write_config` 失败注入打在 `os.replace` 上 | `tests/test_discover.py`（T-17：挂不上的桩会恒绿） |
| `RefreshError` 的 code 进码表、双语文案、漏斗带原文 | `tests/test_error_codes.py` |

## 变异验证记录（Session 04）

29 条变异逐一反证，**全部被打红**。有价值的这些：

| 变异 | 结果 |
| --- | --- |
| 素材扫描不剪 `EXCLUDE_DIRS`（导出目录爬进素材库） | 红 ✔ |
| 同名 PDF 不再盖过位图（两把尺分叉） | 红 ✔ |
| 素材签名只比 size，不比 mtime_ns | 红 ✔ —— 等长重画是用户最常做的事 |
| registry diff 只比脚本名（entry/cost/notes 不看） | 红 ✔ |
| stems 按列表比（纯重排也算变化） | 红 ✔ |
| 没跑静态扫描时把冲突说成「已确认没有」 | 红 ✔ |
| **素材 diff 退回「同一次调用里前后比」** | 红 ✔ —— 这条**先是设计缺陷**：第一版照 Prompt 的流程写，恒等于空，没有任何信号提醒；靠一条真加了图片的用例红出来（T-14） |
| 项目打开时不落素材基线 | 红 ✔ |
| 无差异也发事件 / 批量也带单脚本字段 / 单脚本兼容字段没了 | 红 ✔ ×3 |
| worker 失效不限项目（打掉另一个项目里的同名脚本） | 红 ✔ |
| 任何刷新都作废全部 worker | 红 ✔ |
| **锁改成全局一把** | 红 ✔ —— **第一版判据是空的**（见下） |
| 干脆不加锁（同一项目并发刷新） | 红 ✔ |
| 装载失败时把内存里的注册表清空 | 红 ✔ |
| reason 原样透传 | 红 ✔ |
| **端点认客户端传来的 `changed_paths`** | 红 ✔ —— **第一版判据是空的**（见下） |
| 无变化也回写注册表（给 watcher 制造假的外部修改） | 红 ✔ |
| 自写识别永远说「是我们写的」 | 红 ✔ |
| watcher 每次刷新都重挂 | 红 ✔ |
| **快照与注册表共享同一个 stems 列表** | 红 ✔ —— **第一版判据是空的**（见下） |
| `/api/registry/scan` 换掉旧响应形状 | 红 ✔ |
| 手工登记 / probe 成功绕开统一刷新 | 红 ✔ ×2 |
| 注册表落盘绕开 `atomicio` | 红 ✔（`tests/test_discover.py`） |

### 三条活下来的变异：判据把结论当成了前提

| 判据 | 它假设了什么 | 改成 |
| --- | --- | --- |
| 并行刷新 | 在测试里拿住 A 的那把锁 = **假设被测代码用的正是那把**；换成全局锁照样绿 | 从**里面**卡住 A（停在它自己的临界区），再要求 B 刷完 |
| 并行刷新（第二轮） | 「B 最终回来了」——A 的等待迟早超时放行，全局锁下 B 也会回来，只是慢十秒 | 断言 B 刷完的**那一刻** A 还没出来 |
| `changed_paths` | 只喂项目外的路径，而那些本来就会被规整器丢掉 | 补一条**项目内**的 |
| registry 快照 | 只量「`load_data` 之后还在」，而今天 `load_data` 把每个容器都重建，浅拷贝碰巧也够 | 补第二维：原地改同一个 list |

> 另一条教训：`discover.write_config` 换成 `atomicio` 之后，钉在 `Path.replace`
> 上的故障注入**打不中了**。那条用例是以 `DID NOT RAISE` 红出来的（它的注释
> 早写明了这个红法）——换一条形状（比如只断言"文件没坏"）就会静默变成一条
> 空门禁。**桩挂不上的用例会恒绿，而恒绿看起来和"守住了"一模一样。**

## 评审回合 1（PR #201）：三条findings 与它们的判据

Codex 在 `7efe8e0` 上报了 1 条 P1 + 2 条 P2，**三条都成立**，都已修 + 配用例。

| finding | 核实结论 | 判据 |
| --- | --- | --- |
| P1 自动保存的「判冲突 → 写」不是原子的 | **成立**。判据本身两条边都钉住了，但它只在请求串行时有效：两个标签页同时新建同一份文档，双方都在对方落盘前读到「磁盘上没有」、双方都判没冲突，后写的把先写的整份盖掉，**而两边都收到 200**——正是 `absent` 哨兵要挡的那个场景，被交错执行绕了过去 | `test_two_concurrent_creates_cannot_both_win`（把缝**撑开**：让第一个请求停在读完修订号之后。不撑开的话串行执行下那个交错根本不会发生，等于没测） |
| P2 目录 fsync 失败被吞 | **成立**。两个 `except` 挡的不是同一件事（Windows 打不开目录 ≠ 目录项没落盘），一并 `pass` 的后果是调用方收到成功、前端据此删掉本机兜底副本 | `test_directory_fsync_failure_is_not_swallowed` + `..._unsupported_is_still_ignored`（两个方向都量：EIO 要抛、EINVAL 要放过） |
| P2 没有修订号时「覆盖」点了等于没点 | **成立但描述偏重**：不是死循环。`stale_write` 的 409 不带磁盘修订号 → 「覆盖」只把 `diskRevision` 删掉 → 下一次写前的确认探到一份「没读过的」→ 再弹一次同样的框；第二次点才会成功（那时 `saveIssue.disk` 已经是带 revision 的摘要）。**用户看到的是「覆盖」按钮点一次没反应** | `saveStateMachine.test.ts` 的「没有修订号时「覆盖」也只要点一次」 |

### 这一轮的变异反证（6 条，全部 KILLED）

| 变异 | 结果 |
| --- | --- |
| 去掉自动保存的互斥（评审 P1 的原形态） | 红 ✔ |
| **修订号挪到锁外读** | 红 ✔ —— **第一轮活了下来**：黑盒看到的两种实现在单个请求下完全一样，差别只在锁决定的那个交错窗口里。补了一条白盒判据（交回去的那个修订号是不是在锁里读的） |
| 目录 fsync 失败照旧吞掉 | 红 ✔ |
| EINVAL 也当成失败（判据宽过它要守的东西） | 红 ✔ |
| 「覆盖」不去补基线 | 红 ✔ |
| `AtomicWriteError` 的 code 不进扫描范围 | 红 ✔（`test_error_codes.py`：加 `engine/atomicio.py` 之前，`write_failed` / `replace_failed` / `non_finite_number` 三个早就会落到界面上的 code **一直没有英文文案**，而没人会红） |

> **又踩了一次「变异前先提交」。** 前两条变异用 `git checkout --` 还原，把
> 同一个文件里**还没提交的修复**一起吃掉了——第三条变异因此在"没有修复"的
> 代码上跑，锚点找不到、`s.replace` 静默 no-op，结果看起来像"变异活下来了"。
> 这条纪律在 Session 03 就记过一次，这次是同一个形状的第二次。

## Session 05 的变异反证（30 条，全部 KILLED）

每条变异都钉着**它应该打红的那条用例**（不是"跑全量看红不红"——那样一条
不相干的用例红了也算过）。

| 变异 | 结果 |
| --- | --- |
| 签名丢掉 size 这一维 / 丢掉 mtime 这一维 | 红 ✔ ×2 —— **两维各有一条守着** |
| 目录不可用返回空快照而不是 `None` | 红 ✔ |
| 脚本只盯起草候选（漏掉 `paper_style` 等基础设施脚本） | 红 ✔ |
| 不盯旧名注册表 / 不盯素材 | 红 ✔ ×2 |
| diff 只看两边都在的键（漏掉新增与删除） | 红 ✔ |
| 快照在 dispatch **之后**才换（刷新期间的写入会丢） | 红 ✔ |
| 没有批次年龄上限（永不安静的目录永远不刷新） | 红 ✔ |
| 不防抖，每轮都结算 | 红 ✔ |
| 已删除的脚本也发 `panel.file_changed` | 红 ✔ |
| 作废不限于已登记脚本 | 红 ✔ |
| 样式模块变更只作废它自己 | 红 ✔ |
| 注册表变化**一律不**刷新 / **一律**刷新（同一行的两个方向） | 红 ✔ ×2 |
| 不认自写（每次刷新触发下一次） | 红 ✔ |
| 自写时把整批都丢掉 | 红 ✔ |
| 刷新失败不发项目级错误 / 直接抛出去 | 红 ✔ ×2 |
| 回调抛出不再被吞 | 红 ✔ |
| `stop()` 不取消 pending 批次 | 红 ✔ |
| 同路径重复 start 不停旧的（两个线程同时跑） | 红 ✔ |
| `run()` 的一轮异常不再兜底 | 红 ✔ |
| `prime()` 不建基线 | 红 ✔ |
| watcher 的刷新 `reason` 不是 `watcher` | 红 ✔ |
| `close_project` 不停 watcher / `open_project` 不挂 watcher | 红 ✔ ×2 |
| `invalidate_project` 不按项目过滤 | 红 ✔ |
| `panel.file_changed` 不带 stems | 红 ✔ |
| watcher 自己再发一条 `registry.changed` | 红 ✔ |

### 这一轮学到的：变异自己可能不是变异

第一版的「注册表变化一律不刷新」写成了

```python
keep.registry = set() or {name for name in batch.registry if …}
```

——`set()` 是假值，`or` 于是原样求值到后面那个推导式。**代码文本变了，行为
一个字节没变**，跑完全绿。

这是"变异显示绿"的**第四种**成因：不是判据弱（Session 03/04 各撞过一次），
不是那条分支从没被执行过，也不是 `__pycache__` 命中旧 `.pyc`，而是
**这条变异根本不是一次变异**。变异脚本里"先证明产物真的变了"那一步只比
文本，挡得住 `s.replace()` 的静默 no-op，挡不住语义 no-op。

**处置**：改成整段替换（`keep.registry = set()`），并**顺手补上反方向的那一条**
（`keep.registry = set(batch.registry)`，即自写也不摘）——一行判据的两个越界
方向各来一次，比只钉一侧可靠（同 Session 04 的 EINVAL 教训）。

> 三条老纪律这一轮全部生效：脚本在脏树上**拒跑**（Session 03/04 各踩过一次
> "`git checkout --` 吃掉未提交的修复"）；每轮清 `src/**/__pycache__`；
> 锚点计数必须恰好 1。

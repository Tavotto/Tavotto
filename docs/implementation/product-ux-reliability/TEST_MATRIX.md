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
| **素材清单的并发治理** | **已有（06）** | `web/src/store/assetStore.test.ts`（14 条） | — |
| ├ 合并 / 结束后的新事件另发 / force 不被吞 | 已有（06） | `describe('合并')` ×3 | — |
| ├ 旧响应不覆盖新响应（成功与失败两条路径） | 已有（06） | `describe('旧响应不覆盖新响应')` ×2 | — |
| ├ 换项目：成功与失败的响应都作废；`null` 与具体 id 不合并 | 已有（06） | `describe('项目隔离')` ×4 | — |
| ├ 后台失败保留最后成功数据 / 首次失败仍是「没加载过」 | 已有（06） | `describe('失败的处置')` ×3 | — |
| **PanelObject 派生元数据原地同步** | **已有（06）** | `web/src/store/panelSourceSync.test.ts`（19 条） | — |
| ├ `script: null → 有值` / `有值 → null` / cost / 位图尺寸 / 载体类型 | 已有（06） | 升级 + 降级 + 其它派生字段三组 | — |
| ├ 同 fileId 多实例（含非激活画布）；激活画布只算一遍 | 已有（06） | `describe('同 fileId 的多个实例')` ×2 | — |
| ├ 几何 / crop / 旋转 / overrides / 成组 / 锁定 / 名称不变 | 已有（06） | `describe('绝不修改的东西')` ×2（含 runtime 面板整个跳过） | — |
| ├ 图幅（nativeW/H）**不是**派生字段 | 已有（06） | `不是派生字段：图幅的权威是渲染回来的 manifest` | — |
| ├ 缺失素材保留对象、不抹 script；只有缺失时零改动 | 已有（06） | `describe('缺失素材')` ×2 | — |
| ├ 返回 upgraded / downgraded / changed / missing 差异 | 已有（06） | 各 describe 都断言了返回值 | — |
| ├ 素材粒度**三档**：有脚本→重建 / 刚失去→清缓存 / 从来没有→都不做 | 已有（06） | `从来没有脚本的面板…` + 升级/降级两组 | — |
| **派生同步与保存链路** | **已有（06）** | `web/src/store/derivedAutosave.test.ts`（8 条） | — |
| ├ 置 dirty + 排落盘，但不推 `saveState`、不进历史 | 已有（06） | 前三条 | — |
| ├ 崩溃兜底副本里带着最新的派生元数据 | 已有（06） | `防抖窗口过去之后…` | — |
| ├ 冲突未决时只写本机副本；无差异不排落盘；空载荷是 no-op | 已有（06） | 后三条 | — |
| **SSE 事件 → 画布** | **已有（06）** | `web/src/hooks/useServerEvents.test.ts`（26 条） | — |
| ├ registry / assets / panel.file_changed 三条的消费 | 已有（06） | 前三个 describe | — |
| ├ 一批事件合并成一次请求、一条提示 | 已有（06） | `describe('一批事件')` | — |
| ├ `pj` 不匹配忽略 / 切项目途中的旧响应作废 | 已有（06） | `describe('项目隔离')` ×2 | — |
| ├ 升级标 stale、不自动进编辑态；降级退出编辑、清缓存、保画布选择 | 已有（06） | `describe('降级')` ×6 | — |
| ├ 刷新失败不拿旧清单去同步、不弹提示 | 已有（06） | `describe('刷新失败')` | — |
| ├ `project.error` 走 `errors:*` 码表，未知 code 有回退 | 已有（06） | `describe('project.error')` ×2 | — |
| ├ SSE 重连恢复：补一次、节流、没开项目不做 | 已有（06） | `describe('SSE 重连恢复')` ×3 | — |
| **事件字段解码（三个纯函数）** | **已有（06）** | `web/src/lib/serverEventFields.test.ts`（13 条） | — |
| **手动刷新入口** | **已有（06）** | `AssetBrowser.refresh.test.tsx`（5 条）+ `useServerEvents.test.ts::手动刷新`（2 条） | — |
| ├ 调 `POST /api/project/refresh`、loading、失败可见、无障碍名不含内部术语 | 已有（06） | 同上 | — |
| 统一 refresh 服务 | **已有（04）** | `tests/test_project_refresh.py`（39 条） | — |
| **项目 watcher：整棵树的快照** | **已有（05）** | `tests/test_project_watch.py`（44 条） | — |
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
| **Readiness 事实模型 / capability API** | **已有（07）** | `tests/test_project_readiness.py`（53 条） | — |
| ├ 六个状态各一条：registered→editable / 静态唯一→auto_linkable / 动态输出→needs_probe / 双认领→conflict / 脚本丢失→source_missing / 无候选→layout_only | 已有（07） | `TestClassification` 前六条 | — |
| ├ 冲突**绝不自动裁决**（更新的 mtime + 更像的文件名都不许赢） | 已有（07） | `test_two_scripts_claiming_one_stem_is_a_conflict_and_is_never_auto_resolved` | — |
| ├ 注册表优先于静态冲突，`resolved_by` 带出裁决人 | 已有（07） | `test_the_registry_wins_over_a_static_conflict` | — |
| ├ `source_missing` 仍给出新认领者作为候选（改名/重构） | 已有（07） | `test_source_missing_offers_a_new_claimant_as_a_candidate` | — |
| ├ 只读项目 / 非法注册表 / 写失败：三种 `auto_linkable` 成因分得开 | 已有（07） | `TestClassification` ×3（+ 一条真走失败刷新记账） | — |
| ├ legacy `mm_registry.json` / 没有注册表文件（`registry_valid: null`） | 已有（07） | ×2 | — |
| ├ 子目录脚本 / 同脚本多 stem / 同 stem 多格式（各计一次、共享一条来源） | 已有（07） | ×3 | — |
| ├ 状态 × reason 的组合必须在 `REASONS_BY_STATUS` 里备案；表里无死行 | 已有（07） | `TestEnums` ×2 | — |
| ├ **不执行用户脚本**：磁盘 CANARY + probe/pool 入口全桩两层证据 | 已有（07） | `TestNeverRunsUserCode::test_readiness_never_probes…` | — |
| ├ 不改注册表（磁盘字节 + 内存索引）、不写项目任何文件、不发 SSE | 已有（07） | ×3 | — |
| ├ 报告里不出现绝对路径、脚本源码、注册表原文 | 已有（07） | `test_no_absolute_path_and_no_file_content_leaks_into_the_report` | — |
| ├ summary 每项只计一次、总数相等、空项目全零 | 已有（07） | `TestSummaryAndFingerprint` ×2 | — |
| ├ 排序按 **id 字符串**（`a.pdf` / `a/z.pdf` 把两种顺序分开） | 已有（07） | `test_panel_order_is_stable` | — |
| ├ fingerprint：同事实不变 / 状态变则变 / `generated_at` 无关 / 无关文件与 touch 无关 | 已有（07） | ×4 | — |
| **就绪度缓存** | **已有（07）** | `TestCache`（12 条） | — |
| ├ 第二次请求不重扫（判据是 `discover()` 真跑了几遍，不是对象身份） | 已有（07） | `test_a_second_request_does_not_rescan` | — |
| ├ 进出都深拷贝：第一个与第二个调用方各改一次都污染不了缓存 | 已有（07） | `test_the_returned_report_is_never_the_cached_object` | — |
| ├ 签名两维各一条：同尺寸改写（mtime 维）/ 同 mtime 改写（size 维） | 已有（07） | ×2 | — |
| ├ 缓存键含内存里那份注册表（磁盘没动、索引变了也要跟上） | 已有（07） | `test_the_in_memory_registry_is_part_of_the_cache_key` | — |
| ├ 新脚本 / 新素材 / 可写性翻转 / 注册表合法性翻转都失效 | 已有（07） | ×4 | — |
| ├ 刷新改了事实才失效；无差异不动缓存 | 已有（07） | ×2 | — |
| ├ 多项目缓存隔离；扫描失败不进缓存且能恢复；并发读只扫一遍 | 已有（07） | ×3 | — |
| **`/api/project/readiness` 与 `/api/panels` 集成** | **已有（07）** | `TestEndpoint`（5 条）+ `TestPanelsIntegration`（5 条） | — |
| ├ 未打开项目 409 `no_project`；响应字段集固定；项目隔离 | 已有（07） | ×3 | — |
| ├ 非法注册表**不是 500**，素材一张不少 | 已有（07） | `test_an_invalid_registry_is_not_a_500_and_keeps_every_asset` | — |
| ├ capability 逐字段等于就绪度同名字段（同源，不是第二次计算） | 已有（07） | `test_capability_comes_from_the_same_report` | — |
| ├ editable 保留旧 `script`/`cost`；layout_only 与有候选的 panel 都不伪造 `script` | 已有（07） | ×3 | — |
| ├ `/api/panels` 旧字段不回归 | 已有（07） | `test_the_old_panel_fields_do_not_regress` | — |
| ├ 报告带 `stem`（关联动作的键）；`sub/Fig.v2.pdf` → `Fig.v2` | **已有（08）** | `test_the_stem_is_reported_and_is_not_the_id_nor_the_first_dot_segment` + 同 stem 两份素材那条 | — |
| **就绪度前端 store** | **已有（08）** | `web/src/store/projectReadinessStore.test.ts`（22 条） | — |
| ├ 一批事件合并成一次请求；`force` 永远另起一次 | 已有（08） | `describe('取回报告')` ×3 | — |
| ├ 旧响应不覆盖新的（请求序号）；切项目的在途响应一个字节不落地 | 已有（08） | `describe('并发：旧响应不覆盖新的')` ×2 | — |
| ├ fingerprint 相同 = **不换报告对象引用**（fixture 每次造新对象，否则判据恒真） | 已有（08） | `describe('fingerprint')` ×2 | — |
| ├ 后台失败保留上一次成功那份；再成功一次清 error | 已有（08） | `describe('失败')` ×2 | — |
| ├ 横幅关闭按「项目 + fingerprint」记：落盘 / 换代重现 / 跨项目隔离 / 坏 blob 恢复 | 已有（08） | `describe('横幅的关闭状态')` ×6 | — |
| ├ 横幅显示条件：全 editable 不显示 / 空项目不显示 / 报告未到不显示；「待连接」是四个非终态之和 | 已有（08） | `describe('横幅的显示条件')` ×4 | — |
| ├ `focusPanel()` 打开既有开关并记聚焦；关闭清聚焦；换项目 `clear()` | 已有（08） | 后两个 describe ×3 | — |
| **项目摘要横幅** | **已有（08）** | `web/src/components/ProjectReadinessBanner.test.tsx`（9 条） | — |
| ├ 四个数分得开（可编辑 / 待连接 / 仅排版）；文案不含实现术语 | 已有（08） | `describe('显示条件')` ×2 | — |
| ├ 全 editable / 空项目 / 报告未到 / 中心已开 四种沉默 | 已有（08） | 同上 ×4 | — |
| ├ 「查看接入状态」开中心；「关闭」只关横幅、报告仍在；换代后重现 | 已有（08） | `describe('两个动作')` ×3 | — |
| **接入中心（原 RegistryDialog）** | **已有（08）** | `web/src/components/RegistryDialog.test.tsx`（18 条） | — |
| ├ 六个状态各有一行、各有状态名与一句自然话 | 已有（08） | `describe('六个状态')` ×1 | — |
| ├ 普通用户可见的段落不出现 registry / stem / manifest / AST | 已有（08） | 同上 ×1 | — |
| ├ 顶部四个数；`layout_only` 不画成错误且说清它还能干什么 | 已有（08） | 同上 ×2 | — |
| ├ 打开对话框零 probe；试运行点了才跑且先说清会运行脚本 | 已有（08） | `describe('绝不替用户决定')` ×2 | — |
| ├ 冲突：候选全列、不预选、不自动写；点哪个写哪个，**写的键是 stem** | 已有（08） | 同上 ×2 | — |
| ├ 技术详情默认收起；展开才有源脚本 / 入口 / reason code | 已有（08） | `describe('技术详情')` ×2 | — |
| ├ 聚焦：焦点落到那一行；聚焦标记当场清掉 | 已有（08） | `describe('聚焦到指定的一张图')` ×2 | — |
| ├ 动作之后走统一刷新（就绪度 + 素材都重取）；重新扫描调既有端点 | 已有（08） | `describe('动作之后的刷新')` ×2 | — |
| ├ 只读 / 没扫成 / 记录读不回来 三句**分开**说 | 已有（08） | `describe('项目级状态')` ×3 | — |
| ├ 首次取不到：可重试的错误态，不是空白对话框 | 已有（08） | `describe('取不到就绪度')` | — |
| **素材卡状态与说明条** | **已有（08）** | `web/src/components/left/AssetBrowser.readiness.test.tsx`（11 条） | — |
| ├ 五种不可编辑各有自己的角标文字；editable 保留 `{}`、不再加角标 | 已有（08） | `describe('卡片上的状态')` ×2 | — |
| ├ 状态进 `aria-label`；`capability` 缺席**不补默认状态** | 已有（08） | 同上 ×2 | — |
| ├ option 内零 `<button>`、零可 Tab 控件；方向键导航不回归 | 已有（08） | `describe('无障碍：option 里不许…')` ×2 | — |
| ├ 说明条在 listbox 外、随选中切换、editable 与 capability 缺席都沉默 | 已有（08） | `describe('选中卡片后的说明条')` ×5 | — |
| **画布与属性栏的解释入口** | **已有（08）** | `panelReadinessEntry.test.tsx`（7 条）+ `panelCapabilityNote.test.tsx`（5 条） | — |
| ├ 出现条件：非 editable 才有；editable / capability 缺席 / 派生同步未跑完都不出现 | 已有（08） | `describe('入口出现的条件')` ×4 | — |
| ├ 点下去只打开中心 + 聚焦：选择不变、不进裁剪/图内编辑、文档零改动 | 已有（08） | `describe('点下去只解释，什么都不改')` ×3 | — |
| ├ 属性栏那条：非阻塞（无 alert）、按 reason 取句子、可编辑与缺席都沉默 | 已有（08） | `panelCapabilityNote.test.tsx` ×5 | — |
| **常驻左侧工作区外壳** | **已有（08）** | `uiStore.test.ts` 的两个新 describe（9 条）+ `drawerViewportResize.test.tsx`（5 条） | — |
| ├ 默认展开停在素材页；用户折叠落盘；重启恢复（两个方向各一条） | 已有（08） | `describe('左侧工作区：默认常驻、可折叠、偏好跨会话')` ×4 | — |
| ├ **响应式让位不写回偏好**：互斥断点自动收起 / 窄屏开机裁剪，本机存的仍是展开 | 已有（08） | `describe('窄窗口的自动让位不许覆盖桌面偏好')` ×3 | — |
| ├ 用户自己关掉的那一侧照旧落盘（豁免不是"什么都不记"） | 已有（08） | 同上 ×1 | — |
| ├ 抽屉开合 → 画布视口重算；`zoom/panX/panY` 一位不动；无差异零 `set` | 已有（08） | `drawerViewportResize.test.tsx` ×5 | — |
| ├ 六个状态名 / 三个新按钮的中英文字数预算 | 已有（08） | `web/src/i18n/overflow.test.tsx` 的 `BUDGETS`（9 条） | — |

## FAST EDIT / LAYOUT / STYLE / SPEC / VALIDATION / EXPORT

| 场景 | 状态 | 位置 | 归属 |
| --- | --- | --- | --- |
| 出版规范预检（两侧同一份向量） | 已有 | `tests/test_preflight.py` + `web/src/lib/preflight.golden.test.ts` + `tests/golden/preflight_vectors.json` | — |
| 导出端点 | 已有 | `tests/test_export_endpoint.py` | — |
| 打包（图 + 脚本 + 证明） | 已有 | `tests/test_package.py` | — |
| 写回事务（prepare/verify/commit、像素门） | 已有 | `tests/test_write_back.py`、`test_pixel_compare.py` | — |
| 几何权威 / 面板渲染 | 已有 | `web/src/store/geometryAuthority.test.ts`、`tests/test_manifest_geometry.py` | — |
| 快速编辑模式（不经画布） | 已有（09） | `web/src/store/workspace.test.ts`、`web/src/canvas/fastEditStage.test.tsx` | — |
| 原图规格合同（`OriginalOutputSpec`） | 已有（09） | `web/src/lib/originalSpec.test.ts`、`tests/test_original_spec.py` | — |
| 原图规格 → 真的导出成那个尺寸 | 未开始 | — | 12 |
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

## Session 05 的变异反证（31 条，全部 KILLED）

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
| `stop()` 不取消 pending 批次 / stop 之后仍结算已取走的那一批 | 红 ✔ ×2 —— 见下「抽掉不红」 |
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

### 第二件：一条守卫把另一条判据的行为面盖住了

`_dispatch()` 开头补上 `stop_event` 检查（"这一批可能已经被线程取走了"）之后，
**「`stop()` 不清 pending」这条变异活了下来**——新守卫把它的行为面整个盖住。

这是「抽掉不红」的两种成因之一，而**删错了会把刚防住的东西放回去**。逐条问：

* 这一句还有没有独立价值？**有**——一个停掉的 watcher 抱着一批永远不会结算
  的变化，是个会在下一个人手里变成 bug 的状态；
* 那为什么量不到？因为它的维度是**状态**，而那条用例断言的是**行为**。

处置不是删代码、也不是放宽判据，而是**换一把量得到那个维度的尺**：
`assert not w._pending`。顺带补一句**前提断言**（`assert w._pending`）——
否则"这一批本来就没攒上"会让后半句恒真（同 04 的「恒等成立的 diff」）。

> 三条老纪律这一轮全部生效：脚本在脏树上**拒跑**（Session 03/04 各踩过一次
> "`git checkout --` 吃掉未提交的修复"）；每轮清 `src/**/__pycache__`；
> 锚点计数必须恰好 1。


---

## Session 06 的变异反证（55 条，全部被打红）

前端这一轮没有 `__pycache__` 那类陷阱，但换了两个新的：**`npx vitest` 会漏掉
`NODE_OPTIONS=--no-experimental-webstorage`**（localStorage 全局是 undefined，
用例报的是"读不到属性"而不是"断言不成立"），以及 `npx` 会在仓库根建一个空的
`node_modules/.vite`——跑完记得删，`.gitignore` 只忽略 `web/node_modules/`。

变异脚本还原走**备份文件**而不是 `git checkout --`：工作树里此刻有一堆未提交
的新文件，后者会把它们一起吃掉（Session 03/04 各踩过一次）。

| 变异 | 结果 |
| --- | --- |
| `affectedScriptsOf` 漏掉单脚本兼容字段 `script` | 红 ✔ |
| `strings()` 不过滤非字符串 / `union()` 不去重 | 红 ✔ ×2 |
| `affectedAssetIdsOf` 只看并集字段（丢三个细分） | 红 ✔ |
| `affectedStemsOf` 只认一种事件 | 红 ✔ |
| assetStore：旧响应照样落地（去掉序号判据） | 红 ✔ |
| assetStore：成功路径 / 失败路径不看项目 | 红 ✔ ×2 |
| assetStore：`null` 项目被当成"随便哪个项目都行" | 红 ✔ |
| assetStore：`force` 也被在途请求吞掉 | 红 ✔ |
| assetStore：根本不合并（每次都发新请求） | 红 ✔ |
| assetStore：失败时清空 `panels` / `loading` 不看在途请求 | 红 ✔ ×2 |
| sync：`script` 缺席与 `null` 不归一（每轮都判成"变了"） | 红 ✔ |
| sync：顺手把图幅（`nativeW`）也同步了 —— 几何！ | 红 ✔ |
| sync：素材不见了就当降级 | 红 ✔ |
| sync：runtime 面板不跳过 | 红 ✔ |
| sync：激活画布被数两遍 | 红 ✔ |
| sync：无差异也换一份新 `doc` | 红 ✔ |
| sync：降级的面板也进重建名单 | 红 ✔ |
| sync：`affectedIds` 过滤失效 | 红 ✔ |
| documentStore：派生同步照样推成「未保存」 | 红 ✔ |
| documentStore：派生同步不排落盘 | 红 ✔ |
| documentStore：派生写入清空撤销栈 / 不升代次 / 空守卫被抽掉 | 红 ✔ ×3 |
| liveSync：升级的面板不转入引擎跟踪 | 红 ✔ |
| liveSync：降级不清渲染缓存（manifest 残留 → 界面还显示"可编辑"） | 红 ✔ |
| liveSync：降级不退出图内编辑 / 顺手把画布选择也清了 | 红 ✔ ×2 |
| liveSync：降级提示不分「正在编辑那张」 / 升降级同批时说反了 | 红 ✔ ×2 |
| liveSync：丢弃的响应也拿去同步文档 | 红 ✔ |
| liveSync：手动刷新只重取素材、不走统一刷新 | 红 ✔ |
| liveSync：后端刷新失败就不再取素材 | 红 ✔ |
| liveSync：重连顺手扫一遍磁盘 / 不节流 / 没开项目也去恢复 | 红 ✔ ×3 |
| events：`assets.changed` 没人消费 | 红 ✔ |
| events：`registry.changed` / `panel.file_changed` 不刷新素材 | 红 ✔ ×2 |
| events：`panel.file_changed` 等刷新回来才 markStale | 红 ✔ |
| events：项目隔离守卫被抽掉 | 红 ✔ |
| events：`project.error` 一律用通用文案 / 弹成普通提示 | 红 ✔ ×2 |
| events：stems 取不到（面板认领失效） | 红 ✔ |
| UI：刷新按钮退回只调 `assetStore.load()` | 红 ✔ |
| UI：刷新期间不挡重复点击 / 失败静默吞掉 / 无障碍名回到内部术语 | 红 ✔ ×3 |
| liveSync：打开项目时不对账 / 对账拿的不是当前清单 | 红 ✔ ×2 |
| sync：素材粒度的三档塌成两档（降级的进重建 / 没脚本的进重建 / 没脚本的算降级） | 红 ✔ ×3 |

### 五条第一轮活下来的，三种成因

**成因一：变异没问对问题（两条）。**

* 「`null` 项目与具体 id 合并」——锚点落在**失败分支**上，而那条用例走的是
  成功分支。同一句话在两条路径上各有一份判据，改错哪一份都测不到想测的
  那一维。处置：拆成两条变异（失败路径的项目守卫、合并入口的项目守卫），
  并补一条"换项目之后旧项目那次**失败**不许把错误记到新项目头上"。
* 「无差异也写一次文档」——`applyDerivedUpdate({})` 被写入口**自己的空守卫**
  接住了，于是这条变异是个语义 no-op（Session 05 撞过的同一族）。处置：拆成
  两条各自可观测的变异（写入口的空守卫、调用方的差异条件），并补一条直接
  调 `applyDerivedUpdate({})` 的用例——那个守卫是它自己契约的一部分，
  下一个调用方（07 的就绪度）不一定会先算差异。

**成因二：判据缺一维（两条）。**

* 「升降级同批时优先级反了」——所有用例要么只有升级、要么只有降级，
  两者同时发生的那一格空着。而它正是最该说清楚的一格：**得而复失里用户
  必须知道的是"失"**，得到的那份下次双击自然会发现，失去的那份不说就变成
  "点进去什么都没有"。补了一条同批升+降的用例。
* 「丢弃的响应也拿去同步文档」——用例里被丢弃时 `byId` 恰好是空的，于是
  "拿旧数据同步"与"不同步"看起来一样。补的那条把 `byId` 预置成**上一轮
  成功刷新留下的、此刻已经不新鲜的**清单，两种行为这才分得开。

**成因三：那个守卫本来就是多余的（一条）。**

`syncLoadedDocument()` 第一版写了 `if (!useAssetStore.getState().loaded) return`，
抽掉它**不红**。逐条问下来这次的答案是"删"而不是"补用例"：清单还没取回来时
`byId` 是空的，每个面板都会走「素材不在清单里」那一支，而那一支
**在结构上就一个字节都不改**（T-28，已有用例钉着）。也就是说这个守卫没有
任何一个用例能分辨的行为面——它不是缺一维，它是重复了一遍下游已经保证的事。

判「删」之前先证伪了唯一一个可能的反例：`byId` 非空而 `loaded` 为假。
全仓只有 `embedded/session.ts` 会直接 `setState` 素材，而它 `loaded: true`
一起写。所以那个组合不存在。**删掉之后两条变异（不对账 / 拿错清单）都是红的**
——真正在守东西的是那一句 `applyPanelSync(syncPanelSourceMetadata(byId))` 本身。

---

## Session 07 的变异反证（35 条，全部被打红）

跑法与 05/06 一样：还原走**备份文件**而不是 `git checkout --`（工作树里有
未提交的新文件），并且带 `PYTHONDONTWRITEBYTECODE=1`——没有它，同尺寸 +
同一秒写入会命中旧 `.pyc`，变异跑出来是假绿。**选择器不缩窄**：每条变异都跑
整个 `tests/test_project_readiness.py`（改 `project_refresh.py` 的那四条再带上
`tests/test_project_refresh.py`）。

| 变异 | 结果 |
| --- | --- |
| `editable` 不看脚本文件在不在 | 红 ✔ |
| `source_missing` 降成 `layout_only` | 红 ✔ |
| `conflict` 自动挑第一个候选 | 红 ✔ |
| 阻塞成因一律不报 / 只读不报 / 写失败与「还没登记」合并 | 红 ✔ ×3 |
| 扫描失败报成 `no_source_candidate` | 红 ✔ |
| 扫描失败时 `conflicts` 给 `[]` 而不是 `null` | 红 ✔ |
| 扫描失败的报告也进缓存（永远恢复不了） | 红 ✔ |
| `can_probe` 恒 true / `can_manual_link` 不看可写性 | 红 ✔ ×2 |
| 静态冲突推翻注册表裁决 | 红 ✔ |
| fingerprint 掺进时间 | 红 ✔ |
| summary 不预置六个零（空项目缺键） | 红 ✔ |
| panels 不按 id 排序 | 红 ✔ |
| 脚本签名丢掉 mtime 维 / 丢掉 size 维 | 红 ✔ ×2 |
| 缓存键不含注册表内容 / 素材集合 / 可写性 / 注册表合法性 | 红 ✔ ×4 |
| 缓存出口不深拷贝 / 入口不深拷贝 | 红 ✔ ×2 |
| 「没有注册表文件」与「注册表坏了」合并成一个 false | 红 ✔ |
| 注册表结构校验被跳过（只看 JSON 解得动） | 红 ✔ |
| dynamic 候选不算数（`needs_probe` 塌成 `layout_only`） | 红 ✔ |
| `capability_map` 吞掉 `candidates` | 红 ✔ |
| `issues` 不报注册表非法 | 红 ✔ |
| 刷新：写失败不记账 / 写成功不清账 | 红 ✔ ×2 |
| 刷新：永不失效就绪度缓存 / 无条件失效 | 红 ✔ ×2 |
| `/api/panels` 拿候选冒充 `script` | 红 ✔ |
| `/api/panels` 自己另算一遍 capability（不同源） | 红 ✔ |
| 就绪度端点少了 `Cache-Control: no-store` | 红 ✔ |

### 第一轮活下来的七条，两种成因

**成因一：同一条保证有两个实现，谁也杀不死谁（2 条）。**
「panels 不按 id 排序」与「素材清单不排序」两条**都存活**——因为排序做了两遍
（`assets.sort()` 一次、`sorted(panels, key=id)` 一次），删掉任意一处都还有
另一处兜着。这不是"多一层保险"，是**判据量不到自己**：那一维在变异下恒绿，
而恒绿的门禁与没有门禁的区别只是它看起来像有。处置是**删掉冗余的那一处**
（决策 T-36），顺序的契约只留 `panels` 那一份；再跑，变异当场被打红。

**成因二：用例只跑了「方便的那个时刻」（5 条）。**
剩下五条的形状完全一样——判据在，但用例把状态**摆好之后才第一次读**，于是
缓存里根本没有旧值可以过期：

* 只读项目：`_Ctx` 是 `chmod` **之后**才建的 → 「可写性在缓存键里」量不到；
* 非法注册表：改坏**之后**才第一次 `compute()` → 同上；
* 内存注册表：全程没有"磁盘没动、只有索引变了"这一刻；
* 深拷贝出口：只改了第一个调用方拿到的那份（它本来就不是缓存本体），
  没有人去改**命中缓存**交出来的那一份；
* 结构校验：三处非法注册表用的都是 `"{ 坏了"`——`json.loads` 那一步就挂了，
  `Registry.load_data()` 那一层从来没被走到。

五条的处置都是**把用例挪到"恢复/失效的那一刻"**：先热一遍缓存，再改条件，
再读第二遍；结构校验换成一份 JSON 解得动、`entry` 非法的注册表。这与
Session 05 的「测恢复的那一刻」是同一族——**缺陷藏在状态翻转那一下，而用例
最容易只跑翻转之后。**

---

## Session 08 的变异反证（33 条，全部被打红）

跑法与 05/06/07 一样：还原走**备份文件**而不是 `git checkout --`（工作树里有
未提交的新文件）；后端那一条带 `PYTHONDONTWRITEBYTECODE=1`。前端每条变异跑
**整个**对应的用例文件，不缩窄选择器——选择器缩到看不见判据的那条用例上，
变异当然是绿的（那是「变异显示绿」的第三种成因）。

脚本：`scratchpad/mutate.py`（本轮一次性工具，不进仓库）。

| 变异 | 打红的用例文件 | 结果 |
| --- | --- | --- |
| 拿掉「旧响应不覆盖新的」（请求序号） | `projectReadinessStore.test.ts` | 红 ✔ |
| 拿掉「不落进另一个项目」（pj 判据） | 同上 | 红 ✔ |
| 横幅忽略关闭状态 | 同上 | 红 ✔ |
| 全部可编辑也显示横幅 | 同上 | 红 ✔ |
| 后台失败清空报告 | 同上 | 红 ✔ |
| 同 fingerprint 也换报告对象引用 | 同上 | 红 ✔ |
| `persist()` 照抄当前状态（响应式让位写回偏好） | `uiStore.test.ts` | 红 ✔ |
| `editable` 也画状态角标 | `AssetBrowser.readiness.test.tsx` | 红 ✔ |
| 状态不进 `aria-label` | 同上 | 红 ✔ |
| 说明条给 `capability` 缺席补一个默认状态 | 同上 | 红 ✔ |
| `capability` 缺席也给「为什么不能编辑？」 | `panelReadinessEntry.test.tsx` | 红 ✔ |
| 打开接入中心就跑脚本 | `RegistryDialog.test.tsx` | 红 ✔ |
| 技术详情默认展开 | 同上 | 红 ✔ |
| 只读项目也渲染手工关联控件 | 同上 | 红 ✔ |
| 关联写的键改成文件名（而不是 stem） | 同上 | 红 ✔ |
| 冲突只列第一个候选 | 同上 | 红 ✔ |
| 视口同步顺手 `fit()`（对象跳动） | `drawerViewportResize.test.tsx` | 红 ✔ |
| 统一刷新里不带就绪度 | `RegistryDialog.test.tsx` | 红 ✔ |
| 一句话按 `status` 查而不是按 `reason_code` | `panelCapabilityNote.test.tsx` | 红 ✔ |
| 报告里去掉 `stem` 字段（后端） | `tests/test_project_readiness.py` | 红 ✔（3 条） |
| 仅排版的图不给「选择源脚本」 | `RegistryDialog.test.tsx` | 红 ✔ |
| 入口取不到时编一个 `'main'`（前端造第二个默认） | 同上 | 红 ✔ |
| 入口三个出处各屏蔽一个（已登记 / 候选 / 脚本清单） | 同上 | 红 ✔ ×3 |
| 可编辑的图不给改绑 / 改绑摆到第一层 | 同上 | 红 ✔ ×2 |
| 改绑候选里也列当前脚本 / 候选不排在前面 | 同上 | 红 ✔ ×2 |
| 「待连接」漏掉 `source_missing` / 把 `layout_only` 也算进去 | 三个用例文件 | 红 ✔ ×2 |

### 第一轮活下来的两条，成因各不相同

**1. 「同 fingerprint 不换引用」——判据预设了自己的结论。**
fixture 写的是 `mockFetch.mockResolvedValue(report())`：`report()` **只求值
一次**，两次响应是同一个对象引用。于是 `expect(...).toBe(first)` 恒真——
它证明的是"我摆进去的东西还在"，不是"store 复用了旧的那一份"。
处置：改成 `mockImplementation(async () => report())`（每次造一个新的、内容
相同的对象），并先断言这一轮**真的**有新对象进来。

**2. 「说明条不给 capability 缺席补默认」——判据缺一维。**
判据在，但没有任何一条用例**选中**过一张没有 capability 的卡片：卡片角标那条
只看角标，说明条那三条用的卡片都带 capability。缺席这一维在说明条上从来没被
量过。处置：补一条「选中 capability 缺席的卡片 → 说明条不出现」。

两条都不是"判据写错了"，而是**判据没有被执行到自己该看的那个点上**——与
Session 07 的第二类成因同形（用例只跑了方便的那个时刻）。

**3. 「入口取自已登记的那份」第一轮也活了下来——第三种形状：两个出处给出
同一个答案。** fixture 里 `ok.py` 的登记 entry 与静态解析出的
`entry_candidates[0]` **写成了同一个值**，于是屏蔽掉第一个出处，第三个出处
照样返回它，判据永远量不到自己（这正是 T-36 的形状，只不过冗余在 fixture 里
而不在实现里）。处置：让两个值真的不同（`draw` vs `main`）——现实里它们本来
就会分开（用户手工改过 entry、或脚本后来变了），不是为了测试硬造的形状。

### 另外两条：尺子看不见那一维，与一条杀不死的冗余

**「改绑候选里不含当前脚本」第一轮量不到。** 判据原本打在 Radix Select 的
触发器文本上——而选项住在弹层里，触发器上显示的是 placeholder。**那把尺子
根本看不见这一维**，所以无论实现怎么改它都恒真。处置：把算选项的那段抽成
纯函数 `sourceOptions(panel, allScripts)`，判据直接打在它上面（顺带多守住
一条「候选排前面」）。

**同一条判据里还藏着一条杀不死的冗余。** 抽出来之后
`panel.candidates.filter((s) => s !== panel.script)` 仍然杀不死——因为后端
**结构性地**不会把已绑定的脚本放进候选（`editable` 的候选恒为空，
`source_missing` 的候选是 `[s for s in claims[stem] if s != script]`）。
没有任何输入能让那一句生效，也就没有任何用例能打红它。**处置是删掉它**
（不是去造一个后端给不出来的输入来"覆盖"它）：`allScripts` 那一半的同名过滤
是真需要的，两者不是一回事。

---

## Session 09 新增用例（后端 17 / 前端 53）

### 后端 `tests/test_original_spec.py`（16 条）

| 组 | 条数 | 守什么 |
| --- | ---: | --- |
| `TestRasterDpi` | 10 | 密度先量后猜：PNG `pHYs` / JPEG JFIF / Exif（英寸与厘米）读得到就报 `metadata`；单位字节为 0 的 pHYs 与 unit=1 的 Exif 只是长宽比，不是密度；**「没写」与「写着 96」是两个答案**；JFIF 压过 Exif；读不动文件退到 `assumed` 而不是崩；alpha 照实报 |
| `TestVectorSpec` | 2 | 矢量报视口不编像素；没测量的维度报 `unknown` / `None` |
| `TestPanelsProjection` | 2 | `/api/panels` 的 `native_*_mm` 是 `original_spec` 的**投影**；没有密度元数据时尺寸与改造前**逐位相同** |
| `test_frontend_and_backend_agree_on_the_dpi_source_set` | 1 | **闭集跨进程**：`DPI_SOURCES` ↔ `web/src/lib/api.ts` 的 `dpi_source` 联合（已登记进根 `AGENTS.md` 的严格同源对表）。对不上的表现不是报错，是「这个数是假定的」那句提示**安静地不出现** |
| （构造工具） | — | PNG / JPEG / Exif 都自己拼字节——只有这样才控制得住"写没写密度"这一维 |

### 前端

| 文件 | 条数 | 守什么 |
| --- | ---: | --- |
| `web/src/store/workspace.test.ts` | 19 | 打开单图直接进快速编辑；没有源脚本时诚实降级；项目里没有就不发明面板；重复打开/重复「添加到画布」不叠对象；**加入画布后 overrides 还在同一个对象 id 上**；从画布进图内编辑再返回位置尺寸 edits 全不动；切模式不进撤销栈、不置 dirty；按 documentId 恢复（对象没了 / 别的文档那一档 / 不认识的模式值 / 坏 blob 四种都回排版）；删除或隐藏当前面板就退出；图在另一张画布上时先切过去而不是复制一份 |
| `web/src/lib/originalSpec.test.ts` | 25 | 四档来源的优先级各一条；矢量/位图/可编辑 Figure 各报各的维度；缺 DPI 报 `assumed`；透明度没测量报 `null`；`source_missing` 保留上次已知并标 `stale`、密度改报 `derived`；runtime 不在清单里不算"源丢了"；**画布缩放/裁剪/旋转/翻转/透明度只进 `ignored`**；四种路径形态（含 Windows 反斜杠与盘符）不影响规格；绑定层对不认识的 id 回 `null` |
| `web/src/canvas/fastEditStage.test.tsx` | 8 | 快速编辑这一屏只画那一张图、没有页面纸；排版模式照旧画整张版；两个出口都在；**切进切出文档一个字节没动**（比整份 doc 的 JSON + 历史长度）；没有源脚本时说清原因并给出下一步；**规格不确定时说出来**（假定密度 / 上次已知 / 尺寸未知三档各一条） |
| `web/src/i18n/overflow.test.tsx` | +9 | 新文案的英文字数预算（模式标签 / 两个出口 / 降级说明条 / 素材卡「打开」） |
| `web/src/components/left/AssetBrowser.runtime.test.tsx` | 改 1 | 素材卡主动作换成「打开」之后，落面板那一步仍然把描述符交给 `addRuntimePanel`（反证 #2 原样成立） |
| `web/e2e/*.spec.ts` | 改 8 个文件 | 「打开」的语义变了：双击卡片当场进图内编辑态，8 个 spec 里「再点一次『编辑图内元素』」那一步会点到一个不存在的按钮，整批删掉；`golden-paths` 用标注工具前先回画布排版。**本轮没有真跑过**（Playwright 要真实后端与浏览器），只确认 `playwright test --list` 收得到全部 110 条 |

## Session 09 的变异反证（26 条，全部被打红）

脚本：`scratchpad/mutate.py`（一次性工具，不进仓库）。跑法与前几轮相同——
**先提交再变异**（`git checkout --` 才不会吃掉未提交的修复），Python 侧每条
先清 `__pycache__`（同尺寸 + 同一秒写入会命中旧 `.pyc`，那是"变异显示绿"的
又一种成因），每条都先断言"变异真的写进去了"。

| 变异 | 打红的用例 | 结果 |
| --- | --- | --- |
| PNG pHYs 的单位字节不看了 | `test_original_spec.py` | 红 ✔ |
| 量化还原关掉（299.9994 不再还原成 300） | 同上 | 红 ✔ |
| JFIF 不再压过 Exif | 同上 | 红 ✔ |
| 位图密度一律按假定（不读文件） | 同上 | 红 ✔ |
| `native_*_mm` 不再取自 spec | 同上 | 红 ✔ |
| raster probe 不再报 alpha | 同上 | 红 ✔ |
| 渲染回来的图幅不再压过文档那份 | `originalSpec.test.ts` | 红 ✔ |
| 文档那份不再压过磁盘事实 | 同上 | 红 ✔ |
| `stale` 恒假 | 同上 | 红 ✔ |
| 反算出来的密度冒充 `metadata` | 同上 | 红 ✔ |
| 缩放判据改用页面包围盒（裁过的图会被误报） | 同上 | 红 ✔ |
| fallback 不再标记自己 | 同上 | 红 ✔ |
| 文档里已有面板也再造一个 | `workspace.test.ts` | 红 ✔ |
| 没有源脚本也进图内编辑 | 同上 | 红 ✔ |
| 恢复时不验对象还在不在 | 同上 | 红 ✔ |
| 恢复时不看模式那一维（一律当 `fast_edit`） | 同上 | 红 ✔ |
| 坏 blob 不再兜底（抛出去） | 同上 | 红 ✔ |
| 快速编辑顺手把图的尺寸改掉 | 同上 | 红 ✔ |
| 对象消失后不退出快速编辑 | 同上 | 红 ✔ |
| 快速编辑仍然画整张版 | `fastEditStage.test.tsx` | 红 ✔ |
| 快速编辑仍然铺纸面 | 同上 | 红 ✔ |
| 「假定密度」标记不显示 | 同上 | 红 ✔ |
| 「上次已知」标记不显示 | 同上 | 红 ✔（**第一轮活下来的第二条**，见下） |
| 「尺寸未知」时照样显示那个编出来的尺寸 | 同上 | 红 ✔ |
| 三个来源标记全关掉 | 同上 | 红 ✔ |
| 后端的 `DPI_SOURCES` 少两个取值 | `test_original_spec.py` | 红 ✔ |

### 第一轮活下来的那一条：又是一条杀不死的冗余

「本机存着的模式值不合法就当没有」这条判据，改成恒假之后**没有任何用例会红**。
查下来不是判据错了，是它与 `restoreWorkspace` 里的 `saved.mode !== 'fast_edit'`
**说的是同一件事**——前者拦下不合法的取值，后者对任何不是 `fast_edit` 的取值
都回排版模式，两条路的结果逐字相同。这是 T-36 的形状。

处置**不是**造个输入去覆盖它，而是合成一处：`readFastEditTarget()` 直接回
「上次停在哪个面板上」（`string | null`），模式那一维只在这里判一次。合完之后
它变成可杀的，并且顺手暴露出**一个从来没被量过的维度**——"上次停在画布排版"
这一档：改造前的两条判据都能让它绿，而它才是最常见的那种存档。补了两条用例
（`mode: 'layout'` 与一个不认识的模式值），变异当场红。

一句话：**「杀不死」不总是"判据多余"，也可能是"两条判据合起来盖住了一个没人
量过的维度"。** 合并之后那个维度才露出来。

### 第一轮活下来的第二条：又是一个没被量过的维度

「源文件不在了 → 尺寸旁边说『上次已知』」这条判据改成恒假之后不红：三条新用例
里，一条走 `assumed`、一条走 `fallback`，**没有一条让素材从清单里消失过**。
`stale` 这一维在界面上从来没被量到。处置是补一条用例（打开之后把 `assetStore`
清空 —— 文件被删 / 网盘掉线的真实形状），补完当场红。

与前面那条合起来是同一句话的两个例子：**变异活下来时，先问"这条判据被执行到
它该看的那个点上了吗"，再怀疑判据本身写错了。** 本轮两条活的都是前者。

---

## Session 10 新增用例（后端 38 / 前端 41）

### 后端 `tests/test_profile_store.py`（33 个函数 / **37 条**，一条五路参数化）

| 组 | 判据 |
| --- | --- |
| 内置 | 规范来自 canonical JSON（不是复制）；**样式从默认规范派生**（判据换一份改过数字的规范来量，否则恒等成立）；样式里没有 PPI 字段 |
| 增删改复制 | create/update/delete/duplicate；重名加后缀不合并；内置只读但可复制；乐观并发撞车不覆盖且带回磁盘现值；恢复默认值内容回去、**身份留下**；用户自建规范走与内置同一套校验 |
| 落盘 | 位置是 `<data_dir>/profiles/`（包目录里零字节）；无 `.tmp` 残留；坏文件回退内置且**挪进 backup 不删**；更高 schema **原样不动**；单条坏不拖垮整份 |
| 导入导出 | 往返建的是**新的一条**；五种非法载荷各自的 code；超限**先卡再解析**；未识别字段进 `extra` 并记结构化 warning |
| 旧位置迁移 | 内容进 store、旧位置腾空、原件逐字节备份、幂等（第二次动作数 0）、warning、没有旧文件时什么都不做 |
| 8 pt | 默认规范只有一个数；**代码搜索式回归看护**（求值器/导出面板/设置页里不许出现 8.5 或 8.0 字面量，注释行除外）；两侧兜底常量同源；**显式存下的 8.5 仍然生效** |
| HTTP | CRUD + 409 冲突体带 `current`；PATCH 不带 revision 是 400；首次读触发迁移；未知 kind 是 400 不是 500 |
| resolve_spec | 内置与用户自建都找得到、复制出来的有自己的身份、journal 只覆盖点名的键；未知 id **抛错**（与前端刻意不同，T-51）；**journal 不合法时说的是 journal 不合法**，不是"没有这个规范" |

### 后端 `tests/test_preflight.py`（改 2 条 + 新 1 条）

* `test_the_default_spec_has_exactly_one_minimum_font_size` —— 8.2 通过、正好
  8.0 仍不算过、**且不同时报两条**；
* `test_the_strict_threshold_and_the_absolute_floor_are_still_two_checks` ——
  换 `free-form-v1`（6.0/5.0）来量：统一成一个数是**默认规范的取值**，
  不是把其中一条检查删掉了；
* `test_summarize_blocks_only_on_errors` 的样例从 8.2 换成 8.0 —— 8.2 现在是
  合规的，**留着它会让这条用例在实现坏掉时照样绿**。

### 前端

| 文件 | 条数 | 判据 |
| --- | ---: | --- |
| `lib/specBinding.test.ts` | 18 | 快照优先 / 明确同步 / 跟随（换规范后表态还在）/ 老文档 / 全局被删 / 期刊覆盖；**内容判据本身**（版本号没动而规则改了 → 提示；版本号跳了而规则没改 → 不提示） |
| `store/profileStore.test.ts` | 6 | 后端不在退内置且**不当错误**；200 但形状不对不抹清单；并发撞车留现值且**本地那条不被换掉**；错误文案**在英文界面下**按 code 翻 |
| `store/styleAndSpec.test.ts` | 6 | 应用样式一条历史可撤销（含背景）；「样式没管背景」≠「设成白色」；选规范正确 dirty 且带快照；同步是另一条历史；快照序列化后还在 |
| `components/settings/profilesSettings.test.tsx` | 11 | 默认视图不出现 id/版本（只在 `title` 里）；内置名字跟界面语言走、用户名字不翻译；内置只读且出口是复制；Style/Spec 字段整组换；warning 说得出；「本项目用这套规范」写的是带快照的绑定；「跟随更新」默认关着、打开可撤销、**换一套规范后表态还在**；`aria-current` 与可达名 |
| `components/ExportDialog.test.tsx` | 改 2 | 规范显示成「默认规范」且**断言不含 `lab-publication-v1`**；最小字号样例 8.2 → 7.8 |
| `lib/profile.test.ts` | 改 1 | 三个数收敛成 8 |

---

## Session 10 的变异反证（36 条，全部被打红）

**第一轮 0 条存活。** 两条差点变成空门禁的，在反证之前就先改掉了判据——
理由与 [[fixture-makes-the-predicate-vacuous]] 是同一个形状：

1. **「内置样式派生自规范」原来是恒等成立的。** 判据两侧
   （`el["text"]["fontsize"]` 与 `spec["default_font_size_pt"]`）取自同一份
   文件，把派生换成写死的 `9.0` 也照样绿。处置：用 `TAVOTTO_PROFILES_FILE`
   换一份**改过数字**的规范来量（11.5 / Nimbus Roman / 0.25 / out），
   派生断了当场红。
2. **「错误文案按界面语言渲染」在 zh-CN 下恒等成立。** 透传后端原文与按 code
   翻给出同一句话。处置：切到 en-US 量，并加一条「不含中文」。

### 后端 17 条

| 变异 | 被打红的判据 |
| --- | --- |
| 乐观并发形同虚设（`if False`） | `test_revision_conflict_does_not_silently_overwrite` |
| 坏文件直接删掉而不是挪走 | `test_damaged_store_falls_back_to_builtins_and_keeps_the_bad_file` |
| 更高 schema 的清单也被收容 | `test_a_newer_store_schema_is_left_completely_alone` |
| 清单写回包目录 | `test_the_store_lives_in_the_user_data_dir` |
| 迁移后旧文件留着（两份权威） | `test_migration_is_idempotent` |
| 迁移不备份原件 | `test_legacy_styles_move_into_the_store_and_the_old_slot_is_emptied` |
| 未识别字段被丢掉 | `test_unmapped_fields_survive_the_import_and_are_reported` |
| 导入按名字覆盖既有配置 | `test_export_import_roundtrip_creates_a_new_profile` |
| 复制出来的规范沿用源 id | `test_resolve_spec_finds_both_builtin_and_user_specs` |
| 内置样式写死数字而不是派生 | `test_builtin_style_is_derived_from_the_default_spec` |
| 默认规范退回 8.5 | `test_the_default_spec_carries_exactly_one_minimum_font_size` |
| 求值器里重新写死 8.5 / 8.0 | `test_no_evaluator_or_ui_hardcodes_a_minimum_font_size` |
| 两侧兜底常量分叉（8.0 → 9.0） | `test_font_floor_fallback_is_one_number_on_both_sides` |
| PATCH 不再要求 revision | `test_http_refuses_a_patch_without_a_revision` |
| 两条字号检查合成一条（`elif False`） | `test_the_strict_threshold_and_the_absolute_floor_are_still_two_checks` |
| 绝对下限改成不含等号（`<=` → `<`） | `test_the_default_spec_has_exactly_one_minimum_font_size` |
| `resolve_spec` 靠捕获异常分流（两个成因压成一句话） | `test_a_bad_journal_says_so_instead_of_blaming_the_profile_id` |

### 前端 19 条

| 变异 | 被打红的文件 |
| --- | --- |
| 全局现值压过项目快照 | `specBinding.test.ts` |
| 「有没有新版」改看版本号 | 同上 |
| 绑定只存 id、不存快照 | 同上 |
| 跟随全局的表态被忽略 | 同上 |
| 全局没了还报「有新版」 | 同上 |
| 响应形状不对也照单全收 | `profileStore.test.ts` |
| 后端不在时不退内置 | 同上 |
| 并发撞车时把本地那条换成对方的 | 同上 |
| 错误透传后端中文原文 | 同上 |
| 应用样式时不动背景 | `styleAndSpec.test.ts` |
| 样式计划里丢掉背景 | 同上 |
| 列表把内部 id 摆到正文里 | `profilesSettings.test.tsx` |
| 内置的名字不跟界面语言走 | 同上 |
| 内置也让改（保存按钮可点） | 同上 |
| Style 与 Spec 共用同一组字段 | 同上 |
| 「本项目用这套规范」只写 id | 同上 |
| 跟随开关写成本机偏好而不是文档修改 | 同上 |
| 没绑定这套规范时也显示跟随开关 | 同上 |
| 设置页换一套规范时丢掉跟随的表态 | 同上 |

> **反证顺手抓到一件真事**：「清单写回包目录」那一条跑完之后，
> `src/tavotto/profiles/styles.json` 留在了工作树里。变异本身被打红了，但
> **它写出来的文件不会自己消失**——`git status` 是反证的最后一步，不是可选项。

---

## 评审回合 2（PR #206 / #207 / #208 / #209）：八条 findings 的处置

拆分成四个 stacked PR 之后 Codex 各评了一轮，共 8 条（2 条 P1 + 6 条 P2）。
**8 条全部改掉**，没有一条转 Issue。逐条的判据与变异反证在各自的提交信息里，
这里只记**变异反证的账**——一共 17 条，16 条被打红，1 条查明是语义 no-op。

### #206（05–06）

| 变异 | 结果 | 被打红的判据 |
| --- | --- | --- |
| 去掉 `assetStore` 的 `trailing` 补问 | KILLED ×2 | `assetStore.test.ts`：在途期间来的调用会补问一遍 / 补问本身也要合并 |
| 去掉 `trailing` 的换项目守卫 | KILLED | 同上：补问期间换了项目就不补 |
| watcher 两处遍历去掉 `strict=True` | KILLED ×2 | `test_project_watch.py`：脚本子树 / 素材子树读不动时整张快照作废 |
| 把 `strict` 改成默认打开 | KILLED | 同上：产品视图（`/api/panels`、脚本清单）必须照旧宽容 |

> `take_snapshot` 的那个 `except OSError` **一直都在，只是从来没被执行过**：
> `os.walk` 的默认 `onerror=None` 与 `_iter_py` 的 `except OSError: return`
> 都是静默跳过。判据因此钉在 OS 边界（`Path.iterdir` / `os.scandir` 对一个
> 具体子目录抛 `PermissionError`），不钉在被测函数自己身上；每条先证明
> 「不动任何东西时它是拍得出来的」，再制造故障。

### #207（07–08）

| 变异 | 结果 | 被打红的判据 |
| --- | --- | --- |
| `append` 恒 False | KILLED ×2 | `test_script_probe.py`：接一张图不清空其它 stem / 认领走的 stem 从别人那里摘掉 |
| `cost` 补回 `"medium"` | KILLED | 同上：请求里没提 cost = 保留磁盘上那个值 |
| `append` 恒 True | KILLED | 同上：手工编辑整份清单仍是整条替换 |
| `append` 时不从别的脚本摘 stem | **存活** | —— |

> 存活的那条是**语义 no-op**（`Mutation may not be a mutation`）：这个脚本
> 原先认领的 stem 本来就不可能同时挂在别人名下——重复 stem 会让
> `registry.load` 直接报错，整个项目读不出来。那条保证由「`append` 恒 False」
> 一条已经量到了，不是判据缺了一维。

### #208（09）

| 变异 | 结果 | 被打红的判据 |
| --- | --- | --- |
| 去掉 `useKeyboard` 的两处 `inFastEdit()` 守卫 | KILLED ×2 | `useKeyboardFastEdit.test.tsx`：方向键不动 x/y / 绘制工具快捷键全部无效 |
| 把 `useWorkspaceStore.clear()` 挪回 `switchDocument()` 之前 | KILLED | `projectSwitchWorkspace.test.ts`：切项目不动旧文档那一档 |
| 去掉各向异性位图的守卫 | KILLED | `originalSpec.test.ts`：两轴密度不同时 dpi 保持 `null` |

> 四条 fast-edit 用例各配一个**反向对照**（同一个键在排版模式下必须照常
> 工作）。没有对照的话，「什么都没发生」也可能是判据自己没执行到。

### #209（10）

| 变异 | 结果 | 被打红的判据 |
| --- | --- | --- |
| 去掉 `_write_user()` 的版本守卫 | KILLED | `test_profile_store.py`：更高版本的清单拒绝一切写入 |
| `extra` 桶不认自己（回到旧写法） | KILLED | 同上：`extra` 不许每存一次多包一层 |
| 去掉 `_validate` 的形状判据 | KILLED ×5+ | 同上：嵌套形状坏掉的自建规范被拒 |
| 数字判据改回「必须为正」 | KILLED | 同上（**对照组**）：`absolute_min_font_size_pt: 0.0` 是合法的期刊覆盖 |

> 最后那条对照组是这一轮里最有信息量的一个：形状判据第一版要求那四个数
> **为正**，结果把 golden 向量里一条真实用法（journal 把绝对字号下限覆盖成
> `0`，意思是「不设下限」）判成了非法。**判据窄过它要守的东西同样是缺陷**，
> 只是这次的表现是假红而不是假绿。守的是「形状不对会当场打崩导出对话框」，
> 那就只查形状，不查取值范围。

## Session 11 新增用例（后端 1 / 前端 78）

### 后端

| 文件 | 条数 | 盯的是 |
| --- | ---: | --- |
| `tests/test_preflight.py`（新增 1） | 1 | 导出上下文那条规则在两条入口上是**同一条规则**（同 rule code / 同 message key / 同规范键）；改名当场红 |
| `tests/test_profile_store.py`（改 1） | — | 「求值器与界面不许写字号字面量」的看护范围 **+4 个消费点**（`validation.ts` / `issueFix.ts` / `validationText.ts` / `ProblemPanel.tsx`）——共享判据修一处不算修完 |
| `tests/test_i18n_dead_keys.py`（改 1 + 自检 +2） | — | 匹配器认识 i18next 的**复数后缀**（`fixAll_one` 在源码里永远找不到，后缀是运行时补的）；自检里加了两条：剥离不是万能钥匙、基名有发射点的不许被报成死键 |

### 前端

| 文件 | 条数 | 盯的是 |
| --- | ---: | --- |
| `lib/validation.test.ts` | 21 | 规则目录不漏 code、聚合投影原样留着、逐条命中说自己的数字、gid 查不到时不拿 gid 顶替、一次多个对象逐个入账、画布维度、指纹五维、检查不改文档、导出上下文、摘要透传 `ready`/`failed`、筛选 |
| `lib/validationText.test.ts` | 13 | 主语说人话（元素名压过面板名）、当前值→要求（**「大于」与「≥」不是一句话**）、短标题查不到时不吐 code、gid 只在技术详情里、四个等级各有图标与标签、切语言跟着换 |
| `lib/issueFocus.test.ts` | 14 | 排版模式的五步、图内元素的模式切换、属性字段真的被聚焦（`data-prop`）、跨画布、四种失败各有原因、页面级问题、`openProblems` |
| `lib/issueFix.test.ts` | 12 | **修完真的能过**、按缩放反算、画布标注不乘缩放、枚举类修复、不确定的一律不给计划、`user_choice` 两层各自守住、一个事务 / 一个批事务 / 走 commit、跨画布 |
| `store/validationStore.test.ts` | 12 | 防抖、代次丢弃、按画布增量（**沿用 = 同一个对象引用**）、画布改名跟上、失败不清空、换项目才清空、不写文档、摘要与聚合投影、订阅装卸 |
| `components/left/problemPanel.test.tsx` | 15 | 行里不出现 gid / 对象 id、技术详情默认收起、短标题 + 当前值→要求、空态 / 「查不了」 / 筛选空、等级 chip 的 `aria-pressed`、无障碍名、方向键漫游、**修复不是行的子节点**、修复可撤销、轨道角标、常驻入口、英文界面 |
| `i18n/overflow.test.tsx`（+9 条预算） | 9 | 等级 chip / 行内按钮 / 重试 / 取消筛选 / 轨道名的英文字数上限 |

前端合计 **147 files / 1798 passed**（Session 10 是 138 / 1659）。

## Session 11 的变异反证（44 条，全部被打红）

**先踩了一次「判据自己是空的」**：第一版反证脚本拿 `vitest ... | tail -3` 的
文本找 `failed`，而 vitest 的统计行**不在最后三行里**——于是 44 条全部显示
「存活」。判据没有进控制流（用的是文本而不是退出码），它把一整套好用例报成了
坏用例。改成看退出码 + 先跑一遍基线自检（没有变异时必须绿）之后，第一轮
38/44 被打红。

### 第一轮活下来的六条，四种成因

| 变异 | 为什么没被打红 | 处置 |
| --- | --- | --- |
| `subject` 把 gid 当可读标签（`el?.label ?? occ.gid`） | 样例里的元素**都有 label**，`??` 永远不触发——**语义 no-op** | 补一条真实形状：`tick-label-count` 报的 gid 是**轴前缀**，根本不是一个元素，此时 `elementLabel` 必须是 undefined |
| `summaryFor` 把 `ready` 写死成 true | 用例测的是 `summarizeIssues()`（底层），没有一条经过 `summaryFor`——**判据没落在被改的那条路上** | 加一条直接量 `summaryFor` 的透传 |
| `Sink` 只记第一条命中（`pairs.slice(0,1)`） | 字号那类规则一次只交一个 gid，切片是 no-op | 加一条「两个对象同时越界 = 两条问题」——`out-of-page` 一次交上来一串对象 id |
| 「画布不在了」的守卫改成恒真 | **两道守卫说同一件事**（切之前查成员、切之后查到没到）——冗余的保证杀不死 | **合并成一处**（T-57），不造输入去覆盖它 |
| `planPageWidth` 不要求 choice | 调用方那道闸（`applyIssueFix` 的 `needs_choice`）先拦住了，纯函数自己的契约没被量过 | 加一条直接量 `planFix` 的 |
| `subjectName` 先说面板名 | 样例里**只有** `elementLabel`，优先级换过来照样绿 | 加一条两者都在的样例 |

第二轮：**6/6 全部被打红**。累计 44/44。

四种成因里三种是**老面孔**：语义 no-op（Session 05 撞过）、判据没落在被改的
那条路上（Session 09 两条）、冗余的保证（Session 07 的 T-36、Session 09 一条、
本轮 T-57）。第四种「只测了调用方，没测被调用方的契约」是本轮新增的一种。

### Session 11 的 e2e（真跑，不是 `--list`）

| 命令 | 结果 |
| --- | --- |
| `npx playwright test e2e/a11y.spec.ts --project=chromium` | **8 passed**（新增「问题面板：axe 无违规 + 修复不是定位的子节点」一条） |
| `npx playwright test e2e/asset-library.spec.ts e2e/error-recovery-en.spec.ts e2e/keyboard-golden-path.spec.ts --project=chromium` | **7 passed**（`error-recovery-en` 由 `chromium-en` project 跑，基础 project 显式 testIgnore 它，所以这里是 4+3） |

**a11y 那条第一次跑就红了，而且红得对**：问题面板里「技术详情」的
`<summary>` 用了 `text-ink-faint`（2.54:1，axe serious）。单测里那几条
「有 aria-label / 可键盘到达 / 不嵌套交互」一条都没红——**结构性断言看不见
对比度**，这正是 08/09 两轮只做到 `--list` 时漏掉的那一类。改成 `ink-3` 之后
8/8 绿。

### Session 11 的性能预算

负载 12 画布 × 8 面板 × 60 元素 = **5760 个元素 / 约 5800 条问题**，
本机一遍全量检查 **22ms**，预算定 **300ms**（十几倍余量，CI 更慢 + jsdom 抖动）。

两个坑各踩一次：

1. **第一版量到的是一张画布。** `addCanvas` 建的是空画布，只在激活画布上摆
   对象的话「12 画布」是假的（2.66ms 显得很好看）。用例里现在有一条自检：
   逐张核对每张画布上确实有 8 个面板。
2. **`vi.useFakeTimers()` 默认接管 `performance.now`**，那样 `spent` 恒为 0，
   预算判据什么都量不到。用例里先 `expect(spent).toBeGreaterThan(0)` 证明尺子
   是活的，再谈它落没落在预算里。

---

## Session 12 新增用例（后端 51 / 前端 79）

### 后端 `tests/test_export_request.py`（22 条）

| 组 | 条数 | 钉住的是 |
| --- | ---: | --- |
| golden 向量 | 4 | 向量与本实现一致、**TS 侧也断言了同一份**、每条判据都被碰过、首尾空白不许退回 `str.strip()` |
| 规范化 | 8 | PPI 在无位图时是 `None`、格式顺序稳定（不是点勾选框的先后）、十种坏请求各自的 code |
| 原图 scope | 1 | **载荷里根本没有** x/y/w/h 与页面尺寸（混进来的键被丢掉） |
| 旧契约 | 3 | `stem`+`items[]`+`texts[]` 抬成同一个作业、时间戳、`overwrite: true` |
| 命名 | 6 | 扩展名一次只剥一层、`v1.2` 不许被当扩展名、去重编号 |

### 后端 `tests/test_export_pipeline.py`（29 条）

| 组 | 条数 | 钉住的是 |
| --- | ---: | --- |
| 原图不套用画布缩放 | 4 | 面板摆成 40×30 而源是 200×150 pt → 出来必须是 200×150；矢量整页搬运后**文字仍可提取**、零位图；位图源**保源像素网格**；矢量源按 ppi 栅格化（判据是"换个 ppi 按比例变"，不是等于某个数） |
| 画布忠实布局 | 1 | 页面就是 180×120 mm |
| 多格式同一快照 | 2 | PNG 的像素数 = PDF 的 pt × ppi/72；`document_revision` 原样回传 |
| 覆盖策略 | 3 | `ask` 撞名时**不渲染不写盘**且原文件字节不变、`replace` 报 `replaced`、`rename` 编号 |
| 原子性 | 3 | 一个格式挂了另一个照常交付且**目录里没有别的东西**、publish 失败**不留半文件**、遗留临时目录被扫掉而用户的文件不碰 |
| 取消 | 3 | 取消后零产出 + 临时目录不留、取消不存在的作业不是错误、异步作业到达终局 |
| 样式检查报告 | 3 | 服务端事实（版本/时间/产物）在、**报告失败不牵连成图**、不开就不写 |
| 透明背景 | 1 | **角上那个像素的 alpha 为 0**（不是"有没有 alpha 通道"） |
| 预校验与错误 | 4 | conflicts 不写盘、坏文件名回结构化 error、坏请求**根本不建作业**、源没了是结构化错误 |
| 旧契约 | 2 | `files[]`/`export_dir`/`warnings` 形状 + `_proof.json`；`ppi` 与 `dpi` 是同一个设置 |
| code 注册 | 1 | 抛得出的 code 全在 `ERROR_CODES` 里 |
| 多项目 | 1 | 为项目 B 起的后台作业**出来的是 B 的那张图**（同名不同尺寸） |

### 前端

| 文件 | 条数 | 钉住的是 |
| --- | ---: | --- |
| `lib/exportName.golden.test.ts` | 62 | 与 Python 侧**逐条**一致（37 条 check + 10 条 strip + 2 条 outputName + 5 条 dedupe + 覆盖面自检） |
| `lib/exportRequest.test.ts` | 10 | scope 默认值、`original` 段无布局字段且**尺寸取自 spec 而不是落位**、隐藏对象不发、可用性回**原因**、PPI 语义、扩展名剥离、指纹量的是载荷 |
| `store/exportStore.test.ts` | 7 | 坏文件名不发请求、终局不轮询、**晚到的旧快照挡掉**、「期间被编辑」用此刻的文档、`prepareExport` 不发网络 |
| `components/ExportDialog.test.tsx` | 17 | 文件名在最上方、**§五删除清单逐条不在**、高级选项默认收起、scope 三态、确认→报告、统一载荷、PPI 只在位图时出现、文件名就地校验、冲突两条出路、检查只给摘要 |

---

## Session 12 的变异反证（23 条，全部被打红）

判据用**退出码**，跑之前先做一次基线自检（没有变异时必须全绿）。
`PYTHONDONTWRITEBYTECODE=1`——同尺寸 + 同一秒写入会命中旧 `.pyc`，那种假绿
会让人去"加强"一条本来就好的判据。

### 后端 13 条

原图导出改成套用画布缩放 / 只出 PDF 时 ppi 也回默认值 / `ask` 不再检查重名 /
临时目录不清理 / 部分失败报成全部成功 / 透明背景照样画白底 / 位图原图按 ppi
重采样 / 旧契约丢掉时间戳 / 首尾空白退回 `str.strip()` / `dot_only` 排到
`trailing_dot` 后面 / 取消位不检查 / 后台线程不绑定项目 / 报告失败连累成图。

### 前端 10 条

PPI 恒有值 / 原图尺寸改用画布落位 / 快照指纹恒等 / 「期间被编辑」拿冻住的
文档比 / 晚到的旧快照不再被挡 / 扩展名不剥 / 原图不可用时不说原因 /
可用性不分原因 / 分辨率行不随格式出现消失 / 确认之后不强制生成报告。

### 第一轮活下来的三条，三种成因

**1. 「透明背景照样画白底」活了 —— 判据的维度错了。**
用例断言的是 `pix.alpha == 1`（这张 PNG 有没有 alpha 通道），而变异改的是
"底下有没有铺一层白"。带 alpha 通道 + 铺了白底，`pix.alpha` 照样是 1。
主语对了（就是那张 PNG），维度错了。改成量**右下角那个像素的 alpha 值**。

**2. 「后台线程不绑定项目」活了 —— 判据看不见那个维度。**
所有用例都只开一个项目，于是"落到默认项目"与"落到正确的项目"是同一个答案。
补了一条两项目用例：两个项目里都有 `p1.pdf`，尺寸不同；判据是**出来的是哪
张图**，不是"有没有报错"（少了绑定的话作业照样成功）。

**3. 「原图尺寸改用画布落位」活了 —— 夹具让判据恒真。**
前端用例里，面板在画布上的 `w/h`（80×60）恰好等于它自己的图幅（80×60），
两个数字相等，"用的是哪一个"这个问题量不出来。改成 40×30 之后才红。
这是本轨道第 N 次撞见「夹具让判据恒真」，也是变异存活的第六种成因。

### 顺带修掉的一处空门禁（e2e）

`keyboard-golden-path.spec.ts` 原来用 `dialog.getByText(/\.pdf/)` 判"导出完成
了"。新界面在文件名下方**摆了一行文件名预览**（`Fig 1.pdf`），于是那条判据
在按导出**之前**就成立——不按也绿。改成断言结果区的「已保存到」+ 一个真正
指向 `/exports/` 的链接。

---

## 评审回合 3（PR #214）：六条 findings 的处置

Codex 评审报了 **3 P1 + 3 P2**，**六条全部成立、全部改**，无一转 Issue。

| # | 级 | 现象 | 处置 |
| --- | --- | --- | --- |
| 1 | P1 | `pdfbackend.original_pdf()` 写死 96 dpi：一张带 300 dpi 元数据的图被摆进大三倍多的 PDF 页面，而界面显示的尺寸来自 `OriginalOutputSpec`——文件与界面各说各的 | 页面尺寸改成**参数**；`app.py` 优先用请求里已解析的 `w_mm/h_mm`，缺席时只从 `engine/originalspec` 现算。**`pdfbackend` 从此不认识任何密度常量**；顺带删掉没有调用点、又用同一个错常量的 `original_png(native_grid=False)` 分支 |
| 2 | P1 | 样式检查报告不进覆盖策略：只有旧报告在时 `ask` 静默盖掉它，`rename` 给图编号却仍然覆盖报告 | 报告进 `_plan_names()`，与图共用同一套命名与去重；`app` 侧的生成器**只回字节**，名字由作业决定 |
| 3 | P1 | 并发的两个 `ask` 都能通过存在性检查，渲染完两边都 `os.replace`——后完成的静默盖掉先完成的，两边用户都看到"导出成功" | 名字在渲染**之前**一次决定完并预留（`_RESERVED`）；检查与预留**一次持锁**完成（分两次 = 中间还留着同一个窗口） |
| 4 | P2 | `unknown` 不在终局集合里：后端重启后轮询每 600ms 问一个不存在的作业，对话框永远停在"进行中" | 补进 `TERMINAL`；界面单独一档（**不是 failed**——我们不知道文件写出来没有） |
| 5 | P2 | 用 `spec.stale` 判原图能不能导，而它答的是另一个问题；刚渲染过的图 `stale=false` 而磁盘文件可能早没了 → 一个按下去必然失败的按钮 | 判据换成"素材清单里还有没有它"（runtime 单独放行），与后端 `_resolve_panel_source()` 的前提逐条对应（T-65） |
| 6 | P2 | `resetExportState()` 只有用例在调；切项目后旧结果留着，`/exports/…` 被补上**新**项目的 pj | 接进 `resetForNewProject()`；**只清前端状态，不取消后端作业** |

### 新增用例（后端 4 / 前端 4）

| 用例 | 钉住的是 |
| --- | --- |
| `test_raster_original_pdf_honours_the_resolved_physical_size` | 请求里写着 10.16mm（300 dpi）时页面就是 10.16mm；顺带断言 96 dpi 那个错答案能被分辨出来 |
| `test_raster_page_size_follows_the_file_not_a_constant` | **两侧独立**：两张只有 pHYs 不同的同尺寸 PNG（96 / 无 pHYs→assumed 600）出来的页面必须不同。写死任何常量都会让它们一样大 |
| `test_the_style_check_report_obeys_the_overwrite_policy` | `ask` 撞到"只有报告在"也停下来、`rename` 给报告编号、原报告不被动 |
| `test_two_concurrent_asks_do_not_silently_clobber_each_other` | A 还在渲染时 B 报 conflict；A 结束后预留释放；**别的名字不被上一次的预留挡住** |
| `exportRequest.test.ts` ×2 | stale 源不可用 + **判据是"够不够得着"而不是 `spec.stale`**（刚渲染过的图 `stale=false` 仍然不可用） |
| `exportStore.test.ts` | `unknown` 是终局，`running` 落回 false |
| `ExportDialog.test.tsx` | 源文件不见了时说的是"源文件现在找不到了"，**不是**"先选中一张图" |
| `projectSwitchWorkspace.test.ts` | 切项目把导出结果丢掉 |

### 变异反证（后端 17 / 前端 14，全部被打红）

评审那六条各配一条变异：位图 PDF 退回写死 96 dpi / 报告不进覆盖策略 /
报告自己拼名字 / 并发 ask 不看预留表 / `unknown` 不当终局 / 判据退回
`spec.stale` / 三个不可用原因折成两句 / 切项目不清导出状态。

**第一轮三条锚点找不到**——不是判据坏了，是重构把那几行挪了位置
（`_final_names` → `_plan_names`、`made` → `data`、if 条件挪进函数参数）。
改锚点后 17/17 + 14/14 全红。**「锚点找不到」与「存活」必须分开报**：
合成一档的话，一次重构就能把整套变异悄悄变成空转。

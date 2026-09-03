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

---

## 复审回合（PR #214，`0c92c5a`）：六条 findings 的处置

Codex 复审在改完的那版上又报 **1 P1 + 5 P2**，**六条全部成立、全部改**。

| # | 级 | 现象 | 处置 |
| --- | --- | --- | --- |
| 1 | P1 | JPEG 源被**逐字节复制**进一个叫 `.png` 的文件：签名是 JPEG、扩展名与 MIME 是 PNG，严格的读取端判它损坏 | `.png` 源继续逐字节复制（连 pHYs 一起留住），其余容器**转码**——只换容器，像素数一个不变 |
| 2 | P2 | `produce()` 之后的落盘循环不问取消：`cancel()` 回 `cancelling: true`，作业照常报 `done`；第一个 `os.replace` 之后也不再有"最终目录一个字节没动过" | 立**提交点**：落盘前最后问一次取消，`_committed` 置上之后 `cancel()` 如实回 `False`。**别许一个做不到的承诺** |
| 3 | P2 | `originalAvailability` 的 memo 只挂 `figureId`，而它读的是 store 快照：对话框开着时素材被删，组件重渲染了而 memo 还是旧值，按钮继续亮着 | 依赖补上 `assets` / `runtimeAssets`（`panel` 那个 memo 同族，补 `doc.objects`）。依赖是**触发重算的信号**，注释说明了这一点 |
| 4 | P2 | 像素预览在原图范围下仍按画布页面尺寸算：70.6mm 的图摆在 180mm 画布上、600ppi 显示 4252px，而真实产物约 1668px | 抽出纯函数 `pixelPreview(scope, ppi, page, spec)`：画布按页面、原图矢量按图幅、**原图位图直接报源像素网格**（与 ppi 无关）、规格没解析出来回空串 |
| 5 | P2 | `_sweep()` 按 `max(created_at, finished_at)` 判过期，而在跑的作业 `finished_at` 是 0：跑超 15 分钟的作业被删掉临时目录并移出表，客户端拿到 `unknown` 而生产者还在往不存在的目录里写 | TTL **只清进过终局的**作业 |
| 6 | P2 | 原图 + 矢量源 + PNG 时 `background=transparent` 收不到，永远 `alpha=False`——界面上那个开关**说了而不做** | background 传进 `original_png()`；原图 + 位图源那条路上透明本来就没有意义（照抄源文件），界面把开关**禁用并说出原因** |

### 新增用例（后端 5 / 前端 2）

`test_a_jpeg_source_is_transcoded_not_copied_into_a_png_name`（断言产物真的是
PNG 签名 + 像素数照抄源）、`test_a_png_source_is_copied_byte_for_byte`、
`test_transparent_background_reaches_the_original_vector_path`、
`test_a_running_job_is_never_swept_by_the_ttl`（把作业伪装成很久以前建的，
再触发一次 `_sweep`）、`test_cancel_is_refused_once_publication_has_begun`
（连提交点本身一起钉：把状态改回 running，`cancel()` 仍须回 `False`）；
前端 `pixelPreview` 四档 + 「对话框开着时素材没了，按钮当场灰掉」。

### 变异反证扩到 后端 21 / 前端 16，全部被打红

复审那六条各配一条变异。这一轮又有一条报「锚点找不到」——`original_png` 的
位图分支被拆成了 PNG / 非 PNG 两支，锚点跟着变。**同一个教训第二次出现**：
锚点是变异脚本自己的判据，它也会随重构失效，所以「锚点找不到」必须与「存活」
分开报。

### 顺带清掉的 lint 噪音

`pixelPreview` 挪进 `lib/exportRequest.ts`（纯计算不该从组件文件导出，会打断
fast refresh）；两个 memo 的依赖加了带理由的 `exhaustive-deps` 豁免——那几个
依赖是**触发重算的信号**而不是入参，linter 看不见函数体里对 store 的读取。
前端 lint 回到既有的 19 条 fast-refresh 提示，**无新增**。

---

## 第三轮评审（PR #214，`8c1f7d4`）：四条 findings 的处置

| # | 级 | 现象 | 处置 |
| --- | --- | --- | --- |
| 1 | P1 | 勾了确认之后 `confirmed` 一直是 true：改格式/PPI、文档被编辑、或者导出过一次，**新出现的阻断项不经确认就被导出**，而 `start()` 还把它们的规则码写进报告，写成一句"用户知悉过" | 用**要确认的那批问题的 issueId + 「这次没查成」**拼指纹，指纹一变就撤销确认；每次导出尝试之后也撤销 |
| 2 | P2 | 同一个属性上的多条修复计划挨个写，后写的赢：默认规范一条 6pt 图例文字同时命中 `font-below-absolute-floor`（8.5）与 `legend-font-size`（8.0），8.0 盖掉 8.5 而 8.0 仍过不了绝对下限（`eff <= floor`） | `FixPlan` 带上**这条规则能接受的区间**，批量落地前按 (对象, gid, 属性) 分组取**交集**再夹值——由构造保证同时满足每条。给不出区间或交集为空时**整组不修**并如实计入 `skipped` |
| 3 | P2 | 提交点只有一个布尔：`cancel()` 读到 `False` 之后、`_cancel.set()` 之前，执行线程可能刚好置上 `_committed` —— `cancel()` 回 `True` 而作业照常 `done`，正是提交点想消掉的行为 | 「检查 + 改状态」两边共用 `_commit_lock`，原子完成 |
| 4 | P2 | `summary.failed` 单独触发确认时，报告里 `forced: false`、`acknowledged: []`、`checks: []` —— 与"干干净净跑过一遍"**分不出来**，而确认框上写着这次确认会被记下 | 报告加 `check_failed` / `acknowledged_check_failed` 两个独立字段（T-54 的同一条：「查不了」与「没问题」不许压扁） |

### 新增用例（后端 2 / 前端 8）

`test_cancel_and_the_commit_point_are_serialized`（判据钉在**锁本身**上：
攥着锁时 `cancel()` 必须阻塞，而不是抢先读到一个陈旧的 `False`）、
`test_a_failed_check_is_recorded_in_the_report`（两种情形的报告必须不相等）；
前端新文件 `store/issueFixActions.test.ts`（6 条：交集、降字号方向、矛盾整组
不修、给不出区间不合并、不同属性各走各的、页宽原样通过）+ 对话框 2 条
（问题集合变了撤销确认、导出之后撤销确认）。

**「问题集合变了」那条用例的刺激换过一次**：第一版用"取消勾选 PNG"，而这个
夹具里根本没有导出上下文的问题——问题集合压根没变，用例是空的。改成往文档
里加一段 5pt 文字并显式跑一遍检查，并**先断言界面真的变了**再断言那个勾掉了。

### 变异反证：后端 23 / 前端 20，全部被打红

### 顺带修掉变异脚本自己的一处空转

前两轮各出现过一次「锚点找不到」，我在 `TEST_MATRIX` 里写下了"必须与存活
分开报"，**却没有改脚本**——它的汇总行仍然把两者塞进同一个"存活"清单。
这一轮又撞上一次，才发现结论只写进了文档。现在两者分列，且**有锚点找不到
时脚本以非零码退出**（变异根本没打进去 = 那一条不算跑过）。

> 教训本身不新：**把纪律写进文档而不写进工具，下一次照样会踩。**
> 与「把纪律做进结构」同一条。

---

## 第四轮评审（PR #214，`07fc7c2`）：四条 findings 的处置

**其中一条 P1 是上一轮我自己修出来的**——「每次导出尝试之后撤销确认」那条改动
制造了它。

| # | 级 | 现象 | 处置 |
| --- | --- | --- | --- |
| 1 | P1 | 撞名之后「覆盖 / 另存一份 / 重试」**直接调 `start()`**，绕过主按钮的 `disabled`。上一次已确认的导出撞名之后，点「覆盖」会把同一批阻断项不经确认再导一次，且 `acknowledged` 是空的（确认刚被清掉）、报告也不再被强制生成 | ① 闸放进 `start()` 这个**唯一咽喉**（逐颗按钮加 `disabled` 是治标，下一颗新按钮照样漏）；② 撞名那一次**什么都没写**，界面问的是同一次导出的另一个问题，所以**不清确认** |
| 2 | P1 | 带 override 的位图面板经 `_resolve_panel_source()` 会被 worker 重画成 **PDF**，走矢量那条路按 ppi 栅格化；而界面仍报 `sourceKind: 'raster'` 的源像素网格、还说 PPI 无关 | 「照抄源位图」的判据加上 `!overrides.length`——**有 override = 引擎重画**，ppi 重新有意义。`pixelPreview` 收一个显式的 `copiesVerbatim` |
| 3 | P2 | `export.progress` 是**项目级广播**：本标签页空闲或上一个作业终局时，会收下另一个标签页的快照；`resetExportState()` 之后一个在飞的轮询也能把状态填回去 | 加 `ownedJobId`：**只收自己认领过的那个作业**的快照；`resetExportState()` 一并清掉归属（只停轮询停不掉已经发出去的请求） |
| 4 | P2 | 请求 `include_style_check_report: true` 却没带载荷时，报告生成**整段跳过**，作业仍报 `done`，进度永远差一格 | 记一条失败的报告产出（`report_missing_payload`），作业进 `partial` |

### 新增用例（后端 2 / 前端 5）

`test_a_requested_report_with_no_payload_fails_loudly`（连**进度补齐**一起断言）、
`test_a_raster_panel_with_overrides_is_re_rendered_not_copied`；前端：撞名之后
问题集合变了「覆盖」也被闸挡住、带 override 的位图不报源像素网格（**A/B 两个
夹具**：同一张图有/无 override 各渲一次）、只收自己那个作业的快照 ×2、
`pixelPreview` 的 `copiesVerbatim` 两支。

### 变异反证：后端 24 / 前端 24，全部被打红

**第一轮三条没过**，而且这次三种成因分得清清楚楚（脚本上一轮刚改成分开报）：

| 条目 | 分类 | 成因 |
| --- | --- | --- |
| `start()` 里没有阻断闸 | **存活** | 用例点的是主按钮，而它本来就 `disabled` ——点了等于没点，闸有没有都绿。改成走「撞名 → 问题集合变化 → 点覆盖」这条真的绕过 `disabled` 的路 |
| 带 override 的位图仍报源像素网格 | **存活** | 只测了纯函数 `pixelPreview` 的两个分支，没测**对话框算不算得对那个标志**。补了 A/B 夹具 |
| 导出之后不撤销确认 | **锚点找不到** | 这一轮把那行改成了 `if (job?.status !== 'conflict') setConfirmed(false)`，锚点跟着变 |

前两条都是**判据没打在真正会出事的那条路上**：一个点了一颗按不动的按钮，
一个测了纯函数却漏了调用方。

---

## 第五轮评审（PR #214，`c8479335`）：三条 findings 的处置

**用户口径：P1 必修；P2 视情况修或转 Issue。** 这一轮三条**全修**——两条 P2
一条是我上一轮那个"咽喉闸"漏了一个条件（不修等于那条修复只做了一半），一条与
刚修的归属判据同族，改动都在十行以内，比开 Issue 便宜。

| # | 级 | 现象 | 处置 |
| --- | --- | --- | --- |
| 1 | **P1** | 重渲染出来的中间 PDF 落在 `worker.export_dir/<stem>.pdf` —— 一个**按图名共享**的路径。worker 调用串行，但锁在返回时就放开，而调用方还要接着打开/栅格化它：两次导出打同一张图时，后一次会在前一次读它的过程中覆盖它，第一次**静默拿到别人那套 override 的图** | 中间 PDF 落进**这次作业私有**的临时目录（`_panel_render_target()`）。名字再加一层随机后缀——同一次合成里同一张图也可能出现两次（放了两份、各带一套 override） |
| 2 | P2 | `ownedJobId` 挡不住**一个还在 await 里的 `/api/export/start`**：换项目后 `resetExportState()` 清了归属，而那个 continuation 回来会无条件重新认领 | 加代次 `generation`：在 await **之前**取，回来一比就知道自己作废没有 |
| 3 | P2 | 咽喉闸的条件比主按钮的 `disabled` **少一条**（漏了「原图可不可用」），于是「覆盖 / 另存 / 重试」能起一个界面刚说不可用的导出 | 两边收敛成**同一份** `canStart`——一份判断、两个消费点，就没有"少写一条"这回事了 |

### 新增用例（后端 1 / 前端 2）

`test_two_concurrent_override_renders_do_not_share_one_intermediate_file`：
判据是**两次的中间路径互不相同**（共享路径下这件事不报错，它只是悄悄给错图），
并断言中间产物落在作业私有目录里、作业结束后一个都不留；前端两条分别钉住
「start 回执作废后一个字都不写」与「原图不可用时『覆盖』也发不出请求」。

### 变异反证：后端 25 / 前端 26，全部被打红

又一条「锚点找不到」（第四轮那条闸的写法被这一轮的 `canStart` 收敛改掉了）。
**这已经是第四次**：只要重构碰到被变异盯着的那几行，锚点就会失效——所以
脚本在有锚点找不到时非零退出这件事，是这套反证能一直算数的前提。

---

## 第六轮评审（PR #214，`13ec3b69`）：四条 findings 的处置

**0 P1，4 条 P2 —— 全修。** 四条都在 20 行以内、都落在本 PR 已经动过的文件里，
其中一条还是**本 PR 自己声明的不变式被违反**（T-54）。修比开 Issue 便宜。

| # | 现象 | 处置 |
| --- | --- | --- |
| 1 | 文件名进 URL 不转义：`Fig#1` 是**合法名字**，但 `/exports/Fig#1.pdf` 会被当成"路径 `/exports/Fig` + 锚点"，`Fig%20` 会被解码成另一个路径 | `_export_url()` 一处转义（`safe=""` 连 `/` 一起），图与报告共用 |
| 2 | 取名与预留分成两次持锁：两个 `rename` 作业各自取名时那个空名字都还在，先占住的赢，**后一个被报成 `conflict`——而它请求的明明是"另存一份"** | 合并成 `_plan_and_claim()`，取名 / 查撞 / 预留三件事一次持锁 |
| 3 | 临时目录**先创建后登记**：那一瞬里另一次导出的清扫会在磁盘上看见它、在存活集合里找不到它，于是当垃圾删掉——而本作业正要往里写 | 先登记再创建，创建失败再摘掉 |
| 4 | `resetValidation()` 与那一轮真正开跑之间有 250ms 防抖窗口，那段时间 `ready=false, running=false, issues=[]` → **掉进绿色的「未发现问题」**。换文档时问题面板开着就会看到 | 判据从 `!ready && running` 改成 `!ready`（T-54 的同一条：「还没查」不许压扁成「没问题」） |

### 新增用例（后端 4 / 前端 2）

- `test_export_urls_are_url_encoded`：先断言 `Fig#1` **本来就合法**（不然这条
  用例是在测一个不存在的场景），再断言 URL 里没有裸 `#`、报告那条也转义了、
  链接真的取得回文件；
- `test_two_concurrent_renames_each_get_their_own_number`：A 卡在渲染里时
  B 必须拿到 `Fig 1 (2).pdf` 而不是 `conflict`；
- **`test_naming_happens_inside_the_reservation_lock`**：量的是**同步性质本身**
  （`_LOCK.locked()`），不是某一次时序——那种交错靠 sleep 去撞的话，红不红
  取决于机器；
- `test_a_live_temp_dir_is_never_swept_by_another_job`：在 `mkdir()` 返回的
  那一瞬**当场跑一次清扫**，断言目录还在；
- 前端两条：防抖窗口里说「正在检查」而不是「未发现问题」，查完了才说没问题。

### 变异反证：后端 28 / 前端 27，全部被打红

**第一轮一条存活、两条锚点找不到。** 存活的那条（「取名与预留分开持锁」）
成因值得记：我原本的行为用例**盖不住它**——A 持着预留，B 无论在锁里还是锁外
取名都会看到那个预留，两种实现下结果一样。真正区分它们的是**取名那一刻锁在
不在手上**，所以判据换成了直接量同步性质。

> **判据要落在被修的那个性质上。** 行为用例能覆盖大多数情形，但当修复改的是
> 「两件事原不原子」时，只有量那件事本身才分得出来。

---

## 第七轮评审（PR #214，`4f5f1855`）：三条 findings 的处置

**0 P1，3 条 P2 —— 全修。** 三条都在 20 行以内、都落在本 PR 已经动过的文件里。
其中第一条虽然挂着 P2，**后果是"新导出永远停在进行中"**——按后果处置，不按
标签处置。

| # | 现象 | 处置 |
| --- | --- | --- |
| 1 | 旧作业一个**已经发出去**的 `/state` 迟到回来，`applyExportJob()` 按归属挡掉了快照，但紧接着那句无条件 `schedulePoll(旧作业)` 先 `stopPolling()` 掐掉新作业的定时器，再去轮询旧作业。SSE 不通时轮询是唯一通道 → **新导出完成了，界面永远停在"进行中"** | 排这一轮时取代次 + 作业归属，两条收场路径（`.then` / `.catch`）各问一次再决定续不续 |
| 2 | 在对话框里挑一套出版规范 → `commit()` 新的 `d.profile` → 它在初始化 effect 的依赖里 → 文件名被冲回 `doc.name`、确认态被清、范围改回默认。**"先起名再挑规范"这条正常顺序会静悄悄丢掉用户填的东西** | 依赖收成 `[open, documentId]`；`doc.name` 一并去掉（改文档名不该冲掉导出名） |
| 3 | 这一轮查砸、上一轮结果留着时，顶层 `failed` 分支把整份清单换成错误空态。那些问题仍进计数条与导出摘要，却在**唯一一份完整清单**里翻不到 | 失败横幅与保留的清单同时在场；上一轮真没有结果时仍只出错误空态 |

### 新增用例（前端 5）

- `陈旧的轮询不许改排当下的轮询` **两条**：jA 的 `/state` 卡在网络上 → 用户起
  jB → 让 jA 分别以**回来了**和**报错了**两种方式收场，都断言下一次轮询问的是
  `jB`、且 `running` 已经落回 false；
- `挑一套规范之后，用户敲进去的导出名还在`；
- `换文档仍然重置`——**修"别重跑"的同时把另一侧钉住**，否则下一轮会收到反向
  的评审意见；
- `失败提示与那份留下来的清单同时在场` + `上一轮什么都没有时仍然只出错误空态`。

### 变异反证：5 条，第一轮 4 红 1 存活

存活的是 **M2「`.catch` 上的无条件重排」**。成因不是判据弱，是**判据只钉了一条
边**：我按 `.then`（回执迟到）写了用例，而 `.catch`（请求失败）是同一个缺陷的
另一条出口。补上第二条用例后 5/5 全红。

> 评审原文其实点了名——"including the rejection path"。**照着修一半**比没看见
> 更危险：变异反证不跑的话，那一半会带着"已修复"的标签留在代码里。

### 顺带查出来的一条陈旧断言（e2e，CI 从没跑过）

合并 main 之后在本机跑**完整** Playwright（115 条）时红了一条：
`error-recovery-en.spec.ts:167 导出目录不可写`，等的是
`getByText(/Operation failed/i)`。

**不是回归——是产品变好了而用例没跟上。** 页面快照里对话框给的是
`Couldn't create a temporary file in the export folder: [Errno 13] …`：
统一导出管线现在把失败带**具体错误码**回来（`errors:backend.tmp_dir_failed`），
`Operation failed` 只是查不到译文时的兜底。用例钉的正是那句兜底，于是
「说得出具体原因」这件好事把它打红了。

断言改成「有一句英文报错（具体的或兜底的都算）+ 有一条恢复出口
（`Try again`）」——那才是这条用例的本意。

> **这条用例带 `test.skip(win32)`，在 CI 里从来没跑过（issue #30）。**
> 断言陈旧了没有任何门禁会说话；它是在本机全量跑的时候才现形的。
> 这就是 #30 那个覆盖缺口的实际代价——**不是"少测了一点"，是"改坏了不响"**。

### 在 worktree 里跑 e2e 的两个前置（踩过才知道）

第一次跑全灭（115 条几乎全在 ~120ms 内失败），报错是
`spawn <worktree>/.venv/bin/python ENOENT`：

1. **`TAVOTTO_PYTHON=<主工作区>/.venv/bin/python`** —— `e2e/fixtures.ts` 默认
   在**当前仓库根**找 `.venv`，而 worktree 里没有；
2. **先跑 `python scripts/build_frontend.py`** —— 包内 `src/tavotto/web/`
   优先于 `web/dist`，只跑过 `pnpm build` 的话**测的是上一次的界面**
   （本轮三处前端修复会整个测不到，而且一路绿）。

第 2 条尤其阴：它不会报错，只会让你拿到一份**测了旧代码的绿**。

---

## 合并队列的 Windows 腿：两个真缺陷

第七轮之后 PR 全绿、进了合并队列，**被踢出来两次**。两次都不是"CI 抖动"，
是两个真缺陷——而且**都只在 Windows 那条腿上现形**。

### 1. `os.fsync()` 对只读 fd 在 Windows 上回 EBADF（我引入的）

`backend-platforms (windows-latest)` **70 条失败**，`windows-exe-smoke` 里
打包版 `POST /api/export` 直接 500。

70 条里**没有一条是独立成因**，全带同一句
`write_failed: 临时文件落盘失败：[Errno 9] Bad file descriptor`。
`engine/atomicio.publish_file()` 里：

```python
fd = os.open(tmp, os.O_RDONLY)
os.fsync(fd)
```

Windows 上 `os.fsync()` 走 MSVCRT 的 `_commit()`，**它只接受可写句柄**，
对 `O_RDONLY` 的 fd 直接回 EBADF。改成 `open(tmp, "rb+")`。

> **同一个模块里，一条路对一条路错。** `write_bytes()` 从来没撞上它，因为那边
> fsync 的是 `open(tmp,"wb")` 的可写 fd。新加的那条路照着"先 fsync 再 replace"
> 的序列写，却把"用什么句柄"这一维漏了——**序列对了不等于每一步的前提都对**。

后果不是"测试红"：**Windows 上每一次导出都失败**，打包版也一样。

回归用例两条，按 `tests/test_windows_regressions.py` 的一贯做法把 Windows 语义
搬到本机（只读 fd → EBADF、`os.open` 打开目录直接失败），**外加断言 fsync 真的
发生过**——把这一步整段删掉也能让"没报错"通过，而那样掉电会留下空文件。
另一条钉反方向：真正的 EIO 仍要响亮失败，不留产物、不留半个临时文件。

### 2. pair / rect 属性的数字框没有可访问名（main 上原有的）

修完 1 之后 70 条全消失，剩下**一条**——是那两条新用例里的一条自己红了：
`ModuleNotFoundError: No module named 'fcntl'`。它是"在 POSIX 上模拟 Windows
语义"的用例，而 Windows 上没有 `fcntl`。加 `skipif(os.name == "nt")`：
真 Windows 上这条性质由 `os.fsync()` 本身兑现，整套件走的就是真的那条路。

同一轮 `windows-exe-smoke` 的 Playwright 又红一条：
`[webkit] 问题面板：axe 无违规`，违规是 **critical 的 `label`**，落在
`input[value="80"]` / `input[value="57.6"]` 上。

那不是问题面板——**axe 扫的是整页**，违规节点在右栏：快速编辑下的「图幅」。
`ElementInspector` 的 `pair`/`rect` 通用控件渲染 `NumberField` 时
`prefix`/`title`/`ariaLabel` 一个都没给，于是 `derivedLabel` 是 undefined。

**这是 main 上就有的缺陷**，本 PR 一行没动过那个文件；是 11 阶段新加的那条
a11y 用例把它照出来的，而且只在**恰好停在快速编辑**的那一次。

> 把 critical 加进 `allow` 列表就是亲手挖一个空门禁。修它。

每一格的语义**随属性变**（`size_mm` 宽/高、`xlim`/`ylim` 最小/最大、
`position` x/y/宽/高），不能用统一的序号名糊过去；表里没有的属性退回
「第 N 项」——**有个不精确的名字也好过没有名字**，而且这种退化是听得见的。

单测钉在控件层而不是只靠那条 e2e：**e2e 要恰好走到那一屏才量得到**，
而缺陷属于控件本身，任何 pair/rect 属性都带着它。四条用例，其中两条是不变式
（figure / axes 两个角色各一条：属性栏里没有一个数字框是无名的），另有一条
单独断言**两个名字必须不同**——都挂同一个「图幅」也能骗过"非空"。
变异反证 3/3 全红。

### 为什么本机、ubuntu、macOS 全绿

| 缺陷 | 在别处为什么不响 |
| --- | --- |
| fsync 只读 fd | POSIX 上只读 fd **照样 fsync 得了**。这不是"没测到"，是那条语义在别的平台上不存在 |
| 数字框无名 | axe 是同一份 JS，判据一样——**差的是 DOM**：chromium 那次没停在快速编辑，右栏根本没有那两个框 |

> 第二条值得单独记：**同一个判据在两个浏览器上给出不同结论时，先怀疑输入不同，
> 而不是判据不稳。** 这里"输入"是那一屏的 DOM，而它取决于测试走到哪一步。

### `full-ci` 标签：别拿合并队列当探测器

`backend-platforms` / `windows-exe-smoke` / `package` 在 PR 上默认 skipping，
只在 `merge_group` 跑。第一次被踢出来之后改成给 PR 打 **`full-ci`** 标签
（`.github/workflows/ci.yml` 里那条 `contains(labels.*.name, 'full-ci')`），
两条腿直接在 PR 上跑——第二个缺陷就是这样在 PR 上抓到的，没有再占一次队列。

> 打标签会**当场触发一次新 run 并取消旧的**。被取消的 run 会把 gate 留成
> failure 形状（7–9 秒的"红"），那是假红——判之前先看 `conclusion` 是不是
> `cancelled`。

---

## 记错对象的四次（同一晚，同一形状）

判据落在错的东西上，四次都伪装成"结果"：

| # | 判据 | 量到的其实是 |
| --- | --- | --- |
| 1 | `PYTHONDONTWRITEBYTECODE=1` 下跑全量 | 那条用例断言的正是"桥不给解释器加任何标志"，**环境变量漏进了子进程**——假红 |
| 2 | `(cmd \| tail -N); EXIT=$?` | `tail` 的退出码，恒 0。e2e 明明红着，脚本报绿 |
| 3 | `gh pr checks \| awk '$2=="pending"'` | job 名字里带空格，`$2` 切的是名字的一截，不是状态 |
| 4 | `gh run list --limit 3 -q '.[0]'` | 最新的那个 run **不一定是 CI**（是 `PR conflict domains`，只有一个 job，当然 success） |

> 四条的共同点：**判据本身跑通了、也给出了一个看起来合理的值**。
> 出错的是"这个值回答的是哪个问题"。参见 [[name-the-subject-of-the-predicate]]。

---

## Session 13：属性能力层 / Typography 控件 / 标注字体

### 新增用例（后端 15 / 前端 34）

**后端 `tests/test_typography_families.py`（15 条）**

| 用例 | 守的是 |
| --- | --- |
| `..._one_closed_set_on_both_sides` | `pdfbackend.CANVAS_TEXT_FAMILIES` ↔ `lib/typography.ts` **逐字 + 顺序**；默认族必须是第一个 |
| `..._maps_to_its_own_base14_face`（5 条参数化） | 三族 × 常规/粗/斜/粗斜 → base-14 的正确那张脸 |
| `..._falls_back_to_the_default_instead_of_resolving_it`（6 条参数化） | 认不出来的名字**按默认画**，不抛异常也不去解析一个不存在的字体 |
| `..._reaches_the_pdf_font_resources` | 族走到**产物**里（量 PDF 页面的字体资源表），不是只改了前端预览 |
| `..._uses_the_same_family_as_writing` | 量宽与落笔同族（三个数两两不等 = 尺子看得见「族」这一维） |
| `..._does_not_change_with_the_family` | CJK 只有一张脸——那句注释的看护（实测，不是照抄） |

**前端（34 条）**

| 文件 | 条数 | 守的是 |
| --- | --- | --- |
| `lib/typography.test.ts` | 14 | 能力表 / property path 互查 / 交集不是并集 / 四档取值 / boolean↔枚举换算 / 回到默认删字段 / 校验闭集成因且**不 clamp** / 新建默认值不含字体族 |
| `components/inspector/typographyAdapter.test.tsx` | 14 | mixed 不冒充第一个 / inherit 不算「已修改」/ 必填字段没有恢复按钮 / 不支持说得出为什么 / 多对象一条历史 / **打字也合并**（不先喊 beginGesture）/ 拖动也是一条 / 别处的离散动作先收尾 / invalid 一个字不写 / 恢复 = 删字段 |
| `lib/canvasTextFont.test.ts` | 3 | 载荷缺省不发（主语是**序列化之后的字节**）/ 写回带族但不缩放它 / 老文档打开后字段仍不存在 |
| `canvas/TextView.test.tsx` | +3 | 没设过 = `--font-doc`；设过就按它画；三族在画布上互不相同 |
| `components/inspector/TextSection.test.tsx` | +2（改 1） | 标注有「字体」行；**每条排版属性都挂着锚点**；没设过不显示「已修改」 |
| `components/inspector/textStyleBar.test.tsx` | +2 | 图内六条属性的锚点齐全；`TEXT_BAR_PROPS` = 规范表算出来的那几条 |
| `canvas/contextBar.test.tsx` | +3 | 工具条有斜体与字体；与属性页读同一个 selector（属性页改完当场是新值） |
| `lib/issueFocus.test.ts` | 改 4 | `focusedField: boolean` → `field: none/focused/requested` |
| `tests/golden/preflight_vectors.json` | +2 条向量 | 画布文字的族在两侧求值器上给出同一个答案（既有 21 条**一条没变**） |

### 变异反证：15 条，第一轮 13 红 2 存活，补完 15/15 全红

判定**只看退出码**；Python 侧跑前清 `__pycache__`。脚本在
`<scratchpad>/mutate.py`（不进仓库）。

| 变异 | 第一轮 | 第二轮 |
| --- | --- | --- |
| 画布 `overrideStateOf` 不再区分「可继承」 | 红 | 红 |
| `inherit` 档被压成 `uniform` | 红 | 红 |
| 连续输入不开事务 | **存活** | 红 |
| 越界的数值不再被拒绝 | 红 | 红 |
| 枚举不再校验 | 红 | 红 |
| 回到默认值时写显式值而不是删字段 | 红 | 红 |
| 控件不再挂定位锚点 | 红 | 红 |
| `TEXT_BAR_PROPS` 改成手抄（漏了 style） | 红 | 红 |
| 导出载荷不带 `font_family` | 红 | 红 |
| 画布文字的族不进预检（TS 侧） | 红 | 红 |
| 画布渲染不看对象自己的族 | **存活** | 红 |
| 落笔忽略 family（永远画 Times） | 红 | 红 |
| 量宽忽略 family | 红 | 红 |
| 画布文字的族不进预检（Python 侧） | 红 | 红 |
| 闭集少一个族 | 红 | 红 |

**两条存活的成因是同一个：判据没量到那个维度。**

* 「连续输入不开事务」——**用例自己先调了 `beginGesture()`**，于是「`write()`
  会不会自己开一轮」被挡在判据外面。真实路径是「在字号框里打字」，
  `NumberField` 那条只有 `onChange`，没有 `onScrubStart`。改法不是加一条新
  用例，是把准备阶段换成真实调用方此刻会做的事（见 DECISIONS T-80）。
* 「画布渲染不看对象自己的族」——`TextView.test.tsx` 里压根没有一条断言看
  `style.fontFamily`。补三条：默认族仍是 `--font-doc`（老文档一个像素不变）、
  设过就按它画、三族在画布上互不相同。

---

## Session 14：科学文本 / 字形覆盖 / 字体回退

### 新增用例（后端 100 / 前端 96）

**后端 `tests/test_glyph_plan.py`（88 条，其中 60 条是跨语言向量）**

| 用例 | 守的是 |
| --- | --- |
| `..._golden_vectors_match_python_side`（60 条参数化） | 60 条向量 × 计划 + 缺字单子；vitest 跑同一份 |
| `..._generator_is_up_to_date` | 向量是生成物：改了算法没重跑生成器时红 |
| `..._subscript_two_stays_on_the_fallback_layer` | `₂` 在中日韩脸里有、码位在 CJK 段之外——**覆盖表的裁剪条件**（差一个 `cjk` 就是一个只在下标字符上发作的两侧分歧） |
| `..._box_drawing_is_rescued_by_the_fourth_step` | `━` 是第 4 步救回来的那 87 个码位之一 |
| `..._unrenderable_character_is_missing_not_silently_dropped` | 真画不出来的报 `missing`，不是安静地当成画得出 |
| `..._cjk_is_not_reported_as_a_substitution` | 中日韩落在 `cjk` 层但**不进「换了脸」那张单子**——它只有一张脸，说了用户也改不动 |
| `..._the_script_character_tables_are_identical_on_both_sides` | 两张**手写**的上下标表逐字相同（键与值都比）——vectors 比的是分层计划，覆盖不到它们 |
| `..._the_interpretation_modes_are_the_same_closed_set` | 解释档闭集与默认档两侧一致 |
| `..._the_layer_names_and_cjk_boundary_are_the_same_on_both_sides` | 分层名（**顺序也比**，它就是优先级）与 CJK 段下界 |
| `..._plan_matches_the_faces_the_pdf_actually_uses`（9 条参数化） | **第二把独立的尺子**：读导出 PDF 的字体资源表，层数必须与脸数对得上 |
| `..._fallback_face_is_the_same_regardless_of_family_and_weight` | 回退脸与族/字重无关（六种组合只回一张脸）——`glyph-substituted` 存在的理由 |
| `..._measured_width_equals_the_advance_actually_written`（5 条参数化） | 量宽与落笔**同一份计划**（逐段按计划推进 vs `text_width`） |
| `..._coverage_table_matches_the_live_fonts` | 生成的表还配得上真字体（漂了就红） |
| `..._every_base14_face_shares_one_charset` | 12 张脸共用一张 `primary` 表——单份覆盖表的合法性 |
| `..._auto_mode_keeps_the_pdf_text_layer_verbatim` | 默认档下 PDF 文本层逐字不变（`×10⁵` 抽回来还是 `×10⁵`） |
| `..._scientific_mode_draws_everything_with_one_face` | 一张脸 + **代价说清楚**（文本层降级成 `×105`，这条断言就是那句话的凭据） |
| `..._designed_superscripts_are_never_synthesized` | `m²` 两档都不动 |
| `..._interpretation_only_produces_a_render_representation` | 解释不经 `parse_runs` ↔ `serialize_runs` 那一对，raw text 不变 |
| `..._a_run_of_unicode_scripts_folds_as_one_piece` | **整串一起折**（`m⁻²` 不许一半合成一半留设计字形） |
| `..._superscript_and_subscript_never_merge` | 相邻上标段与下标段是两段 |
| `..._missing_glyph_is_folded_even_in_auto_mode` | auto 档那句承诺（用注入的判据跑：守的是「换个覆盖更窄的后端时它仍然救得回方框」） |

**后端 `tests/test_glyph_coverage_figure.py`（5 条，worker）**

| 用例 | 守的是 |
| --- | --- |
| `..._default_family_draws_the_scientific_characters` | **对照组**：没有它的话「换成 TNR 就缺字」分不清是字体的问题还是判据把所有非 ASCII 都报成缺字 |
| `..._ascii_only_text_never_reports_glyph_trouble` | 纯 ASCII 两张单子都不出现 |
| `..._cjk_label_reports_the_characters_that_come_out_as_boxes` | 中文轴标题**逐字列出**，不是一句「有问题」 |
| `..._named_family_without_the_glyphs_reports_them` | TNR 下 `⁻` 被报出来，且**用户选的那个族仍是 manifest 报的那个** |
| `..._fallback_tail_keeps_the_glyphs_out_of_the_missing_list` | 回退尾巴的兑现凭据：`⁻` 落在 fallback 那张单子上，不在 missing 那张 |
| `..._tick_labels_and_legend_text_are_measured_too` | **刻度文字与图例文字也在内**——补这一条时撞出了真缺陷（见下）。用中文而不是 `⁵` 来量，于是不依赖 Times New Roman 装没装 |

**后端 `tests/test_font_provenance.py`（7 条）** —— 版本库里没有字体文件 /
前端不下载也不内嵌 / 后端没有 `fontfile=` 与 `fontbuffer=` 入口 / 依赖里没有
字体包 / 下拉里每个族后端都真的画得出（3 条参数化）。

**前端（96 条）**

| 文件 | 条数 | 守的是 |
| --- | --- | --- |
| `lib/glyphPlan.golden.test.ts` | 60 | 跨语言向量（与 pytest 同一份） |
| `lib/glyphPlan.test.ts` | 16 | 四步顺序 / 第 4 步救回的那一族 / missing / 合并 / 空串 / **按码位遍历**（代理对算一个字符）/ missing 与 substituted 是两句话 / `textDiagnostics` 量渲染表示 / 覆盖表说得出自己是哪一版后端出的 |
| `lib/richText.test.ts` | +12 | 判据缺席不折 / auto 只救方框 / scientific 一律折 / **整串一起折** / 基础字符画不出时不折 / `^{…}` 不叠一层 / 上下标不合并 / 普通自然语言零影响 / **乘号绝不换成字母 x** / `$…$` 不被双重处理 / `hasScientificChars` |
| `canvas/TextView.test.tsx` | +3 | 默认档原样显示 Unicode 上标 / scientific 合成成 span（字号缩、基线抬）/ `m²` 两档都不动 |
| `components/inspector/TextSection.test.tsx` | +5 | 普通文字不出现那一行 / 有上标才出现 / 锚点来自 `propertyPathOf()` / 缺字逐字列出 / **换脸那句与方框那句分开** |
| `tests/golden/preflight_vectors.json` | +4 条向量 | 画布缺字 / 画布换脸 / **scientific 档下一条都不报**（量原文的话这条会假红）/ 图内两张单子（既有 23 条**一条没变**） |

### 变异反证：15 条，第一轮 14 红 1 存活，补完 15/15 全红

判定**只看退出码**（`| tail` 会把退出码换成 tail 的）；Python 侧跑前清
`__pycache__`；每条先验证「树真的变了」再跑。脚本在 `<scratchpad>/mutate.sh`
（不进仓库）。

| 变异 | 第一轮 | 第二轮 |
| --- | --- | --- |
| 分层第 2 步去掉 CJK 段限制（`₂` 改判成 cjk） | 红 | — |
| 分层去掉第 4 步（`━` 变成 missing） | 红 | — |
| cjk 层也交给拉丁脸落笔 | 红 | — |
| 量宽一律按拉丁脸（与落笔不同源） | 红 | — |
| auto 档按 scientific 折（文本层降级） | 红 | — |
| **逐字符折**（`m⁻²` 一半合成一半留设计字形） | **存活** | 红 |
| 覆盖表生成时多减一个 `cjk`（表与 `layer_of` 对不上） | 红 | — |
| 缺字形与回退合成一张单子 | 红 | — |
| 设族时不带回退尾巴（`⁻` 变回方框） | 红 | — |
| preflight 量原文而不是渲染表示 | 红 | — |
| TS 分层第 2 步去掉 CJK 段限制 | 红 | — |
| TS 按码元遍历（emoji 拆成两个） | 红 | — |
| 预览一律按 scientific 合成 | 红 | — |
| TS 解释器不看 `isDrawable`（auto 变成一律折） | 红 | — |
| 科学文本那一行无条件显示 | 红 | — |
| 刻度文字不取代理的真身（`live_text = artist`） | 红 | — |
| 载荷不带 `interpretation` | 红 | — |
| **缓存上限改成 1（故意的无害变异）** | **存活（预期）** | — |
| TS 上标表少一个 `⁴` | 红 | — |
| TS 下标表值写错（`₂` → `3`） | 红 | — |
| TS 分层名顺序反了 | 红 | — |
| TS 解释档少一档 | 红 | — |

**存活的那条是「同一条规则的第二个消费点漏了」**：「整串一起折」TS 侧
（`richText.test.ts`）有判据，Python 侧没有。补两条（`m⁻²` 整串 + 上下标不
合并）之后 15/15 全红。

**最后那条是正向对照**：把分层缓存的上限从 10000 改成 1 仍然完全正确（只是每个
字符都要重算一次），**它必须存活**。一套 18 条全红的反证说明不了判据强，只说明
我挑的都是真变异；夹一条无害的进去，「全红」才有信息量。

**「所有主要文字对象都要有回归测试」这条退出条件撞出了一个真缺陷**：刻度文字
登记的是 `TickLabel` 代理而不是 `Text`，按 `isinstance(artist, Text)` 判会安静地
漏掉整整一类。补用例之前那条判据看上去无可挑剔——它甚至有一句注释解释「按是不
是 Text 判，不按 role 列白名单」，而那句话本身是对的，**错的是主语**。

**顺带又踩了一次「反证前先提交」**：补完判据没有先提交就再跑了一轮反证，
`git checkout -- .` 把刚补的那条用例还原掉了，于是 M6 第二次仍然显示「存活」
——**看起来像判据没用，实际是判据不在了**。

### 与渲染器的对拍（两把独立的尺子）

图内那条判据读的是字体文件的 cmap，而 matplotlib 在渲染时会自己 warn 缺哪个
码位。两把尺子互相独立（一把读文件，一把看渲染器实际画的时候说了什么），
九组逐组一致：

```text
'×10⁵ A m⁻²'  Times New Roman        我们 ['⁵','⁻']        渲染器 ['⁵','⁻']
'×10⁵ A m⁻²'  DejaVu Sans            我们 []               渲染器 []
'×10⁵ A m⁻²'  [TNR, DejaVu Sans]     我们 []               渲染器 []      ← 回退链
'样品浓度 (mg/L)' DejaVu Sans          我们 ['样','品','浓','度'] 渲染器 同
'样品浓度'     PingFang SC            我们 []               渲染器 []
'H₂O 中文 ⁵'  Arial                  我们 ['₂','中','文','⁵'] 渲染器 同
'plain ascii' Times New Roman        我们 []               渲染器 []
'$10^{-5}$ cm' Times New Roman       我们 []               渲染器 []      ← mathtext 段跳过
'25 °C ± 0.5' Times New Roman        我们 []               渲染器 []
```

**同源了就等于自己验自己**：如果两边都用 `get_char_index`，这张表证明的只是
「我抄对了自己」。


---

## Session 15：图例条目模型 / 源对象绑定 / 高频控件

### 新增用例（后端 31 + 1 向量 / 前端 19）

**后端 `tests/test_legend_binding.py`（28 条，worker）**：导入绑定（label +
指纹 / 脚本改过示意线的项默认 custom / 代理 artist 无源无 binding 字段 / 示意线
类型决定字段集）；源的颜色 / 线型 / 线宽 / marker 变 → 图例同步（4 条参数化）、
markersize 按 markerscale 派生、同步只改像素不改包围盒；脱开（override →
custom，源再变它不动）、撤掉 override 回到跟随、脱开点跨重建保留、脚本自定义
的项显式切回跟随、显式 `binding=custom` 冻结；隐藏一项（整行出盒、元素留着、
序号不变、文字 override 保留）、重排后 override 跟着项走；热态 == 全新重放、
撤销到底像素逐位；布局旋钮四条 + 列距只在多列时有效 + 边框线宽 / 圆角 +
重建保留标题字号；热会话两步的重放；自定义项重建不复利 markerscale；
同名同型双胞胎按位置绑。
**`tests/test_legend_model_pairs.py`（2 条）**：两侧常量严格同源（顺序也比）。
**`tests/test_legend_text.py`**：`test_item_text_follows_display_order_after_reorder`
改成 `test_item_identity_survives_a_reorder`（合同变了：原始序号）。
**`tests/test_invariants_engine.py`**：图例重建豁免删除；新增
`test_legend_rebuild_restores_exactly`；`_ENABLERS` +2（`handle_markersize`
要先有 marker、`columnspacing` 要先多列）；`_NON_VISUAL_PROPS` +`binding`（写了理由）。
**`tests/golden/preflight_vectors.json`** 27 → 28（`legend-entry-custom-handle-width`，
既有 27 条一条没变）。

**前端 `components/inspector/legendCard.test.tsx`（19 条）**：模型（显示顺序 /
每项绑定 / 视图 / 恢复跟随的计划）；分桶（高频项常驻、列距条件显示、图例项
首屏与控件形态）；图例页（没有「自动」、字号与顺序不出第二套、无嵌套可交互、
点文字选中、上下移动写原始序号——含已重排过的情形、显隐）；图例项页（跟随状态
+ 查看源对象、改颜色立刻是自定义、改为自定义 / 恢复跟随一次撤销、脚本 custom
的项恢复写 binding、无源项没有绑定行）。`pickers.test.tsx` 的图例位置用例改成
「最佳位置」+ 断言「自动」不存在。

### 变异反证：17 条，第一轮 14 红 3 存活，补完 16 红 1 存活（成因是双保险）

判定只看退出码；Python 侧跑前清 `__pycache__` + `PYTHONDONTWRITEBYTECODE`；
树不干净直接拒跑；每条跑完 `git checkout` 那一个文件。脚本
`<scratchpad>/mutate15.py`（不进仓库）。

| 变异 | 第一轮 | 第二轮 |
| --- | --- | --- |
| M1 sync 里跳过跟随替换 | 红 | — |
| M2 有 handle_* override 仍算跟随 | 红 | — |
| M3 脱开点拿盒里那份而不是从源派生 | 红 | — |
| M4 重建把快照喂回去（旧路径） | **存活** | **存活**（见下） |
| M5 重建后不放回 markersize | 红 | — |
| M6 重建后不重放 override（单批） | **存活** | M6b 热会话两步：红 |
| M7 双胞胎取第一个匹配而不是按位置 | 红 | — |
| M8 没有源也发 binding 字段 | 红 | — |
| M9 预检对跟随的项也报线宽 | （变异点写错）| 红 |
| M10 隐藏的项元素表里丢掉 | 红 | — |
| M11 重建丢标题字体属性 | 红 | — |
| F1 前端徽标忽略 handle_* override | 红 | — |
| F2 恢复跟随不写 binding=follow_source | 红 | — |
| F3 上下移动写显示位置 | 红 | — |
| F4 图例卡不接管 fontsize | **存活** | 展开「更多」再数：红 |
| F5 位置控件仍有「自动」 | 红 | — |

**M6 的存活是用例形状**：同一批 patch 里 handle override 的 setter 排在重建之后，
天然正确；只有热会话分两步（先改颜色、下一步再改列数）才走到重放。补了那条
用例（M6b）。**F4 的存活也是用例形状**：`fontsize` 在图例模板里落在「更多」，
折叠着的重复数不到，先展开再数。

**M4 的存活是结构性的双保险**：重建从源派生之后，`sync_legends` 在同一次
`apply()` 尾部又会对每个跟随的项从源派生一次——把重建的素材换成快照，同步照样
把它治回来。两处都从源派生是有意的（重建那一步先落对，同步兜底所有别的
setter），一条用例杀不死它是这条冗余的代价，**不是判据缺口**。记在这里，别去
「加强」那条用例。

### 实跑到的、不是假设的

* 改造前四条缺陷一次探针全部现形：`line.set_color` 之后 `leg.legend_handles[0]`
  仍是旧色；ncol 重建后标题字号 12 → 10；markerscale 4×1.5 = 6 → 9 → 13.5；
  误差棒示意线 LineCollection → Line2D。
* 真应用（worktree 起在 5099）走了一遍：选图例 → 图例卡；选「lin」项 →
  「自定义」+「恢复跟随」→ 渲染回来线宽 1.5；改曲线「lin」颜色 → 图例示意线
  跟着变成品红。

## Session 16：坐标轴边框语义命中区 / 四边刻度模型 / 主次刻度分档

### 新增用例（后端 12 / 前端 40 + 22 + 8）

**后端 `tests/test_tick_sides_geometry.py`（12 条，worker）**：四条边框线落在框沿、
`visible` / `ticks` 与四边字段同口径；`spines` 报的是改完 override 之后的状态
（边框显隐与刻度显隐是两件事）；偏出去的左边框在框沿之外、隐藏的边框几何照给、
twinx 第二个 axes 不出上下两条且左右各按真值；极坐标 / 3D 不给 `spines`；对数 +
反转不改几何、`secondary_xaxis` 只有上下两条且下边既不显示也没刻度；色条轴不给；
`length` 只动主刻度、`minor_length` 顺序无关、`minor_width` 同样分档、次刻度没开
时先设长度再开仍生效、次刻度长度像素真变 + 撤销回原样、3D 不出次刻度字段。

**前端 `lib/tickSides.test.ts`（40 条）**：三带分类（四边 × inner / outer / neutral，
10 条参数化）、带外不命中、五档 zoom 下带宽恒定 + 高亮条同尺、触控带、角落
（更近的边 / 等距先取有刻度的 / 固定次序）、偏出去的边框、无目标的边、无
`spines`、端点顺序任意；模型派生（默认 / override 优先 / 没刻度元素的轴不进模型 /
非子图 → null）；计划（inout → in → 隐藏三步、隐藏边点框外只开边、点框里开边 +
inout 且连带点名、另一边不可见不算连带、不在模型的边 → null、**全状态扫描**：
3 方向 × 2 × 2 显隐 × 2 边 × 2 带 = 48 种，切完那一方向必翻转、另一方向按规则）；
四档（派生态「隐藏」、选隐藏写两边 false、选回方向删两边 override、只写方向、
当前方向 → null）、显示边；整图挑边（allow 闸、twinx 取有刻度的那条）。

**前端 `canvas/spineZones.test.tsx`（22 条）**：hover 高亮条 + 状态文字 + pointer
光标 + 条厚度 = band − neutral；外侧说朝外且开着；离开命中带 / 离开面板即消失；
连带的另一边浅色一起亮 + 文字点名；中线无高亮；点击 = inout + 一条历史 + 选中
子图；隐藏的上边点框外只开边、点框里开边 + inout 一次 commit 撤销一起回；左边框
写 Y 不写 X；已选着刻度组不改选区；中线只选中；文字 / 刻度文字优先、外侧带没被
盖住的段照样可点；zoom 0.5 / 3 下 5 px 在带里 20 px 在带外；触控 14 px；旋转
90 / 180 / 270 反旋转后落在同一带；偏出去的边框（pickElement 命中 figure）可点、
框沿空白不命中、点线本身选中子图；无 `spines` 整层无命中。

**前端 `inspector/tickTaskCard.test.tsx`（+8，示意图段整段重写）**：内 / 外两带的
aria-checked 与实线 / 虚线；**内侧带的命中矩形在框里、外侧在框外（四边）、中间
留中性带**；刻度朝内时点框里那一带即可控制；两带各自开关；一次点击一条历史
（方向 + 显隐同一 commit）；连带点名（`data-tick-coupled`）；X / Y 互不影响；
次刻度只画在开着的那一半；关掉一边两带都虚线；四档「隐藏」写两边 false 且方向
不动、选回方向删两边 override；两边用示意图关掉后方向档显示「隐藏」且文档里没有
`hidden` 这个值；「显示边」开关 + 键盘；`minor_length` 写自己的字段不碰 `length`；
`data-prop="direction"` 锚点带 `data-gid`。

### 变异反证：10 条，10/10 全红（第一轮）

判定只看退出码；Python 侧跑前清 `__pycache__`；树先提交（`d2745fc8`）再变异，
每条跑完 `git checkout` 那一个文件。

| 变异 | 结果 |
| --- | --- |
| M1 `length` 回 `which="both"` | 红（2 条） |
| M2 边框几何改用 axes 框而不是 spine 路径 | 红（偏出去的边框） |
| M3 去掉「axis 不可见不出」+「色条轴不出」两道闸 | 红（twinx + 色条各 1） |
| M4 上下边的内 / 外符号反过来（原始缺陷的形状） | 红（25 条） |
| M5 带宽按分数写死、不随 zoom | 红（5 条） |
| M6 去掉优先级闸（文字上也给边框命中） | 红（2 条） |
| M7 计划按 patch 逐条 commit | 红（2 条：历史数 + 撤销一起回） |
| M8 连带永远不报 | 红（3 条） |
| M9 触控带宽忽略 | 红（2 条） |
| M10 从「隐藏」选回方向不删两边 override | 红（2 条） |

### 实跑到的、不是假设的

* matplotlib 3.10.8：`tick_params(which="major", direction="in")` 之后
  `_minor_tick_kw` 里没有 tickdir、次刻度仍朝外；`tick_params(length=6)` 默认只动
  主刻度（次刻度仍 2.0）；`which="both"` 才两档一起——Tavotto 此前所有刻度 setter
  都是 `which="both"`。
* `Spine.get_window_extent()` 把刻度伸出量算进去（下边 y0 = 28.1 而线在 33.0）；
  `_adjust_location()` + `get_transform().transform(get_path().vertices)` 才是那条线，
  且含 `outward` 偏移（左边 x = 36.1 而框在 50.0）。
* 3D 轴也有 `left/right/bottom/top` 四条 `spines`（占位），只按名字判会把 3D 当直角
  轴；`secondary_xaxis` 的左右两条退化成一点（长 2e-8 px）。
* 真浏览器（chromium）里从面板底沿往上扫：外侧带 → 无带 → 内侧带三段依次出现，
  文字与 jsdom 用例里断言的逐字相同；点内侧带后刻度线消失、数字仍在。状态文字
  第一版放在带的外侧，被面板的 overflow hidden 整个裁掉——jsdom 看不见裁剪，
  这一条只有真浏览器抓得到。
* jsdom 的 React `onPointerLeave` 由 `pointerout` 合成，直接派 `pointerleave` 不触发；
  没落进命中带的按下会开始一次拖动（`trackPointer` 挂在 window 上），用例之间不
  松手的话后面的 `pointermove` 全被 `kind !== 'none'` 吃掉——「单跑绿全量红」的
  又一种形状。

## Session 17：多选浮动 Context Bar / 共享排列参照 / 主选语义

### 新增用例（前端 13 + 42 + 5 + 17 + 2；后端无）

**`canvas/context-bar/position.test.ts`（13 条，纯函数）**：上方居中、顶部安全区放不下
翻下方（含边界 = TOP_SAFE 本身）、下方也放不下贴窗口底边、左右不越界、避让停靠侧栏
（左含轨道）、两侧之间比栏还窄贴左；`sidebarInsets` 三态；`barVariant` 阈值 + 可用宽度；
`selectionScreenRect` 原点 + 平移 + 世界像素 × 缩放、缩放翻倍尺寸翻倍。

**`canvas/context-bar/multiSelectionBar.test.tsx`（42 条）**：单选仍是 Object bar、单图内
元素仍是 Element bar（mock `engineRender` 播一份 manifest）、两个 / 三个对象出现 + 计数 +
role / aria-label、图内编辑态不出；两个对象分布 `aria-disabled` 点了不动、三个可用、
有组多出取消成组；对齐（选区 / 画布 / 主选）、等宽 / 等高、水平 / 垂直分布、成组 →
取消成组、撤销回原位、「更多」开属性页且选区不动、**标签与直接调 action 逐字一致**；
pointerdown 隐藏 pointerup 再现、七种 interaction kind 参数化隐藏 / 再现、QuickEdit /
裁剪 / 文字编辑 / 非选择工具 / 模态 / 命令面板 / narrow 抽屉让位、选区掉到 1 换回单选栏；
Esc 焦点在栏内拦事件 + 选区不动、焦点在外不拦、选区一变重新出现且仅缩放不解除；落位五条
对着 `placeToolbar(selectionScreenRect(boundsOf(sel)))` 算（上方 / zoom-pan 重贴 / 侧栏开合 /
顶部不够放下方 / 左右不越界 / 对象挪动重贴）；窄屏压缩（resize 事件 + 弹层里同一批按钮
可用、停靠侧栏吃掉宽度也压缩）；每颗按钮有可达名、分段组有组名、出现不抢焦点、
按钮可 Tab；**与 ArrangeSection 共用参照双向同步、切参照不进历史**。

**`canvas/primarySelection.test.tsx`（5 条）**：单选无标记无联合框；多选末位 id 唯一主选、
2 px、联合框锚点；ids 顺序换主选跟着换；线状对象做主选沿线描示更粗；联合框几何 =
包围盒。

**`store/alignSelectedTo.test.ts`（17 条）**：参照三档 + 主选跟 ids 顺序、等宽 / 等高、
分布两个拒绝三个等距；锁定不动但算进参照框 + 提示、含锁定成员的组整组不动、全锁
不进历史；成组 / 取消成组各一条历史 + `selectionHasGroupIn` 同判据；开着的手势先收
（对齐 / 成组）；活动信号三种各一次且 detail 不含 id、拒绝时不发、监听者抛错不影响动作。

**`store/arrangeStore.test.ts`（2 条）**：默认 selection、同值不产生新状态。

既有 `canvas/contextBar.test.tsx` 的「多选不出现」改成「换成多选栏，单选文字控件不出现」。

### 变异反证：14 条，14/14 全红（第一轮）

判定只看退出码；树先提交（`b0d14f7c`）再变异，脚本树不干净拒跑，每条跑完
`git checkout` 那一个文件。定向集：`context-bar/` + `contextBar.test.tsx` +
`primarySelection.test.tsx` + `alignSelectedTo.test.ts` + `arrangeStore.test.ts`（90 条）。

| 变异 | 结果 |
| --- | --- |
| M1 `alignSelectedTo` 不收手势 | 红（1 条） |
| M2 对齐不跳过锁定对象 | 红（3 条） |
| M3 主选取首位而不是末位 | 红（5 条：action 4 + 栏 1） |
| M4 OverlaySvg 主选标记挂到首位 | 红（3 条） |
| M5 多选栏要三个才出现 | 红（24 条） |
| M6 交互中不隐藏（去掉 `kind === 'none'`） | 红（7 条：七种 kind 全部） |
| M7 顶部不够也不翻到下方 | 红（4 条：纯函数 3 + 栏 1） |
| M8 分布不按数量禁用 | 红（1 条） |
| M9 属性页参照退回本地 state | 红（1 条：共用参照） |
| M10 活动信号不派发 | 红（1 条） |
| M11 Esc 不拦事件 | 红（1 条） |
| M12 左栏占位算成 0 | 红（3 条：纯函数 1 + 栏 2） |
| M13 图内编辑态照出多选栏 | 红（1 条） |
| M14 落位不随 zoom / pan | 红（3 条） |

### 实跑到的、不是假设的

* `pnpm test -- <路径>` 不过滤（pnpm 吞掉 `--`），跑的是全量 165 文件——第一遍就把
  「其余全绿、只有我三条红」这件事顺手证明了。
* 真浏览器（chromium）：完整栏量出 617 px；`fixed` 盒子 `width:auto` 时 left 停在旧值、
  盒子被压到 299 px，静态阈值放行的 600 px 视口下「放得下」是假的——`w-max` 之后压缩档
  才真的出现。jsdom 里 `offsetWidth` 恒为 0，这条判据在 jsdom 里恒真。
* 真浏览器：Radix Popover 自动聚焦第一个分段项，其 tooltip 停在下一排按钮上，
  Playwright 卡在 `data-radix-popper-content-wrapper intercepts pointer events`；内容层
  `pointer-events-none` 不够，外壳没有背景也照样命中，`:has([role='tooltip'])` 选到外壳才行。
* 三段文字的 y 等距、文字 sameh 是 no-op：两条「历史少一条」的假红都是 fixture 已经
  处在目标状态。

## Session 18：QuickEdit 右键菜单 / 重新构建 / 批量动作

### 新增用例（前端 62 + 18；后端 4；真浏览器 1）

**`canvas/objectContextMenu.test.tsx`（62 条）**：右键选择逻辑八条（未选 → 选中、单选保持、多选内
保持且顺序不变、多选外切换、组成员整组、锁定不吃指针、编辑态右键别的对象退编辑、混排标注不退）；
可编辑面板十条（结构逐项、无 override 无恢复项、编辑图内元素、重新构建调 invalidate 再按当前
overrides 渲染且文档历史不动、裁剪、旋转面板 disabled + 原因 + 点了不动、完整放入一条历史、
恢复先问 → 确认清空 / 另一实例不动 / 可撤销、打开全部属性选区不动、窄屏铺开右栏）；仅排版面板
六条（结构、四种非 editable 状态统一「为什么不能编辑？」且只开接入中心、连接源脚本按 can_probe /
can_manual_link 出现、capability 缺席什么都不说、readiness 取不到报告仍保留选区）；文字 / 箭头 /
形状七条（结构、编辑文字、副本、锁定 → 解锁、隐藏、层级子菜单四项带快捷键且标签与直接调 action
逐字一致、删除）；多选十二条（结构 + 计数、对齐子菜单参照行 + 六向两分布 + 左对齐标签与 action
一致、参照读 arrangeStore、两对象分布 disabled + 原因、等宽、成组 → 整组只剩取消成组、混合选区
两项都给、打开排列属性、副本保留组语义、批量锁定一条历史 + 混合两项、批量隐藏可撤销、层级作用
整个选区、删除 N 个）；键盘 / 关闭 / 无障碍十二条（role + aria、↓ ↑ Home End、Enter、子菜单
→ ← 与 Esc、Esc 不冒到 window 且选区不动、首字母不切工具、点外部关掉且另一个对象直接开、滚轮 /
失焦、焦点归还、ContextBar 让位、目标消失自关、动作抛异常仍关）；图内元素弹层两条（仍是 dialog +
「恢复此元素修改」、Select portal 不误关 / 点别处关 / Esc 关）。

**`store/quickEditActions.test.ts`（18 条）**：批量锁定五条（混合 → 全锁一条历史带数量且选区不动、
全解 + 幂等、单数 key、撤销整批、triStateOf）；批量隐藏一条；恢复四条（取消不动、确认只清本实例
一条历史可撤销并渲染 `[]`、写回过的面板换 body key、无 override 不问）；重建八条（先作废后渲染 /
按文件 id / 不清 override / 不改文档 / 不进历史 / 状态 ready / toast、顺序、作废不了 →
`rerendered` + 「没有重跑」、作废失败不渲染 + 报错、渲染失败不叠 toast、替代传输不调作废、
非可编辑面板跳过、同文件另一实例 stale + tracked）。

**`tests/test_engine_invalidate.py`（4 条）**：磁盘面板按脚本 + 项目根作废且不起 worker、源文件
字节不变；未登记 404 且什么都不做；native 会话不杀且 `invalidated: false`；safe runtime 面板按
脚本作废。

**`e2e/quick-menu.spec.ts`（1 条，真浏览器）**：子菜单上的 Esc 不清空选区、重新构建真跑脚本
（冷构建 → toast）、键盘 ↓ / 首字母不切工具、贴画布右下角右键菜单翻上方 + 子菜单翻左边、
⌘A 多选右键 → 对齐子菜单 → 左对齐三对象 x 相等。

### 变异反证：22 条，19 红、3 存活（成因都说得清）

判定只看退出码；树先提交（`608745c8`）再变异，脚本树不干净拒跑，每条跑完 `git checkout`
那一个文件。定向集：`objectContextMenu.test.tsx` + `quickEditActions.test.ts` + `hitTest.test.tsx`
（97 条）；后端三条用 `test_engine_invalidate.py`。

| 变异 | 结果 |
| --- | --- |
| M1 右键不再把未选对象选进去 | 红（4 条） |
| M2 右键永远重置选区为该对象 | 红（2 条） |
| M3 右键别的对象不退图内编辑 | 红（1 条） |
| M4 多选阈值 2 → 3 | 红（3 条） |
| M5 capability 缺席也解释 | 红（1 条） |
| M6 旋转面板照样能裁剪 | 红（1 条） |
| M7 整组选区仍给成组 | 红（1 条） |
| M8 根菜单 Esc 不在捕获层止步 | **存活（结构性）**：jsdom 没有监听器之间的微任务检查点，冒泡层 `onKeyDown` 仍跑到；真浏览器守护 `e2e/quick-menu.spec.ts`（T-98） |
| M9 子菜单 Esc 不在捕获层止步 | **存活（结构性）**：同上；这条正是第一遍真浏览器红的那条 |
| M10 菜单里按键冒到全局 | 红（1 条） |
| M11 关闭后不还焦点 | 红（1 条） |
| M12 批量锁定用 toggle | **存活（语义 no-op）**：目标已按 `!!locked !== locked` 过滤，toggle == set |
| M13 重建不 markStale | 红（1 条） |
| M14 重建先渲染后作废 | 红（1 条） |
| M15 作废不了也说重建了 | 红（2 条） |
| M16 恢复不问直接清 | 红（1 条） |
| M17 native 会话也作废 | 红（pytest） |
| M18 作废不带项目根 | 红（pytest） |
| M19 元素弹层的 Select portal 守卫删掉 | 红（1 条） |
| M20 打开全部属性不切属性页 | 红（2 条） |
| M21 隐藏批量直接改内存不 commit | 红（2 条） |
| M22 后端未登记 404 改成 200 | 红（pytest） |

### 实跑到的、不是假设的

* **真浏览器第一遍就红**：子菜单开着按 Esc → 菜单关了、选区也没了。jsdom 里同一份代码全绿。
  成因是监听器之间的微任务检查点（T-98），修法是捕获层 `onEscapeKeyDown` 止步。
* Radix 把「聚焦下一项」放在 `setTimeout(0)`（RovingFocusGroup）：jsdom 与 Playwright 里
  按完方向键都要等一拍再看 `activeElement`，否则第二个 ↓ 看起来没动。
* `alignSelectedTo('samew', 'selection')` 的结果是**选区包围盒的宽**（90），不是主选的宽——
  第一版用例写成了 10，是我对参照语义的假设错了，判据没错。
* 把 `ObjectView` 一次性渲染出来的用例不跟文档走：改 `locked` 之后 DOM 不重渲染，锁定那条要
  在 seed 里就锁好。
* 拖到 (1300, 820) 的面板落在右栏底下，右键点到的是侧栏——画布区右沿 ≈ 1040。

## Session 19：设置外壳 / 编码 Agent 精简 / 包管理 / 诊断拆页

### 新增用例（后端 45；前端 12 + 19 + 7 + 2 + 3；真浏览器 6）

**`tests/test_package_management.py`（45 条）**：清单六条（没项目 → `no_project` 禁用原因、环境未建
一个子进程都不起、内置 = 闭包不含 lmfit / scipy、状态按环境不按账（missing / changed）、账上的 numpy
在闭包里标 protected、清单里没有路径 / 代理地址 / 凭据）；闭包两条（递归、无盘点只剩基础集）；语法
与安全十六条（三条 argv 逐字节钉、十二种敌意串 × 三种操作在 plan 阶段就死且 pip 一次不调、卸载只收
包名、未知 op、**结构性：作业解释器落在 `managedenv.env_dir` 下 + 签名里没有 `python` 参数**）；
保护与依赖七条（卸 matplotlib / numpy / Pillow / pip 一律 protected、卸没装的、依赖者报出来、update /
uninstall 要有环境、install 无环境就计划创建、无基础 Python 拒绝、磁盘不足只挡 install / update）；
绑定与并发八条（未知作业、环境变了 stale、作业绑项目（A 的作业在 B 项目 409）、run 端点只读 job_id、
plan 端点的稳定码、list 端点没项目 200 + 原因、作业与修复同一把锁、native 会话用自己的码、盘点期间
`busy`）；记账两条（`forget_install` 按 PEP 503、快照上限与文件名无路径）；**离线真安装三条**
（建环境 → 装本地 wheel → 账 / import / 宿主解释器 import 不到 / 清单 `in_use` → 升级幂等 → 卸载 →
import 不到 / 账划掉 / matplotlib 仍好 / 前后快照都在；新 wheel 真升到 1.1；不存在的包报
`dependency_not_found` 且环境仍可用）。

**`SettingsDialog.test.tsx`（12 条）**：十一分区顺序与默认页、四条别名、`profiles` 深链落规范页、
样式 / 规范各自字段、外框宽高常量切分区不变、内容区 `overflow-y-auto` + 切页 scrollTop 归零、
导航不换行可横滚、↓ ↑ Home End 走 + 搬焦点、roving tabindex、returnTo 三条。

**`PackagesSettings.test.tsx`（19 条）**：禁用原因三条（没项目 / 建不了环境 / busy）；清单五条（内置只读 +
用户升级卸载 + 保护只读、来源与规范与版本变化、环境行、无回滚常驻、无路径）；安装六条（敌意串不发
请求、plan → run 两步 + 进度 + 禁用不冻结、plan 失败按 code、进度到终态日志可复制 + 重读清单、别的
作业事件不收、取消真发）；卸载三条（先问 + 依赖者列出 + 取消不 run、确认才 run、后端拒卸内置按 code）；
升级一条。

**`DiagnosticsSettings.test.tsx`（7 条）**：坏的在前说原因好的只有名字、`cli_*` 不显示且不计入异常数、
全部正常一句话、渲染环境卡只在技术详情里一张 + 内置包版本不在、复制先预览后复制（预览阶段剪贴板
零字节）、摘要拿不到说失败、导出按钮还在。

**`agentState.test.ts`（2 条）+ `CodingAgentsSection.test.tsx`（+3）**：版本号只取数字 / 抽不出回 null；
一级页面无路径无内部包名无说明段无卡片框、未安装 / 装坏的第二行、详情可复制。

**`e2e/settings-shell.spec.ts`（6 条，真浏览器）**：切遍十一分区外框逐像素不变 + 对话框本体不滚 +
无横向溢出；1024×640 外框在视口内；600×700 导航在内容上方 + 可切页 + 不溢出；英文四页不溢出；
方向键走导航；三页 axe 无 critical / serious。

### 变异验证记录（Session 19）

**流程**：`scratchpad/mutate19.py`——树不干净拒跑；**先跑一遍基线（pytest 43 条 + vitest 五文件
必须绿）**；每条变异 → 跑定向用例 → 按**退出码**判 → `git checkout -- 文件` 还原。

| 变异 | 结果 |
| --- | --- |
| P1 保护闭包不递归 | 红 |
| P2 内置包可以卸 | 红 |
| P3 卸载不报依赖者 | 红 |
| P4 磁盘检查删掉 | 红 |
| P5 作业不查环境指纹 | 红 |
| P6 run 端点不核项目 | 红 |
| P7 install 也带 `--upgrade` | 红（argv 逐字节钉住） |
| P8 卸载接受版本约束 | 红 |
| P9 plan 不查环境忙 | 红 |
| P10 划账不按 PEP 503 归一 | 红 |
| P11 账上有就报已安装 | 红 |
| P12 network 回代理地址 | 红 |
| F1 卸载不问 | 红（2 条） |
| F2 客户端不挡形状 | 红 |
| F3 别的作业事件也收 | 红 |
| F4 作业跑着也不禁用 | 红（2 条） |
| F5 诊断不过滤 CLI 项 | 红（2 条） |
| F6 版本抽不出就回原文 | 红 |
| F7 `profiles` 别名指到样式 | 红（2 条） |
| F8 关掉设置不回导出 | 红 |
| F9 切页不滚回顶部 | 红 |
| F10 外框不传固定高 | 红 |
| F11 装好的行也显示路径 | 红 |

**23/23 全红。** 没有存活项。

### 实跑到的、不是假设的

* **真浏览器第一遍 5 红**：状态徽章 `w-24` 定宽被英文撑破（4 条溢出用例一起红）；本机 claude 的
  shim `--version` 第一行是 bash 报错，`agentVersionLabel` 回原文 → 一级页面出现 `/Users/…`；
  进场动画中量 `boundingBox`（747×590）；<1024 抽屉遮罩的淡入让 Playwright 恒判不稳定。
* **`"lmfit==1.0 "` 过了敌意用例**：`create_package_job` 在边界 `strip()`，这是合理行为；敌意串
  改成内部空格。
* **`open_project()` 回 dict**，`["id"]` 才是 pj。
* **纯函数单测放进带 `root.unmount()` 的 afterEach 文件里会在 afterEach 炸**——单独成文件。

## Session 20：离线教程资源与 Tutorial API

### 新增用例（后端 47；前端 0；真进程冒烟 1）

**`tests/test_tutorial.py`（47 条）**：资源五条（`importlib.resources` 可达且清单不含 `__pycache__`、
元数据稳定且无路径 / 两张图都带 title · legend_text · axis_label / 至少一条 8 pt 问题、静态验证全过、
体积与无外部数据、PDF 零尺寸被拒）+ 十三种坏资源各一条（缺 PDF / 坏 PDF / 坏注册表 / 语法错 / 读外部
数据 / 网络 import / 绝对路径 / stems 不一致 / 文档 schema 旧 / 文档引用不存在的素材 / 超大文件 / 缺
元数据 / 只有一张图）；副本九条（首次复制到 `v<版本>-<指纹>/Tutorial` + state.json + 可写、幂等且保留
用户改动、reset 恢复原样且无残留、缺文件与坏注册表只补缺的、资源变了换目录旧目录留着（升版本号 /
只改内容各一次）、复制失败旧副本原样且无 `.tmp`、占用（PermissionError）报 `tutorial_locked` 旧副本
原样、放新副本失败把旧的放回、陈旧 `.old/.tmp` 被清、`is_tutorial_path` 只认数据目录那棵树）；
API 十一条（GET 不泄漏包内 / 数据目录路径、GET 对坏资源如实、open 不起 worker 不 probe 不起草 + 两张
面板都连着脚本 + `/api/layouts/Tutorial` 是 schema 3 + 再开复用、最近列表标记 + 可移除、open 不动
用户项目、reset 只清教程 autosave 与 baked 且先 `close_project(pid, wait=True)`、reset 在没开时先建、
默认项目归属两种、锁住 409 且旧副本重新打开、open 修复缺文件、三个端点走认证）；打包六条（读 wheel
成员逐字节比对 + 无 `.pyc` + 体积、读 sdist 成员、解包 wheel 后子进程只靠 `importlib.resources` 找到
资源且验证全过、spec datas 含 resources 与 profiles、pyproject / gitignore 不挡资源、冒烟脚本与 CI 的
`--tutorial` 接线合同）；worker 真跑两条（每张图 build → stems / roles ⊇ editable_roles / 7 pt 文字 /
build 期间副本 PDF 一个字节不变）。

**`scripts/smoke_app.py --tutorial`（真进程）**：`GET /api/tutorial` → open（`default=False`）→ 两张图各
`POST /api/engine/render?pj=` 一次并核 roles → reset → 副本完整。接进 CI 两条内置 runtime 的冒烟①。

### 变异验证记录（Session 20）

**流程**：`scratchpad/mutate.py`——树不干净拒跑；每条变异 → 跑 `tests/test_tutorial.py` → 按**退出码**判
→ `git checkout -- 文件` 还原。反证前先补了四条用例（单张图元数据、reset 先关项目、陈旧残留目录、
冒烟 / CI 接线合同）。

| 变异 | 结果 |
| --- | --- |
| M1 reset 不先挪走旧副本 | 红 |
| M2 缺文件不修 | 红 |
| M3 占用不识别（`_is_locked` 恒假） | 红 |
| M4 放新副本失败不把旧的放回 | 红 |
| M5 指纹不看内容 | 红 |
| M6 `is_tutorial_path` 恒真 | 红 |
| M7 panels 只要一张 | 红（补的用例） |
| M8 不查外部数据调用 | 红 |
| M9 不读 PDF 尺寸 | **存活** → 坏 PDF 在 `probe_asset` 就抛、走的是 except 分支，`w_pt > 0` 那条边没人量。补 probe 回零尺寸的用例，复跑**红** |
| M10 reset 不清教程 autosave | 红 |
| M11 reset 清掉所有 autosave | 红（别的文档的槽位必须还在） |
| M12 open 起 worker | 红（`_forbid_execution` 夹具） |
| M13 `project_status` 不标 tutorial | 红 |
| M14 reset 不先关项目 | 红（补的用例：`close_project(pid, wait=True)` 必须被调） |
| M15 占用回 500 不回 409 | 红 |
| M16 复制失败留半个临时目录 | 红 |
| M17 残留目录不清 | 红（补的用例） |
| M18 spec 漏掉 resources | 红 |
| M19 绝对路径不查 | 红 |
| M20 坏注册表不当缺文件补 | 红 |
| M21 recent 不标 tutorial | 红 |
| M22 副本不设可写位 | **存活** → 语义 no-op：`copyfile` 本就不拷权限位，副本按 umask 建成可写。删掉 chmod 而不是「加强」用例 |

**22 条：20 红；2 存活各自处置（1 补用例后红，1 删冗余代码）。**

### 实跑到的、不是假设的

* **matplotlib 在这台机器上 import 只要 0.3 s**：worker 用例 0.26 s 跑完一度让我怀疑「没真跑」；
  拿 manifest 元素数与 7 pt 文字核过，确实跑了。
* **打开用户项目会起草注册表**：`test_open_does_not_touch_the_user_project` 第一版把起草算到了教程
  头上（假红）。
* **第一版副本目录叫 `project/`**，最近列表里就显示「project」——`project_status()["name"]` 取目录名。
* **前端只加一个类型字段也要重建两个受管产物**（指纹覆盖 `web/src/**`）。
* **桌面 PyInstaller 产物里 `profiles/publication.json` 本来就不在**（datas 没收，Analysis 不收数据
  文件）——本轮顺手补上，但没有本机产物能证明，等 CI 桌面腿。

## Session 21：交互式 Onboarding、真实完成条件与一次性情境提示

### 新增用例（后端 0；前端 8 个文件 78 条；真浏览器 4 条）

**`store/onboardingStore.test.ts`（12）**：start / pause(user|system) / resume / skip / complete / markStep 去重 /
back 不撤完成 / resetOnboarding 与 resetHints 分开；持久化只写白名单字段；坏 blob / 非对象 / schema 不认
→ 安全默认；逐字段校验保住能保住的；flowVersion 升级进行中回第一个未完成、已完成不打扰；persistence
为 null 纯内存；宿主 adapter。

**`lib/activity.test.ts`（5）**：`Record<ActivityKind, 样本>` 与 `ACTIVITY_KINDS` 一一对应（新增 kind 不加
样本编译红）；每种样本的键都在 `ACTIVITY_PAYLOAD_KEYS` 里、`id / gid / name / path / text / value / stem`
不在；emit → 订阅 → 退订；杂事件过滤；监听者抛错不冒回。

**`store/selectionStore.test.ts`（3）**：set / add / toggle / clear 各发一次只带数量；没变不发；prune 不发。

**`lib/onboarding/position.test.ts`（8）**：下 → 上 → 右 → 左 → 夹进视口；居中；offscreen；unionBoxes。

**`lib/onboarding/flow.test.ts`（13，走生产 action）**：步骤表与 id 表对应；要编辑的是带 spec_issue 的 Fig2；
welcome 手动且提前进入的图内编辑态在点「开始」后立刻识别；open_fast_edit 只认那张图；select_text 主选必须
文字类 role（曲线 / figure 不算）；change_typography 要排版 override + 历史（非排版属性不算、只有信号没历史
不算、消费后清零）；locate_problem 真 `focusObject` 成功且落在教程面板（失败不算）；「已解决」出口三条件；
export_original 面板开着确认原图 **且关掉**；add_to_layout 在快速编辑里要按「加入画布」回版面、画布模式
直接完成；multi_select_align 单张不算、两张教程图 + 对齐算；export_canvas 消费过的原图信号不算；done →
complete；切项目系统暂停 / 切回自动继续 / 用户暂停不自动继续；换文档也算离开且教程外的信号（含导出范围）
不累计。

**`lib/onboarding/tutorial.test.ts`（10，stub fetch）**：open → 认领 → 教程画布用 `document_id` → 从头；有
进度用进度；同一项目不再走认领且暂停的继续；完成后再开 = 从头且不 reset；失败按 code 分类（unavailable /
locked / no_api / open_failed）；layout 404 → document_failed；GET 不到 → no_api；reset 先确认并列出另存
画布、取消不发；确认后 POST → 忘掉本机那格（留一份合法但陈旧的画布反证）→ 干净画布 → 从头；409 locked
进度不动。

**`lib/onboarding/hints.test.ts`（8）**：每类一次 / 教程进行中不出 / 不叠 / 到时收起 / 重置后再出；触发：
可编辑面板 / 仅排版面板 / 图内编辑态与快速编辑里的单选不算 / 多选 / 进快速编辑 / 第一次出现问题。

**`components/onboarding/onboardingLayer.test.tsx`（6，jsdom + 假矩形）**：不在教程里不画；欢迎页居中、
非模态、无遮罩、aria 关联、读屏区、「开始」是真动作；关闭键与 Esc = 暂停；锚点在 → 下方落位 + 高亮环 +
进度 + 返回 / 跳过（跳过 = 前进）；锚点缺 → 等待 → 超时说找不到 → 返回真的回上一步；锚点在对话框里 →
portal 进对话框 + 绝对定位；reduced motion 无过渡无进场动画。

**既有用例改动**：`alignSelectedTo.test.ts` 两条只看排列三种 kind（总线上多了通用信号）；
`objectContextMenu.test.tsx` 抓到 Hook 顺序随 `obj` 变（第一版把 `useEffect` 放在早退之后）。

**`e2e/tutorial.spec.ts`（4，chromium，真后端 + 真 matplotlib）**：① 完整走完——素材卡双击 → 点高亮环
中心选标题 → 字号框敲 12 → 「问题」抽屉点 p2 的 7 pt 那条 → 导出（coachmark 在面板里）确认原图 Esc →
「添加到画布」 → Shift 点 p1（层先把它挪回工作区）→ 浮动栏顶对齐 → 导出确认画布 Esc → 「继续探索」→
本机状态 completed；② 刷新回同一步、Tab 顺序返回→跳过→暂停、axe 无 critical/serious（`color-contrast`
交给仓库尺子量 coachmark 与被环套着的卡片）、Esc 暂停后刷新不出现、「更多」菜单继续；③ 拖走第一张图 →
⌘K「重新开始教程」→ 确认 → 图回原位（按页面相对位置判）、欢迎页、最近列表显示「教程项目」不显示数据
目录路径；④ 从别的项目「更多」开始 → 切回原项目 coachmark 消失 → 切回教程自动继续。

### 变异验证记录（Session 21）

**流程**：`scratchpad/mutate21.py`——树不干净拒跑；每条变异 → 跑指定 vitest 文件 → 按**退出码**判 →
`git checkout -- 文件` 还原。

| 变异 | 结果 |
| --- | --- |
| M1 select_text 不看 role | 红 |
| M2 change_typography 的与改成或 | 红 |
| M3 problem.focused 不看 ok | 红 |
| M4 完成时不消费信号 | 红（2 条） |
| M5 export_original 不要求面板关掉 | 红 |
| M6 inTutorial 不看 documentId | 红 |
| M7 离开教程不暂停 | 红（2 条） |
| M8 不在教程里也累计信号 | **存活** → 原用例发的两条信号（属性 / 历史）还有第二道守卫 `editingTutorialPanel()`，切走文档后 `ctx.edit` 为 null 本来就不累计——变异在那两条上不可观测。补「教程外开导出面板确认原图」（没有第二道守卫的那一类），复跑**红** |
| M9 flowVersion 升级不回到未完成步骤 | 红 |
| M10 markStep 不去重 | 红 |
| M11 persistence 为 null 仍写 localStorage | 红（2 条） |
| M12 提示不看 hintSeen | 红 |
| M13 提示不看教程进行中 | 红 |
| M14 落位永远放下方 | 红 |
| M15 coachmark 上的 Esc 不暂停 | 红 |
| M16 锚点在对话框里也 portal 到 body | 红 |
| M17 已解决出口不看检查跑过没有 | 红 |
| M18 open_fast_edit 不看是哪张图 | 红 |
| M19 教程画布不用 document_id | 红（4 条） |
| M20 重置不忘掉本机那格 | 红（反证前先把用例里的陈旧槽位换成**合法**画布——第一版塞的 `{"stale":true}` 不是文档，`readAutosaveDoc` 根本读不回来，变异照样绿） |
| M21 同一项目再点入口也走认领 | 红 |
| M22 对齐信号不看选区里有几张教程图 | 红 |
| M23 activity payload 白名单放进 id | 红 |
| M24 选区没变也发信号 | **存活** → 没人量过这一维。补 `selectionStore.test.ts`，复跑**红** |

**24 条：22 红 + 2 存活各补用例后红。**

### 实跑到的、不是假设的

* **画布上双击面板走的是 `enterElementEdit`，不是 `openFastEdit`**（后者只在素材卡 / 交接 / 拖放）。
  Step 1 的完成条件因此认状态不认那一个 action（T-110）。
* **问题面板的图内问题从渲染后的 manifest 算**；两张图在画布上都会被显示渲染，所以两张图的问题都在——
  第一版 e2e 点了 Fig1 的一条 `font-below-absolute-floor`（8.5 pt 刻度）就完成了 Step 4，教程接着在 Fig1
  的快速编辑里说话。产品上「任一教程面板」都算是对的；e2e 改成点 p2 那条。
* **`display:contents` 的锚点没有盒子**（TypographyControls 的行内 Anchor）：`boxOf()` 并集子节点。
* **画布对象被平移到抽屉后面时 `getBoundingClientRect` 照样有值**：按窗口判 offscreen 是假的，得按
  `[data-canvas-stage]` 的矩形判（T-114）。第一遍 e2e Step 7 就红在这里。
* **coachmark 自己也是 `role=dialog`**：Playwright 的 `getByRole('dialog')` 会把它算进去，导出面板要按
  `:not([data-onboarding-coachmark])` 找。
* **写盘成功后本机 autosave 槽位会被删掉**（`scheduleDiskWrite` 的 then 分支）：拿 `localStorage` 里的
  槽位当「文档内容」判据是假的，e2e 改成按页面相对位置量、单测改成留一份合法的陈旧画布。
* **Radix 把 `data-onboarding-back` 这类布尔属性渲染成 `"true"`**，不是空串。
* **axe 在高亮环下报的 `color-contrast` 落在 `ScriptLibrary` 的「高级详情」summary 上**（2.54:1，
  `text-ink-faint`）——与教程无关的既有问题，记进 STATUS 遗留。
* **assertion 前提错过三次**：back 的目标是上一步不是下一步；`fetchAutosave` 回的就是文档本身；
  重置后那格槽位装的是**新的**干净画布不是空。

## Session 22：Codex / AI 显式刷新、去重、遥测整合、入口与文档

### 新增用例（后端 47；前端 44；真浏览器复跑 3 个 spec）

**`tests/test_ai_refresh.py`（18）**：`refresh_outcome` 五档（skipped / not_wired / ok 只有枚举与布尔 /
RefreshError 带 code / 未预期异常归 refresh_failed）；`_after_ai_change` 七条：三件事各做一次且 watcher 随后
两轮零动作、再改一次照常触发；**pending 里躺着的写入也算没消化**（反证 M3 补）；不 probe 不跑脚本
（RAN.txt 不存在）；watcher 先结算时不再作废、不再发第二份事件；没有 watcher 全做；新脚本进 diff；项目已
关闭 → `project_closed`；注册表坏 → failed 带 code；两个项目互不相干。`ai_bridge.run` 四条（假 CLI 是一段
python）：changed=true 时刷新在 `ai.done` 之前、`ai.done.refresh` 与历史库一致；changed=false 跳过；刷新失败不把
会话记成失败；后端重启后 `get()` 从历史库带回 refresh。端点接线一条（源码判据）。

**`tests/test_telemetry_integrations.py`（12）**：manual 无变化 → `none`、无脚本名 / 路径；codex 新脚本 →
`one`；未知来由归 manual；probe / registry 不记（守的是表的枚举）；刷新失败零事件；桶边界六档；包操作只记
终态、无包名；CONSENT_VERSION ≥ 2 且九条都在表里；`step_id` 枚举与 `stepIds.ts` 逐字同源。

**`tests/test_mcp_server.py`（+16）**：schema + description + 无 `_meta` + instructions；经会话刷新；越界路径拒且注册表
零改动、**越界的不存在路径也是 `path_out_of_scope`**（反证 M13 补）；空 diff（第二轮 baseline=false）；新脚本 +
新素材；readiness 摘要只有六个键；不 probe 不起 worker 不 import；不可达 → local 且文字说「未在运行」；可达 →
open(default=false) → refresh?pj= reason=codex → readiness；可达失败原样带 code；no_project；两项目 →
ambiguous（错误里无路径）+ session_id 只刷一个；结果无绝对路径；reason 固定 codex；无注册表目录。
**`tests/test_mcp_resolver.py`（+1）**：降级 server 对 `tavotto_refresh_project` 回结构化错误。

**前端**：`lib/activityTelemetry.test.ts`（13：十二种映射、桶边界、不从浮动栏不映射、其余十五种 kind 逐种不映射、
payload 只有两键、作用域收回 / 抛出也收回、more、没同意不发、幂等、`readinessStatusBucket`）；
`components/CommandPalette.test.tsx`（7：六条命令中英文齐、两份资源 id 集合一致、不重复、没项目整组不出现、
刷新调统一端点 reason=manual、接入状态 source=palette、英文关键词可搜）；`lib/onboarding/flowTelemetry.test.ts`
（5：完成记 / 跳过不记 / 走完另记 completed / 十个 id 闭集 / 没同意不发）；`store/projectReadinessStore.test.ts`
（+3：banner + mixed、quickedit + all_editable、不带来源 / 报告失败 / 没同意都不记）；`hooks/useServerEvents.test.ts`
（+5：ai.done 不 markStale 且两条事件之间不弹「脚本已更新」、watcher 那条照旧提示、刷新失败单独提示且错误码翻译、
skipped 走普通结局、老后端无字段行为不变）；`lib/onboarding/tutorial.test.ts`（+3：started 记来源与版本无 id、
继续不记、无来源 / 没同意不记）；`canvas/context-bar/multiSelectionBar.test.tsx`（+3：三种按钮各一条无对象 id、
禁用按钮不记 + more、程序调用不算浮动栏）；`store/documentStore.test.ts`（+5：autosave ok、manual、409 →
conflict、restore / keep_main、没同意不发）。

### 变异验证记录（Session 22）

后端 17 条（`scratchpad/mutate_backend.py`，每条变异后只跑点名的用例、`git checkout` 还原、清 `__pycache__`）：

| # | 变异 | 结果 |
| --- | --- | --- |
| M1 | AI 路径不再问 watcher（`fresh = None`） | 红 |
| M2 | `absorb` 不更新快照签名 | 红 |
| M3 | `absorb` 忽略 pending（只比快照） | **存活** → 成因：用例的 watcher 防抖为 0，从没出现「拍进 pending、未结算」这一档；补 `test_a_write_already_pending_in_the_watcher_is_still_fresh`（防抖 0.5 + 假时钟）后红 |
| M4 | 刷新失败记成 ok | 红 |
| M5 | `ai.done` 在刷新之前发 | 红 |
| M6 | 刷新遥测收所有来由（删 app 层守卫） | **存活** → 成因：冗余保证——`EVENTS` 表的枚举本来就丢掉 probe / registry。处置：删掉 app 层那份；改成「刷新成功不记」重跑 → 红 |
| M7 | 桶边界 5→6 | 红 |
| M8 | `panel.file_changed` 丢掉 reason | 红 |
| M9 | 包操作中间态也记 | 红 |
| M10 | MCP reason 透传 | 红 |
| M11 | 可达但失败时退回本地 | 红 |
| M12 | 多项目时挑第一个 | 红 |
| M13 | 越界路径不再先 `check_scope` | **存活** → 成因：`resolve_target` 之后的第二次 `check_scope` 兜着。前一次有独立理由（不泄露范围外路径的存在性），补「越界的不存在路径也是 `path_out_of_scope`」后红 |
| M14 | 结果带绝对路径 | 红 |
| M15 | 客户端表少一条 step id | 红（两侧对拍 + 与 stepIds.ts 对拍双红） |
| M16 | 项目已关闭仍刷新 | 红 |
| M17 | 历史库不写 refresh 列 | 红 |

前端 19 条（`scratchpad/mutate_frontend.py`）：F1–F3 映射来源 / 作用域 / more；F4 **reason=ai 照样弹「脚本已更新」存活**
→ 成因：用例只看 `ai.done` 之后的最终状态，而它本来就会盖掉前一条——补「两条事件之间状态为空」的断言后红；
F5 ai.done 再 markStale、F6 刷新失败当成功、F7 报告没到也记、F8 不带来源默认 panel、F9 跳过也记、F10 走完不记
completed、F11 继续也记 started、F12 没项目也列刷新命令、F13 面板入口来源写错、F14 浮动栏不包作用域、
F15 手动记成 autosave、F16 409 记成 failed、F17 保留主版本不记、F18 选区桶 6→7、F19 零张图也给桶——全部红。

### 实跑到的、不是假设的

- 静态发现只登记看得见 stem 的脚本：`def main(): pass` 不进注册表，三条用例第一遍假红（diff 空 / 桶 none）。
- 假 CLI 用 `sys.executable -c` 改文件即可端到端跑 `ai_bridge.run`：pump 线程、快照、历史库、`ai.done` 全走真代码。
- jsdom 没有 `scrollIntoView`；React 受控输入直接赋 `value` 不触发 onChange。
- `panel.file_changed` 顺手 `refreshAssetsAndSync`：`fetchPanels` 没给 resolved 值 = unhandled rejection，全绿 exit 1。
- `pnpm i18n:extract` 往四个命名空间塞空键、拆复数基键，`i18n:check` 立刻红（`docs/i18n.md` 早有记录）。
- 后台命令继承上一条的 cwd：`pnpm test` 在 worktree 根起步 → `ERR_PNPM_NO_PKG_MANIFEST`。

## Session 23：全量 QA、真实用户流程、性能与发布门禁

### 先跑基线（改动前，本树 5608008f，机器 Apple M4 Pro / 24 GB / macOS 26.6.2）

| 命令 | 结果 | 耗时 |
| --- | --- | --- |
| `ruff check . && ruff format --check .` | ✅ | 0 s |
| `build_mcp_widget.py --check` / `build_browser_playground.py --check` | ✅ 一致 | 1 s |
| `PYTHONPATH=<wt>/src .venv/bin/python -m pytest -q tests --junitxml`（`TAVOTTO_NO_TELEMETRY=1`） | **3840 条：1 failed / 34 skipped**——唯一红是 `tests/native/test_run_cli_integration.py::test_ctrl_c_reaches_the_script_and_leaves_no_orphan`（90 s 内没退出；四路审计扫描并行时负载 13.6）；单跑 3.8 s 绿。它是 STATUS 遗留表里「对机器负载敏感」的那条，本轮多一条证据 | 752 s |
| `cd web && pnpm test` | ✅ 182 文件 / 2496 条 | 15 s |
| `pnpm build` | ✅（主 chunk 1.85 MB / gzip 574 kB，R-17 从 1.57 MB 涨上来） | 6 s |
| `pnpm i18n:check` / `pnpm lint` | ✅ / ✅ 无 error | 1 s |
| `build_frontend.py` + `TAVOTTO_PYTHON=<repo>/.venv/bin/python PYTHONPATH=<wt>/src npx playwright test`（三个 project 全量） | **126 条：2 failed / 1 skipped / 123 passed**——两条红正是 STATUS 登记的 `ux-consistency` 流程 B / D | 1068 s |

### 审计（四路只读扫描 + 本人复核，报告在会话目录）

| 维度 | 结论 | 处置 |
| --- | --- | --- |
| 反序列化 | pickle / marshal / yaml / eval 零命中；`exec` 只在 bridge 子进程跑用户脚本（本职） | — |
| 文档落盘 | layouts / autosave / versions / baked / profiles / 注册表 / 教程 / 导出全部经 `atomicio`；**`POST /api/layouts/<name>` 不调 `validate_document`**、无冲突检测 | 判据已补（T-123）；冲突检测记 #222 |
| 上限 | versions 40/120 条、baked 50、备份 20；**autosave 目录无上限无清理** | #221 |
| 非有限数 | 写侧 `allow_nan=False` 唯一实现；读侧四处裸 `json.loads` | #222（P3） |
| 外部修改 | autosave 内容 hash + 409、旧前端 updatedAt、写回 mtime+sha1+size 三套各管一条路，无静默覆盖 | — |
| 原图写回 / AI 回滚 | 裸 `replace` / `copy2`，无 fsync，有备份（20 份）兜底 | R-05 族，择机 |
| 刷新漏斗 | 六条入口全经 `app.refresh_project`；`registry.changed` / `assets.changed` 只在 `project_refresh.py` 一处发 | — |
| 检查引擎 | `runSpec` / `buildSpec` 只在 `validation.ts` 调；无第二个阈值；问题面板不露 gid | — |
| 导出 | 五个端点全经 `exportreq.normalize` + `exportjob`；对话框顺序与 ADR 0031 §6 逐项一致；**MCP 插件自己那套导出** | #224 |
| 属性写入 | **`ElementBar` 文字分支绕过 `TypographyAdapter`** | 已修（T-126） |
| 遗留物 | console.log / debugger / TODO / `.only` / 杂散文件 / feature flag 零命中 | — |
| 品牌 / i18n | `playground/main.tsx` 三条硬编码产品名与双语分支；`mcp/main.tsx` Splash 三条硬编码中文 | 已修 |
| 打包 | wheel / sdist 四项资源齐；**`canvas_coverage.json` 不在 PyInstaller datas**；发行链桌面冒烟不开教程；README 没提 Windows ARM | 已修（T-125） |
| 包管理 | argv 列表无 shell；spec 按空白整条拒；受保护集合在活环境现算闭包；签名里没有解释器参数 | — |
| 遥测 | 18 条事件三方逐位一致；唯一非枚举值 `target_version` 字符集限界且来自发布源 | privacy.md 措辞收窄 |

### 新增用例（后端 23；前端 7；e2e 修 2）

- `tests/test_ci_qualification.py`（+6）：R-18 的四个判据（发文档本身 / 字符串列表 / 读回是文档 / 没写成算失败）。
- `tests/test_document_persistence.py`（+6）：另存为 round-trip；四种非文档 400；未来 schema 400 且旧文件不动。
- `tests/test_tutorial.py`（+1，改 1）：`canvas_coverage.json` 进 datas；`desktop-tauri.yml` 的冒烟也带 `--tutorial`。
- `tests/test_support_matrix.py`（+1）：矩阵判 windows-arm64 unsupported 时两个 README 必须说出来。
- `tests/test_scientific_text_matrix.py`（新，7）：六项字符 × 六个文字位置 × 预览 manifest / 原图 PDF 文本层 / 原图 PNG / 画布三族 `missing_glyphs` / 画布 PDF+PNG。
- `web/src/canvas/context-bar/elementBar.test.tsx`（新，3）：五件控件在；加粗 / 斜体离散写入落 override；属性页写的值浮动栏当场一致。
- `web/src/hooks/useKeyboardSave.test.tsx`（新，4）：⌘S / Ctrl+S → `runManualSave` 且吃默认动作；输入框里照存；⇧⌘S 另存为；裸 s 不动。
- `web/e2e/ux-consistency.spec.ts`：流程 B 改读「边 × 半区」锚点并先切 Y 页签；流程 D 改成十一分区里的五个 + 「关于与隐私」/「诊断 · 技术详情」。

### 变异反证（全部提交后再做，`git checkout` 还原）

| # | 变异 | 结果 |
| --- | --- | --- |
| R1 | 自动保存又包一层 `{"doc": …}` | 红 |
| R2 | `layout_names` 只认对象 | 红 |
| R3 | `missing_state_checks` 恒空 | 红 |
| R4 | `document_readback` 认包一层的 | 红 |
| E1 | 加粗永远写 `bold` | 红 |
| E2 | 去掉斜体控件 | 红 |
| E3 | 加粗态不读适配器 | 红 |
| P1 | 去掉 datas 那一行 | 红 |
| P2 | desktop-tauri 去掉 `--tutorial` | 红 |
| P3 | README 删掉 ARM 那句 | 红 |
| L1 | 另存为不调 `validate_document` | 5 红 |
| K1 | ⌘S 判 `'x'` | 红 |
| S1 | fonttype 不接管（= 修复前的树） | 矩阵用例 2 红（这就是它第一遍抓到缺陷的样子） |

**这一轮的一次事故**：L1 反证时对未提交的 `app.py` 做 `git checkout`，把处理器改动一起还原了——
「变异前先提交」这条纪律再踩一次；测试与 ADR 改动没丢，重新套上后 275 条绿再提交。

### 真实用户流程 A–N：自动化覆盖映射

映射在会话目录 `scenario-coverage.md`（逐条读过用例体，不按名字匹配）。本轮补上的：A 的 ⌘S 键位、
I 的整套矩阵（两个 mu 并排 / β γ Δ / `°C` 两字形式 / Å / ≤ ≥ / 标注文字 / 四种产物串起来）。
**仍没有自动化的**：B 的三选一关闭对话框（产品里不存在，#223）与真实进程 kill；C 的迁移前逐文档备份
（前端原地迁移；原始 schema 2 文件在用户显式覆盖前不动）；D 的自动保存不进 watcher（autosave 在数据目录，
不在被监视的项目树）；G 的多面板画布导出与裁剪面板导出的像素断言；L 的真 `deviceScaleFactor 1.5`
（用 600 px 视口等价）。逐条打勾见 STATUS 的场景表。

### 故障注入与并发（既有用例，按关键字点数）

磁盘满 / replace 失败 2、损坏 4、冲突 26、watcher 与手动同时 2、SSE 旧响应 1、cancel / partial 8、
内存 6、并发 / 中断 38、教程资源缺失 1、字体 / 字形缺失 15；前端 stale 46 文件、cancel 4、防抖 3、
冲突 6、恢复 23。**没有长固定 sleep 的新用例**；本轮没有新增故障注入（缺口都归到场景表与 issue）。

### 性能（数据在 `docs/perf-baseline.md`「发布终审」）

交错 A/B/C 三方各 3 轮；`scripts/bench_document.py`（新）三档；watcher 空闲真进程 6 采样。
结论：热渲染 +15%（manifest +27%，#220）；导出 +20–35 ms 全部是 fonttype 42；文档层线性；
版本时间线塞满后 547 ms（#221）；空闲 CPU 0.0–0.1%、147 MB、3 线程、无孤儿。

### 打包

`python -m build`（隔离环境装 hatchling）7 s → `tavotto-0.12.0-py3-none-any.whl` 1.50 MB + sdist 4.59 MB；
`scripts/ci/lab_acceptance.py --dist dist`（`TAVOTTO_CI_STATE_ROOT` 指到会话目录）：import / 版本 /
web/index.html / worker / patchspec / profiles / console script / `--help` / `doctor --json` / 端到端冒烟
（启动 → 渲染 → 热渲染 → 导出 → 覆盖导出 → 干净退出）**全部 ✅**；`test_tutorial` 的三条真 wheel 用例
（教程资源集合逐一相等、sdist 同、解开后经 importlib.resources 定位）✅。桌面产物：**本机未验证**
（PyInstaller / Tauri 产物在 CI 桌面腿；本分支的 `--tutorial` 与 datas 改动会在合并队列第一次执行）。

### 评审回合 1（PR #228，Codex 两条 P2，全改）

| 条 | 处置 | 判据 |
| --- | --- | --- |
| `packageStore.onProgress` 空闲标签页认领别人的作业 | 只认本标签页 `run()` 起过的 `job_id`（`startedJobs`） | `PackagesSettings.test` +1（陌生 job_id 在 progress 为空时不显示、不刷清单）；变异「退回只挡不同作业」红 |
| `/api/engine/packages/cancel` 不核作业归属 | cancel 与 job 补拉都按 `job.project == root` 核（与 `/run` 同一判据） | `test_cancel_and_poll_refuse_another_projects_job`；变异「归属恒 False」红 |

两条都是 Session 19 的代码；Codex 定 P2，本轮按「跨项目事件污染」（P0 类）处置。第一遍假红是夹具的
假 `cancel` 对任何 id 都回 True（[[fixture-makes-the-predicate-vacuous]] 同族）。

### 评审回合 2（PR #228 的 `full-ci` 桌面腿：Windows 打包产物上的 Playwright 第一次跑本分支）

`windows-exe-smoke`：99 passed / 4 flaky / 2 failed / 21 skipped（25.8 min）。两条真红都是**竞态**，mac 上稳绿：

| 条 | 现场证据（trace.zip 的 network / 快照） | 处置 | 判据 |
| --- | --- | --- | --- |
| `tutorial.spec` 重新开始教程：画布没恢复原位（差 0.199 = 拖过的那段） | `POST tutorial/reset` 返回前后前端又 `PUT autosave` 两次（派生同步作用在还没换掉的旧文档上），随后 `GET autosave` 200 读回拖过的文档 | `resetTutorial` 先 `suspendAutosaveFor(当前教程画布)`：防抖不排、`flush` 回 skipped；`switchDocument` 换到干净画布即恢复；重置没做成手动接回 | `saveStateMachine.test` +2、`tutorial.test` +2；变异「flush 不看挂起」「reset 不挂起」各红 |
| `quick-menu.spec` 重新构建：90 s 没弹「已按源脚本重新构建」，状态区空 | 首次渲染 3.1 s 还在飞时点了重新构建：`invalidate` 200 → 排队的那次 239 ms 才画完；`settledRender` 只等一次，在飞的结束→排队的开始是同步两次 `patch`，消费者恢复时键仍是 `rendering` → 静默 `failed` | `settledRender` 循环等到真的不在渲染 | `quickEditActions.test` +1（第二次渲染也延后释放）；变异「只等一次」红 |

**这一轮的一次假绿**：第一版用例让排队那次即时返回，jsdom 里 `await settledRender()` 比 `await engineRender()`
多一个 microtask，单次等待也恒绿——变异存活后加临时日志才看出来（[[mutation-may-not-be-a-mutation]] 的反面：
判据没变、是**用例的输入形状**让它量不到）。四条 flaky（playground 两条、settings-shell 窄窗口、教程完整走完）
重试即过，记在 #31 的 Windows 桌面 e2e 稳定性之下。

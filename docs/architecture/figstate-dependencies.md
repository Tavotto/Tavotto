# FigState 与 overrides.py 的依赖清单（PR D 第二步之前）

审计任务书 PR D 的原话：**不要一开始就机械搬整个 `FigState`**：它还涉及 TickLabel、状态恢复
和 resolve 语义。先列出其依赖，再决定是后续迁移还是暂留。第二步才考虑按 artist family 分离
只读事实、能力声明与 setter / restore 实现。这份清单就是那一步「先列」；数字由 `ast` 在
main @ 77f23250 + PR D 第一步（`axestraversal` 已提出）的树上算出。

## FigState 本身很小，但它的字段被十几处外部读写

`overrides.FigState`：66 行、10 个实例属性、3 个方法（`__init__` / `index_ids` / `resolve`）。
它是一个**数据袋** + 一个按 gid 形状现解刻度文字的 `resolve`。

| 属性 | overrides 内读写它的函数数 | 外部模块 |
| --- | ---: | --- |
| `fig` | 3（apply / _refresh_axes_follow / sync_legends） | manifest、figsession、browser、preview_complexity |
| `elements` | 4（apply / _reverse_index / _reindex_legend_children / _alias_colorbar_ticks） | manifest（`instrument` 填充） |
| `index` | 4 | manifest |
| `applied` | 4（apply / _set_ticklabel_text / _text_has_bbox_left / _reindex_legend_children） | manifest、figsession、browser |
| `originals` | 1（apply） | manifest（`marker_original` / `cmap_original`） |
| `pending` | 3（apply / _pending_inverted / _cb_target_rect） | — |
| `alias_seeded` | 1（apply） | — |
| `colorbar_axes` | 1（_refresh_axes_follow） | manifest、preview_complexity |
| `axes_follow` | 1（_refresh_axes_follow） | manifest |
| `unregistered` | 0（manifest.census 填） | manifest |

`resolve` 的依赖：`_TICKLABEL_GID`（正则常量）、`TickLabel`（刻度文字 handler 类，属于刻度
模型那一族）、`axestraversal.ordered_axes`（第一步已提出）。

**结论：`FigState` 暂留 `overrides.py`。** 单独搬它出去只有两条路：把 `resolve` 留在 overrides
（那 FigState 就只是个 dataclass，搬与不搬对环 / 边界都没有影响），或者连 `TickLabel` 一起搬
（那就是刻度模型那一族的迁移，不是 FigState 的）。第一步拆环之后，FigState 对外只剩
`axestraversal` 一条边，它已经不在任何环上。

## overrides.py 的家族分布（5793 行、235 个模块级函数 / 类）

按函数名前缀粗分（判据是名字，不是行为；「其它」里还有按 getter / setter 命名的一大批，
下面的数字只用来定切割顺序，不当精确值用）：

| 族 | 函数 / 类 | 行 | `HANDLERS` 里的条目 |
| --- | ---: | ---: | ---: |
| legend（LegendEntries / rebuild / 位置模型 / sync） | 20 | 506 | 25 |
| apply / 分发（apply / _apply_rank / caps 三表 / _RESTORE） | 6 | 417 | — |
| colorbar（方向 / 延伸 / 大小 / 随行 / ColorbarProxy） | 19 | 376 | 15 |
| axes / figure / 3d / arrow（几何 / 图幅 / 三维 / 箭头 / 钉住的布局引擎） | 16 | 331 | 44 + 4 + 12 |
| tick（tick_cfg / apply_tick_model / TickSet / TickLabel） | 22 | 281 | 19 + 2 |
| text / font / CJK | 6 | 73 | 20 |
| spine（spine_cfg / apply_spine_model） | 7 | 52 | 2 |
| marker / scatter / series | 5 | 48 | 11 + 6 + 1 |
| image | — | — | 10 |
| 未归族（getter / setter / caps 判据 / 工具） | 134 | 1911 | — |
| `HANDLERS` 表本体 | — | 443 | 172 条 |

`HANDLERS[(family, prop)] = (getter, setter, …)` 是分发的唯一入口；`_COLLECTION_CAPS` /
`_PATCH_CAPS` / `_GENERIC_CAPS` 经 `_install_caps`（setdefault）按 family 注册。
「只读事实」（manifest 侧的 `marker_current` / `cmap_current` / `spines` / `face` 之类）今天
住在 `manifest.py`，「能力声明」住在这三张表 + `collection_caps()`，「setter / restore」住在
`HANDLERS` 与 `_RESTORE`——三样东西按**家族**是交叉的，按**文件**是分开的。

## 第二步的切割顺序（建议）

每一刀的判据相同：搬出去的模块**只依赖标准库 + matplotlib + axestraversal**，不 import
`overrides` 也不 import `manifest`；`overrides.HANDLERS` 只登记它导出的 getter / setter；
`test_axes_traversal_authority` 的扫描面加上它；bridge / playground / spec 四张登记表各加一项
（`test_bridge_namespace` / `test_playground_build` / `test_runtime_build` 会替你数）。

1. **spine**（52 行、2 条 handler）——最小的一族，用来验证这条切割配方本身：`spine_cfg` /
   `apply_spine_model` + 两条 handler。
2. **tick**（281 行）——`tick_cfg` / `apply_tick_model` / `TickSet` / `TickLabel` /
   `invalidate_tick_cfg`。搬完 `FigState.resolve` 从这里 import `TickLabel`，那时 FigState
   才值得考虑单独成文件。
   **已切（2026-09-18，`engine/tickmodel.py`）**：按名字前缀数的 281 行只是刻度模型那一段；
   真正的族还包括刻度标签记忆表与「画着的刻度」判据（`ticklabel_memo` / `_ticklabels` /
   `_keep_projected_ticks` / `drawn_tick_label_entries`）、两个伪元素类、四边开关（`_mk_tick_side`，
   key 在 "axes" 族名下）、单条文字冻结（`_freeze_tick_texts` / `_set_ticklabel_text`）与 gid 形状
   `TICKLABEL_GID`——实际搬出约 800 行（overrides 5636 → 4838）。`_set_ticklabel_text` 对 FigState
   的依赖收成 `tickmodel.EditState` 协议（`.applied` + `.resolve`），族模块不 import overrides；
   `("ticks", "fontfamily")` 属字体族，留在 overrides。FigState 现在从 tickmodel 取 `TickLabel` 与
   `TICKLABEL_GID`，它自己仍留在 overrides（理由同上：搬它等于搬一个 dataclass）。
3. **colorbar**（376 行）——`_cb_reorient` / extend / `_cb_release_aspect` / `ColorbarProxy` /
   `colorbar_maps` / `follow_map`。它与 axes 几何耦合（`_set_axes_position` 落到色条轴时要
   `_cb_release_aspect`），切的时候 axes 那一侧留一个调用点。
   **已切（2026-09-18，`engine/colorbarmodel.py`）**：实际搬出约 640 行（overrides 4838 → 4194）——
   连 `_refresh_axes_follow` / `coincident_shared_axes_pairs` 一起（它们只读 FigState 的
   `fig` / `pending` / `colorbar_axes` / `axes_follow`，收成 `FollowState` 协议）。axes 那一侧留的两个
   调用点是 `overrides._set_axes_position → colorbarmodel._cb_release_aspect` 与
   `_restore_axes_position → _cb_restore_aspect`。`colorbar_mapping_is_live` 依赖 `color_mapping_is_live`
   （映射 / 能力那一族），`_alias_colorbar_mappable` / `_alias_colorbar_ticks` 属别名分发，三者留在 overrides。
   这一刀让族模块第一次依赖另一族（翻方向要 `tickmodel.invalidate_tick_cfg`）：配方门禁改成
   「只许 import `FAMILIES` 里排在自己前面的族」，顺序即分层。
4. **legend**（506 行）——最大的一族，也最独立（ADR 0034 已经把它模型化）。
   **已切（2026-09-18，`engine/legendmodel.py`）**：实际搬出约 970 行（overrides 4194 → 3263），连 `_entry_*` /
   `_handle_read|write` / 指纹与几何助手一起——它们全只被图例用。对外只剩两条边：`_frac_to_display`
   （text pos / arrow endpoints 也用）搬进基座 `pathgeom.frac_to_display`；重建后的重放要查 `HANDLERS`，
   收成 `FigState.reapply(artist, prop, value)`（分发留在 overrides，族模块经 `RebuildState` 协议只提要求）。
   `legend_handle_props`（依赖 `color_mapping_is_live`）与 `legend_text` 那组镜像登记（分发层接线）留在
   overrides。配方门禁新增一条：族模块导出了 `RESTORE`，overrides 必须有 `_RESTORE.update(<module>.RESTORE)`
   ——这一刀就漏过一次，靠 `_RESTORE` 键数对拍（75 → 72）抓到。
5. `apply` / `_apply_rank` / caps 三表留在 `overrides.py`，它就是分发层；到那时 overrides
   只剩分发 + 几何（axes / figure / 3d / arrow）+ 未归族的 getter / setter。
   **四刀切完（2026-09-18）**：overrides.py 5793 → 3263 行；HANDLERS 248 键的顺序、_RESTORE 75 键、
   `_needs_state` 标记四刀各验一次逐字节相同。

不建议一次做完：每一刀都要过 `test_invariants_engine` / `test_equivalence_matrix`（热态 ==
全量重放）、家族自己的用例、以及 bridge 的两张装载表；一刀一个 PR，与任务书「不混 PR」
一致。

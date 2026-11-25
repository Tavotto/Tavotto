# 往组图排版上加标注（箭头、文字、显著性括号、连线、外框）

用户想在**项目里的一份排版**（`tavottofile/<名>.json`，桌面「排版」里那份组图）上加矢量标注时用这四个工具。
单张图里改字号、颜色、位置走 `tavotto_apply_overrides`，不走这里。

## 四步

1. `tavotto_layout_scene`：读场景。回每张子图（短号 `P1`、`P2`…）、坐标轴（`gid`、毫米框、范围）、元素（`gid`、角色、显示名）、
   空白区（`free`）、图里的字体 / 字号 / 线宽，以及 `base_revision` 与 `scene.fingerprint`。`unmeasured` 里的子图只有外框。
2. `tavotto_layout_annotate_plan`：用**描述**（不是坐标）说要加什么，回核对结果、**复述**（`echo`）和一张局部图。不写盘。
3. 拿复述逐条对照用户的原话。对不上就改描述，回到第 2 步。
4. `tavotto_layout_annotate_apply`：`items` 与第 2 步**一字不差**，带回 `echo_digest`。写完把复述原话告诉用户，并说明可以撤销。
   用户要撤销：`tavotto_layout_annotate_undo`（带 `undo_token`）。

## 描述怎么写

每个元素 `{kind, name, …}`，`name` 在这一批里唯一（别的元素用 `item` 锚点引用它）。位置一律写锚点：

```json
{"anchor": {"data":    {"panel": "P1", "axes": "axes_0", "x": 8, "y": 0.6}}, "offset_mm": [0, -2]}
{"anchor": {"element": {"panel": "P1", "gid": "axes_0.lines_1", "point": "n"}}}
{"anchor": {"panel":   {"id": "P2", "point": "nw"}}, "offset_mm": [1, 1]}
{"anchor": {"item":    {"name": "peak_arrow", "point": "e"}}}
```

方位点：`nw n ne w center e sw s se`。`data` 的坐标是数据坐标，引擎从这次导出实测换算，别自己算毫米。

| kind | 键 |
| --- | --- |
| `arrow` | `from`、`to`；`head`（`end` / `both` / `none`）、`headEnd`（`triangle` / `open` / `bar` / `dot` / `diamond` / `stealth`）、`strokePt`、`color` |
| `text` | `text`、`at`、`attach`（框上哪个方位点对到 `at`，缺省 `w`）、`sizePt`、`bold`、`italic`、`fontFamily`、`color`、`provenance` |
| `shape` | `shape`（`ellipse` / `rect`，缺省 `ellipse`）、`at`、`size_mm`、`attach`、`fill`、`strokePt`、`color` |
| `bracket` | `between`（两个位置）、`side`（`above` / `below`，缺省 `above`）、`gap_mm`、`tick_mm` |
| `connector` | `from`、`to`、`route`（`straight` / `elbow-h` / `elbow-v`）、`headEnd` |
| `box` | `fit`（本批里要框住的元素名）、`padding_mm`、`dash`、`cornerRadius` |

写错的键不会被悄悄丢掉：整批回 `bad_item`。字号、字体不写就照图里的。故意压在数据上的标注写 `over_data: true`。

## 必守

- **认不准就问**：用户说的「那条线」「处理组」对应场景里不止一个元素，或一个都对不上时，把候选（显示名 + 子图）列给用户选，**不要自己挑**。
- **复述里 `intent: "unnamed"`**：锚到的元素在脚本里没有 `label=`，复述只说得出 `gid`。告诉用户「这个箭头指向的是 P1 里的第 2 条曲线（脚本没给它起名字）」，让用户确认。
- **统计标注不编**：星号、n.s.、p 值、n 只能来自用户的话（`provenance: "user"`）或数据里算出来的结果（`"data"`）；不写来源整批回 `needs_confirmation`，先问用户。
- `failed` 非空不能写入；`unverified` 是量不了的项（比如那张子图没有脚本），写入后照实告诉用户这几项没核。
- 写入只追加，不改用户已有的东西。桌面正开着这份排版时，用户下一次保存会看到「别处有改动」。

## 出错时

| code | 怎么办 |
| --- | --- |
| `layout_changed` | 排版在这期间被改过：重新 `tavotto_layout_scene`，用新的 `base_revision` 重出方案 |
| `scene_stale` | 子图或它的图变了：同上 |
| `echo_mismatch` | 要写的描述不是出方案时那一份：重新出方案、重新对照用户原话 |
| `anchor_unresolvable` | 锚点量不到（`detail.reason`：子图没量、元素没有墨迹几何、数据点不在坐标轴范围里……）：换一个锚点 |
| `needs_confirmation` | 统计文字没写来源：问用户 |
| `undo_unavailable` | 撤销副本存不下（数据目录只读 / 磁盘满），这次没写：告诉用户，腾出空间后重试 |
| `undo_stale` | 写入之后排版又被改过，不能一步撤销：告诉用户，在桌面里手动删 |
| `layout_annotate_unavailable` | 这个 Tavotto 版本没有这项能力：请用户升级后新开会话 |

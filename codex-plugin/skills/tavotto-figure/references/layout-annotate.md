# 往组图排版上加标注、改已有的矢量对象

用户想在**项目里的一份排版**（`tavottofile/<名>.json`，桌面「排版」里那份组图）上加矢量标注（箭头、文字、显著性括号、
连线、外框），或改画布上已有的文字 / 箭头 / 形状 / 路径（含用户手画的，见文末「改已有的」）时用这里的工具。
单张图里由脚本画出的元素（曲线、柱子、坐标轴文字）走 `tavotto_apply_overrides`，不走这里。

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
- 加标注只追加，不改用户已有的东西。桌面正开着这份排版时会自动并进去（一步可撤销），用户不用处理。

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
| `bad_select` | `tavotto_layout_scene` 的 `select` 写错了（`detail.field` 指出哪一项）：改了重读 |

## 改已有的（`tavotto_layout_edit_plan` → `tavotto_layout_edit_apply`）

同样三步：`tavotto_layout_scene` 读场景（`vector_objects` 列出能改的对象：id、类型、名字、文字、毫米框、是不是 Agent 加的、
锁定 / 隐藏、所在组；`select` 筛，如 `{"types": ["arrow"], "anchored_to": "P1"}`）→ `tavotto_layout_edit_plan` 出方案 →
对照复述 → `tavotto_layout_edit_apply`（`ops` 一字不差 + `echo_digest`）。撤销同样用 `tavotto_layout_annotate_undo`。

`ops` 是一列操作，按顺序执行、整批成或整批不动。服务端重算，**不要自己算坐标**：

```json
[{"op": "set", "id": "a_x1", "set": {"strokePt": 0.75, "color": "#c0392b"}},
 {"op": "move", "id": "a_x1", "to": {"anchor": {"data": {"panel": "P1", "axes": "axes_0", "x": 8, "y": 0.6}}}, "attach": "to"},
 {"op": "align", "ids": ["a_x1", "a_x2", "a_x3"], "mode": "left"},
 {"op": "pathfinder", "ids": ["p_c1", "p_c2"], "mode": "unite"}]
```

| op | 键 |
| --- | --- |
| `set` | `id`、`set`（字段表里这一类的键 + `x y w h rotationDeg name locked hidden`；值有上下限） |
| `move` | `id` + `by_mm: [dx, dy]`，或 `to`（锚点，同上）+ `attach`（方位点；箭头可写 `from` / `to` = 把箭尾 / 箭头尖对过去） |
| `delete` | `id` |
| `align` | `ids`、`mode`（`left hcenter right top vcenter bottom`）、`to`（缺省 `"selection"`；`"page"`、`{"key": id}`、`{"panel": "P1"}`） |
| `distribute` | `ids`（≥ 3；给 `gap_mm` 时 ≥ 2）、`axis`（`h` / `v`）、`gap_mm` |
| `order` | `id`、`to`（`front back forward backward`） |
| `group` / `ungroup` | `ids` + `name` / `group`（组 id，取 `vector_objects` 的 `group`） |
| `rename_group` | `group`（组 id，取 `vector_objects` 的 `group`）、`name`（空串 = 去掉名字）：同组每个成员一起写；场景里 `group_names` 是 `{组 id: 名字}`；`group` 的 `name` 也真存下来 |
| `rotate` | `id`、`deg`（绝对角度，顺时针为正，绕框中心） |
| `pathfinder` | `ids`、`mode`（`unite minus_front minus_back intersect exclude divide trim merge crop`） |
| `offset` / `outline_stroke` / `simplify` | `id` + `delta_mm`、`join` / 无 / `tolerance_pt` |
| `convert_to_path` | `ids`（形状 / 箭头 → 同 id 的路径，画面不动） |
| `move_anchors` | `id`、`anchors`（`[{sp, i}]`）、`by_mm: [dx, dy]`（页面上的位移，旋转过的路径服务端换算）；锚点连同两侧手柄一起动 |
| `move_handle` | `id`、`anchor`（`{sp, i}`）、`which`（`in` / `out`）、`to_mm: [x, y]`（页面上的点）、`break_smooth`（缺省 false：平滑点另一侧手柄保持共线；true：只动这一侧、锚点变尖角） |
| `add_anchors` | `id` + `anchors`（在每个锚点**后面那一段**的中点加）；都不给 = 每一段的中点；或 `at: {sp, seg, t}`（第 sp 条子路径第 seg 段、0 < t < 1 处）。曲线一点不变 |
| `delete_anchors` / `average_anchors` | `id`、`anchors`（平均要 ≥ 2 个）。删到闭合子路径不足 3 个会变开放；子路径不足 2 个整条去掉；删光 = 路径一起删 |
| `convert_anchors` | `id`、`anchors`、`to`（`smooth` / `corner`；缺省 = 桌面的「切换」：选中的全是平滑点 → 尖角，否则 → 平滑） |
| `cut_path` | `id`、`anchors`：在这些锚点处断开；相邻两个都选 = 剪掉它们之间那段。第一段留原对象，其余各成新路径（样式照搬、没有名字、紧挨着往上放，复述里写新 id） |
| `join_paths` | `id` + `anchors: [甲, 乙]`（同一对象里两个**端点**：同一条子路径的两端 = 闭合，两条子路径的端点 = 连成一条）；或 `ids: [甲, 乙]`（两个路径对象取最近的一对开放端点合成一个，**留最后一个**的 id 与样式、删另一个）。两端重合合成一个锚点，否则补直线 |
| `reverse_path` | `id`：起点 / 终点互换，端型字段不跟着换 |
| `make_compound` / `release_compound` | `ids`（≥ 2，子路径并进最靠后的那个、奇偶填充）/ `id`（≥ 2 条子路径拆成各自的对象，第一条留原 id） |

**路径锚点的写法**（只收 `path`；形状先 `convert_to_path`）：

- 场景里每个路径带 `subpaths: [{sp, closed, count}]`，锚点不多时还带 `anchors: [{sp, i, at_mm, smooth, in_mm?, out_mm?}]`
  （页面毫米坐标、已按旋转换算）。锚点太多的路径只有计数，清单有上限（每个路径 60、整次 400；`select: {ids: [...]}` 点名读时放宽到 200），
  回包里的 `anchors_note` 会说。**不要自己猜锚点下标**：没清单就先点名读，或让用户在桌面里处理。
- 锚点地址 `{sp, i}` 指的是**这一条操作执行时**的状态：同一批里前面的加 / 删 / 剪 / 连会让后面的下标变。每条复述末尾写着「现为
  sp0 闭合 8 个；sp1 开放 3 个」，加锚点还列出新锚点的地址；一批里要连着改，就照这个往下数，或分两批（先出方案、写入，再读场景）。
- 位移与手柄目标是**页面上的坐标**（不是对象内的比例）；服务端按对象的旋转换算，包围盒随曲线实际范围重算，画面上没点名的锚点不动。
- 错误：`bad_value` 的 `field` 是 `anchors`（写法不对）/ `anchors:unknown`（地址不存在）/ `anchors:duplicate`（重复）/
  `anchors:not_end`（`join_paths` 点的不是端点）/ `at`、`at:unknown`；`bad_op` 的 `field` 是 `no_open_ends`（两个对象都没有开放端点）、
  `not_compound`（释放的不是复合路径）、`empty_result`；`out_of_range` 的 `field` 是 `x y w h subpaths nodes`（改完超出范围）。

- **能改用户手画的**，但每次都复述、都能一步撤销；复述逐条是「谁：字段 前 → 后」，原话念给用户。
- 桌面正开着这份排版时会按字段自动并进去（一步可撤销）；用户在桌面里也改了同一处（同一对象的同一字段，或改了你删的）才会在保存时让用户选。
- 认不准是哪个对象（「那支箭头」有三支）就列候选（名字 + 位置）给用户选。
- 只核这次动到的对象；`failed` 非空不能写入。拆组 / 删除让布局组成员不足 2 个时，约束一并去掉，复述里会说。
- 布尔 / 偏移收路径与封闭形状（矩形、椭圆、多边形…会先转为路径，结果是路径），轮廓化另收直线 / 弧线 / 箭头，简化只收
  `path`（形状先 `convert_to_path`）；回 `kernel_unavailable` = 这台机器的几何内核没装上，其余操作照常，告诉用户这一步可在桌面里做。
- 方案没出来时 `errors` 是 `[{code, index, field}]`（`index` 从 0 数）：`bad_op` 写法不对、`unknown_object` id 不在这张画布、
  `not_vector` 点名了子图 / 图片、`bad_field` 这一类没有这个字段、`bad_value` / `out_of_range` 值不对。

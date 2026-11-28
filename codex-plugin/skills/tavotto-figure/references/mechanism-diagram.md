# 画机理示意图：受体在细胞膜上（示意图框 + 膜 + 受体）

用户要在图旁边画「受体插在细胞膜上」「配体结合 → 二聚化 → 胞内磷酸化」这类**最小机理图**时读这里。写入入口和改已有对象一样：
`tavotto_layout_scene` → `tavotto_layout_edit_plan`（`ops` 里放一个 `add`）→ 对照复述 → `tavotto_layout_edit_apply`。

## 免费与高级版

- **示意图框（`frame`）、箭头、文字、形状、括号都免费**。
- **膜（`membrane`）和受体（`receptor_rtk`）这类语义图元是高级版**。没有授权时，只要这一批里有一个图元，整批回结构化
  `{ok: false, code: "pro_required", message, learn_more}`，**一条也不写**。照实告诉用户这是高级版功能、给 `learn_more` 的链接，
  **不要**退回去用一堆椭圆和线手画一个「差不多的膜」。用户明说只要免费的，就只画 `frame` + 箭头 + 文字。
- 回 `pro_unavailable` = 这台机器上的高级版组件不在、太旧或回包不对：请用户在 Tavotto 里更新高级版组件。

## 先读图元清单

图元有几十种（膜蛋白、可溶分子、核酸、细胞器、细胞骨架……），**不要凭记忆写种类名和参数**：先调 `tavotto_mechanism_catalog`（只读、免费，不用授权）。
它列出每个图元的 `kind`、中英名、家族、`embeddable`（能不能嵌进膜）、端口名、默认尺寸与参数规格（范围 / 缺省 / 枚举）；可用 `family` 筛一个家族。
用户说「画个 GPCR / 线粒体 / 抗体」，先在清单里找对应的 `kind`；找不到合适的就老实告诉用户现在没有这个图元，不要拿别的图元硬凑。
清单回 `pro_unavailable` = 高级版组件没装好或太旧：照实说，示意图框 / 箭头 / 文字这些免费条目照常可画。
只有 `embeddable: true` 的图元能写 `embed`；其余图元用 `at_mm` 摆在示意图框里。

## 一个 `add` 里写什么

同一个 `add` 最多 24 条（含机理图条目时）。互相引用的条目必须写在**同一个** `add` 里。顺序：框 → 膜 → 受体 → 免费条目。

```json
[{"op": "add", "items": [
  {"kind": "frame", "name": "frm", "title": "EGFR 信号", "at_mm": [5, 18], "size_mm": [72, 50]},
  {"kind": "glyph", "name": "mem", "glyph": "membrane", "frame": "frm", "block": "细胞膜",
   "at_mm": [12, 45], "identity": "质膜", "params": {"length_mm": 58}},
  {"kind": "glyph", "name": "r1", "glyph": "receptor_rtk", "frame": "frm", "block": "细胞膜",
   "embed": {"membrane": "mem"}, "identity": "EGFR", "params": {"dimer": false}},
  {"kind": "glyph", "name": "r2", "glyph": "receptor_rtk", "frame": "frm", "block": "细胞膜",
   "embed": {"membrane": "mem"}, "identity": "EGFR", "params": {"dimer": false}},
  {"kind": "shape", "name": "lig1", "shape": "ellipse", "frame": "frm", "size_mm": [4, 4], "attach": "center",
   "at": {"anchor": {"item": {"name": "r1", "point": "n"}}, "offset_mm": [0, -8]}},
  {"kind": "arrow", "name": "bind1", "frame": "frm",
   "from": {"anchor": {"item": {"name": "lig1", "point": "s"}}},
   "to": {"anchor": {"item": {"name": "r1", "point": "n"}}}},
  {"kind": "text", "name": "t_lig1", "text": "EGF", "sizePt": 7, "attach": "w", "frame": "frm",
   "at": {"anchor": {"item": {"name": "lig1", "point": "e"}}, "offset_mm": [1.5, 0]}},
  {"kind": "arrow", "name": "dimerize", "frame": "frm",
   "from": {"anchor": {"item": {"name": "r1", "point": "ne"}}, "offset_mm": [0, 1]},
   "to": {"anchor": {"item": {"name": "r2", "point": "nw"}}, "offset_mm": [0, 1]}},
  {"kind": "text", "name": "t_phos", "text": "胞内磷酸化", "sizePt": 7, "attach": "n", "frame": "frm",
   "at": {"anchor": {"item": {"name": "r1", "point": "s"}}, "offset_mm": [0, 1]}}
]}]
```

- **框**：`at_mm` 左上角、`size_mm` 宽高（画布毫米）。把后面所有条目都放进去（`frame: "frm"`），框要比内容大一圈。
- **膜**：`at_mm` 是膜左上角；`params.length_mm`（弦长 20–60）定长度，`curvature`（−0.35–0.35）让它弯。**膜要比所有受体加起来宽**，每个受体两侧至少留 2 个脂质间距。
- **受体嵌进膜**：写 `embed: {"membrane": "<膜的 name>"}`，**不要写 `at_mm`**。没给 `embed.at` 就按各自实际宽度沿膜等空隙排；
  引擎把受体的跨膜段对到膜中线、弯膜上沿法线转、膜给它让位。受体不许超出膜、不许互相交叠，否则整批回 `bad_item`。
  `dimer: true`（缺省）是一个二聚体，`false` 是单体；要画「二聚化」这一步就用两个单体加一支箭头。
- **免费条目贴着图元放**：位置写 `{"anchor": {"item": {"name": 框或图元的 name, "point": nw|n|ne|w|center|e|sw|s|se}}, "offset_mm": [dx, dy]}`，
  **别自己算毫米**——受体放在哪是引擎算出来的，只有放好之后才知道。
- **文字不能压在别的对象上**（回 `checks_failed` / `text` 项）：标签放在图元外侧、离它 1–3 mm；`P` 这类小标记当文字放在受体下方，不要再叠一个圆。
- 名字（`name`）在这批里唯一；不要和框 / 图元重名。显示名由引擎按「角色 + 身份」生成（`identity` 写「EGFR」「质膜」这类短名），落地后每个东西可单独选中、改色、拆组。

## 出错时

错误是 `[{code, index, field}]`，**`field` 会点名哪个条目、哪个参数、允许什么范围**（如 `mem: param length_mm range 20.0..60.0`），照它改，不要猜。

| code / field | 怎么办 |
| --- | --- |
| `pro_required` | 没有高级版：照实说、给链接，不要手画替代 |
| `bad_item`：`param X range a..b` / `param X unknown (allowed: …)` / `glyph unknown (known: …)` | 参数越界 / 不认 / 图元种类不对：按提示改 |
| `bad_item`：`no room on the membrane` / `overlaps …` / `outside the membrane` | 膜太短放不下：加长 `length_mm`（≤ 60）或少放一个受体；弯膜上两个受体交叠时也会回它 |
| `bad_op`：`items: at most N per add` | 一次写太多：删掉不必要的标签，或拆成两次（拆开后后一次不能再引用前一次的名字，改用位置锚点） |
| `unknown_ref` | `item` 锚点指到的名字不在这个 `add` 里 |
| `duplicate_name` | 条目重名 |
| `not_editable` | 落地后不够好改（一个东西被拆成几条、分组 / 命名不合规矩）：按 `field` 里的核对项改 |
| `checks_failed` | 免费条目的核对没过（文字压到别的对象、箭头悬空……）：看 `failed` 里的 `check` 与 `with` 挪开 |

写入后把复述原话念给用户，并说明可以一步撤销（`tavotto_layout_annotate_undo`）。

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

## 生物学名词 → 图元速查

用户的说法先在这张表里找；表里没有的名词用 `tavotto_mechanism_catalog` 的 `search` 搜（每个图元带中英关键词 / 同义词）。**推荐参数**是第一次就能画出像样图的取值，不写就取缺省；
超范围的参数会回 `bad_item` 并点名范围。嵌膜的图元写 `embed`，其余用 `at_mm` 摆。

| 用户说的名词 | `glyph`（kind） | 推荐参数 |
| --- | --- | --- |
| 细胞膜、质膜、磷脂双层、脂双层 | `membrane` | `length_mm` 40–58（放几个受体就加长）；要弯成细胞表面 `curvature` 0.2 |
| GPCR、G 蛋白偶联受体、肾上腺素受体、视紫红质 | `gpcr` | 嵌膜；占宽约 6 mm，膜至少 30；`tails: false` 去掉 N / C 端尾 |
| 整合素、黏附受体 | `integrin` | 嵌膜；`leg_len` 6，`splay_deg` 20（激活态张大到 35） |
| 离子通道、钾通道、钠通道、钙通道 | `ion_channel` | 嵌膜；`is_open` 开 / 关，`per_side` 2–3；它是阵列对象 |
| EGFR、受体酪氨酸激酶、胰岛素受体、生长因子受体 | `receptor_rtk` | 嵌膜；`dimer: true` 画二聚体，`false` 画单体（要画「二聚化」这一步用两个单体） |
| TLR、细胞因子受体、TCR、单次跨膜受体 | `receptor_single` | 嵌膜；默认即可，`ecd_height_mm` 调胞外域高度 |
| 转运体、Na⁺/K⁺ 泵、葡萄糖转运体、载体蛋白 | `transporter` | 嵌膜；`outward` 朝外 / 朝内开放，`substrate` 显示底物（Na⁺/K⁺ 泵画两个，方向相反） |
| 抗体、IgG、免疫球蛋白 | `antibody` | `height_mm` 12；`arm_deg` 40 |
| 酶、蛋白酶、催化酶 | `enzyme` | `size_mm` 8；`pocket_depth` 0.35；底物放在端口 `pocket` |
| G 蛋白、Gαβγ、异源三聚体 G 蛋白 | `g_protein` | `scale` 1；贴在 GPCR 胞内侧，用端口 `receptor_site` |
| 钙离子、Ca²⁺、Na⁺、K⁺ | `ion` | `charge`: `plus` / `plus2`（Ca²⁺）/ `minus` / `minus2`；`radius_mm` 1.1 |
| 激酶、Src、PKA、MAPK | `kinase` | `size_mm` 6–8；ATP 位在端口 `atp_site` |
| 配体、EGF、激素、神经递质 | `ligand` | `shape: blob`（默认）/ `wedge` / `diamond` / `square`，`radius_mm` 1.5–2.5；要结合尾 `tail: true` |
| 磷酸基团、磷酸化、P | `phosphate` | `radius_mm` 0.8；「P」文字由你放在端口 `label` 旁 |
| 小分子、药物、抑制剂、苯环 | `small_molecule` | `size_mm` 4；`aromatic`、`substituent` 可关 |
| 泛素、Ub、泛素化 | `ubiquitin` | `units` 1 是单泛素，4 是多聚链；`tail: true`（单个时必须带尾） |
| 蛋白复合体、复合物、受体复合物 | `complex` | `subunits` 3–8，`size_mm` 6 |
| 蛋白酶体、26S 蛋白酶体 | `proteasome` | `height_mm` 16；`caps: true` 带 19S 帽（26S） |
| 核糖体、翻译机器 | `ribosome` | `size_mm` 9 |
| 转录因子、TF、p53 | `transcription_factor` | `dna_length_mm` 18；`tf_size_mm` 4.5 |
| 染色体、染色单体、姐妹染色单体 | `chromosome` | `length_mm` 24；`spread_deg` 16 |
| DNA、双螺旋、基因、启动子 | `dna_helix` | `turns` 3；`length_mm` 30 |
| mRNA、信使 RNA、转录本 | `mrna` | `length_mm` 40；`cap`、`polya` 可关 |
| 核小体、组蛋白、染色质 | `nucleosome` | `core_r_mm` 4 |
| 中心体、中心粒 | `centrosome` | `size_mm` 10；`nine_fold` 显示端面九点 |
| 内质网、ER、粗面内质网 | `er` | `radius_mm` 12；`sheets` 3；`ribosomes: true` 画粗面内质网；它是阵列对象 |
| 高尔基体、高尔基复合体 | `golgi` | `width_mm` 18；`cisternae` 4；它是阵列对象 |
| 脂滴、脂质小滴 | `lipid_droplet` | `size_mm` 10；它是阵列对象 |
| 溶酶体、自噬溶酶体 | `lysosome` | `size_mm` 8；`enzymes` 6 |
| 线粒体、嵴、能量工厂 | `mitochondrion` | `size_mm` 22；`cristae` 5 |
| 细胞核、核膜、核孔 | `nucleus` | `size_mm` 24；`pores` 8 |
| 囊泡、分泌小泡、内体 | `vesicle` | `size_mm` 6；`cargo` 显示货点；它是阵列对象 |
| 肌动蛋白丝、微丝、F-actin | `actin_filament` | `length_mm` 24；`turns` 3 |
| 微管、微管蛋白 | `microtubule` | `length_mm` 30；`subunits` 14；它是阵列对象 |
| 细菌、大肠杆菌、鞭毛 | `bacterium` | `length_mm` 24，`width_mm` 8（长要比宽大 3 以上）；`flagellum` 鞭毛 |
| 衣壳、无包膜病毒、噬菌体头部 | `capsid` | `size_mm` 10 |
| 细胞、细胞轮廓 | `cell` | `width_mm` 36，`height_mm` 24；`membrane_line` 带细胞膜内线 |
| 突触、神经递质释放 | `synapse` | `width_mm` 20；`vesicles` 4；`receptors` 受体点；它是阵列对象 |
| 紧密连接、上皮屏障、claudin | `tight_junction` | `height_mm` 20；`strands` 3 |
| 病毒、包膜病毒、HIV、冠状病毒 | `virus` | `size_mm` 14；`spikes` 12；`capsid: false` 去内部衣壳 |

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

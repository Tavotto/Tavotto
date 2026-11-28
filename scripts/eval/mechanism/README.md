# 机理图评测：Agent 画「受体在膜上」的最小机理图（ADR 0220 第 6.D 期）

沿用 `scripts/eval/vector_edit/` 的约定：题库 `corpus.json`（`build_corpus.py` 生成，改题改生成脚本再重新生成，单测核对一致）、
判据 `predicates.py`（纯标准库、只判结果不判用了哪些条目）、结果每次运行一行 JSONL。不进 CI（单测只验题库与判据，见 `tests/test_mechanism_eval_corpus.py`），不在 PR 里跑。

- **题库**：6 题，每题一块 150 × 100 mm 画布（只有一个用户写的小标签，用来核「原有对象原样保留」）+ 一句中文指令 + 判据 + 标准答案批（`reference`，一个 `add` 的 `items`）。
  单个受体 / 配体结合 / 三步最小机理图（配体结合 → 二聚化 → 胞内磷酸化）/ 并排两个二聚体 / 弯膜上两个受体 / 对照（没有高级版授权，要整批 `pro_required`、画布不动）。
- **成功判据**（全部通过才算成功）：落地成功（方案 ok，含引擎自己的可编辑性核对与免费条目的核对）· 数量够（框 / 膜 / 蛋白 / 箭头 / 文字）· 蛋白在膜内（竖直方向横跨膜、水平中心在膜范围内）·
  蛋白真实轮廓两两不交叠（按旋转后的真轮廓，不是外框）· 全部在示意图框内 · 要求的文字都在 · **标签与目标相邻**（离最近的非文字对象 ≤ 8 mm、文字中心不压在膜 / 蛋白轮廓里）·
  箭头尖落在对象上（≤ 3 mm）· 原有对象原样保留。
- **高级版组件用本地真组件**：`EVAL_PRO_SRC`（缺省 `../tavotto-pro-wt-mech`）直接 import `tavotto_pro.mechanism`，授权在进程里置真；不安装、不发布、不碰任何用户配置。

## 跑法

```sh
export PYTHON=/path/to/python                # 要有 matplotlib（Tools 模式要生成合成子图）
export TAVOTTO_FONTS_DIR=<批准字体目录>      # 文字量宽要
export EVAL_PRO_SRC=/path/to/tavotto-pro     # 本地高级版组件源码
export EVAL_RESULTS=$PWD/scripts/eval/mechanism/results/<日期-标签>
python scripts/eval/mechanism/harness.py list
python scripts/eval/mechanism/harness.py run Aref     # 标准答案批 → 真的 vectoredit.propose_memory → 判据
python scripts/eval/mechanism/harness.py run Tools    # 经插件工具层：scene → edit_plan → edit_apply → 读盘判分 → undo 回到原版本
python scripts/eval/mechanism/harness.py run Mut      # 判据自检：故意写坏的变形必须被拦下
python scripts/eval/mechanism/harness.py judge <模型产出.jsonl>   # 每行 {"task": 题号, "items": [...]}
python scripts/eval/mechanism/harness.py report
```

## 2026-11-28 第一轮（`results/2026-11-28-local/`）：**只有模拟 / 接线结果，没有任何模型成绩**

三个模式都**不叫模型**（记 `simulated: true`），证明的是「条目词汇 → 引擎编排 → 真组件 → 落地 → 判据」这条链是通的、判据有牙齿，**不是 Agent 能不能画对**：

| 模式 | 结果 | 说明 |
| --- | --- | --- |
| `Aref` | 6/6 | 标准答案批 → `vectoredit.propose_memory` → 判据 |
| `Tools` | 6/6 | 同一批经 MCP 工具层 `call_tool`（真的 scene → plan → apply 写盘 → undo）：落盘后判分、撤销后版本号与对象表回到原样；对照题整批 `pro_required` 且文件没动 |
| `Mut` | 25/25 拦下 | 25 个故意写坏的变形（两个蛋白同位置、蛋白放到膜外、标签离目标太远、箭头悬空、示意图框太小、膜长越界）都被引擎核对或点名的判据拦下 |

**没做 / 待你定**：真模型（B 组：`opencode run` + Tavotto MCP）。花额度，等你点头；而且 B 组要让 MCP 子进程用到本地真组件，得走 `TAVOTTO_PRO_DIR` + 测试授权端点那套开发模式（本期用进程内替身绕开了，所以 `Tools` 也不是子进程 stdio）。
桌面改图助手（A 组）按设计不能新增对象（ADR 0220 第 6.I 期才接三种 `kind`），不在本评测里。

## 这一轮顺带发现并修掉的 Agent 端问题（评测接线时撞到的）

- 免费条目（箭头 / 文字 / 形状）**不能贴到框或图元上**（`unknown_ref`）：受体放在哪是引擎算的，Agent 只能猜毫米。现在同一个 `add` 里可以用 `item` 锚点贴框 / 图元（`vectorscene.solve(known_items=…)`）。
- 一个 `add` 最多 12 条，而一幅最小机理图就有十几条（互相引用的条目必须同批）：含机理图条目的放宽到 24。
- `bad_item` 只回条目名，Agent 看不出是哪个参数、什么范围：现在回 `mem: param length_mm range 20.0..60.0` / `param X unknown (allowed: …)` / `glyph unknown (known: …)` / `no room on the membrane` / `overlaps …`。
- 工具说明里没有图元种类和参数：`ITEMS_DOC` 现在列了 `membrane` / `receptor_rtk` 及参数范围（`tests/test_mechanism.py::test_items_doc_names_every_kind_and_param_of_the_real_catalog` 对着真组件的 catalog 夹具核对）；插件加了 `references/mechanism-diagram.md`。

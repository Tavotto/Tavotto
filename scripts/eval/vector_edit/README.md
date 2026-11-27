# 矢量编辑评测：桌面改图助手（A） vs 编码 agent + Tavotto MCP 排版工具（B）

ADR 0212 §五：每期合入后重跑 0208 的评测任务集，另加编辑类任务。0208 那套评测此前没有建过，这里一并建了（「加对象」类题）。
结构、跑法、结果格式、密钥纪律沿用 `scripts/eval/fix_compat/`。不进 CI（单测只验题库与判据，见 `tests/test_vector_edit_eval_corpus.py`），不在 PR 里跑；被测模型要额度。

- **题库**：`corpus.json`（由 `build_corpus.py` 生成，改题改生成脚本再重新生成；单测核对两者一致）。21 题，每题一个合成小项目：
  一张合成子图（`FigM.pdf`，坐标轴矩形与坐标范围写死，数据点的页面位置有闭式解）+ 画布矢量对象 + 一句中文指令 + 可机判判据 + 一份标准答案（`reference`）。
  - 加对象（0208 类，6 题）：加箭头指向峰、加标注、加外框、加括号。
  - 编辑（13 题）：对齐 + 线宽、等距分布、统一颜色、相对改字号、合并两个圆、偏移、轮廓化、删除、改层级、编组、挪位置、旋转、改文字 + 颜色。
  - 对照（2 题）：要改脚本画的数据曲线颜色（不在画布矢量对象里改）、含糊指令——应进 `unhandled` / 不动画布。
- **成功判据**（`predicates.py`，只判结果不判用了哪个操作）：一列声明式谓词作用在改完的画布上；另有两条每题隐含的：没点名的对象与原来深相等（`may_change` 之外），
  除非题目允许（`may_add`）不许多出对象。浮点带容差。对照题的判据是「画布逐字段不变」。
- **A 桌面改图助手**：起真 `tavotto --desktop-sidecar`，经 `POST /api/ai/connections` 存一条 OpenCode Go 连接（`deepseek-v4.1-flash`），
  对每题 `POST /api/vector-edit/plan` 指令模式（canvas = 该题画布，selection = 题目给的选区，`locale: zh-CN`）→ 轮询到终态 → 把后端回的 `ops`
  用 `vectoredit.apply_ops` 落到画布上再判。**落位口径**：前端落位是 TS，Python 里无法原样复现，这里用 `apply_ops` 的结果判（补丁语义同一份；方案卡勾选与前端指纹核对不在内）。
  **A 组按设计不能新增对象**（系统提示写明「不能新增对象」），所以加对象题在 A 里期望是进 `unhandled`、画布不动，**不进 A 的合计**。
- **Aref（接线自检，不叫模型）**：同一条链路，`{canvas, ops}` 模式，补丁用题库标准答案；记 `simulated: true`。用来证明「sidecar → 落位 → 判据」接线是通的，不是模型成绩。
- **B 插件 agent**：`opencode run --standalone -m opencode-go/deepseek-v4.1-flash` + Tavotto MCP（`codex-plugin/mcp/server.py`，`TAVOTTO_MCP_ROOTS` = 该题项目）。
  任务说明 = 题目指令 + 项目目录 / 排版名 / 画布 id + 选区 id（若有）+「请在 Tavotto 里改这个排版」。agent 的 shell 与文件编辑权限**关掉**（只能走 MCP 工具；否则它会直接改排版 JSON，测不到工具面）。
  结束后用 `layoutannotate.open_layout` 读排版判据。选区只有 A 有「选中」语义；B 里是写在任务说明里的 id。
- **轨迹**：每次运行一行 JSONL；A：`ops` `unhandled` `errors` `echo` `state/code` `wall_s`；B：`trajectory`（`tavotto_layout_*` 的参数与结果记全）`steps` `tokens_in/out` `final_text`；
  共有：`success` `predicate_detail`（每条判据的通过与说明）`stuck`（失败原因归类，规则在 `classify_a` / `classify_b`）。

## 跑法

```sh
export PYTHON=/path/to/python          # 要有 matplotlib；合并 / 偏移 / 轮廓化三题要 wasmtime
export PYTHONPATH=$PWD/src
export EVAL_A_API_KEY=...              # 只有 A 组需要（缺省读 OPENCODE_GO_API_KEY）；只经环境变量
export EVAL_RESULTS=$PWD/scripts/eval/vector_edit/results/<日期-模型>
python scripts/eval/vector_edit/harness.py list
python scripts/eval/vector_edit/harness.py run Aref              # 接线自检，不叫模型
python scripts/eval/vector_edit/harness.py run A --reps 1
python scripts/eval/vector_edit/harness.py run B --reps 1 --jobs 3 --timeout 600
python scripts/eval/vector_edit/harness.py report
```

B 组用 opencode 自己已登录的 OpenCode Go 凭据（`opencode auth`），harness 不读它。同一 (题, rep) 已有结果就跳过，可断点续跑；
基础设施故障打印 `INFRA_ERROR` 不记结果；输出里有 `usage limit` / `go-limit` 就停。opencode 报错时退出码也可能是 0，`agent_errors` 里查 `Error:`。

## 2026-11-27 基线（`results/2026-11-27-deepseek-flash/`，`deepseek-v4.1-flash`，Tavotto main d3b935210，每题 1 次）

**A 组（桌面改图助手，真模型）没跑成**：环境里没有 `EVAL_A_API_KEY` / `OPENCODE_GO_API_KEY`，按规矩不去翻别的凭据。需维护者 `! export OPENCODE_GO_API_KEY=…` 后
`harness.py run A`。A 的接线已用 `Aref`（题库标准答案走 `{canvas, ops}` 端点 → 落位 → 判据）验证通过 21/21，**这不是模型成绩**（`simulated: true`）。

**B 组分两档**：
- `B`：opencode 原样接 Tavotto MCP。**走不通**：排版工具把 `base_revision`、`echo_digest`、场景里的对象 id 只放进 `structuredContent`（server.py 头注释写明那是给 Codex 这类会把它交给模型的宿主），
  而 opencode 只把 `content` 文本交给模型。agent 拿不到 `base_revision`，`edit_plan` 反复 `layout_changed` / 参数缺失，直到超时。只跑了 4 题（3 超时、对照题 `ctrl-vague` 没动画布算对），不再多烧额度。
- `B2`：同 B，另经 `mcp_proxy.py` 把 `structuredContent` 也以文本附在工具结果后面（模拟「宿主把它给了模型」）。**这一档才反映工具面本身**。

| 合计 | B2 | B（4 题） |
|---|---|---|
| 编辑题 | 12/13 | 0/2 |
| 加对象题（0208 类） | 5/6 | 0/1 |
| 对照题 | 1/2 | 1/1 |

B2 平均 8.8 次工具调用、126 s、输入 token 3.5 万。失败三次：`distribute-h`（核对逼出与指令无关的改动）、`add-box`（外框没框住）、`ctrl-vague`（含糊指令照做了）。

**判据口径的一次修订（如实记）**：加外框题原判据「外框与子图框各边差 ≤ 4 mm」过严（agent 留 2–3 mm 白边就判失败）。改成「框住子图、每边最多留白 6 mm」（`new_box_covers`），
这两题 B2 重跑一次；原结果在 `results/.../superseded/B2-box-tol4.jsonl`（两次都失败）。重跑后 `add-box-and-title` 过、`add-box` 仍失败（外框顶边没盖住子图）。

## 已知局限（如实）

- 每题每组只跑 1 次；单次差异不要下结论。B2 的 21 题是同一份 agent 提示，不同题之间有随机性。
- B 组 agent 没有 shell / 文件编辑权限，但能读项目目录与调 MCP；`tavotto_health` 的输出会带出源码树路径，agent 偶尔试着去读源码（`external_directory` 拒绝了绝大多数）。
- B 组的「选区」是写在任务说明里的对象 id，不是 UI 里的选中态；A 组才有真选区。
- A 组落位用 `apply_ops`，没有复现前端落位与方案卡勾选；`plan.ok`（核对）记在记录里但不参与判据——所以 A 组可能比桌面里「能点应用」的实际结果乐观（见下面 style 核对的发现）。
- 子图是合成的、坐标轴矩形写死；真实数据点量得出来（`add-arrow-peak` B2 成功，箭头尖离闭式解 0.0 mm），但没覆盖量不了的子图。

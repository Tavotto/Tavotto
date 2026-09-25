# 出版规范 profile 与预检

> 原文出自 `src/tavotto/AGENTS.md`「出版规范 profile 与预检」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **规则的唯一权威文件是 `src/tavotto/profiles/publication.json`**（随 wheel 分发）。
  Python 走 `engine/profiles.py`（`importlib.resources` 定位，装成 wheel 后源码树的
  相对路径不存在），TypeScript 经 **`@profiles` 路径别名**整份 import 进 bundle
  （`web/vite.config.ts` / `vitest.config.ts` / `vite.mcp.config.ts` **各配一次**）。
  **绝不在任一侧硬编码同一条规则**——旧代码里 `preflight.ts` 的 6pt/300dpi 与
  ExportDialog 的 85/150/180mm 就是各写一份，规范一改两处同时开始撒谎。
- **预检有两个求值器**（`engine/preflight.py` 给 MCP，`web/src/lib/preflight.ts` 给
  画布与导出对话框）——浏览器跑不了 Python，这是必需的第二份，不是重复。
  两份靠 `tests/golden/preflight_vectors.json` 对齐：**pytest 与 vitest 各跑一遍同一份
  向量**（与 patchspec ↔ Rust 同一套纪律），只比 `id/severity/object_ids/gids/detail`，
  不比中文措辞。改任一侧跑 `python scripts/gen_preflight_vectors.py --write` 并人工读 diff。
- 输入是**规范化的 figure spec**（页面 + 面板 + 文字 + 对象几何），不是画布文档也不是
  manifest 本身：同一套规则同时服务「一张图」（MCP，scale=1）与「多面板拼版」
  （画布，每面板带自己的 scale）。
- **字号按最终物理尺寸判**：manifest 的 `fontsize` 是脚本坐标系里的 pt，面板缩到 60%
  时读者量到的是 `fontsize × scale`。只看原始值 = 「缩一缩就放行」。默认规范里字号下限
  **只有一个数 8.0pt**（绝对下限，**正好 8.0 不算过**；ADR 0029 删掉了比它更严的那条
  8.5pt）。两条检查仍然是两条——规范把两档设成不同值时（`free-form-v1` 6.0/5.0、
  期刊覆盖）各自出场。**阈值一个字都不许写进求值器**，缺键时的兜底只有
  `profiles.FALLBACK_MIN_FONT_SIZE_PT` 一处（TS 侧同名，严格同源对）。
- **`element-outside-figure`（审计 T14）**：manifest 里可见元素的 bbox **先折进
  `clip_bbox`**（见下条），再看任一边超出 [0, 1]、折成**图自身 mm** 后大于
  `FIGURE_CLIP_EPS_MM = 0.3`（两侧同名同值，理由写在常量旁：布局框不是墨迹，
  0.56 mm 的探出已经是肉眼可见的裁切）就报，阻断级、定位到那个元素、不给自动
  修复；`figure` 与 `ticks` 组不查（前者按定义满幅，后者由单条 `ticklabel`
  代言）。它说的是「导出会静默丢内容」，不是规范偏好。
- **`clip_bbox`（manifest 的第二个几何维度）**：matplotlib 会在这个框处把元素
  切掉，框外一笔都不画。**`bbox` 不含这一维**——曲线 / 散点 / 填充的包围盒是
  未裁剪的整个数据范围，`xlim=(0, 1)` 配一个 x=1e3 的离群点能把它撑到图幅的
  几百倍宽，而图幅边界处什么都没丢。产生者只有 `manifest._clip_bbox()`，消费者
  只有上面那条规则；**命中与选中高亮仍然用 `bbox`**（改它会连带改掉命中几何与
  写回自检比的那个框）。判据**两个维度缺一不可**：`get_clip_on()` 为 False 时
  `get_clip_box()` 照样是子图框（`Axes.text()` 默认就是这个组合），为 True 时
  框却可能整个是 None（标题 / 图例 / 刻度 / spine）——只看一个都会判错。
  裁剪框包住整幅图时不发（等于什么都没裁掉），说不出裁到哪也不发（当作不裁，
  宁可多报不漏报）。看护在 `tests/test_manifest_clip_bbox.py`。
- 四档：`error`（默认阻止导出，显式确认才放行且写进 proof）/ `warn`（放行必展示）/
  `not_verifiable`（**查不了**，如位图内部文字，需人工确认并写进 proof）/ `suggestion`
  （数据语义类全在这档，**绝不替用户裁决**）。**没登记的检查项兜底为 warn**，
  刻意不是 suggestion——忘了登记会让用户以为它通过了。
- 文档里存**绑定 + 绑定那一刻的规则全文快照**（`FigureDocument.profile`，可选，
  schema 仍是 2）：`{id, journal?, snapshot?, snapshotVersion?, follow?}`。ADR 0029
  之前这里只存 `{id, journal}`，于是改一次全局规范，上个月定稿的图会**悄悄换一套
  判据**。规则进文档**只有 `snapshot` 这一个位置**（写入口在 `web/src/lib/
  specBinding.ts`），「有没有新版可同步」的判据是**内容不等**而不是版本号——版本号
  是人写的，谁都可能忘了改；同步由用户明确确认，进文档历史。
  期刊自定义仍走覆盖（浅合并 + 几个子对象深合并），结果带 `derived_from`/`journal`
  并进 proof report。整套换掉用 `TAVOTTO_PROFILES_FILE`。
- 全局清单（Style / Spec 两类）落在用户数据目录，磁盘入口只有
  `engine/profilestore.py`（原子写、乐观并发、损坏回退内置、**坏文件不删**）。
  磁盘上那份是更高版本时**只读不写**：判据在 `_write_user()` 一处，因为
  `_read_user()` 对「读不懂」与「一条都没有」回的是同一个空清单。
- 导出目录规则收在 `engine/config.project_export_dir(project, fallback)` —— Flask 与
  MCP server 都调它（`fallback` 是参数不是常量：app 的 `EXPORT_DIR` 会被测试 monkeypatch）。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 规则唯一权威是那份 JSON，两侧求值器靠 golden vectors 对齐（只比判据）
- 字号按最终物理尺寸判、阈值不进求值器
- `clip_bbox` 只给 `element-outside-figure` 用
- 没登记的检查项兜底 warn
- 磁盘更高版本只读不写

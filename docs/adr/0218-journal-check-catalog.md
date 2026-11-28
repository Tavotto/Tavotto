# ADR 0218：期刊规范检查项清单——固定词表、来源事实挂靠、出版社模板层

日期：2026-11-28 · 状态：**Proposed（只定契约与分期；维护者 2026-11-28 对 §一–§四「都按建议」）**
**1.0 收敛纪律：§九 第 2 期起把 v2 数值接进已有求值器，会让预检多报警，属于判据路径改动；该期 PR 仍须维护者对那个 PR 当面点头。是否需要 1.0 豁免待维护者确认，确认前只合本 ADR 与第 1 期（不改判据）。**
相关：[0155 期刊标准库](0155-journal-catalog.md)（v1 覆盖、出处、「需要你确认」，§14 v2 折叠）、[0216 投稿核对](0216-submission-check.md)（一键改要结构化数值）、
[0213 期刊线索](0213-journal-contributions.md)、[0029 Style / Spec 与规范快照](0029-style-spec-profiles.md)；
`services/journal_catalog/FACTS-V2.md`、`src/tavotto/engine/journalfacts_fold.py`、`src/tavotto/engine/preflight.py` / `web/src/lib/preflight.ts`。

## 问题

v2 期刊目录到 rev26（2026-11-28）已有 1,024 本刊、22,807 条带原文与截图、双模型盲判过的来源事实，但软件能用上的很少：

- **能进预检或「需要你确认」的不到 20%。** 引擎只认 `FOLDABLE` 三条（`raster_dpi` gte dpi、`min_line_width` gte pt、`font_family` in）和 `CONFIRM_UNITS` 六个主语。
- **两边词汇漂移。** 4,083 条栏宽数据用 `figure_width`，引擎只认 `width`；数据有 `stage=accepted`，引擎 `_STAGE` 只有 submission / production / any。
- **词表随来源增长。** 主语已有 62 种，条件键 16 种，其中 `artifact_type` 有 32 种取值；每接一批刊就多出新说法，要新的口径裁决，还要撤回、重判。
- **已有的求值器喂不到数据。** 栏宽（`page_columns` / `widths_mm`）与字号上下限（`min_effective_font_size_pt` / `max_font_size_pt`）都有前后端求值器，但 v2 的 `width` / `font_size` 只进「需要你确认」。带条件的事实（按栏、按文字角色、按图类）一律不折。
- **重复维护。** 96% 的事实（21,856 条）落在 298 个「同一出版社内、引文与结构化字段逐字相同」的簇里；跨出版社的簇为 0。

根因：采集以「把期刊原文翻成数据」为目标，**没有先固定 Tavotto 能检查什么**。所以数据词表没有边界，也不和引擎共享。

## 决定

### 一、固定的检查项清单（维护者 2026-11-28「都按建议」）

检查项是**软件侧的能力单位**，由 Tavotto 能测什么定义，不由来源文本定义。首版 19 项，分三档：

| 档 | 检查项（`tck:`） | 对象维度 | 现状 |
| --- | --- | --- | --- |
| A：已有求值器，只差接数据 | `width_final` | `column` | `page_columns` / `widths_mm` 已判 |
| | `font_size_min`、`font_size_max` | `text_role`（other_text / script） | 已判，v2 只进确认 |
| | `raster_dpi_min` | `image_kind`、`image_origin` | 已判（不分图类） |
| | `line_width_min` | — | 已判，v2 已能折 |
| | `font_family` | — | 已判；v2 只折 required / recommended（#2357，preferred 只进确认） |
| B：数据已有，要新加检查 | `font_size` 的 `text_role=panel_label`（求值器先要能认出分图标签）、`height_max`、`width_min`（画布 / 像素下限）、`file_format`、`color_mode`、`file_size_max`、`font_embedding`、`panel_label_style` | 各自 | 只展示或只列名单 |
| C：只给人看 | `packaging`、`file_naming`、`caption_rules`、`figure_count_max`、`package_size_max`、`text_only` | — | 文字 |

清单只能经 ADR 修订：新增或删除检查项，或者改单位。

### 二、条件取值闭集与绑定强度

借 FHIR binding strength 的两档：

- **required**（取值只能来自值集）：`column`（single / one_half / double）、`text_role`（panel_label / other_text / script，script 对齐 v1 的 `min_script_font_size_pt`）、`image_kind`（photo / line_art / combination …，由现有 `artifact_type` 的 32 种写法归并）、`image_origin`（scanned / digital）、`stage`。
- **extensible**（值集能覆盖就必须用，覆盖不了才照原文写）：`article_type`。

值集之外的说法不新增键，写进 `condition_text`，由引擎当作「带条件、判不了」处理。这条沿用现行纪律。

### 三、阶段

引擎侧把 v2 的 `accepted` 并入现有 `production` 档（维护者 2026-11-28）。v2 数据里的 `accepted` 不改。

### 四、一份定义，两边生成

- **定义文件**：`src/tavotto/profiles/check_catalog.yaml`，用 LinkML 写，放在主仓库，不含任何期刊数据。每个检查项写明：
  - 单位、允许的比较关系（如 `width` 允许 `interval`，给 1.5 栏用）；
  - 对象维度、条件值集及其绑定强度、阶段档；
  - **折叠语气集**：哪些 strength 能折进预检、哪些只进 advice 或确认。例如 font_family 只折 required / recommended（#2357）。
- **生成物**：Python 侧 `engine/check_catalog.py`，前端 `web/src/lib/checkCatalog.ts`。
  - 文件头写明「生成物，勿手改，源 = profiles/check_catalog.yaml」，并登记进 AGENTS.md 的严格同源对。
  - 引擎的 `FOLDABLE`、`CONFIRM_UNITS`、`CONFIRM_RELATIONS`、`_STAGE`、各折叠语气集改由生成物提供，不再手写。
  - LinkML 只作开发依赖：生成物提交进仓库，运行时不 import linkml。yaml 是否随 wheel 分发，在第 1 期确认。
- **CI 守卫**：
  - 生成物与定义不一致即判红；
  - 新旧定义做破坏性变更比较，删项、改单位、收窄值集须带 ADR 修订号才放行（思路借 Buf breaking）。
- 期刊数据仓库按版本号引用这份定义，签发工具 `publish_facts_v2.py` 用它校验。

### 五、证据层与检查项层

- **证据层不动**：现有来源事实（引文、截图、盲判记录）原样保留，签发契约不变。
- **挂靠表**：每种来源 subject 挂到检查项上，格式为 SSSOM。谓词用 `skos:exactMatch` / `narrowMatch` / `broadMatch` / `relatedMatch`，挂不上的写 `noMatch`。挂靠表放在期刊数据仓库。首版草稿 67 行，覆盖全部 62 种 subject，只有 1 种为 noMatch。
- **检查项层**由证据层加挂靠表**机械推导**，不手改。推导规则本身经两位盲判复核一次。
- **新说法先挂靠**：挂不上的进扩展池，不直接进词表；扩展池按季度评审，决定转正或并入（借 XBRL anchoring）。

### 六、出版社模板层（只在建库侧）

- 结构照 CSL `journals` 仓库：每个出版社一份模板、一张刊表、一张整条替换的覆盖表、一张跳过表。刊级只记相对模板的差分，完整内容由展开器生成。
- 同一出版社内，事实的 quote 与结构化字段逐字相同即归入同一份模板。继承来的值标明来自模板，各刊快照仍逐刊比对。
- **发给客户端的刊包格式不变**，仍是每刊展开后的完整内容，所以 0.21.x 及更早的客户端不受影响。
- 展开语义是按检查项整条替换，不做深合并，免得拼出原文没有的规则。

### 七、每刊每项的状态与回归样例

- 每刊每项五态：写了可查 / 写了不可查 / 没写（`not_stated`）/ 继承模板 / 冲突待裁。
- 覆盖度报告就是「刊 × 检查项 × 状态」表，每次签发都重出。
- 每个 A、B 档检查项配三类样例图：应通过、应报警、不适用。用 pytest 参数化跑，改定义或改求值器时都跑（借 W3C ACT Rules 的用例清单格式）。

### 八、表达纪律

- 现行纪律不变（0155 / 0216）：语气照原文；「符合」只给引擎真判过的；带条件或判不了的进「需要你确认」。
- 状态「写了不可查」是正式结论，不算缺口，界面不暗示「已全部核对」。

### 九、分期

1. **定义与生成，不改判据。** 写 `check_catalog.yaml` 和生成物；`FOLDABLE` / `CONFIRM_UNITS` / `CONFIRM_RELATIONS` / `_STAGE` / 折叠语气集改为读生成物，取值与现在逐字相同，golden 向量（含 `font-family-preferred-is-listed-not-folded`）不变；加上 CI 守卫。数据侧同时定稿挂靠表，出覆盖度报告。
2. **A 档接数据（判据改动，逐 PR 当面点头）。**
   - 带 `column` 的 `width` 折进栏宽判定；
   - 带 `text_role=other_text` 的 `font_size` 折进字号上下限，**沿用 v1 的 `QUALIFIER_DIRECTION` 与 advice 规则**：只有 required 是硬判据；recommended / typical 只进 advice 或确认，不因换成 v2 数据就从「不判」变成报警（例：Nature「should be 7 pt」）；
   - 折进预检的规则继续带 `fact_id`，并保留原条件（column / text_role），让投稿核对的一键改能从「已判」一侧找回原始事实与目标值；
   - `accepted` 并入 production；
   - `raster_dpi` 按 `image_kind` 分判。
   - 每项配三类样例。
3. **出版社模板层。** 建库侧展开器、刊表与覆盖表；签发产物与第 2 期前逐字节相同，作为回归。
4. **B 档逐项**：每项一个 PR，先补求值器，再接数据。

### 十、不做

- 不引入策略引擎（OPA / Cedar 等）：规则是「阈值 + 条件」的参数表，不是逻辑程序。
- 不追求全部事实可执行：「写了不可查」是正式状态。
- 不改刊包与签名索引的格式。
- 不在采集流程里引入直连外部模型 API 的抽取框架；要引入须维护者另行同意（私有数据不外发，第二模型只经 agy）。

## 后果

- 引擎和数据共用一份词表，漂移变成 CI 红灯，不再靠人发现。
- 新刊不再随手加词；覆盖度可量化，能直接回答「某刊缺哪一项」。
- 出版社模板层按 96% 的重复度估算，可把维护量降约一个数量级；刊级差异仍逐刊可见。
- 代价：
  - 第 1 期多一层生成；
  - 第 2 期每项都改判据，要逐项过维护者；
  - 挂靠规则要先经一轮盲判复核。

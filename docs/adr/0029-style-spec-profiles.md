# ADR 0029：Style / Spec / Export 三层 —— 规范进项目要带着快照，最小字号只留一个数

状态：**Accepted**
日期：2026-08-29
相关：[0006 Codex MCP 与出版规范](0006-codex-mcp-app-and-publication-profile.md)（8.5 / 8.0 两档阈值的出处，本 ADR **修订其中一条**）、
[0023 文档落盘权威](0023-document-persistence-authority.md)（快照落盘走同一条原子写）、
[0028 原图输出规格](0028-original-output-spec.md)（同一条纪律的另一面：「按原图」是一句有定义的话）、
本轨道文档 [`docs/implementation/product-ux-reliability/`](../implementation/product-ux-reliability/STATUS.md)。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 三层边界 | **Style** = 图长什么样（应用到图 = 可撤销的文档修改）；**Spec** = 图要满足什么（只检查，不改图）；**Export** = 文件怎么生成（PPI / 格式 / 路径）。三层各有唯一出处 |
| 全局清单存哪 | 用户数据目录 `<data_dir>/profiles/{styles,specs}.json`（`engine/config.data_dir()`），**不在安装目录**。原子写、版本化、损坏回退内置 |
| 谁管磁盘 | `engine/profilestore.py` 一处。React 组件里没有 fetch，更没有磁盘格式的知识 |
| 「任意 id → 规范」 | 后端只有 `profilestore.resolve_spec()`；`engine/profiles.load()` 仍然只读内置那份 canonical JSON（避免 profiles ↔ profilestore 循环 import） |
| 项目里存什么 | **绑定 + 当时生效的规则全文快照**（`CanvasData.profile.snapshot`），外加 `snapshotVersion` 与用户是否选了跟随 |
| 默认优先级 | **项目结果稳定 > 规范自动升级**。有快照就按快照；全局后来变了，界面提示「有新版可同步」，由用户明确确认（那一步进文档历史） |
| 「有没有新版」的判据 | **内容不等**，不是版本号 |
| 最小字号 | 默认规范只留**一个数：8 pt**。8 pt 那条边的语义一个字没动（`eff <= floor` 仍然不算过），删掉的是比它更严的那条 |
| 界面上的身份 | 默认视图只出现自然名称（「默认规范」/「默认样式」）；id 与版本进技术详情、导入冲突与迁移 |
| 内置 | 只读。改内置的出口是**复制一份**，不是一个点了没反应的保存按钮 |
| 内置样式 | 从默认规范**派生**，不是第二份数字 |
| 默认输出 PPI | **不进 Style**。PPI 归 Export，"规范推荐多少"已经在 Spec 里 |

---

## 1. 背景：一件事有三个半出处

改造前这三层是混在一起的：

| 层 | 在哪 | 问题 |
|---|---|---|
| Spec | `src/tavotto/profiles/publication.json` | 只有内置两条，用户加不了；文档里只存 `{id, journal}` |
| Style | `<data_dir>/layouts/_styles.json`，`GET/POST/DELETE /api/styles` | 与用户的画布文件同目录（于是要一张 `RESERVED_DOCUMENT_FILENAMES` 表把它从「打开画布」里挡掉）；没有 schema 版本、没有 revision、不能导入导出 |
| Export | `web/src/lib/exportDefaults.ts`（localStorage） | 换台电脑就没了 |

而最小字号有**三个数**：`min_effective_font_size_pt: 8.5`、
`absolute_min_font_size_pt: 8.0`、`legend_policy.min_font_size_pt: 8.5`，
外加两个求值器里各自写死的兜底 `_num_or(..., 8.5)`。

## 2. 裁决一：项目里存快照，默认「项目结果稳定」

改造前文档只存 `profile.id`，注释写着「规范升级后旧文档自动跟新规则走，而不是
把一份过期的规则冻在布局文件里」。那个判断在**规范只会变严**的世界里成立；
一旦规范可以由用户自己改（本阶段做的正是这件事），它的代价变成：

> 改一次全局规范，上个月定稿、已经投出去的图，体检结论换了一套。
> 用户没有任何办法知道结论为什么变了，也没有办法要回原来那一套。

所以默认反过来：**文档里带着当时那份规则的全文**。

```jsonc
"profile": {
  "id": "lab-publication-v1",
  "snapshot": { /* 规则全文 */ },
  "snapshotVersion": "1.1.0",
  "follow": true            // 用户明确选了跟随全局时才写这个字段
}
```

三个字段全部**可选**，磁盘 schema 一个字节没升版：老文档没有它们 = 从没绑过
快照 = 按 id 去全局取现值（这正是「未显式保存的旧默认迁移到新规则」）。

**「有没有新版」的判据是内容不等，不是版本号。** 版本号是人写的，谁都可能
忘了改；而用户在意的从来是"判据变没变"。看护用例两条对称：版本号没动而规则
改了 → 提示；版本号跳了而规则一个字没改 → 不打扰。

**全局那份被删了 ≠ 这个项目没有规范。** 快照还在，照常检查，界面另说一句话
（`globalMissing`）——与 ADR 0028 的 `stale` 是同一条纪律。

## 3. 裁决二：最小字号统一为 8 pt

ADR 0006 定的是「8.5pt（严格下限）与 8.0pt（绝对下限，必须大于 8pt）」，
而规范文件自己的 `source` 里写着这条 8.5 的来历：

> 8.5 pt 严格下限为本项目补充（原文示例里有 8 pt 图例/刻度，这里从严）

也就是说，**那条规则比它想守护的规范更严**——课题组文档里本来就有 8 pt 的
图例与刻度，而 Tavotto 会把它们判成阻断项。本 ADR 删掉那一条：

```
min_effective_font_size_pt   8.5 → 8.0
legend_policy.min_font_size_pt  8.5 → 8.0
absolute_min_font_size_pt    8.0（不动）
```

**8 pt 那条边的语义一个字没改**：`eff <= floor` 仍然成立，正好 8.0 仍然不算过
（ADR 0006 的「必须大于 8pt」原样有效）。变的只是"比 8 pt 更严的那条规则"——
8.2 pt 从阻断项变成了通过。

`font-too-small` 与 `font-below-absolute-floor` **仍然是两条检查**：默认规范
把两档设成同值，只是让它在这一份规范里退居幕后；`free-form-v1`（6.0 / 5.0）
与任何期刊覆盖照样两条各自出场。看护用例两条都在。

两个求值器里那两处 `_num_or(..., 8.5)` / `8.0` 收成一个常量
`profiles.FALLBACK_MIN_FONT_SIZE_PT`，TS 侧同名，登记为严格同源对。
它**不是"规范的下限"**：规范的下限在 profile 里，这一条只在那两个键缺席时
兜底（外部喂进来的 spec、老文档里的快照）。TS 侧原来根本没有兜底，缺键时
比较对象是 `NaN`——`x < NaN` 恒假，**静默放行**，那是最坏的那种"通过"。

## 4. 裁决三：全局清单在用户数据目录，磁盘只有一个入口

```
<data_dir>/profiles/styles.json     用户自建的样式
<data_dir>/profiles/specs.json      用户自建的规范
<data_dir>/profiles/backup/         坏文件与迁移前的原件（**不删**）
```

- **内置不落盘**：规范来自 `publication.json`，样式**从默认规范派生**。
  规范说正文 9 pt / 拉丁 Times New Roman / 线宽第一档，样式照它生成——
  改规范时样式跟着变，两者从此不可能互相矛盾。
- **乐观并发**：每条带 `revision`，改的时候必须带上手里那一版；对不上回 409
  加磁盘现值。不挡的表现是"我改的东西不见了"，且没有任何报错。
- **损坏回退内置，坏文件挪进 `backup/` 不删**——回退是为了应用起得来，
  不删是因为那是用户的东西，只是我们读不懂它。比本构建**新**的清单
  原样留着完全不动：挪走它等于把用户在新版里建的东西藏起来。
- **导入建成新的一条，id 一律重新分配**，所以不存在「导入把我的改动冲掉了」。
  载荷只取白名单，不执行其中任何东西。
- **规范的身份跟着记录走**：从内置复制一份时 `data.profile_id` 改成新 id。
  留着源 id 的话，proof report 上写的是 `lab-publication-v1` 而实际用的是
  用户改过的规则——「这张图按哪套规矩过的检」就此说不清了。

### 迁移

`<data_dir>/layouts/_styles.json` 首次访问时一次性迁进 store，**迁完腾空旧
位置**（与 `config._migrate_ai_agents()` 同一条纪律：两份权威并存的话，一边
改样式另一边不知道，下次读哪份全看读取顺序）。腾空前把原件**逐字节**复制进
`backup/`；备份写不出来就整个不迁——宁可保持现状，也不在没有退路时动用户的
东西。没能映射的字段进 `data.extra` 并记一条 `unmapped_field:<键名>` 的
**结构化** warning（存句子的话换一次语言就成了历史遗留的外语）。

`RESERVED_DOCUMENT_FILENAMES` 里的 `_styles.json` **保留**：老装机上那份文件
可能还在，而「画布列表 = 对目录 glob("*.json")」。

## 5. 裁决四：默认界面不出现内部身份

`lab-publication-v1 · v1.0.0` 从导出面板上撤掉。它对用户没有任何意义，而摆在
主界面上会让人以为那是要记住的东西。内置的名字**跟界面语言走**（`name_key` →
i18n），用户自己起的名字**不翻译**。id 与版本留在技术详情（那一行的 `title`）、
导入冲突与迁移报告里，唯一出口是 `lib/profileText.profileTechnicalDetail()`。

## 6. 明确不做的事

- **不在 Style 里放"默认输出 PPI"。** PPI 归 Export（`exportDefaults`，一台
  机器一份偏好），"规范推荐多少"已经在 Spec 的 `preferred_formats.
  export_dpi_default` 里。再放一份就是同一个数的第三个出处——正是本阶段要
  消掉的那种东西。顺带：应用样式时**不写**导出偏好，那会把一次临时动作变成
  用户的长期设置。
- **不让 `engine/profiles.py` 知道用户数据目录。** 它是规则文件的读取器；
  "任意 id → 规范"是 `profilestore.resolve_spec()` 的事。
- **前后端对"不认识的 id"取舍不同，且是有意的**：后端抛错（MCP 调用方拿着
  不存在的规范时，"按默认规范放行了"是最坏的答案），前端退默认并把
  `globalMissing` 报出来（旧文档里可能存着已删掉的 id，导出对话框不能整个崩掉）。
- **不做规范的"继承 / 覆盖链"。** 期刊覆盖（`journal`）已经是一层，再加一层
  会让"这张图按哪套规矩过的检"变成一次图遍历。

## 7. 看护

| 判据 | 在哪 |
|---|---|
| 内置来自 canonical JSON；内置样式派生自默认规范 | `tests/test_profile_store.py` |
| 增删改复制 / 乐观并发 / 恢复默认值 / 重名策略 | 同上 |
| 落盘在用户数据目录、无临时文件残留、损坏回退且坏文件不删、更高 schema 原样不动、单条坏不拖垮整份 | 同上 |
| 导入导出往返、五种非法载荷各自的 code、超限先卡再解析、未识别字段留存 | 同上 |
| 旧 `_styles.json` 迁移：内容进 store、旧位置腾空、有备份、幂等、warning | 同上 |
| 8 pt：默认规范只有一个数；求值器/界面**没有硬编码残留**（代码搜索式看护）；两侧兜底常量同源；显式存下的 8.5 仍然生效 | 同上 + `tests/test_preflight.py` |
| 两条字号检查仍然是两条（`free-form-v1` 6.0 / 5.0） | `tests/test_preflight.py` |
| 快照优先、内容判据、跟随、globalMissing、期刊覆盖 | `web/src/lib/specBinding.test.ts` |
| 后端不在退内置、形状不对不抹清单、并发撞车留现值 | `web/src/store/profileStore.test.ts` |
| 应用样式一条历史可撤销（含背景）、选规范正确 dirty 并带快照、同步是另一条历史 | `web/src/store/styleAndSpec.test.ts` |
| 默认界面不出现 id/版本、内置只读、Style/Spec 不混改、可达名与 `aria-current` | `web/src/components/settings/profilesSettings.test.tsx` |

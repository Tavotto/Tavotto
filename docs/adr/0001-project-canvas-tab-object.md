# ADR 0001 — 对象层级：Project / Canvas / Tab / Object

日期：2026-08-15 · 状态：已接受

## 背景

代码与界面里混用了「项目、项目包、布局、文档、画布」五个词指代三种东西：
`FigureDocument`（schema 2 的单画布文档）、layouts/ 下的命名 JSON、
`.mmpack.zip` 项目包。Tavotto 要支持一个项目多张图，必须先固定名词。

## 决定

四层，且只有这四层：

| 层级 | 定义 | 承载 |
|---|---|---|
| **Project** | 一个 Tavotto 项目 | 图库路径（figures 目录）、素材根、导出位置、项目设置、布局版本、AI 历史 |
| **Canvas** | 项目中一张可独立编辑、命名、排序、导出的科学图 | page、objects、guides、layoutGroups（即原 schema 2 文档的主体） |
| **Tab** | 当前项目里**已打开**的 Canvas | 打开顺序、激活态、dirty 点；≠ 最近文件列表 |
| **Object** | Canvas 内的面板 / 文字 / 箭头 / 形状 | 现有 CanvasObject 类型不变 |

命名统一：

- 前端类型：`TavottoProject`（schema 3 顶层）、`Canvas`、`CanvasObject`；
  「布局文档 / 文档」在代码注释与 UI 里一律改称「项目 /（某张）画布」。
- 后端：`FIGURES_DIR` 语义为「当前项目的图库目录」；layouts/ 下的持久化按
  项目组织。
- UI 文案：不再出现「布局文件 / 文档」，改为「项目」「画布」。

## 存储形态

- **工作态**：项目目录 + manifest（服务器端 `layouts/` 内按项目存 JSON，
  自动保存原子写）。可 diff、可增量、可被版本时间线复用。
- **便携分享**：单文件 **`.tavotto`** 压缩包（zip 容器：`project.json` +
  `assets/` + `scripts/` + sha1 清单），kind=`tavotto-package`。
- ~~**兼容**：读取端继续接受 `.mmpack.zip`（kind=`magic-matplot-package`、
  layout.json schema 2）；打开时迁移为单 Canvas 的 schema 3 项目。~~
  **2026-08-20 作废**：从 Magplot 改名为 Tavotto 时选了干净断裂，
  `brand.py` / `brand.ts` 不再有 `LEGACY_*` 那一档（连带 `magplot-package` /
  `.magplot` 也不认）。检视端点仍按 zip 的结构而非 `kind`/扩展名判断，
  所以老包实际上还打得开——但那是不校验的副作用，不再是承诺。
  schema 2 → 3 的迁移（`migrateToProject`）与品牌无关，照旧。

## Schema 3 概要

```jsonc
{
  "schema": 3,
  "project": { "id": "p…", "name": "…", "settings": { /* 导出默认值等 */ } },
  "canvases": [
    { "id": "c…", "name": "Fig 1", "page": {…}, "objects": […],
      "guides": […], "layoutGroups": […] }
  ],
  "activeCanvasId": "c…",
  "createdAt": 0, "updatedAt": 0
}
```

schema 2 → 3 迁移：整份旧文档变成唯一 Canvas，`name` 提升为 Canvas 名，
对象逐字段搬运不改值；project.name 取旧文档 name。

## 后果

- 版本时间线、论文样式、项目包 API 的载荷校验从「schema 2」放宽为
  「schema 2（迁移后接受）或 3」。
- Tab 状态属于 UI 持久层（per-project），不进 schema。
- 拒绝的备选：多文件（每 Canvas 一文件）工作态——跨画布原子性与重命名复杂度
  高于单 manifest；数据量（JSON 数百 KB 级）不构成瓶颈。

## 修订 2026-09-26：界面名词固定为四个（用户拍板）

上面「UI 文案：不再出现『布局文件 / 文档』」这条一直没有看护，后来的文案又用回了
「文档」「画布文件」「另存为文档」「项目文档」「本机最近文档」；同一个 ⇧⌘S 在快捷键
帮助里叫「保存为画布文件」、在对话框里叫「另存为文档」；「画布」一词同时被拿来指一张图
和整份文件。用户于 2026-09-26 拍板：界面名词**只有这四个**——

| 界面名词 | English | 指什么 | 对应代码概念 |
|---|---|---|---|
| 项目 | Project | 用户的脚本文件夹（左栏「工作区」里的条目） | 上表的 **Project**（`project_status.figures_dir`） |
| 排版 | Layout | schema 3 的一份 JSON，可以包含多张画布；⌘S 存它、⇧⌘S 另存它、版本时间线记它 | `ProjectDocument` / `TavottoProject`（`documentStore.buildProject()`），后端 `layouts/` 与 `tavottofile/*.json` |
| 画布 | Canvas | 排版里的一张图（标签页） | 上表的 **Canvas** |
| 项目包 | Project package | 导出的 `.tavotto` 单文件，用于分享 | `api_package`（kind=`tavotto-package`） |

- 「排版」取代界面上的「文档 / 画布文件 / 项目文档」；中文「文档」与英文
  document(s) 作为这个概念从界面上彻底去掉。「使用文档 / Documentation」指帮助手册，
  不是这个概念，按条豁免。
- 这一修订**改的是界面名词，不是上表的层级**：上表把 schema 3 顶层叫 Project，是当时
  「一个项目一份文件」的设想；实际落地后一个项目文件夹里可以有多份排版（`tavottofile/`
  下每个命名 JSON 一份），所以界面上「项目」只指文件夹，那份 JSON 叫「排版」。代码里的
  `ProjectDocument` / `documentId` / i18n key 名（`topbar.saveDocumentAs` 等）**不改名**
  ——改 key 代价大、与用户无关；开发者读到 `document` 时按本表对应到「排版」。
- 每样东西存在哪、存什么，面向用户的说明在 README「文件都放在哪」；前端细则与看护
  在 `docs/rules/frontend/i18n.md`「界面名词」。看护：`tests/test_ui_terminology.py`
  （扫 zh-CN / en-US 全部语言包，豁免按条枚举且必须仍命中）；壳内菜单文案由
  `tests/test_desktop_i18n.py` 看护。

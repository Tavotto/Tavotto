# ADR 0001 — 对象层级：Project / Canvas / Tab / Object

日期：2026-08-15 · 状态：已接受

## 背景

代码与界面里混用了「项目、项目包、布局、文档、画布」五个词指代三种东西：
`FigureDocument`（schema 2 的单画布文档）、layouts/ 下的命名 JSON、
`.mmpack.zip` 项目包。Magplot 要支持一个项目多张图，必须先固定名词。

## 决定

四层，且只有这四层：

| 层级 | 定义 | 承载 |
|---|---|---|
| **Project** | 一个 Magplot 项目 | 图库路径（figures 目录）、素材根、导出位置、项目设置、布局版本、AI 历史 |
| **Canvas** | 项目中一张可独立编辑、命名、排序、导出的科学图 | page、objects、guides、layoutGroups（即原 schema 2 文档的主体） |
| **Tab** | 当前项目里**已打开**的 Canvas | 打开顺序、激活态、dirty 点；≠ 最近文件列表 |
| **Object** | Canvas 内的面板 / 文字 / 箭头 / 形状 | 现有 CanvasObject 类型不变 |

命名统一：

- 前端类型：`MagplotProject`（schema 3 顶层）、`Canvas`、`CanvasObject`；
  「布局文档 / 文档」在代码注释与 UI 里一律改称「项目 /（某张）画布」。
- 后端：`FIGURES_DIR` 语义为「当前项目的图库目录」；layouts/ 下的持久化按
  项目组织。
- UI 文案：不再出现「布局文件 / 文档」，改为「项目」「画布」。

## 存储形态

- **工作态**：项目目录 + manifest（服务器端 `layouts/` 内按项目存 JSON，
  自动保存原子写）。可 diff、可增量、可被版本时间线复用。
- **便携分享**：单文件 **`.magplot`** 压缩包（zip 容器：`project.json` +
  `assets/` + `scripts/` + sha1 清单），kind=`magplot-package`。
- **兼容**：读取端继续接受 `.mmpack.zip`（kind=`magic-matplot-package`、
  layout.json schema 2）；打开时迁移为单 Canvas 的 schema 3 项目。

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

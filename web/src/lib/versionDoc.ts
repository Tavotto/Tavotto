import type { LayoutVersionMeta } from '@/lib/api'
import {
  canvasToDoc,
  migrateToProject,
  type FigureDocument,
  type ProjectDocument,
} from '@/types/document'

/** 版本节点正文：schema 2 的单画布节点，或 schema 3 / 4 的整份项目节点。 */
export type VersionDocument = FigureDocument | ProjectDocument

/** 这个节点存的是整份项目吗？（schema 2 才是单画布） */
export function isProjectVersion(doc: VersionDocument): doc is ProjectDocument {
  return doc.schema !== 2
}

/**
 * 列表元数据里的 `schema` 能不能判定「整份项目」：
 * `true` = 项目节点，`false` = 单画布，`null` = 不知道（节点正文读不出来 / 旧后端
 * 没发这个键）。
 *
 * 与 `isProjectVersion` 是同一条判据的元数据侧入口（`schema !== 2`），入口确认与
 * 恢复分派都只经这两个函数，不在调用点各写一遍 `=== 3 || === 4`。后端对**可读**
 * 节点一律按正文发这个键（schema 号缺失时按 `objects` / `canvases` 骨架补），
 * 所以 `null` 只在正文取不回来时出现——那种节点不会走到写盘。
 */
export function projectVersionFromMeta(
  meta: Pick<LayoutVersionMeta, 'schema'>,
): boolean | null {
  return typeof meta.schema === 'number' ? meta.schema !== 2 : null
}

/**
 * 版本正文 → 预览里要画的那**一张**画布。
 *
 * - schema 2：节点本来就是一张画布，原样返回（既有节点，行为不变）；
 * - schema 3 / 4：整份项目正文，经 `migrateToProject` 校验后按节点记录的
 *   `canvasId` 取那一张；节点没记画布身份（旧节点 / 项目节点没带）时取正文自己的
 *   `activeCanvasId`。
 *
 * **取不出返回 null**（记录的那张画布不在正文里 / 正文不是能识别的项目文档）：
 * 调用方必须明确失败，不许退到第一张或当前画布——那会让用户以为在看节点里的
 * 那一张，实际看的是另一张。
 */
export function versionCanvasDoc(
  doc: VersionDocument,
  meta: Pick<LayoutVersionMeta, 'canvasId'>,
): FigureDocument | null {
  if (doc.schema === 2) return doc
  const project = migrateToProject(doc)
  if (!project) return null
  // 节点自己记的画布身份优先。**记了却找不到 = 取不出**，不拿 activeCanvasId 顶上：
  // 那是另一个问题的答案，用户会看到一张不属于这个节点的画布。
  const wanted = meta.canvasId ?? project.activeCanvasId
  const canvas = project.canvases.find((c) => c.id === wanted)
  return canvas ? canvasToDoc(canvas) : null
}

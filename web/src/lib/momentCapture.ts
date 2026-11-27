import type { FigureDocument } from '@/types/document'

/**
 * 时间线「保存时刻」的捕获层：在**操作发起那一刻**冻结身份与图源引用，任何 writer 的 await
 * 之前完成。完成时再合成缩略图——那时用户可能已经改了面板，现取会画出之后的样子。
 *
 * 图源的真正实现由 `renderStore` 在模块初始化时注入（`installMomentSources`）：这一层只
 * 记账「拍的是哪一份正文」，不自己去读渲染态，免得 lib 反向依赖 store。
 *
 * 类型也住在这里（`timelineThumb` 从这里导入），`renderStore → 本文件` 那条边因此不成环。
 */

/** 缩略图合成的栅格尺寸（长边像素）：时间线快照与缩略图合成共用这一个数 */
export const TIMELINE_THUMB_PX = 360

/**
 * 一份图源：`svg` 是矢量正文，`url` 是可寻址的位图地址；`png` 是算好才回来的位图
 * （历史 Figure / 归档快照那条路要发一次请求，不能在拍的当下同步取到）。
 */
export type PanelSource = { svg?: string; url?: string; png?: Promise<string | null> } | null
/** 一份文档此刻的各面板图源；取不出来 = `null`（没有缩略图） */
export type ThumbSources = ReadonlyMap<string, PanelSource> | null

export interface MomentDocumentState {
  doc: FigureDocument
  activeCanvasId: string
  documentId: string
  projectMeta: { id: string; name: string; createdAt: number }
}

/** 与 `lib/timelineContext.ts` 同一把尺子：换项目 / 换排版才换上下文 */
export const timelineCtxKey = (gen: number, docId: string) => `${gen}:${docId}`

export function activeCanvasIdentityOf(state: MomentDocumentState, pj: string | null) {
  return {
    documentId: state.documentId,
    versionIdentity: {
      project: { id: state.projectMeta.id, name: state.projectMeta.name },
      canvasId: state.activeCanvasId,
      createdAt: state.projectMeta.createdAt,
      updatedAt: Date.now(),
    },
    pj,
    doc: state.doc,
    canvasId: state.activeCanvasId,
    canvasName: state.doc.name,
  }
}

export interface MomentSnapshot {
  readonly ctx: string
  readonly identity: ReturnType<typeof activeCanvasIdentityOf>
  readonly thumb: ThumbSources
  readonly thumbDoc?: FigureDocument
  readonly bakedInto?: ReadonlyMap<string, string>
}

type SourceCapture = (doc: FigureDocument, pj: string | null) => ThumbSources
let captureSources: SourceCapture | null = null

/** 真实渲染层在模块初始化时提供这个同步入口 */
export function installMomentSources(capture: SourceCapture): void {
  if (typeof capture !== 'function') throw new TypeError('A synchronous moment source capture is required.')
  captureSources = capture
}

export class MomentCaptureUnavailable extends Error {
  constructor() {
    super('Moment sources have not been initialized.')
    this.name = 'MomentCaptureUnavailable'
  }
}

/** 身份与真实图源引用现在冻结，早于任何 writer 的 await */
export function captureDocumentMoment(
  state: MomentDocumentState,
  pj: string | null,
  gen: number,
  doc?: FigureDocument,
): MomentSnapshot {
  if (!captureSources) throw new MomentCaptureUnavailable()
  const identity = activeCanvasIdentityOf(state, pj)
  if (doc) identity.doc = doc
  return {
    ctx: timelineCtxKey(gen, identity.documentId),
    identity,
    thumb: captureSources(identity.doc, identity.pj),
  }
}

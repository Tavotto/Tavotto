import { createVersion, putVersionThumb, type LayoutMoment, type LayoutVersionMeta } from '@/lib/api'
import { currentProjectId } from '@/lib/session'
import { composeTimelineThumb } from '@/lib/timelineThumb'
import { documentDigest, recordDiagnosticEvent, versionHash } from '@/diagnostics'
import { useDocumentStore } from '@/store/documentStore'
import { useTimelineStore } from '@/store/timelineStore'

/**
 * 给排版时间线打一个节点（ADR 0101）——自动节点、关键时刻、手动 / 命名节点、
 * 「恢复前」**全走这一个入口**。
 *
 * 一处的理由：四条路各拍一次的话，总有一条会忘了带画布身份（R-03 就是这么来的）、
 * 或忘了带项目（关项目那一刻 pj 随后就变）、或忘了拍缩略图。
 *
 * 顺序：先建节点（拿到 id），再合成缩略图、单独 PUT 上去。缩略图失败只是那个节点
 * 没有图，节点本身已经在了。
 */

/**
 * 「当前这一份节点拍的是谁」—— doc + 画布身份 + 项目，**一起取，一次取**。
 *
 * 分开取的话总有一天会有人只取 doc（改造前全仓就是这样），于是节点在时间线上
 * 看着好好的，恢复时才发现它不知道自己来自哪张画布（R-03）。
 */
export function activeCanvasIdentity() {
  const { doc, activeCanvasId, canvases, documentId } = useDocumentStore.getState()
  return {
    documentId,
    pj: currentProjectId(),
    doc,
    canvasId: activeCanvasId,
    canvasName: canvases.find((c) => c.id === activeCanvasId)?.name ?? doc.name,
  }
}

export interface CheckpointOptions {
  auto: boolean
  moment?: LayoutMoment
  /** 用户起的名字 → 命名节点；程序起的名字（「恢复前 …」）同时给 `programName` */
  name?: string
  /** 名字是程序起的，不算命名节点 */
  programName?: boolean
  /** 空画布也拍（手动 / 命名：用户明确要这一版） */
  allowEmpty?: boolean
}

export async function takeCheckpoint(
  opts: CheckpointOptions,
): Promise<{ version?: LayoutVersionMeta; skipped?: boolean } | null> {
  const id = activeCanvasIdentity()
  if (!opts.allowEmpty && !id.doc.objects.length) return null
  const name = opts.name?.trim() || undefined
  // 缩略图的图源在这里、在任何 await 之前取（见 `composeTimelineThumb`）
  const thumbP = composeTimelineThumb(id.doc)
  const res = await createVersion(
    id.documentId,
    {
      auto: opts.auto,
      moment: opts.moment,
      name,
      named: name && !opts.programName ? true : undefined,
      doc: id.doc,
      canvasId: id.canvasId,
      canvasName: id.canvasName,
    },
    id.pj,
  )
  if (res.skipped || !res.version) return res
  recordDiagnosticEvent({
    type: 'layout_version.save',
    // **只有 id 的 hash**：名字是用户自己敲的，一个字都不取
    version: versionHash(res.version.id),
    document_hash: documentDigest(id.doc),
    auto: opts.auto,
  })
  useTimelineStore.getState().bump()
  const vid = res.version.id
  // 缩略图不挡调用方：恢复 / 回主页不该等一张小图
  void thumbP
    .then((thumb) => (thumb ? putVersionThumb(id.documentId, vid, thumb, id.pj) : null))
    .then((r) => {
      if (r) useTimelineStore.getState().bump()
    })
    .catch(() => {
      /* 没有缩略图的节点退回草图；不打扰编辑 */
    })
  return res
}

/* ------------------------------ 关键时刻 ---------------------------------- */

/**
 * 关键时刻的出口。导出 / 写回 / 保存 / 打开 / 关闭各自的成功点调它。
 *
 * **只有时间线在跑（`startVersionCheckpoints` 挂上了）时才打点**：单元测试里
 * 那些成功路径不会因此多发一个请求，而应用里它总是在跑的。
 */
let momentSink: ((moment: LayoutMoment) => Promise<unknown>) | null = null

export function setMomentSink(sink: typeof momentSink): void {
  momentSink = sink
}

export function markMoment(moment: LayoutMoment): Promise<unknown> {
  if (!momentSink) return Promise.resolve(null)
  return momentSink(moment).catch(() => null)
}

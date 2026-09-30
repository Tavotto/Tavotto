import { createVersion, putVersionThumb, type LayoutMoment, type LayoutVersionMeta } from '@/lib/api'
import { currentProjectId } from '@/lib/session'
import { currentTimelineCtx } from '@/lib/timelineContext'
import { captureThumbSources, composeTimelineThumb, type ThumbSources } from '@/lib/timelineThumb'
import { documentDigest, recordDiagnosticEvent, versionHash } from '@/diagnostics'
import { useDocumentStore } from '@/store/documentStore'
import { useTimelineStore } from '@/store/timelineStore'
import { useUiStore } from '@/store/uiStore'
import { msg } from '@/i18n'
import type { FigureDocument } from '@/types/document'

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
  const { doc, activeCanvasId, documentId } = useDocumentStore.getState()
  return {
    documentId,
    pj: currentProjectId(),
    doc,
    canvasId: activeCanvasId,
    // 当前画布的名字取**活文档**的：改名只写 `doc.name`，`canvases` 里那一条要等下一次切画布
    // 才同步，从列表里读会把改名之前的旧名字记进节点（Codex #679）
    canvasName: doc.name,
  }
}

/**
 * 一个关键时刻的**快照**：发起那一刻的上下文、那份文档（连画布身份、项目）与面板图源，
 * **一起取、一次取**（Codex #679）。
 *
 * 导出 / 保存 / 写回都要 await；完成时现拍的话，拍到的是之后又改过的样子——「导出」节点
 * 里放着一份从没被导出过的内容，从它恢复就不是用户导出的那一版。所以这三处在发起时取
 * 快照、完成时拿它打点。documentStore 里的文档是不可变的（每次 commit 换新对象），留住
 * 引用就是留住那一刻。
 */
export interface MomentSnapshot {
  /** 发起那一刻的时间线上下文（项目代际 + 排版 id） */
  readonly ctx: string
  readonly identity: ReturnType<typeof activeCanvasIdentity>
  readonly thumb: ThumbSources
}

/**
 * 在操作发起那一刻取快照。`doc` 给的话用它（这次操作**实际送出**的那一份，例如导出请求
 * 里的文档），画布身份、项目仍取此刻的。
 */
export function captureMoment(doc?: FigureDocument): MomentSnapshot {
  const identity = activeCanvasIdentity()
  if (doc) identity.doc = doc
  return { ctx: currentTimelineCtx(), identity, thumb: captureThumbSources(identity.doc) }
}

/**
 * 同一个时刻、换一份**操作完成后**的文档：上下文、画布身份、面板图源仍是发起那一刻的。
 * 给「操作本身会改文档」的时刻用——写回带标注时，写成之后画布上的标注原件会被删掉，
 * 节点要记删掉之后的样子，否则从它恢复标注会出现两份（Codex #679）。
 */
export function momentWithDoc(snapshot: MomentSnapshot, doc: FigureDocument): MomentSnapshot {
  return { ...snapshot, identity: { ...snapshot.identity, doc } }
}

export interface CheckpointOptions {
  auto: boolean
  moment?: LayoutMoment
  /** 用户起的名字 → 命名节点；程序起的名字（「恢复前 …」）同时给 `programName` */
  name?: string
  /** 名字是程序起的，不算命名节点 */
  programName?: boolean
  /**
   * 空画布也拍：手动 / 命名（用户明确要这一版）、恢复前、关键时刻（发生过的事不因画布空着
   * 就缺席）。只有普通自动节点在空画布上不拍。「空」看的是这次**实际拍的**那份文档——带快照
   * 时是快照里的，不是此刻的。
   */
  allowEmpty?: boolean
}

export async function takeCheckpoint(
  opts: CheckpointOptions,
  snapshot?: MomentSnapshot,
): Promise<{ version?: LayoutVersionMeta; skipped?: boolean } | null> {
  // 给了快照就拍快照里那一刻（关键时刻，见 `MomentSnapshot`）；没给就拍此刻
  const id = snapshot?.identity ?? activeCanvasIdentity()
  if (!opts.allowEmpty && !id.doc.objects.length) return null
  const name = opts.name?.trim() || undefined
  // 缩略图的图源在这里、在任何 await 之前取（见 `composeTimelineThumb`）；快照带着的用快照的
  const thumbP = composeTimelineThumb(id.doc, snapshot ? snapshot.thumb : undefined)
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

/**
 * 「把现在存为命名节点」的动作本体：顶栏浮层与抽屉里的输入框共用。
 * 成功说一句话；失败（例如命名节点超出上限的 409）原样抛给调用方——两处都要把
 * 那句话留在输入框旁边，而不是一闪而过的状态条上。
 */
export async function saveNamedNode(name: string): Promise<void> {
  const trimmed = name.trim()
  if (!trimmed) return
  await takeCheckpoint({ auto: false, name: trimmed, allowEmpty: true })
  useUiStore.getState().setStatus(msg('versions.saved', { name: trimmed }, 'dialogs'))
}

/* ------------------------------ 关键时刻 ---------------------------------- */

/**
 * 关键时刻的出口。导出 / 写回 / 保存 / 打开 / 关闭各自的成功点调它。
 *
 * **只有时间线在跑（`startVersionCheckpoints` 挂上了）时才打点**：单元测试里
 * 那些成功路径不会因此多发一个请求，而应用里它总是在跑的。
 *
 * **快照**（`MomentSnapshot`，`captureMoment()` 在操作发起那一刻取）：跨了 await 的调用点
 * 必须带。完成时两件事都用它——
 * - **上下文**：已经换了排版 / 项目，这一刻就丢掉。给换上来的那一份打点，它的时间线上会
 *   多一个内容从没被导出 / 保存 / 写回过的节点。不回头给原来那一份补（要离开 documentStore
 *   另起一条拍节点的路：pj、缩略图请求都得绕开「当前」）。
 * - **内容**：节点拍的是快照里那份文档、缩略图用快照里的面板图源——同一份排版里途中又改过
 *   的话，现拍的是之后的样子，从它恢复不是那一版（Codex #679）。
 *
 * 调用点清单：
 * - 带快照（内容 = 发起时那份）：导出（`runExport` 用导出请求里的那份文档取，记进
 *   exportStore，终局时带上）；保存（⌘S 本机与另存为在 `captureSaveContext()` 里取、
 *   ⌘S 写回项目文件在 `buildProject()` 那一刻取——那才是写出去的那份；经
 *   `emitLayoutSaved` 的事件带过来）；写回（两处按钮在发起时取；带标注写回的那一处在
 *   删掉画布上的标注原件之后换成删之后的文档——`momentWithDoc`，上下文不变）；
 * - 同步、不带：打开（`adoptNow` 换完代之后、`markWorkspaceOpenedAfter` 核过项目代际）、
 *   离开（`settleAndMarkLeaving`：**先收手势再打点**，`adoptNow` 认领新项目之前与 `showPicker`
 *   共用这一份顺序）——调的那一刻就是那件事发生的上下文与内容。
 * 没有「拿不到快照、只能按编辑代次丢弃」的路径：三处都在发起时拿得到送出去的那份文档。
 * 三件事逐个核过（Codex #679）：拍之前手势 / 事务落定了没有、拍的是不是操作完成后的文档、
 * 上下文是不是发起那一刻的；新增调用点照这三问再核一遍。
 */
let momentSink: ((moment: LayoutMoment, snapshot?: MomentSnapshot) => Promise<unknown>) | null = null

export function setMomentSink(sink: typeof momentSink): void {
  momentSink = sink
}

export function markMoment(moment: LayoutMoment, snapshot?: MomentSnapshot | null): Promise<unknown> {
  if (!momentSink) return Promise.resolve(null)
  if (snapshot && snapshot.ctx !== currentTimelineCtx()) return Promise.resolve(null)
  return momentSink(moment, snapshot ?? undefined).catch(() => null)
}

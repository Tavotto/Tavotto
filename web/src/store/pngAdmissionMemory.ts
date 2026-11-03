import { create } from 'zustand'
import { isDefiniteRefusal, type ArtifactFailure } from '@/lib/artifactReason'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'

/**
 * PNG 采用被「明确拒绝」的会话内记忆（ADR 0139 PR1）。
 *
 * 首次采用要把脚本在 safe worker 里完整跑一遍才知道答案；被 `permanent` / `source_mismatch` 拒了之后，
 * 同一张 PNG（同 fileId + 同磁盘身份）再点「进入编辑」只会再烧一遍脚本、再被拒一遍。这里记下「这张图
 * 这次被拒的原因」，让入口与元素树空态直接给同一句话 + 同一出路，不再自动重试。
 *
 * 纪律：
 * - **瞬态**：只在内存，随项目换代（`clear()`）清空；**不写进文档**、不进 `renderStore`（不制造假条目）、
 *   不改 `isUnadoptedPngSource` 的判据（#1144 / #1145 靠它拦住未采用 PNG 的补渲染）。
 * - 身份 = fileId + 素材清单里的 mtime：文件变化（外部重写）后 mtime 变，旧记忆自然失效；
 *   再试一次 = 显式 `forget`。
 * - 只记「明确拒绝」类；`retry` / 冲突类 / 脚本真故障都不记（它们本来就该再试）。
 */
export interface PngRefusal {
  failure: ArtifactFailure
  identity: string
}

interface PngAdmissionMemory {
  /** 项目代际：`clear()` 递增 */
  epoch: number
  byFile: Record<string, PngRefusal>
  remember: (fileId: string, failure: ArtifactFailure) => void
  forget: (fileId: string) => void
  clear: () => void
}

/** 这张 PNG 此刻的磁盘身份（素材清单里的 mtime；清单没有它时为空串） */
export function pngIdentityOf(fileId: string): string {
  const mtime = useAssetStore.getState().byId[fileId]?.mtime
  return mtime === undefined ? '' : String(mtime)
}

/** PNG 采用失败 toast 的主人：采用成功 / 面板删除 / 切项目（`projectStore`）时按它撤掉 */
export const pngAdmitOwner = (panelId: string): string => `png.admit:${panelId}`

/**
 * 「重建为可编辑图」的执行者由 `pngRebuild.ts` 在加载时登记，toast 的按钮只发意图。
 * 为什么不直接 import：重建链路（`prepEnterEdit` → `workspace`）回到 `actions`，而采用失败的 toast 是
 * `actions` 造的——`actions → pngRebuild` 一条边（含动态 import）会把一大片模块连成新的 import 环
 * （`importArchitecture` 门禁）。登记发生在 `ElementTree` 静态 import `pngRebuild` 的那一刻，应用启动即就位。
 */
type RebuildRunner = (panelId: string) => unknown
let rebuildRunner: RebuildRunner | null = null
export const registerPngRebuildRunner = (fn: RebuildRunner): void => {
  rebuildRunner = fn
}
export const runPngRebuild = (panelId: string): void => {
  void rebuildRunner?.(panelId)
}

export const usePngAdmissionMemory = create<PngAdmissionMemory>((set) => ({
  epoch: 0,
  byFile: {},
  remember: (fileId, failure) => {
    if (!isDefiniteRefusal(failure.recoverability)) return
    set((s) => ({ byFile: { ...s.byFile, [fileId]: { failure, identity: pngIdentityOf(fileId) } } }))
  },
  forget: (fileId) =>
    set((s) => {
      if (!(fileId in s.byFile)) return s
      const byFile = { ...s.byFile }
      delete byFile[fileId]
      return { byFile }
    }),
  clear: () => set((s) => ({ epoch: s.epoch + 1, byFile: {} })),
}))

/** 这张 PNG 当前仍有效的拒绝记忆；身份对不上（文件变了）就当没有 */
export function pngRefusalOf(fileId: string, byFile = usePngAdmissionMemory.getState().byFile): PngRefusal | null {
  const hit = byFile[fileId]
  return hit && hit.identity === pngIdentityOf(fileId) ? hit : null
}

/**
 * 此刻挂着的 toast 是不是某个 `fileId` 的 PNG 采用失败说明（主人 `png.admit:<panelId>`，且那个面板指向这个文件）。
 * 后端对同一次 409 还会广播 `render.failed`——它的文案是后端原句（「Couldn't render same…」），若晚到会把这一句
 * 「原因 + 出路」顶掉；采用失败的说明才是这一次失败的权威报告，所以这条 SSE 在这种时候让位。
 */
export function admissionToastCovers(fileId: string): boolean {
  const owner = useUiStore.getState().statusOwner
  const prefix = pngAdmitOwner('')
  if (!owner?.startsWith(prefix)) return false
  const id = owner.slice(prefix.length)
  const s = useDocumentStore.getState()
  return [s.doc.objects, ...s.canvases.map((c) => c.objects)].some((objs) =>
    objs.some((o) => o.id === id && o.type === 'panel' && o.fileId === fileId),
  )
}

// 面板删除（任一画布里都找不到了）：它名下的 toast 不该比它活得更久
useDocumentStore.subscribe((s, prev) => {
  if (s.doc === prev.doc && s.canvases === prev.canvases) return
  const { statusOwner, clearStatusOwnedBy } = useUiStore.getState()
  const prefix = pngAdmitOwner('')
  if (!statusOwner?.startsWith(prefix)) return
  const id = statusOwner.slice(prefix.length)
  const exists = [s.doc.objects, ...s.canvases.map((c) => c.objects)].some((objs) => objs.some((o) => o.id === id))
  if (!exists) clearStatusOwnedBy(statusOwner)
})

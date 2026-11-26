import { useEffect, useMemo, useState } from 'react'
import { useRuntimePreview } from '@/hooks/useRuntimePreview'
import { useVariantPng } from '@/hooks/useVariantPng'
import { renderUrl, runtimePreviewUrl } from '@/lib/api'
import { adoptionGuardOf } from '@/lib/artifactValidation'
import { engineTransport } from '@/lib/engineTransport'
import { useAssetStore } from '@/store/assetStore'
import { applyGroups, toOverrides, type PlanSource } from '@/lib/changePlanApply'
import { enginePreviewPngSnapshot, type Manifest } from '@/lib/api'
import { useChangePlanStore, sourceOf, type PlanEntry } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import type { PanelObject } from '@/types/document'

/** 这份方案对应的那张图（只认发起时的文档与画布）；图不在了 = undefined */
export function usePlanPanel(e: Pick<PlanEntry, 'panelId' | 'documentId' | 'loadSeq' | 'canvasId'>): PanelObject | undefined {
  return useDocumentStore((s) => {
    if (s.documentId !== e.documentId || s.loadSeq !== e.loadSeq || s.activeCanvasId !== e.canvasId) return undefined
    const o = s.doc.objects.find((x) => x.id === e.panelId)
    return o?.type === 'panel' ? o : undefined
  })
}

/**
 * 这张图**此刻**的缩略图地址（方案还没出来时卡片 / 审阅意见卡用它当主体；`enabled = false` 时不为它发出图请求，只剩磁盘图地址）：有图内修改就按 override 出图
 * （与版本预览同一条 `useVariantPng`），出来之前 / 没有修改时挂磁盘图；runtime 面板挂最近一次预览。拿不到 = null（卡片显示纸底）。
 */
/** 卡片里的图约 300 CSS px 宽：按 2× 屏取 800（400 在视网膜屏上被放大、发糊；800 是引擎已有的档） */
const THUMB_BUCKET = 800

export function usePanelThumbSrc(panel: PanelObject | undefined, enabled = true): string | null {
  const byId = useAssetStore((s) => s.byId)
  const nonce = useRuntimeAssetStore((s) => s.previewNonce)
  const fileId = panel?.fileId ?? ''
  const rev = panel?.fileKind === 'runtime' ? (nonce[fileId] ?? 0) : (byId[fileId]?.mtime ?? 0)
  const variant = useVariantPng(fileId, panel?.overrides ?? [], THUMB_BUCKET, !!panel && enabled, rev, panel ? adoptionGuardOf(panel) : undefined)
  const byState = panel?.fileKind === 'runtime' && !engineTransport()
  const runtime = useRuntimePreview(fileId, panel?.overrides ?? [], !!panel && enabled && byState, rev)
  if (!panel) return null
  const disk = panel.fileKind === 'runtime' ? (byState ? (runtime.exact === true ? runtime.url : null) : runtimePreviewUrl(fileId, nonce[fileId])) : renderUrl(fileId, THUMB_BUCKET, byId[fileId]?.mtime)
  return variant.url ?? disk
}

/** 一张图的宽高比（读出来之前用 `fallback`）：两张卡的画框按真实图的比例，不拉伸 */
export function useImageAspect(src: string | null | undefined, fallback = 4 / 3): number {
  const [aspect, setAspect] = useState<{ src: string; v: number } | null>(null)
  useEffect(() => {
    if (!src) return
    let live = true
    const img = new Image()
    img.onload = () => {
      if (live && img.naturalWidth > 0 && img.naturalHeight > 0) setAspect({ src, v: img.naturalWidth / img.naturalHeight })
    }
    img.src = src
    return () => {
      live = false
    }
  }, [src])
  return aspect && aspect.src === src ? aspect.v : fallback
}

/** 这张图有没有方案卡要画（助手转录据此不显示空态） */
export function useHasChangePlans(panelId: string | undefined): boolean {
  const documentId = useDocumentStore((s) => s.documentId)
  const loadSeq = useDocumentStore((s) => s.loadSeq)
  const canvasId = useDocumentStore((s) => s.activeCanvasId)
  return useChangePlanStore((s) =>
    s.order.some((k) => {
      const e = s.entries[k]
      if (!e) return false
      // 一键排版的整版方案不挂在某张图上：当前文档 / 画布上有就算（没选图也要看得到）
      if (e.kind === 'layout') return e.documentId === documentId && e.loadSeq === loadSeq && e.canvasId === canvasId
      return !!panelId && e.panelId === panelId
    }),
  )
}

/** 这张图此刻的图源身份（订阅素材修订：脚本 / 素材在磁盘上变了，方案卡跟着转「图已改过」） */
export function usePlanSource(panel: PanelObject | undefined): PlanSource | null {
  const mtime = useAssetStore((s) => (panel ? s.byId[panel.fileId]?.mtime : undefined))
  return useMemo(() => (panel ? sourceOf(panel, mtime) : null), [panel?.fileId, panel?.script, panel?.fileKind, mtime]) // eslint-disable-line react-hooks/exhaustive-deps
}

export interface SelectionPreview {
  /** 按勾选组合出的「修改后」；不需要单独出图（全选 / 一组都没勾）、还没出来、出失败了 = null */
  url: string | null
  loading: boolean
  failed: boolean
  /** 这张图对应的勾选组合（签名）：和此刻的勾选对得上才算数 */
  sig: string | null
}

const DEBOUNCE_MS = 350

/**
 * 弹窗里「修改后」按**勾选的组合**出图（与「应用」写入的完全一致）：勾选 ≠ 全部且不是空时，用状态中立的出图
 * （`enginePreviewPngSnapshot`，带 `applyGroups(base_patches, groups, 勾选)`）渲染；防抖，换勾选 / 关弹窗取消过期请求；
 * 加载中保留旧图（调用方叠轻遮罩），失败回退全量图并标 `failed`。
 */
export function useSelectionPreview(entry: PlanEntry | undefined, active: boolean): SelectionPreview {
  const plan = entry?.plan
  const keys = plan && entry ? plan.groups.filter((g) => !entry.unchecked[g.key]).map((g) => g.key) : []
  const partial = !!plan && keys.length > 0 && keys.length < plan.groups.length
  const sig = partial ? keys.join('\u0000') : null
  const [state, setState] = useState<SelectionPreview>({ url: null, loading: false, failed: false, sig: null })
  const pj = entry?.pj ?? null
  const fileId = entry?.fileId
  useEffect(() => {
    if (!active || !sig || !plan || !fileId) {
      setState((s) => (s.url || s.loading || s.failed ? { url: null, loading: false, failed: false, sig: null } : s))
      return
    }
    const ctrl = new AbortController()
    setState((s) => ({ ...s, loading: true, failed: false, sig }))
    const timer = window.setTimeout(() => {
      const patches = toOverrides(applyGroups(plan.base_patches, plan.groups, keys))
      enginePreviewPngSnapshot(fileId, patches, 1600, ctrl.signal, {}, pj)
        .then((r) => {
          if (!ctrl.signal.aborted) setState({ url: r.url, loading: false, failed: false, sig })
        })
        .catch(() => {
          if (!ctrl.signal.aborted) setState({ url: null, loading: false, failed: true, sig })
        })
    }, DEBOUNCE_MS)
    return () => {
      window.clearTimeout(timer)
      ctrl.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, sig, plan, fileId, pj])
  return state
}

const manifestCache = new Map<string, Manifest>()

/**
 * 这一版「修改后」的元素表（批注点选的来源）：用全量候选（`candidate_patches`）经状态中立的出图取回 manifest，按方案 id 缓存；
 * `enabled` 才取（高级版里第一次把鼠标移到图上 / 进入批注模式时），取不到 = null（悬停点选就不出，批注仍可框选 / 整体）。
 */
export function usePlanManifest(entry: PlanEntry | undefined, enabled: boolean): Manifest | null {
  const plan = entry?.plan
  const id = plan?.id
  const [got, setGot] = useState<{ id: string; manifest: Manifest } | null>(null)
  const fileId = entry?.fileId
  const pj = entry?.pj ?? null
  useEffect(() => {
    if (!enabled || !plan || !id || !fileId) return
    const hit = manifestCache.get(id)
    if (hit) {
      setGot({ id, manifest: hit })
      return
    }
    const ctrl = new AbortController()
    enginePreviewPngSnapshot(fileId, toOverrides(plan.candidate_patches), 1600, ctrl.signal, {}, pj)
      .then((r) => {
        if (ctrl.signal.aborted) return
        if (manifestCache.size >= 16) manifestCache.delete(manifestCache.keys().next().value as string)
        manifestCache.set(id, r.manifest)
        setGot({ id, manifest: r.manifest })
      })
      .catch(() => undefined)
    return () => ctrl.abort()
  }, [enabled, id, fileId, pj]) // eslint-disable-line react-hooks/exhaustive-deps
  return got && got.id === id ? got.manifest : null
}

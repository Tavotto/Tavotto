import { useEffect, useState } from 'react'
import { useRuntimePreview } from '@/hooks/useRuntimePreview'
import { useVariantPng } from '@/hooks/useVariantPng'
import { renderUrl, runtimePreviewUrl } from '@/lib/api'
import { adoptionGuardOf } from '@/lib/artifactValidation'
import { engineTransport } from '@/lib/engineTransport'
import { useAssetStore } from '@/store/assetStore'
import { useChangePlanStore, type PlanEntry } from '@/store/changePlanStore'
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
export function usePanelThumbSrc(panel: PanelObject | undefined, enabled = true): string | null {
  const byId = useAssetStore((s) => s.byId)
  const nonce = useRuntimeAssetStore((s) => s.previewNonce)
  const fileId = panel?.fileId ?? ''
  const rev = panel?.fileKind === 'runtime' ? (nonce[fileId] ?? 0) : (byId[fileId]?.mtime ?? 0)
  const variant = useVariantPng(fileId, panel?.overrides ?? [], 400, !!panel && enabled, rev, panel ? adoptionGuardOf(panel) : undefined)
  const byState = panel?.fileKind === 'runtime' && !engineTransport()
  const runtime = useRuntimePreview(fileId, panel?.overrides ?? [], !!panel && enabled && byState, rev)
  if (!panel) return null
  const disk = panel.fileKind === 'runtime' ? (byState ? (runtime.exact === true ? runtime.url : null) : runtimePreviewUrl(fileId, nonce[fileId])) : renderUrl(fileId, 400, byId[fileId]?.mtime)
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
  return useChangePlanStore((s) => !!panelId && s.order.some((k) => s.entries[k]?.panelId === panelId))
}

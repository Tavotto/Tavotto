import { create } from 'zustand'
import { fetchPanelFontFacts, type PanelFontFacts } from '@/lib/api'
import { engineTransport } from '@/lib/engineTransport'
import { currentProjectId } from '@/lib/session'
import { useAssetStore } from '@/store/assetStore'

/**
 * PDF 原件的字体事实，会话内记忆（T-005 补充，ADR 0139 修订 2026-11-24）。
 *
 * 画布在用户**单选**一张有脚本、没动过的 PDF 原件时才问一次（`request`），据此决定给不给「用脚本重画」角标。
 * 打开项目 / 扫描时不问：要把每份 PDF 的内容流走一遍，图多时白白拖慢打开；也不在这里跑任何脚本。
 *
 * 纪律：
 * - **瞬态**：只在内存，切项目（`clear()`）清空；不写进文档。
 * - 身份 = fileId + 素材清单里的 mtime：文件被脚本重写后 mtime 变，自然重新问。
 * - 读不出（请求失败 / `readable: false`）不说有问题，也不重试到烦人：同一身份只问一次。
 */
type Entry = PanelFontFacts | 'pending' | 'failed'

interface PdfFontFactsStore {
  epoch: number
  byKey: Record<string, Entry>
  request: (fileId: string) => void
  clear: () => void
}

/** 这份 PDF 此刻的身份键（清单里没有它时为 null：不问） */
export function pdfFactsKey(fileId: string): string | null {
  const mtime = useAssetStore.getState().byId[fileId]?.mtime
  return mtime === undefined ? null : `${fileId}\0${mtime}`
}

export const usePdfFontFacts = create<PdfFontFactsStore>((set, get) => ({
  epoch: 0,
  byKey: {},
  request: (fileId) => {
    const key = pdfFactsKey(fileId)
    // MCP App 画布没有 HTTP 服务可问（`engineTransport`）：不给角标，照旧显示原件
    if (key === null || engineTransport() || Object.hasOwn(get().byKey, key)) return
    const born = get().epoch
    const pj = currentProjectId()
    set((s) => ({ byKey: { ...s.byKey, [key]: 'pending' } }))
    const land = (entry: Entry) => {
      if (get().epoch !== born || pj !== currentProjectId()) return
      set((s) => ({ byKey: { ...s.byKey, [key]: entry } }))
    }
    fetchPanelFontFacts(fileId, pj).then(
      (facts) => land(facts.id === fileId ? facts : 'failed'),
      () => land('failed'),
    )
  },
  clear: () => set((s) => ({ epoch: s.epoch + 1, byKey: {} })),
}))

/** 这份 PDF 已知的字体事实；还没问 / 在问 / 读不出时为 undefined */
export function pdfFontFactsOf(fileId: string, byKey = usePdfFontFacts.getState().byKey): PanelFontFacts | undefined {
  const key = pdfFactsKey(fileId)
  const e = key === null ? undefined : byKey[key]
  return typeof e === 'object' && e.readable ? e : undefined
}

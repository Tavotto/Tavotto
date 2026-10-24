import { msg } from '@/i18n'
import { PRODUCT_NAME } from '@/lib/brand'
import type { PdfPagesCapture } from '@/lib/api'

/**
 * 脚本把图写进了多页 PDF（`PdfPages`）时的提醒（#1370）：通知轨上的一条胶囊（只有一句话，不带按钮），
 * **不是**横条、不占画布。Tavotto 把每一页认领成一张图（来源 pyplot，没有原件），导出时可在导出对话框合成回一个多页 PDF（点名的那句；缓冲区 / 多份 PDF 的不点名那句仍说单页）——
 * 用户的多页 PDF 不会被改写，这句话把「为什么多出这几张图」说清楚。
 *
 * - 同一个脚本同一份证据只提醒一次（重跑出同样的结果不再弹）；
 * - 之后这个脚本不再写多页 PDF 了，把它的提醒撤掉。
 */
export const PDF_PAGES_NOTICE_OWNER_PREFIX = 'pdfpages.notice:'
export const pdfPagesNoticeOwner = (pj: string | null | undefined, script: string) =>
  `${PDF_PAGES_NOTICE_OWNER_PREFIX}${pj ?? ''}:${script}`

const noticed = new Set<string>()

/** 同 `workdirHintNotice`：依赖由调用方递进来，本文件不 import store（import 环只减不增） */
export interface PdfPagesNoticeDeps {
  ui: {
    setStatus: (
      status: ReturnType<typeof msg> | null,
      tone?: 'info',
      opts?: { owner?: string },
    ) => void
    clearStatusOwnedBy: (owner: string) => void
  }
}

export function noticePdfPages(
  owner: string,
  info: PdfPagesCapture[] | null | undefined,
  deps: PdfPagesNoticeDeps,
): void {
  const shown = (info ?? []).filter((p) => p.captured > 0 || (p.omitted ?? 0) > 0)
  if (shown.length === 0) {
    deps.ui.clearStatusOwnedBy(owner)
    return
  }
  const captured = shown.reduce((n, p) => n + p.captured, 0)
  const omitted = shown.reduce((n, p) => n + (p.omitted ?? 0), 0)
  // 只有一份命名的 PDF 才点名；多份 / 写进缓冲区的用不带文件名的那句
  const name = shown.length === 1 ? shown[0].name : null
  const parts = shown.map((p) => `${p.name ?? ''}:${p.pages}:${p.captured}:${p.omitted ?? 0}`)
  const signature = `${owner}|${parts.join(',')}`
  if (noticed.has(signature)) return
  noticed.add(signature)
  const values = {
    count: captured + omitted,
    name,
    product: PRODUCT_NAME,
    omittedNote: omitted > 0 ? msg('engine.pdfPagesOmittedNote', { count: omitted }, 'errors') : '',
  }
  deps.ui.setStatus(
    msg(name ? 'engine.pdfPagesNotice' : 'engine.pdfPagesNoticeAnon', values, 'errors'),
    'info',
    { owner },
  )
}

/** 测试用：忘掉「提醒过」的记账 */
export function resetPdfPagesNotices(): void {
  noticed.clear()
}

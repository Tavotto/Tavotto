import { msg } from '@/i18n'
import type { StatusAction } from '@/store/uiStore'

/**
 * 写回被拒（后端 409 `write_back_multipage_pdf`）时的提醒：目标是脚本用 `PdfPages` 写的多页 PDF（#1370）。
 * 写回只能把**一张图**刻进文件，覆盖它会丢掉其余各页，所以一律不写；通知轨上的一条胶囊（一句话 + 一个「导出」，
 * 按钮打开现有导出对话框），**不是**横条、不占画布。
 *
 * - 同一个文件只挂一条（主人按文件名区分，重复点写回不叠）；
 * - 胶囊是要用户回头处理的一句话，不自己消失（挂到用户点 × 或被同主人撤掉）。
 */
export const WRITE_BACK_MULTIPAGE_OWNER_PREFIX = 'writeback.multipage:'
export const writeBackMultipageOwner = (file: string) => `${WRITE_BACK_MULTIPAGE_OWNER_PREFIX}${file}`

/** 与 `workdirHintNotice` 同一条纪律：依赖由调用方递进来，本文件不 import store（import 环只减不增） */
export interface WriteBackMultipageNoticeDeps {
  ui: {
    setStatus: (
      status: ReturnType<typeof msg> | null,
      tone?: 'info',
      opts?: { owner?: string; action?: StatusAction },
    ) => void
    setExportOpen: (open: boolean) => void
  }
}

export function noticeWriteBackMultipage(file: string, deps: WriteBackMultipageNoticeDeps): void {
  deps.ui.setStatus(msg('engine.writeBackMultipagePdfNotice', { file }, 'errors'), 'info', {
    owner: writeBackMultipageOwner(file),
    action: {
      label: msg('engine.writeBackMultipagePdfExport', undefined, 'errors'),
      run: () => deps.ui.setExportOpen(true),
    },
  })
}

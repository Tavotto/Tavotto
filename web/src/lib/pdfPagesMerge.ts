/**
 * 「合成一个多页 PDF」能不能提供、提供什么（#1370，PdfPages 导出）。纯函数，不读 store。
 *
 * 判据的主语是**这次要导的那几张图是不是出自脚本的同一份 `PdfPages`**：
 * - 每张图是 runtime 素材（`RuntimeAssetInfo`：`script` + `stem`），且都属于同一个脚本；
 * - 该脚本这次运行的 `pdf_pages` 里有一项，它的 `page_stems` 含这几张图的 stem，且 `complete`
 *   （每一页都有 stem 且都记上了观察——缺页的多页 PDF 页序不可信，宁可不提供）、有项目内的路径（文件名出处）。
 * - 所选素材的运行配置（`run_config`）必须与产出该账本的那次运行一致（账本只属于那一次运行）。
 * 页序跟脚本走：按 `page_stems` 的顺序排，**同一张图写了多页就重复**，页序不可在界面上改。
 * 不满足就回 `null`（对话框不出现这个勾选）。
 */
import type { PdfPagesCapture, RuntimeAssetInfo } from '@/lib/api'

export interface MergePdfPlan {
  /** 多页 PDF 的基名（不含扩展名），取脚本写的 PdfPages 的文件名 */
  name: string
  /** 按页序的 figureId（同一张图写多页时重复） */
  figureIds: string[]
}

/** 目的地路径 → 基名：只取最后一段、去掉 `.pdf` */
export function pdfPagesBaseName(path: string): string {
  const last = path.split(/[\\/]/).pop() ?? ''
  return last.replace(/\.pdf$/i, '')
}

export function mergePdfPlan(
  figureIds: readonly string[],
  assets: readonly RuntimeAssetInfo[] | null,
  pdfPagesOf: (script: string) => { pages: readonly PdfPagesCapture[]; runConfig: string } | undefined,
): MergePdfPlan | null {
  if (figureIds.length < 2 || !assets) return null
  const byId = new Map(assets.map((a) => [a.id, a]))
  const picked = figureIds.map((id) => byId.get(id))
  if (picked.some((a) => !a)) return null
  const scripts = new Set(picked.map((a) => a!.script))
  if (scripts.size !== 1) return null
  const [script] = [...scripts]
  const stemToId = new Map(picked.map((a) => [a!.stem, a!.id]))
  if (stemToId.size !== picked.length) return null // 两张图同一个 stem（不同运行配置）：对不上页，不提供
  const ledger = pdfPagesOf(script)
  // 账本只属于产出它的那一次运行：素材的运行配置必须与之一致（无参数 = ''），
  // 否则会把配置 X 的 a 和配置 Y 的 b 拼成一个两次运行都没产出过的混合 PDF
  if (!ledger || picked.some((a) => (a!.run_config ?? '') !== ledger.runConfig)) return null
  for (const entry of ledger.pages) {
    const stems = entry.page_stems
    if (!entry.path || entry.complete !== true || !stems) continue
    if (![...stemToId.keys()].every((s) => stems.includes(s))) continue
    const name = pdfPagesBaseName(entry.path)
    const ids = stems.flatMap((s) => (s && stemToId.has(s) ? [stemToId.get(s)!] : []))
    if (!name || ids.length < 2) continue
    return { name, figureIds: ids }
  }
  return null
}

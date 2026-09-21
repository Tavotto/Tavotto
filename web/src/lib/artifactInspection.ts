/**
 * 导出回执里产物检查结果的**唯一解读**（ADR 0068；RC-088「未核验不显示绿」）。
 *
 * 服务端给的是逐项四值（`verified / failed / unknown / not_applicable`），界面要的是
 * 「这一件怎么画」。规则只有一份、在这里：
 *
 * * 没有 manifest（老服务端、检查器没跑）→ `unknown`：整份未核验；
 * * 有 `failed` → `failed`（standard 政策下可选项失败仍会交付，但必须红着说出来）；
 * * 没有失败、有 `unknown` → `unknown`，并列出未核验的项；
 * * 只有全部可判项都 `verified` 才是 `verified`——一项 `verified` 都没有（全 `not_applicable`）
 *   也不算，绿色只给量到了的东西。
 *
 * `not_applicable` 不出现在任何列表里（不画）。
 */
import type { ArtifactManifestSummary } from './api'

export type InspectionTone = 'verified' | 'unknown' | 'failed'

export interface InspectionState {
  tone: InspectionTone
  /** 判定为 failed 的检查名（服务端的键，界面再翻译） */
  failed: string[]
  /** 判定为 unknown 的检查名 */
  unknown: string[]
  /** 判定为 verified 的检查名 */
  verified: string[]
}

export function inspectionState(
  manifest: ArtifactManifestSummary | null | undefined,
): InspectionState {
  const checks = manifest?.checks ?? {}
  const failed = Object.keys(checks).filter((k) => checks[k] === 'failed')
  const unknown = Object.keys(checks).filter((k) => checks[k] === 'unknown')
  const verified = Object.keys(checks).filter((k) => checks[k] === 'verified')
  if (!manifest || manifest.verdict === 'rejected' || failed.length) {
    return { tone: failed.length ? 'failed' : 'unknown', failed, unknown, verified }
  }
  if (unknown.length || !verified.length) return { tone: 'unknown', failed, unknown, verified }
  return { tone: 'verified', failed, unknown, verified }
}

/** 检查名 → i18n 键（`dialogs:export.inspection.check.<name>`）；没有条目的名字原样显示 */
export const CHECK_NAMES = [
  'integrity',
  'size',
  'carrier',
  'fonts_embedded',
  'text_layer',
  'image_ppi',
  'clipping',
  'dpi_tag',
  'raster_density',
] as const

/**
 * 一批已发布文件的核验汇总（Codex 内嵌画布的一句话提示用）：哪些文件有失败项、哪些文件
 * 有未核验项——两组都按文件名点名，一组都不省。`verified` 的文件不出现（没什么要提醒的）。
 */
export function inspectionRollup(
  entries: readonly { path?: string | null; format?: string; manifest?: ArtifactManifestSummary | null }[],
): { failed: string[]; unknown: string[] } {
  const failed: string[] = []
  const unknown: string[] = []
  for (const e of entries) {
    const name = (e.path ?? '').split(/[\\/]/).pop() || e.format || '?'
    const s = inspectionState(e.manifest)
    if (s.tone === 'failed') failed.push(name)
    else if (s.tone === 'unknown') unknown.push(name)
  }
  return { failed, unknown }
}

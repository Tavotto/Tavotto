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
  'text_size',
] as const

/**
 * 成品实测字号（`checks.text_size`，ADR 0201）那一项要说的一句话：
 *
 * * `below`：失败时用界面上会显示的两个数说清「最小的字多小、规范要多少」；
 * * `unmeasured`：有规范下限，但有文字画成了轮廓（Tavotto 的公式）或 em 不明，量不全——单说一句，免得用户
 *   以为出了错；
 * * 其余（通过、没规范、老服务端没带 `text_size`）→ `null`：不说话。通过与「没规范只记录」都不上界面
 *   （核验只说失败，2026-11-06 用户）。
 */
export type TextSizeNote =
  | { kind: 'below'; min: string; floor: string }
  | { kind: 'unmeasured' }

export function textSizeNote(manifest: ArtifactManifestSummary | null | undefined): TextSizeNote | null {
  const verdict = manifest?.checks?.text_size
  const ts = manifest?.text_size
  if (!ts) return null
  if (verdict === 'failed' && ts.reason === 'below_min' && isNum(ts.min_pt) && isNum(ts.floor_pt)) {
    return { kind: 'below', ...ptPair(ts.min_pt, ts.floor_pt) }
  }
  if (verdict === 'unknown' && ts.reason === 'unmeasured_runs') return { kind: 'unmeasured' }
  return null
}

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/** 去掉末尾的 0：`5` 不写成 `5.0`，`5.25` 原样 */
const trim = (v: number, digits: number) => String(Number(v.toFixed(digits)))

/**
 * 「4.9 pt，低于规范的 5 pt」的两个数：下限照规范写（至多两位小数）；最小字号先给一位小数，
 * 四舍五入后不再小于下限（4.996 → 5.0 对 5）就多给一位，直到读得出「更小」——绝不显示「5 pt 低于 5 pt」。
 */
export function ptPair(minPt: number, floorPt: number): { min: string; floor: string } {
  const floor = trim(floorPt, 2)
  for (const digits of [1, 2, 3]) {
    const min = trim(minPt, digits)
    if (Number(min) < Number(floor)) return { min, floor }
  }
  return { min: String(minPt), floor }
}

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

/**
 * 内嵌画布「已导出」提示的语气：有失败项就是 `bad`（红），只有未核验项仍是中性——
 * 「未核验」不是错误，「核验未通过」是。
 */
export function noticeTone(rollup: { failed: string[]; unknown: string[] }): 'ok' | 'bad' {
  return rollup.failed.length ? 'bad' : 'ok'
}

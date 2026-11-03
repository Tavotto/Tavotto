/**
 * 思考小球的形态与尺寸（ADR 0164）。
 *
 * 形态是**封闭集**，按「这一步在干什么」选，不为单个调用处新加：
 * - `breathing`  思考中；纯思考的过程组结束后（settled）
 * - `solving`    运行脚本
 * - `composing`  读 / 改脚本等其他动作
 * - `connecting` 发出后还没回第一个字
 * - `listening`  带动作的过程组结束后（settled）
 * - `searching`  改图助手还没开始对话的空白面板（64px）
 *
 * 尺寸只有两档：16（过程行图标槽）与 64（空白面板）。64 用上游原样的调参；16 由上游 20 那档派生——
 * 点数约 ×0.55–0.7、点径约 ×1.3：小尺寸下点少一些、大一些才不糊成一团灰（用户 2026-11-03 演示页确认）。
 */
import { BASE_PROFILES, scaleCounts, scaleRadii, type ModeOpts } from './engine/profiles'
import type { ModeKey } from './engine/registry'

export type OrbForm = 'breathing' | 'solving' | 'composing' | 'connecting' | 'listening' | 'searching'
export type OrbSize = 16 | 64

export const FORM_TO_MODE: Record<OrbForm, ModeKey> = {
  breathing: 'ring',
  solving: 'rubik',
  composing: 'ribbon',
  connecting: 'web',
  listening: 'wave',
  searching: 'globe',
}

interface Preset {
  speed: number
  count: number
  size: number
  extra?: ModeOpts
}

/** 上游 thinking-orbs 0.3.2 的 64 / 20 调参（只留用到的六种模式） */
const UPSTREAM: Record<OrbForm, { 64: Preset; 20: Preset }> = {
  breathing: {
    64: { speed: 3.24, count: 0.25, size: 0.956, extra: { spin: 0, bandMul: 3.627, wobMul: 0.368 } },
    20: { speed: 3.78, count: 0.028, size: 1.622, extra: { spin: 0, bandMul: 3.968, wobMul: 0.565 } },
  },
  solving: {
    64: { speed: 1.82, count: 0.35, size: 1.05 },
    20: { speed: 1.95, count: 0.088, size: 1.9 },
  },
  composing: {
    64: { speed: 2.34, count: 0.25, size: 0.85, extra: { spin: 0, bandMul: 3.9, wobMul: 1 } },
    20: { speed: 3.12, count: 0.051, size: 1.073, extra: { spin: 0, bandMul: 4.94, wobMul: 1 } },
  },
  connecting: {
    64: { speed: 3.315, count: 1.35, size: 0.95 },
    20: { speed: 6.63, count: 0.25, size: 1.52 },
  },
  listening: {
    64: { speed: 4.388, count: 0.341, size: 1 },
    20: { speed: 3.998, count: 0.105, size: 1.6 },
  },
  searching: {
    64: { speed: 2.015, count: 0.42, size: 1.15, extra: { scanMul: 4.08, dimBase: 0.45 } },
    20: { speed: 2.665, count: 0.105, size: 1.75, extra: { scanMul: 4.335, dimBase: 0.45 } },
  },
}

/** 16px 由 20 那档派生：[点数倍率, 点径倍率] */
const DERIVE_16: Record<OrbForm, [number, number]> = {
  breathing: [0.7, 1.3],
  solving: [0.6, 1.3],
  composing: [0.6, 1.3],
  connecting: [0.7, 1.3],
  listening: [0.55, 1.35],
  searching: [0.55, 1.35],
}

/** 结束后（settled）那一相的起点：小球从一帧好看的姿态开始慢慢动；减少动态效果时就停在这一帧 */
export const REST_PHASE: Record<OrbForm, number> = {
  breathing: 1.1,
  solving: 0.6,
  composing: 0.6,
  connecting: 0.6,
  listening: 0.6,
  searching: 0.6,
}

/** 全局放慢（演示页拍板 0.75×）与结束后再减半 */
export const BASE_SPEED = 0.75
export const SETTLED_SPEED = 0.5

export interface ResolvedPreset {
  mode: ModeKey
  speed: number
  opts: ModeOpts
}

const cache = new Map<string, ResolvedPreset>()

export function resolvePreset(form: OrbForm, size: OrbSize): ResolvedPreset {
  const key = `${form}-${size}`
  const hit = cache.get(key)
  if (hit) return hit
  const mode = FORM_TO_MODE[form]
  const p = size === 64 ? UPSTREAM[form][64] : UPSTREAM[form][20]
  let count = p.count
  let radius = p.size
  if (size === 16) {
    count *= DERIVE_16[form][0]
    radius *= DERIVE_16[form][1]
  }
  let opts: ModeOpts = { ...BASE_PROFILES[mode] }
  if (count !== 1) opts = scaleCounts(opts, count)
  if (radius !== 1) opts = scaleRadii(opts, radius)
  if (p.extra) opts = { ...opts, ...p.extra }
  const resolved = { mode, speed: p.speed, opts }
  cache.set(key, resolved)
  return resolved
}

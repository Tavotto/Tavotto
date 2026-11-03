/**
 * 内嵌画布导出的「确认」：**永远可导出、不警告**，未处理的项由后端记进回执（Codex #1295 P2）。
 *
 * 缓存的预检只是上一次的结论：干净的缓存之后编辑引入了阻断 / 无法核验项（`preflightStale`），
 * 后端按**当前图**重跑预检，会回 `preflight_blocked`——再点也一样。所以：
 *
 * * 预检过期，或缓存里就有阻断 / 无法核验项 → 第一次就带 `explicit_confirm: true`；
 * * 仍收到 `preflight_blocked`（缓存说干净、实际不是）→ 带 `explicit_confirm: true` **只重发一次**（防循环）；
 *   重发的成功回包里带的是后端此刻的预检结论，界面据此更新。
 */
import { EngineError } from '@/lib/api'

export interface PreflightLike {
  errors?: unknown[]
  not_verifiable?: unknown[]
}

export function exportNeedsConfirm(preflight: PreflightLike | null, stale: boolean): boolean {
  return stale || (!!preflight && ((preflight.errors ?? []).length > 0 || (preflight.not_verifiable ?? []).length > 0))
}

export async function exportWithAutoConfirm<T>(
  call: (explicitConfirm: boolean) => Promise<T>,
  confirmFirst: boolean,
): Promise<T> {
  try {
    return await call(confirmFirst)
  } catch (err) {
    if (!confirmFirst && err instanceof EngineError && err.code === 'preflight_blocked') return call(true)
    throw err
  }
}

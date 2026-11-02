/**
 * 打开项目后的后台预热选图规则（ADR 0159）。纯函数，便于测试；触发与节流在 `hooks/usePrewarm.ts`。
 *
 * 只选**一张**：画布上第一个带脚本的面板，且它此刻没有在渲染（正在 building 的那一路本来就在取会话）。
 */
import type { CanvasObject } from '@/types/document'

export function pickPrewarmTarget(
  objects: readonly CanvasObject[],
  building: Readonly<Record<string, unknown>>,
): string | null {
  for (const o of objects) {
    if (o.type !== 'panel' || !o.script) continue
    if (o.fileId in building) return null // 前台已经在渲染一张图：不插手
    return o.fileId
  }
  return null
}

/**
 * 面板内容变换只有一份（Codex #679）：画布的 CSS 与缩略图的 canvas 2D 从同一处取，
 * 顺序都是「先在内容空间翻转，再旋转落位」。
 */
import { describe, expect, it } from 'vitest'
import { applyPanelTransform, panelContentTransform, panelTransformCss } from './panelTransform'

describe('panelContentTransform / panelTransformCss', () => {
  it.each([
    [{}, undefined],
    [{ rotation: 90 }, 'rotate(90deg)'],
    [{ flipH: true }, 'scale(-1, 1)'],
    [{ flipV: true }, 'scale(1, -1)'],
    [{ rotation: 270, flipH: true, flipV: true }, 'rotate(270deg) scale(-1, -1)'],
    // 与画布同一口径：旋转归一到 90° 步进
    [{ rotation: 450 }, 'rotate(90deg)'],
  ] as const)('%j → %s', (o, css) => {
    expect(panelTransformCss(panelContentTransform(o as never))).toBe(css)
  })
})

describe('applyPanelTransform', () => {
  it('canvas 上按「rotate 再 scale」调用（= 先翻转、再旋转，与 CSS 从右往左同一个结果）', () => {
    const calls: string[] = []
    const ctx = {
      rotate: (a: number) => calls.push(`rotate(${Math.round((a * 180) / Math.PI)})`),
      scale: (x: number, y: number) => calls.push(`scale(${x},${y})`),
    } as unknown as CanvasRenderingContext2D
    applyPanelTransform(ctx, panelContentTransform({ rotation: 90, flipH: true }))
    expect(calls).toEqual(['rotate(90)', 'scale(-1,1)'])
    calls.length = 0
    applyPanelTransform(ctx, panelContentTransform({ flipV: true }))
    expect(calls).toEqual(['scale(1,-1)'])
    calls.length = 0
    applyPanelTransform(ctx, panelContentTransform({}))
    expect(calls).toEqual([])
  })
})

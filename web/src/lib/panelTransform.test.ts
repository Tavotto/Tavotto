/**
 * 面板内容变换只有一份（Codex #679）：画布的 CSS 与缩略图的 canvas 2D 从同一处取，
 * 顺序都是「先在内容空间翻转，再旋转落位」。
 */
import { describe, expect, it } from 'vitest'
import {
  applyPanelTransform,
  contentToPageVec,
  pageToContentVec,
  panelContentTransform,
  panelTransformCss,
} from './panelTransform'
import { elementSnapCandidates } from './elementGeom'
import type { Manifest } from './api'
import type { PanelObject } from '@/types/document'

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

// 向量版与 CSS 同序（先翻转、再旋转）：图内元素的框、读数、吸附线、点选 / 拖动 / 方向键都走这一对（#832 / #833 评审）
describe('contentToPageVec / pageToContentVec', () => {
  it.each([
    [{}, [3, 1]],
    [{ flipH: true }, [-3, 1]],
    [{ flipV: true }, [3, -1]],
    [{ rotation: 90 }, [-1, 3]],
    // 先翻转 (−3, 1) 再转 90° → (−1, −3)；顺序反了是 (1, 3)
    [{ rotation: 90, flipH: true }, [-1, -3]],
  ] as const)('%j：内容 (3, 1) → 页面 %j，且逆回去', (o, page) => {
    const t = panelContentTransform(o as never)
    const [x, y] = contentToPageVec(t, 3, 1)
    expect(x).toBeCloseTo(page[0], 9)
    expect(y).toBeCloseTo(page[1], 9)
    const [u, v] = pageToContentVec(t, x, y)
    expect(u).toBeCloseTo(3, 9)
    expect(v).toBeCloseTo(1, 9)
  })

  it('翻转面板上的图内中心线吸附候选落在元素看得见的位置（elementSnapCandidates）', () => {
    const manifest = {
      stem: 'F', size_mm: [100, 80],
      elements: [
        { gid: 'axes_0', role: 'axes', label: 'a', bbox: [0.1, 0.1, 0.2, 0.2], editable: [], draggable: false, resizable: true },
      ],
    } as unknown as Manifest
    const panel = { id: 'p', type: 'panel', x: 0, y: 0, w: 100, h: 80, overrides: [], flipH: true } as unknown as PanelObject
    // 子图中心在内容 x = 20 mm；水平镜像后在 80 mm
    expect(elementSnapCandidates(panel, manifest).xs).toEqual([80])
  })
})

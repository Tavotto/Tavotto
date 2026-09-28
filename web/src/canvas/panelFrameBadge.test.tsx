/**
 * 画布角标「按 figsize 显示」的提示（ADR 0098 §三，#688）：通常说「在右侧属性里可以改用原图的图幅」；
 * 旧裁剪与脚本图幅不相交时属性里的按钮是禁用的，提示改说先调整或重置裁剪——不许指向一个点不了的动作。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return { ...real, fetchRuntimeStatus: vi.fn(), fetchRuntimeAssets: vi.fn() }
})

import { t } from '@/i18n'
import { PanelView } from '@/canvas/PanelView'
import { LEGACY_FRAME_OVERRIDE, type ManifestFrame } from '@/lib/figureFrame'
import { useRenderStore } from '@/store/renderStore'
import { seedExactRender } from '@/test/renderFixtures'
import type { CropRect, PanelObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

// 脚本图幅是 figsize 的左半边
const HALF: ManifestFrame = { source: 'savefig', active: false, figsize_mm: [80, 57.6], savefig_mm: [0, 0, 40, 57.6] }

const croppedPanel = (crop: CropRect): PanelObject => ({
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 57.6,
  x: 10,
  y: 10,
  w: 80 * crop.w,
  h: 57.6 * crop.h,
  crop,
  script: 'fig1.py',
  overrides: [{ ...LEGACY_FRAME_OVERRIDE }],
  figureFrame: 1,
})

const hintOf = (key: string) => t(`panelBadge.${key}`, { ns: 'workspace' })

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  useRenderStore.getState().clear()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

function badgeHint(p: PanelObject): string | null {
  seedExactRender(p, { stem: 'Fig1', size_mm: [80, 57.6], elements: [], frame: HALF })
  act(() => root.render(<PanelView obj={p} />))
  expect(host.textContent).toContain(hintOf('frameLegacy'))
  return host.querySelector('[title]')?.getAttribute('title') ?? null
}

describe('「按 figsize 显示」角标的提示', () => {
  it('旧裁剪与脚本图幅不相交：提示先调整或重置裁剪，不说「可以改用」', () => {
    expect(badgeHint(croppedPanel({ x: 0.6, y: 0.2, w: 0.3, h: 0.5 }))).toBe(hintOf('frameLegacyBlockedHint'))
  })

  it('对照：部分相交时照常提示去属性里改用', () => {
    expect(badgeHint(croppedPanel({ x: 0.4, y: 0.2, w: 0.3, h: 0.5 }))).toBe(hintOf('frameLegacyHint'))
  })
})

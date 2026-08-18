/**
 * 面板显示按**自己那份变体**取图（Phase F）。
 *
 * 这条是整个阶段的用户可见症状：画布上放两个同文件不同 override 的副本时，
 * 旧实现两个面板都用 `<img src=/api/engine/png>`——那个端点从 live figure
 * 出图，谁最后渲染谁说了算，于是「一个面板显示了另一个面板的图」。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PanelView } from './PanelView'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import type { Manifest } from '@/lib/api'
import type { PanelObject } from '@/types/document'

const previewPng = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  enginePreviewPng: (id: string, patches: unknown[], bucket: number) => {
    previewPng(id, patches, bucket)
    return Promise.resolve(new Blob(['png']))
  },
}))

const panel = (id: string, value: string): PanelObject =>
  ({
    id,
    type: 'panel',
    x: 0,
    y: 0,
    w: 100,
    h: 80,
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW: 100,
    nativeH: 80,
    script: 'fig.py',
    overrides: [{ gid: 'title', prop: 'text', value }],
  }) as unknown as PanelObject

const manifest = { stem: 'Fig1', size_mm: [100, 80], elements: [] } as unknown as Manifest

let container: HTMLDivElement
let root: Root
let urls = 0

beforeEach(() => {
  previewPng.mockClear()
  urls = 0
  // jsdom 没有 objectURL；给一个每次都不同的桩，正好用来断言两张图不是同一张
  URL.createObjectURL = vi.fn(() => `blob:mock/${++urls}`)
  URL.revokeObjectURL = vi.fn()
  useRenderStore.getState().clear()
  useUiStore.setState({ elementPanelId: null })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

/** 两个变体都已经渲染过（各有各的 manifest 与 rev） */
function seed(...panels: PanelObject[]) {
  const store = useRenderStore.getState()
  for (const p of panels) {
    store.patch(renderKeyOf(p), {
      fileId: p.fileId,
      manifest,
      rev: 1,
      status: 'ready',
      lastPatches: JSON.stringify(p.overrides),
    })
  }
}

describe('PanelView：引擎位图按自己的 overrides 取', () => {
  it('两个同文件不同 override 的面板各取各的图', async () => {
    const a = panel('a', 'AAA')
    const b = panel('b', 'BBB')
    seed(a, b)

    await act(async () => {
      root.render(
        <>
          <PanelView obj={a} />
          <PanelView obj={b} />
        </>,
      )
    })

    // 两次请求，各带自己的 patches（旧实现是同一个 GET /api/engine/png）
    expect(previewPng).toHaveBeenCalledTimes(2)
    expect(previewPng.mock.calls.map((c) => c[1])).toEqual([a.overrides, b.overrides])

    // 排除 aria-hidden 的那层：换图交叉淡入期间每个面板会多挂一张**旧图**做淡出
    // （CrossfadeImage），它带 alt="" + aria-hidden，不是「这个面板显示的图」
    const srcs = [...container.querySelectorAll('img:not([aria-hidden])')].map((el) =>
      el.getAttribute('src'),
    )
    expect(srcs).toHaveLength(2)
    expect(new Set(srcs).size).toBe(2)          // 不是同一张图
    expect(srcs.every((s) => s?.startsWith('blob:'))).toBe(true)
  })

  it('没有图内修改的面板照旧走 /api/render，不惊动引擎', async () => {
    const plain = { ...panel('c', 'x'), overrides: [] } as PanelObject
    await act(async () => {
      root.render(<PanelView obj={plain} />)
    })
    expect(previewPng).not.toHaveBeenCalled()
    expect(container.querySelector('img')?.getAttribute('src')).toContain('/api/render')
  })

  it('图内编辑态用自己变体的 SVG（要 gid 命中）', async () => {
    const a = panel('a', 'AAA')
    seed(a)
    useRenderStore.getState().patch(renderKeyOf(a), {
      svg: '<svg data-variant="AAA"></svg>',
    })
    useUiStore.setState({ elementPanelId: 'a' })

    await act(async () => {
      root.render(<PanelView obj={a} />)
    })

    expect(container.querySelector('[data-element-svg="a"]')?.innerHTML).toContain('AAA')
    expect(previewPng).not.toHaveBeenCalled()
  })
})

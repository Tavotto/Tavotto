/**
 * 面板换图的交叉淡入（CrossfadeImage）。
 *
 * 这里守的是一条很容易被「简化」掉的实现约束：**淡出的那一层必须是原来
 * 那个 `<img>` 节点，不能现建一个指向旧 URL 的新节点**。
 * 引擎位图是 blob: URL，`useEnginePngBlob` 在新 URL 到手的同一刻就把旧的
 * `revokeObjectURL` 了——现建的 `<img>` 指过去只会加载失败、露出一块空白，
 * 比不做淡入淡出还糟；而已经解码过的那个节点即使 URL 失效照样画得出来。
 *
 * jsdom 不加载图片、也不跑动画，所以这里断言的是**结构与节点身份**：
 * 谁带真实 alt、谁是 aria-hidden 的装饰层、以及换图前后节点是不是同一个。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PanelView } from './PanelView'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import type { Manifest } from '@/lib/api'
import type { PanelObject } from '@/types/document'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  enginePreviewPng: () => Promise.resolve(new Blob(['png'])),
}))

const manifest = { stem: 'Fig1', size_mm: [100, 80], elements: [] } as unknown as Manifest

const panel = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 100,
    h: 80,
    name: '面板一',
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW: 100,
    nativeH: 80,
    script: 'fig.py',
    overrides: [{ gid: 'title', prop: 'text', value: 'A' }],
  }) as unknown as PanelObject

let container: HTMLDivElement
let root: Root
let urls = 0

beforeEach(() => {
  urls = 0
  URL.createObjectURL = vi.fn(() => `blob:mock/${++urls}`)
  URL.revokeObjectURL = vi.fn()
  useRenderStore.getState().clear()
  useUiStore.setState({ elementPanelId: null })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  vi.unstubAllGlobals()
  act(() => root.unmount())
  container.remove()
})

function seed(p: PanelObject, rev: number) {
  useRenderStore.getState().patch(renderKeyOf(p), {
    fileId: p.fileId,
    manifest,
    rev,
    status: 'ready',
    lastPatches: JSON.stringify(p.overrides),
  })
}

const imgs = () => [...container.querySelectorAll('img')]
const live = () => container.querySelector('img:not([aria-hidden])') as HTMLImageElement
const fading = () => container.querySelector('img[aria-hidden]') as HTMLImageElement | null

describe('CrossfadeImage', () => {
  it('换图时旧图留一层做淡出，真实 alt 始终只有一张图带着', async () => {
    const p = panel()
    seed(p, 1)
    await act(async () => {
      root.render(<PanelView obj={p} />)
    })

    // 初始是磁盘图，引擎位图到位后换成 blob —— 旧的那张变成装饰层
    expect(live().getAttribute('src')).toBe('blob:mock/1')
    expect(live().getAttribute('alt')).toBe('面板一')

    const decor = fading()
    expect(decor, '换图后应当留一层旧图做淡出').not.toBeNull()
    expect(decor!.getAttribute('src')).not.toBe('blob:mock/1')
    // 装饰层不进无障碍树、不吃事件
    expect(decor!.getAttribute('alt')).toBe('')
    expect(decor!.className).toContain('pointer-events-none')
    expect(imgs().filter((el) => el.getAttribute('alt') === '面板一')).toHaveLength(1)
  })

  it('再换一次：淡出的是**原来那个节点**，不是新建的（blob URL 已被 revoke）', async () => {
    const p = panel()
    seed(p, 1)
    await act(async () => {
      root.render(<PanelView obj={p} />)
    })
    const first = live()
    expect(first.getAttribute('src')).toBe('blob:mock/1')

    // 新一版渲染 → 新 blob；旧 objectURL 在同一刻被 revoke
    seed(p, 2)
    await act(async () => {
      root.render(<PanelView obj={p} />)
    })

    expect(live().getAttribute('src')).toBe('blob:mock/2')
    // 关键：刚才那个已经解码过的节点原地变成淡出层，没有被卸载重建
    expect(fading()).toBe(first)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock/1')
  })

  it('reduced-motion：不挂淡出层，行为与从前一致', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: q.includes('prefers-reduced-motion'),
      media: q,
      addEventListener() {},
      removeEventListener() {},
    }))
    const p = panel()
    seed(p, 1)
    await act(async () => {
      root.render(<PanelView obj={p} />)
    })
    expect(live().getAttribute('src')).toBe('blob:mock/1')
    expect(fading()?.className ?? '').not.toContain('animate-crossfade-out')
  })
})

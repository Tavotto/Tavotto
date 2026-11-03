/**
 * runtime 面板冷画布的 cache 预览按**此刻的 overrides** 取（QA-WIN-21）。
 *
 * 缺陷：图例外侧右 → 外侧左 → 撤销回右 → 另存 → 重启，冷画布与导出预览显示「左」。
 * 根因：撤销回一个前端自己已画过的变体时 `useEngineSync` 见 `lastPatches === want` 就不发渲染请求，
 * 后端 runtime cache 的 `preview.svg`（「最近一次渲染」）停在撤销前的左；重开文档（runtime 面板
 * lazy rehydrate，不自动执行脚本）画布挂的就是它。
 *
 * 主语：冷画布挂上的预览，问后端时带的是**这个面板此刻的 overrides**（谁的、哪个时刻），且后端说
 * 「不是这个状态的图」时画布出「近似预览」角标，而不是安静地挂着。
 * 反证：把 PanelView 里 fileSrc 的 runtime 分支改回 `panelSrc(…)`（GET 最近一次）→ 第一条红；
 * 把 `approxPreview` 里 `runtimePreview.exact !== true` 那一项拿掉 → 第二条红。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const previewForState = vi.fn()

vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return {
    ...real,
    fetchRuntimeStatus: vi.fn(),
    fetchRuntimeAssets: vi.fn(),
    fetchRuntimePreviewForState: (id: string, patches: unknown[]) => previewForState(id, patches),
  }
})

import { PanelView } from '@/canvas/PanelView'
import { useRenderStore } from '@/store/renderStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import type { PanelObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const FILE_ID = 'runtime:two.py#two'
const LEGEND = 'axes_0.legend'
const RIGHT = [
  { gid: LEGEND, prop: 'loc', value: 'center left' },
  { gid: LEGEND, prop: 'loc_anchor', value: [1.02, 0.5] },
]
const LEFT = [
  { gid: LEGEND, prop: 'loc', value: 'center right' },
  { gid: LEGEND, prop: 'loc_anchor', value: [-0.02, 0.5] },
]

const panel = (overrides: unknown[]): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    fileId: FILE_ID,
    fileKind: 'runtime',
    x: 0,
    y: 0,
    w: 80,
    h: 60,
    nativeW: 80,
    nativeH: 60,
    overrides,
    name: 'two',
    script: 'two.py',
    source: {
      script: 'two.py',
      entry: '__main__',
      stem: 'two',
      captureSource: 'pyplot',
      fingerprint: 'sha256:x',
      sizeMm: [80, 60],
    },
  }) as unknown as PanelObject

let root: Root
let host: HTMLDivElement
const mount = (p: PanelObject) => act(async () => root.render(<PanelView obj={p} />))
const text = () => host.textContent ?? ''
const img = () => host.querySelector('img') as HTMLImageElement | null

beforeEach(() => {
  previewForState.mockReset()
  let n = 0
  URL.createObjectURL = vi.fn(() => `blob:mock/${++n}`)
  URL.revokeObjectURL = vi.fn()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  useRenderStore.getState().clear()
  useRuntimeAssetStore.setState({
    byId: {
      [FILE_ID]: { status: 'fresh', cached: true, registered: true, profile: 'safe', checked: true },
    },
  })
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  useRuntimeAssetStore.getState().clear()
})

describe('runtime 冷画布的预览按此刻的 overrides 取', () => {
  it('问后端带的是面板自己的 overrides，撤销回右再问的就是右（不是「最近一次」的左）', async () => {
    previewForState.mockResolvedValue({ blob: new Blob(['<svg/>']), exact: true })
    await mount(panel(LEFT))
    expect(previewForState).toHaveBeenLastCalledWith(FILE_ID, LEFT)
    // 撤销：文档里的面板回到右。前端已画过右，不会发渲染请求——能救场的只有冷画布这条按状态取的路
    await mount(panel(RIGHT))
    expect(previewForState).toHaveBeenLastCalledWith(FILE_ID, RIGHT)
    expect(img()?.getAttribute('src')).toMatch(/^blob:mock\//)
    expect(text()).not.toContain('近似预览')
  })

  it('后端说「不是这个状态的图」→ 仍显示，但出「近似预览」角标', async () => {
    previewForState.mockResolvedValue({ blob: new Blob(['<svg/>']), exact: false })
    await mount(panel(RIGHT))
    expect(img()?.getAttribute('src')).toMatch(/^blob:mock\//)
    expect(text()).toContain('近似预览')
  })

  it('取到之前不挂别的状态的图（换了 overrides 立刻不再显示上一个状态的 blob）', async () => {
    let release: (v: unknown) => void = () => {}
    previewForState.mockResolvedValueOnce({ blob: new Blob(['L']), exact: true })
    await mount(panel(LEFT))
    const leftSrc = img()?.getAttribute('src')
    expect(leftSrc).toBeTruthy()
    previewForState.mockImplementationOnce(() => new Promise((r) => (release = r)))
    await mount(panel(RIGHT))
    expect(img()?.getAttribute('src') ?? null).not.toBe(leftSrc) // 右还在路上：不拿左冒充
    await act(async () => release({ blob: new Blob(['R']), exact: true }))
    expect(img()?.getAttribute('src')).toMatch(/^blob:mock\//)
    expect(img()?.getAttribute('src')).not.toBe(leftSrc)
  })
})

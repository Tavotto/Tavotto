/**
 * 松手之后那几百毫秒的「无感」（2026-09-25 用户反馈：拖动时整张图糊一下、左上角
 * 闪「渲染中」）。两件事：
 *
 * 1. **角标**：画布上已经是用户要的样子（预览平面 / 上一版挂着）时，普通重渲染
 *    过了阈值还没画完才说「渲染中」；冷启动与「挂着磁盘原图」照旧立刻说。
 *    SSE 的 `render.started` 每次渲染都写一条 `cold: false` 的 building——
 *    它不等于「要等很久」（第一版就是在这里判错、实测角标照闪）。
 * 2. **换图**：新一版 SVG 里嵌着位图时，先解码再换上去，旧画面留到那时；
 *    解码失败或太慢有上限，绝不把新图扣住。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PanelView } from './PanelView'
import { renderKeyOf, useRenderStore, type PanelRender } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { VECTOR_PREVIEW } from '@/lib/previewBudget'
import { SWAP_DECODE_CAP_MS, embeddedRasterHrefs, useDecodedSvg } from '@/lib/useDecodedSvg'
import type { Manifest } from '@/lib/api'
import type { PanelObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const MANIFEST = {
  stem: 'Fig1',
  size_mm: [100, 80],
  elements: [{ gid: 'figure', role: 'figure', bbox: [0, 0, 1, 1], editable: [] }],
} as unknown as Manifest

const BEFORE: PanelObject = {
  id: 'p1',
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
  overrides: [{ gid: 'axes_0.texts_0', prop: 'pos_frac', value: [0.2, 0.3] }],
} as unknown as PanelObject

/** 松手后的文档：多了一条 override，这一版还没画出来 */
const AFTER: PanelObject = {
  ...BEFORE,
  overrides: [{ gid: 'axes_0.texts_0', prop: 'pos_frac', value: [0.25, 0.3] }],
} as unknown as PanelObject

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  vi.useFakeTimers()
  useRenderStore.getState().clear()
  useUiStore.setState({ elementPanelId: 'p1' })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.useRealTimers()
})

function seedExact(panel: PanelObject, extra: Partial<PanelRender> = {}) {
  useRenderStore.getState().patch(renderKeyOf(panel), {
    fileId: panel.fileId,
    manifest: MANIFEST,
    rev: 3,
    status: 'ready',
    lastPatches: JSON.stringify(panel.overrides),
    preview: VECTOR_PREVIEW,
    svg: '<svg id="old"/>',
    ...extra,
  })
  useRenderStore.setState((s) => ({ latest: { ...s.latest, [panel.fileId]: renderKeyOf(panel) } }))
}

/** 松手：新变体排上渲染，画布退回上一版（fallback）挂着 */
function release({ cold = false }: { cold?: boolean } = {}) {
  useRenderStore.getState().patch(renderKeyOf(AFTER), {
    fileId: AFTER.fileId,
    status: 'rendering',
    wantPatches: JSON.stringify(AFTER.overrides),
  })
  // SSE 的 render.started：每次渲染都有，冷启动时 cold = true
  useRenderStore.getState().noteBuilding(AFTER.fileId, { cold, cost: '' })
}

const mount = async (obj: PanelObject) => {
  await act(async () => root.render(<PanelView obj={obj} />))
}
const text = () => container.textContent ?? ''

describe('「渲染中」角标：普通重渲染过了阈值才说', () => {
  it('松手后 700ms 内画完：角标一次都不出现', async () => {
    seedExact(BEFORE)
    await mount(BEFORE)
    release()
    await mount(AFTER)
    expect(text()).not.toContain('渲染中')
    await act(async () => vi.advanceTimersByTime(300))
    expect(text()).not.toContain('渲染中')
    // 画完了
    seedExact(AFTER, { svg: '<svg id="new"/>' })
    useRenderStore.getState().noteBuilding(AFTER.fileId, null)
    await act(async () => vi.advanceTimersByTime(1000))
    expect(text()).not.toContain('渲染中')
  })

  it('真的慢（超过 700ms 还没画完）：角标出现', async () => {
    seedExact(BEFORE)
    await mount(BEFORE)
    release()
    await mount(AFTER)
    await act(async () => vi.advanceTimersByTime(750))
    expect(text()).toContain('渲染中')
  })

  it('冷启动：立刻说（本来就要等很久）', async () => {
    seedExact(BEFORE)
    await mount(BEFORE)
    release({ cold: true })
    await mount(AFTER)
    expect(text()).toMatch(/首次构建|冷启动/)
  })
})

/* ================================ 换图解码 ================================ */

const PNG_A = 'data:image/png;base64,AAAA'
const PNG_B = 'data:image/png;base64,BBBB'
const svgWith = (id: string, href: string) =>
  `<svg id="${id}"><image width="10" height="10" xlink:href="${href}"/></svg>`

function Probe({ html }: { html: string | null }) {
  const shown = useDecodedSvg(html)
  return <div data-shown={shown ?? 'null'} />
}
const shown = () => container.querySelector('[data-shown]')?.getAttribute('data-shown')

describe('换一版 SVG：先把新图里的位图解码好再换', () => {
  let decodes: { href: string; resolve: () => void }[]
  beforeEach(() => {
    decodes = []
    // jsdom 没有 decode：装一个可控的
    ;(Image.prototype as unknown as { decode: () => Promise<void> }).decode = function (
      this: HTMLImageElement,
    ) {
      return new Promise<void>((resolve) => decodes.push({ href: this.src, resolve }))
    }
  })
  afterEach(() => {
    delete (Image.prototype as unknown as { decode?: unknown }).decode
  })

  it('认得出 SVG 里嵌着的 data URI 位图（去重）', () => {
    expect(embeddedRasterHrefs(svgWith('a', PNG_A) + svgWith('b', PNG_A))).toEqual([PNG_A])
    expect(embeddedRasterHrefs('<svg><image href="a.png"/></svg>')).toEqual([])
  })

  it('从无到有不等（第一次挂载、刚进图内编辑）：之前什么都没有，等待只会让图晚出来', async () => {
    await act(async () => root.render(<Probe html={svgWith('a', PNG_A)} />))
    expect(shown()).toContain('id="a"')
    await act(async () => root.render(<Probe html={null} />))
    await act(async () => root.render(<Probe html={svgWith('b', PNG_B)} />))
    expect(shown()).toContain('id="b"')
    expect(decodes).toHaveLength(0)
  })

  it('解码完之前旧图留着，解码完立刻换上新图', async () => {
    await act(async () => root.render(<Probe html={svgWith('a', PNG_A)} />))
    await act(async () => root.render(<Probe html={svgWith('b', PNG_B)} />))
    expect(shown()).toContain('id="a"')
    expect(decodes.map((d) => d.href)).toEqual([PNG_B])
    await act(async () => decodes[0].resolve())
    expect(shown()).toContain('id="b"')
  })

  it('解码一直不回来：到上限就换，绝不把新图扣住', async () => {
    await act(async () => root.render(<Probe html={svgWith('a', PNG_A)} />))
    await act(async () => root.render(<Probe html={svgWith('b', PNG_B)} />))
    await act(async () => vi.advanceTimersByTime(SWAP_DECODE_CAP_MS - 1))
    expect(shown()).toContain('id="a"')
    await act(async () => vi.advanceTimersByTime(2))
    expect(shown()).toContain('id="b"')
  })

  it('纯矢量的新图、撤下（null）：立即生效', async () => {
    await act(async () => root.render(<Probe html={svgWith('a', PNG_A)} />))
    await act(async () => root.render(<Probe html={'<svg id="vec"/>'} />))
    expect(shown()).toContain('id="vec"')
    await act(async () => root.render(<Probe html={null} />))
    expect(shown()).toBe('null')
  })

  it('等待途中又来一版：以最新那一版为准，旧的解码结果不会把它盖回去', async () => {
    const PNG_C = 'data:image/png;base64,CCCC'
    await act(async () => root.render(<Probe html={svgWith('a', PNG_A)} />))
    await act(async () => root.render(<Probe html={svgWith('b', PNG_B)} />))
    await act(async () => root.render(<Probe html={svgWith('c', PNG_C)} />))
    await act(async () => decodes[0].resolve()) // b 的解码迟到
    expect(shown()).toContain('id="a"')
    await act(async () => decodes[1].resolve())
    expect(shown()).toContain('id="c"')
  })
})

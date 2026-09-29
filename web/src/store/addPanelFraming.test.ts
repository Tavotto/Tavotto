/**
 * 加图即取景「页面 ∪ 这张图」，**在加图 action 这一层**（#706 评审 P2）：此前只有
 * `addFigureToLayout` 做，选图对话框 / 脚本库 / 接入状态对话框直接调 `addPanel` /
 * `addRuntimePanel`，从那里加进来的超页图有一截落在视口外。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CapturedFigureDescriptor, PanelInfo } from '@/lib/api'
import { emptyProject } from '@/types/document'
import { addPanel, addRuntimePanel } from './actions'
import { useDocumentStore } from './documentStore'
import { useViewportStore } from './viewportStore'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const VIEW = { left: 0, top: 0, width: 800, height: 600 }
const vp = () => useViewportStore.getState()

function info(w: number, h: number): PanelInfo {
  return {
    id: 'big.pdf',
    name: 'big.pdf',
    folder: '.',
    kind: 'pdf',
    native_w_mm: w,
    native_h_mm: h,
    mtime: 1,
  }
}

function descriptor(w: number, h: number): CapturedFigureDescriptor {
  return {
    asset_id: 'rt_big',
    script: 'fig.py',
    entry: 'fig.py',
    stem: 'big',
    capture_source: 'savefig',
    execution_profile: 'safe',
    original_artifact: null,
    size_mm: [w, h],
    source_fingerprint: 'f',
    can_writeback_artifact: false,
    can_writeback_source: false,
  }
}

/** 取景框按「页面 ∪ 对象」独立算（不借 `pageUnion`：那等于拿被测代码验自己） */
function unionOf(o: { x: number; y: number; w: number; h: number }) {
  const { w, h } = useDocumentStore.getState().doc.page
  const x = Math.min(0, o.x)
  const y = Math.min(0, o.y)
  return { x, y, w: Math.max(w, o.x + o.w) - x, h: Math.max(h, o.y + o.h) - y }
}

beforeEach(async () => {
  // reduced motion：补间同步落终态
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('prefers-reduced-motion'),
    media: q,
    addEventListener() {},
    removeEventListener() {},
  }))
  localStorage.clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_addframe')
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, fitted: false })
  vp().setViewRect(VIEW)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('加图 action 取景「页面 ∪ 这张图」', () => {
  it('addPanel（选图对话框 / 接入状态对话框走的就是它）：超页图整块取进来，留在适应模式', () => {
    const { w, h } = useDocumentStore.getState().doc.page
    vp().setView({ zoom: 3, panX: -500, panY: -400 })
    const obj = addPanel(info(w * 3, h * 3))
    expect(obj.w).toBeGreaterThan(w) // 软上限下确实伸出页面
    expect(vp().fitted).toBe(true)
    expect(vp().fitFrame()).toMatchObject(unionOf(obj))
  })

  it('addRuntimePanel（脚本库 / 接入状态 / tavotto run 交接走的就是它）同样取景', () => {
    const { w, h } = useDocumentStore.getState().doc.page
    vp().setView({ zoom: 3, panX: -500, panY: -400 })
    const obj = addRuntimePanel(descriptor(w * 3, h * 3))
    expect(vp().fitted).toBe(true)
    expect(vp().fitFrame()).toMatchObject(unionOf(obj))
  })

  it('拖放到一点、图整张已在视口里：视口不动', () => {
    vp().fit(useDocumentStore.getState().doc.page.w, useDocumentStore.getState().doc.page.h)
    vp().zoomAt(0.5, 400, 300) // 缩小一点，留出余地；退出适应模式
    const before = { zoom: vp().zoom, panX: vp().panX, panY: vp().panY }
    const { w, h } = useDocumentStore.getState().doc.page
    addPanel(info(10, 10), w / 2, h / 2)
    expect(vp()).toMatchObject({ ...before, fitted: false })
  })

  it('拖放到一点、图伸出视口：同样取景「页面 ∪ 这张图」', () => {
    const { w, h } = useDocumentStore.getState().doc.page
    vp().setView({ zoom: 3, panX: -500, panY: -400 })
    const obj = addPanel(info(w * 3, h * 3), w / 2, h / 2)
    expect(vp().fitted).toBe(true)
    expect(vp().fitFrame()).toMatchObject(unionOf(obj))
  })
})

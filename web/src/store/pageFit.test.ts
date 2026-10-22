/**
 * 换画布尺寸 / 类型后视口按新页面重新取景（2026-09-28 用户反馈）。
 * 切标签与换文档各有自己的适配点，这条订阅不许插手。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { BASE_PX_PER_MM } from '@/lib/units'
import { emptyProject, type CanvasObject, type PanelObject } from '@/types/document'
import { setPageSize } from './actions'
import { activateCanvas, createCanvasAndActivate } from './canvasSession'
import { useDocumentStore } from './documentStore'
import { startPageSizeFit } from './pageFit'
import { useViewportStore } from './viewportStore'
import { startWorkspaceModeSync, useWorkspaceStore } from './workspace'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const VIEW = { left: 0, top: 0, width: 800, height: 600 }
const vp = () => useViewportStore.getState()
let stop: () => void = () => {}
let stopMode: () => void = () => {}

const fig = (id: string, x: number, y: number): PanelObject =>
  ({
    id,
    type: 'panel',
    fileId: `${id}.pdf`,
    fileKind: 'pdf',
    nativeW: 20,
    nativeH: 20,
    overrides: [],
    x,
    y,
    w: 20,
    h: 20,
  }) as PanelObject
/** 往画布上放一张图（单张 = 单图编辑，第二张 = 多图排版；模式订阅 `startWorkspaceModeSync` 判） */
const addFig = (f: PanelObject) =>
  useDocumentStore.getState().commit(literal('加图'), (d) => {
    d.objects.push(f as CanvasObject)
  })

/** 适配落点按公式独立算（不借 `fit` 求值：那等于拿被测代码验自己） */
function expectedFit(w: number, h: number) {
  const pad = 72
  const zoom = Math.min((VIEW.width - pad) / (w * BASE_PX_PER_MM), (VIEW.height - pad) / (h * BASE_PX_PER_MM))
  return {
    zoom,
    panX: (VIEW.width - w * BASE_PX_PER_MM * zoom) / 2,
    panY: (VIEW.height - h * BASE_PX_PER_MM * zoom) / 2,
  }
}

beforeEach(async () => {
  // reduced motion：补间同步落终态，不用等
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('prefers-reduced-motion'),
    media: q,
    addEventListener() {},
    removeEventListener() {},
  }))
  localStorage.clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_pagefit')
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, fitted: false })
  vp().setViewRect(VIEW)
  stop = startPageSizeFit()
  stopMode = startWorkspaceModeSync()
})

afterEach(() => {
  stop()
  stopMode()
  useWorkspaceStore.setState({ mode: 'layout' })
  vi.unstubAllGlobals()
})

describe('startPageSizeFit', () => {
  it('换页面尺寸后按新页面适配——即使用户之前平移缩放过', () => {
    vp().setView({ zoom: 3, panX: -500, panY: -400 })
    expect(vp().fitted).toBe(false)
    const want = expectedFit(300, 200)
    setPageSize(300, 200)
    expect(vp().zoom).toBeCloseTo(want.zoom)
    expect(vp().panX).toBeCloseTo(want.panX)
    expect(vp().panY).toBeCloseTo(want.panY)
    expect(vp().fitted).toBe(true)
    // 取景的是页面本身：画布会话不存矩形，切回来按那时的页面算（#706 评审 P2）
    expect(vp().fitFrame()).toBeNull()
  })

  it('撤销页面尺寸同样重新取景', () => {
    const { w, h } = useDocumentStore.getState().doc.page
    setPageSize(300, 200)
    vp().setView({ zoom: 3, panX: -500, panY: -400 })
    const want = expectedFit(w, h)
    useDocumentStore.getState().undo()
    expect(useDocumentStore.getState().doc.page).toMatchObject({ w, h })
    expect(vp().zoom).toBeCloseTo(want.zoom)
  })

  it('尺寸没变的页面改动（背景色）不动视口', () => {
    vp().setView({ zoom: 3, panX: -500, panY: -400 })
    useDocumentStore.getState().commit(literal('bg'), (d) => {
      d.page.bg = '#eeeeee'
    })
    expect(vp()).toMatchObject({ zoom: 3, panX: -500, panY: -400 })
  })

  it('单图编辑里不按页面取景', () => {
    addFig(fig('p1', 50, 40)) // 一张图 = 单图编辑
    expect(useWorkspaceStore.getState().mode).toBe('fast_edit')
    vp().setView({ zoom: 3, panX: -500, panY: -400 })
    setPageSize(300, 200)
    expect(vp()).toMatchObject({ zoom: 3, panX: -500, panY: -400 })
  })

  it('单图编辑里改了页面尺寸：第二张图进来时按新页面取景「页面 ∪ 图」（#706 评审 P2）', () => {
    vp().setView({ zoom: 3, panX: -500, panY: -400 })
    addFig(fig('p1', 50, 40)) // 进单图编辑
    vp().setView({ zoom: 2, panX: 10, panY: 20 }) // 单图编辑那一屏自己的取景
    setPageSize(300, 200)
    expect(vp()).toMatchObject({ zoom: 2, panX: 10, panY: 20 })
    addFig(fig('p2', 80, 40)) // 第二张进来 = 多图排版
    expect(useWorkspaceStore.getState().mode).toBe('layout')
    const want = expectedFit(300, 200)
    expect(vp().zoom).toBeCloseTo(want.zoom)
    expect(vp().panX).toBeCloseTo(want.panX)
    expect(vp().panY).toBeCloseTo(want.panY)
    expect(vp().fitted).toBe(true)
  })

  it('切回一张手动取过景的画布：原样还原，订阅不插手', () => {
    const first = useDocumentStore.getState().activeCanvasId
    vp().setView({ zoom: 3, panX: -500, panY: -400 })
    const second = createCanvasAndActivate()
    setPageSize(300, 200) // 第二张画布换成不同尺寸
    activateCanvas(first)
    expect(vp()).toMatchObject({ zoom: 3, panX: -500, panY: -400, fitted: false })
    activateCanvas(second)
    // 第二张离开时在适应模式：回来按它自己的页面重新适配
    const want = expectedFit(300, 200)
    expect(vp().zoom).toBeCloseTo(want.zoom)
  })
})

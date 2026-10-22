/**
 * 新加的图自动避开已有的图（设计稿 C9）：所有加图入口最终都落到 `actions.addPanel` /
 * `addRuntimePanel` → `placePanelInPage`，这里从入口一侧钉住「第二张不压第一张」，
 * 拖放落点仍尊重、隐藏对象不算障碍。放置算法本身的边界见 `lib/panelPlacement.test.ts`。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { msg } from '@/i18n'
import type { CapturedFigureDescriptor, PanelInfo } from '@/lib/api'
import { emptyProject } from '@/types/document'
import { useAssetStore } from './assetStore'
import { useDocumentStore } from './documentStore'
import { useViewportStore } from './viewportStore'
import {
  addFigureToLayout,
  addPanelToCanvas,
  addRuntimePanelToCanvas,
  startWorkspaceModeSync,
  useWorkspaceStore,
} from './workspace'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const info = (id: string, w: number, h: number): PanelInfo => ({
  id,
  name: id,
  folder: '.',
  kind: 'pdf',
  native_w_mm: w,
  native_h_mm: h,
  mtime: 1,
})

const descriptor = (w: number, h: number): CapturedFigureDescriptor => ({
  asset_id: 'rt_a',
  script: 'fig.py',
  entry: 'fig.py',
  stem: 'a',
  capture_source: 'savefig',
  execution_profile: 'safe',
  original_artifact: null,
  size_mm: [w, h],
  source_fingerprint: 'f',
  can_writeback_artifact: false,
  can_writeback_source: false,
})

type Box = { x: number; y: number; w: number; h: number }
const overlaps = (a: Box, b: Box) =>
  a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y
const doc = () => useDocumentStore.getState().doc

beforeEach(async () => {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('prefers-reduced-motion'),
    media: q,
    addEventListener() {},
    removeEventListener() {},
  }))
  useWorkspaceStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_avoid')
  const assets = [info('a.pdf', 40, 30), info('b.pdf', 40, 30)]
  useAssetStore.setState({ panels: assets, byId: Object.fromEntries(assets.map((a) => [a.id, a])) })
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, fitted: false })
  useViewportStore.getState().setViewRect({ left: 0, top: 0, width: 800, height: 600 })
})

describe('加图入口避开已有的图', () => {
  it('空页面：第一张仍居中', () => {
    const a = addPanelToCanvas(info('a.pdf', 40, 30))!
    expect(a.x).toBeCloseTo((doc().page.w - 40) / 2)
    expect(a.y).toBeCloseTo((doc().page.h - 30) / 2)
  })

  it('addPanelToCanvas：第二张不压第一张，排在它右边', () => {
    const a = addPanelToCanvas(info('a.pdf', 40, 30))!
    const b = addPanelToCanvas(info('b.pdf', 40, 30))!
    expect(overlaps(a, b)).toBe(false)
    expect(b.x).toBeGreaterThan(a.x + a.w)
  })

  it('addRuntimePanelToCanvas（脚本库 / 接入状态 / tavotto run）走同一个判据', () => {
    const a = addPanelToCanvas(info('a.pdf', 40, 30))!
    const r = addRuntimePanelToCanvas(descriptor(40, 30))!
    expect(overlaps(a, r)).toBe(false)
  })

  it('addFigureToLayout（素材栏 / 快编栏 / 教程）走同一个判据', () => {
    const a = addPanelToCanvas(info('a.pdf', 40, 30))!
    addFigureToLayout('b.pdf')
    const b = doc().objects.find((o) => o.type === 'panel' && o.fileId === 'b.pdf')!
    expect(overlaps(a, b)).toBe(false)
  })

  it('拖放有明确落点：尊重落点，哪怕压住已有的图', () => {
    const a = addPanelToCanvas(info('a.pdf', 40, 30))!
    const b = addPanelToCanvas(info('b.pdf', 40, 30), a.x + 20, a.y + 15)!
    expect(b.x).toBeCloseTo(a.x)
    expect(b.y).toBeCloseTo(a.y)
  })

  it('隐藏的对象不算障碍', () => {
    const a = addPanelToCanvas(info('a.pdf', 40, 30))!
    useDocumentStore.getState().commit(msg('history.addPanel', { name: 'hide' }, 'workspace'), (d) => {
      d.objects.find((o) => o.id === a.id)!.hidden = true
    })
    const b = addPanelToCanvas(info('b.pdf', 40, 30))!
    expect(b.x).toBeCloseTo(a.x)
  })
})

/**
 * 单图编辑（画布上恰好一张图）里加第二张：与排版里「加到画布」是**同一条**放置路径（`addPanel` → `placePanelInPage`，
 * 带避让），放置结果不依赖当时的工作区模式；放下之后才由 `settleMode` 切多图排版。
 * 页面放不下两张时按既有规则退回居中（两种模式一模一样）——e2e 里 73 × 58 加 59 × 52 在默认 150 × 100 页面上就是这种情形。
 */
describe('单图编辑里加第二张', () => {
  let stop: () => void = () => {}
  afterEach(() => stop())

  const secondPlacement = async (withSync: boolean, add: () => void) => {
    stop()
    useWorkspaceStore.getState().clear()
    await useDocumentStore.getState().switchDocument(emptyProject(), `d_${withSync}_${Math.random()}`)
    if (withSync) stop = startWorkspaceModeSync()
    const a = addPanelToCanvas(info('a.pdf', 40, 30))!
    expect(useWorkspaceStore.getState().mode).toBe(withSync ? 'fast_edit' : 'layout')
    add()
    const panels = doc().objects.filter((o) => o.type === 'panel')
    expect(panels).toHaveLength(2)
    return { a, b: panels[1] as Box }
  }

  for (const [label, add] of [
    ['addPanelToCanvas（拖入 / 选图对话框）', () => void addPanelToCanvas(info('b.pdf', 40, 30))],
    ['addFigureToLayout（素材卡 Shift+Enter / 就近入口）', () => void addFigureToLayout('b.pdf')],
  ] as const) {
    it(`${label}：两张不相交、都在页面内；落点与排版里同一个`, async () => {
      const single = await secondPlacement(true, add)
      expect(useWorkspaceStore.getState().mode).toBe('layout') // 第二张放下后切了多图排版
      expect(overlaps(single.a, single.b)).toBe(false)
      const page = doc().page
      for (const o of [single.a, single.b]) {
        expect(o.x).toBeGreaterThanOrEqual(0)
        expect(o.y).toBeGreaterThanOrEqual(0)
        expect(o.x + o.w).toBeLessThanOrEqual(page.w)
        expect(o.y + o.h).toBeLessThanOrEqual(page.h)
      }
      const layout = await secondPlacement(false, add)
      expect(single.b.x).toBeCloseTo(layout.b.x)
      expect(single.b.y).toBeCloseTo(layout.b.y)
    })
  }

  it('页面放不下两张时：单图编辑与排版都退回居中（既有规则，不因模式而异）', async () => {
    const add = (w1: number, h1: number, w2: number, h2: number) => async (withSync: boolean) => {
      stop()
      useWorkspaceStore.getState().clear()
      await useDocumentStore.getState().switchDocument(emptyProject(), `d_big_${withSync}_${Math.random()}`)
      if (withSync) stop = startWorkspaceModeSync()
      addPanelToCanvas(info('a.pdf', w1, h1))
      addPanelToCanvas(info('b.pdf', w2, h2))
      const [, b] = doc().objects.filter((o) => o.type === 'panel') as Box[]
      return b
    }
    const run = add(73.33, 57.77, 59, 52)
    const single = await run(true)
    const layout = await run(false)
    expect(single.x).toBeCloseTo(layout.x)
    expect(single.y).toBeCloseTo(layout.y)
  })
})

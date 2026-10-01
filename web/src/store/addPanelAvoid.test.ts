/**
 * 新加的图自动避开已有的图（设计稿 C9）：所有加图入口最终都落到 `actions.addPanel` /
 * `addRuntimePanel` → `placePanelInPage`，这里从入口一侧钉住「第二张不压第一张」，
 * 拖放落点仍尊重、隐藏对象不算障碍。放置算法本身的边界见 `lib/panelPlacement.test.ts`。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
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
    const a = addPanelToCanvas(info('a.pdf', 40, 30))
    expect(a.x).toBeCloseTo((doc().page.w - 40) / 2)
    expect(a.y).toBeCloseTo((doc().page.h - 30) / 2)
  })

  it('addPanelToCanvas：第二张不压第一张，排在它右边', () => {
    const a = addPanelToCanvas(info('a.pdf', 40, 30))
    const b = addPanelToCanvas(info('b.pdf', 40, 30))
    expect(overlaps(a, b)).toBe(false)
    expect(b.x).toBeGreaterThan(a.x + a.w)
  })

  it('addRuntimePanelToCanvas（脚本库 / 接入状态 / tavotto run）走同一个判据', () => {
    const a = addPanelToCanvas(info('a.pdf', 40, 30))
    const r = addRuntimePanelToCanvas(descriptor(40, 30))
    expect(overlaps(a, r)).toBe(false)
  })

  it('addFigureToLayout（素材栏 / 快编栏 / 教程）走同一个判据', () => {
    const a = addPanelToCanvas(info('a.pdf', 40, 30))
    addFigureToLayout('b.pdf')
    const b = doc().objects.find((o) => o.type === 'panel' && o.fileId === 'b.pdf')!
    expect(overlaps(a, b)).toBe(false)
  })

  it('拖放有明确落点：尊重落点，哪怕压住已有的图', () => {
    const a = addPanelToCanvas(info('a.pdf', 40, 30))
    const b = addPanelToCanvas(info('b.pdf', 40, 30), a.x + 20, a.y + 15)
    expect(b.x).toBeCloseTo(a.x)
    expect(b.y).toBeCloseTo(a.y)
  })

  it('隐藏的对象不算障碍', () => {
    const a = addPanelToCanvas(info('a.pdf', 40, 30))
    useDocumentStore.getState().commit(msg('history.addPanel', { name: 'hide' }, 'workspace'), (d) => {
      d.objects.find((o) => o.id === a.id)!.hidden = true
    })
    const b = addPanelToCanvas(info('b.pdf', 40, 30))
    expect(b.x).toBeCloseTo(a.x)
  })
})

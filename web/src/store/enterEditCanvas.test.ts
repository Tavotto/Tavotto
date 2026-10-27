/**
 * 打开第二个脚本的图：落在哪张画布上（beta 0.19.0b3 反馈「打开了不同的脚本，时间线还是混一起」的根因）。
 *
 * 规则（ADR 0028 / workflows-and-original-spec.md）：「打开」= 换看那一张，不是往当前画布上加第二张——
 * 当前画布有内容就新建一张以图名命名的画布放它。`enterEdit` 在素材清单还没有这张图时先
 * `addRuntimePanelToCanvas`（加进**当前**画布）再 `openFastEdit`，后者按素材 id 找到刚加的面板、
 * 不再新建画布，于是 A、B 两张图落在同一张画布上——时间线按画布分（#1350）当然分不开。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
}))

import type { CapturedFigureDescriptor } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject } from '@/types/document'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { enterEdit } from '@/store/prepEnterEdit'

const fig = (script: string, stem: string): CapturedFigureDescriptor =>
  ({
    asset_id: `runtime:${script}#${stem}`,
    script,
    entry: '__main__',
    stem,
    capture_source: 'pyplot',
    execution_profile: 'safe',
    original_artifact: null,
    size_mm: [80, 60],
    source_fingerprint: 'sha256:x',
    can_writeback_artifact: false,
    can_writeback_source: false,
  }) as CapturedFigureDescriptor

const host = { epoch: () => 0, note: () => {} }

beforeEach(async () => {
  useRuntimeAssetStore.setState({ assets: [], loadAssets: async () => {} })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_enter_edit')
})

describe('enterEdit：第二个脚本的图要落在自己的画布上', () => {
  it('先开 A 再开 B：两张画布，各只有一张图（时间线才分得开）', async () => {
    await enterEdit(host, fig('a.py', 'fig_a'))
    await enterEdit(host, fig('b.py', 'fig_b'))
    const st = useDocumentStore.getState()
    const figuresOf = (objs: readonly { type: string }[]) =>
      objs.filter((o) => o.type === 'panel').map((o) => (o as unknown as { fileId: string }).fileId)
    // 激活画布的内容在 `doc`（`canvases` 里那一条要等切画布才同步）
    const perCanvas = st.canvases.map((c) => figuresOf(c.id === st.activeCanvasId ? st.doc.objects : c.objects))
    expect(perCanvas).toEqual([['runtime:a.py#fig_a'], ['runtime:b.py#fig_b']])
  })

  it('资产缓存里是上一轮的旧描述符、enterEdit 传入新描述符：面板按新的尺寸建（显式传入 > 缓存）', async () => {
    const fresh = { ...fig('a.py', 'fig_a'), size_mm: [120, 90] as [number, number], source_fingerprint: 'sha256:new' }
    const stale = { ...fig('a.py', 'fig_a'), size_mm: [50, 40] as [number, number] }
    useRuntimeAssetStore.setState({ assets: [{ id: stale.asset_id, descriptor: stale } as never], loadAssets: async () => {} })
    await enterEdit(host, fresh)
    const panel = useDocumentStore.getState().doc.objects.find((o) => o.type === 'panel') as unknown as {
      nativeW: number
      nativeH: number
    }
    expect([panel.nativeW, panel.nativeH]).toEqual([120, 90])
  })
})

describe('enterEdit：脚本是拖到某张画布上发起的（placeIn，2026-10-27 用户拍板）', () => {
  const figuresOn = (canvasId: string) => {
    const st = useDocumentStore.getState()
    const objs = canvasId === st.activeCanvasId ? st.doc.objects : (st.canvases.find((c) => c.id === canvasId)?.objects ?? [])
    return objs.filter((o) => o.type === 'panel').map((o) => (o as unknown as { fileId: string }).fileId)
  }

  it('放进那张画布、落在放下的位置，不新建画布', async () => {
    await enterEdit(host, fig('a.py', 'fig_a'))
    const paper = useDocumentStore.getState().activeCanvasId
    await enterEdit({ ...host, placeIn: { canvasId: paper, at: { x: 120, y: 80 } } }, fig('b.py', 'fig_b'))
    const st = useDocumentStore.getState()
    expect(st.canvases).toHaveLength(1)
    expect(st.activeCanvasId).toBe(paper)
    expect(figuresOn(paper)).toEqual(['runtime:a.py#fig_a', 'runtime:b.py#fig_b'])
    const b = st.doc.objects.find((o) => o.type === 'panel' && (o as unknown as { fileId: string }).fileId === 'runtime:b.py#fig_b') as unknown as {
      x: number
      y: number
      w: number
      h: number
    }
    // 页面 150 × 100、图 80 × 60：不带落点时排在 (35, 20)，盖不到 x = 120
    expect(b.x <= 120 && 120 <= b.x + b.w && b.y <= 80 && 80 <= b.y + b.h, JSON.stringify(b)).toBe(true)
  })

  it('AI 修复期间用户切去了别的画布：修好后切回拖入的那张，图放在那里', async () => {
    await enterEdit(host, fig('a.py', 'fig_a'))
    const paper = useDocumentStore.getState().activeCanvasId
    await enterEdit(host, fig('c.py', 'fig_c')) // 普通打开：新建画布并切过去
    expect(useDocumentStore.getState().activeCanvasId).not.toBe(paper)
    await enterEdit({ ...host, placeIn: { canvasId: paper } }, fig('b.py', 'fig_b'))
    expect(useDocumentStore.getState().activeCanvasId).toBe(paper)
    expect(figuresOn(paper)).toEqual(['runtime:a.py#fig_a', 'runtime:b.py#fig_b'])
    expect(useDocumentStore.getState().canvases).toHaveLength(2)
  })

  it('那张画布已经不在了：按普通「打开」走，新建画布放它', async () => {
    await enterEdit(host, fig('a.py', 'fig_a'))
    await enterEdit({ ...host, placeIn: { canvasId: 'c_gone' } }, fig('b.py', 'fig_b'))
    const st = useDocumentStore.getState()
    expect(st.canvases).toHaveLength(2)
    expect(figuresOn(st.activeCanvasId)).toEqual(['runtime:b.py#fig_b'])
  })
})

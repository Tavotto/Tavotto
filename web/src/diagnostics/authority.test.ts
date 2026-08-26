/**
 * 几何权威不变式的**诊断侧**（ADR 0016 §6 + ADR 0017）。
 *
 * 护栏本身（拒绝写入）由 alignAction 那边的用例看护；这里守的是另一半：
 * **被拒的那一刻，trace 说清楚了没有**。两者都要有用例——只验行为，诊断会在
 * 某次重构里悄悄退化成一串没有信息量的记录；只验 trace，一个「只记录不阻断」
 * 的实现照样绿。
 *
 * 判据统一委托给 `exactPanelRender`：诊断报的必须就是护栏实际用的那个判据，
 * 否则会出现「诊断说权威就绪、写路径当场拒绝」这种两边各说各话。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { alignSelectedPanelElements } from '@/store/alignAction'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { renderKeyOf, useRenderStore, type PanelRender } from '@/store/renderStore'
import type { Manifest } from '@/lib/api'
import type { PanelObject } from '@/types/document'
import {
  __resetDiagnosticsForTests,
  clearDiagnosticTrace,
  readDiagnosticTrace,
  recordDiagnosticEvent,
} from './store'
import { __setDiagnosticSaltForTests, variantHash } from './hash'
import { readAuthority } from './authority'

const PANEL_ID = 'p1'
const FILE = 'Fig1.pdf'

/** 两个可对齐的文字元素——对齐要求至少两个几何目标 */
const MANIFEST = {
  stem: 'Fig1',
  size_mm: [100, 80],
  elements: [
    {
      gid: 'axes_0.title', role: 'text', label: '标题', draggable: true,
      anchor: [0.5, 0.1], drag_prop: 'pos_frac', bbox: [0.4, 0.08, 0.2, 0.05],
      editable: [],
    },
    {
      gid: 'axes_0.xaxis.label', role: 'text', label: 'X 轴', draggable: true,
      anchor: [0.5, 0.9], drag_prop: 'pos_frac', bbox: [0.3, 0.88, 0.2, 0.05],
      editable: [],
    },
  ],
} as unknown as Manifest

function panelWith(overrides: { gid: string; prop: string; value: unknown }[]): PanelObject {
  return {
    id: PANEL_ID, type: 'panel', x: 0, y: 0, w: 100, h: 80,
    fileId: FILE, fileKind: 'pdf', nativeW: 100, nativeH: 80,
    script: 'fig.py', overrides,
  } as unknown as PanelObject
}

const entry = (lastPatches: unknown[], extra: Partial<PanelRender> = {}): PanelRender =>
  ({
    fileId: FILE, rev: 1, manifest: MANIFEST, svg: '<svg/>', status: 'ready',
    error: null, code: '', module: '', traceback: '', warnings: [], timings: {},
    stale: false, lastPatches: JSON.stringify(lastPatches), wantPatches: null,
    previewDpi: null,
    ...extra,
  }) as PanelRender

/**
 * `renderedFor` 决定那条渲染条目**声称自己画的是哪一版**。
 * 与文档 overrides 不同 = 几何权威过期（exactPanelRender 认 lastPatches 逐字相等）。
 */
function setup(
  docOverrides: { gid: string; prop: string; value: unknown }[],
  renderedFor: unknown[],
  extra: Partial<PanelRender> = {},
) {
  const panel = panelWith(docOverrides)
  useDocumentStore.setState({
    doc: { ...useDocumentStore.getState().doc, objects: [panel] },
    past: [], future: [], txn: null,
  })
  const key = renderKeyOf(panel)
  useRenderStore.setState({
    byKey: { [key]: entry(renderedFor, extra) },
    latest: { [FILE]: key },
    tracked: {}, building: {},
  })
  useUiStore.setState({
    elementPanelId: PANEL_ID,
    selectedGids: ['axes_0.title', 'axes_0.xaxis.label'],
  })
  useSelectionStore.setState({ ids: [] })
  return { panel, key }
}

const typesOf = () => readDiagnosticTrace().map((e) => e.type)
const find = (t: string) => readDiagnosticTrace().find((e) => e.type === t)

beforeEach(() => {
  __resetDiagnosticsForTests()
  __setDiagnosticSaltForTests('test-salt')
})

describe('几何权威过期时', () => {
  it('写入被拒，文档与历史都不动，trace 说清了原因', () => {
    // 渲染条目声称画的是「没有 override」那一版，而文档已经有一条了
    setup([{ gid: 'axes_0.title', prop: 'fontsize', value: 11 }], [])
    const before = JSON.stringify(useDocumentStore.getState().doc)

    const res = alignSelectedPanelElements(PANEL_ID, 'left')

    expect(res.ok).toBe(false)
    expect(JSON.stringify(useDocumentStore.getState().doc)).toBe(before)
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(useDocumentStore.getState().future).toHaveLength(0)

    expect(typesOf()).toContain('align.request')
    expect(typesOf()).toContain('align.blocked')
    expect(typesOf()).not.toContain('align.commit')

    const blocked = find('align.blocked')!
    expect(blocked.reason).toBe('authority_stale')
    expect(blocked.document_variant).toMatch(/^var:[0-9a-f]{12}$/)

    // 请求必须排在拒绝之前——读 trace 的人不该为了找原因往回翻
    const order = typesOf()
    expect(order.indexOf('align.request')).toBeLessThan(order.indexOf('align.blocked'))
  })

  it('脚本变过（stale）同样不算权威', () => {
    setup([], [], { stale: true })
    expect(alignSelectedPanelElements(PANEL_ID, 'left').ok).toBe(false)
    expect(find('align.blocked')!.reason).toBe('authority_stale')
    expect(useDocumentStore.getState().past).toHaveLength(0)
  })

  it('trace 里不含任何用户文本与文件名', () => {
    setup([{ gid: 'axes_0.title', prop: 'text', value: 'SUPER_SECRET_PAPER_TITLE_12345' }], [])
    alignSelectedPanelElements(PANEL_ID, 'left')
    const dump = JSON.stringify(readDiagnosticTrace())
    expect(dump).not.toContain('SUPER_SECRET')
    expect(dump).not.toContain(FILE)
    expect(dump).not.toContain('标题')
  })
})

describe('几何权威精确时', () => {
  it('放行，并按 request → authority.ready → commit 的顺序留下几何', () => {
    setup([], [])
    const res = alignSelectedPanelElements(PANEL_ID, 'left')

    expect(res.ok).toBe(true)
    expect(useDocumentStore.getState().past).toHaveLength(1)

    const order = typesOf()
    expect(order.indexOf('align.request')).toBeLessThan(order.indexOf('authority.ready'))
    expect(order.indexOf('authority.ready')).toBeLessThan(order.indexOf('align.commit'))
    expect(order).not.toContain('align.blocked')
    expect(order).not.toContain('invariant.violation')

    const commit = find('align.commit')!
    expect(commit.exact_authority).toBe(true)
    expect(commit.patch_count).toBeGreaterThan(0)
    // bbox 数字在，文字不在
    expect(Array.isArray(commit.input_geometry)).toBe(true)
    expect(JSON.stringify(commit)).not.toContain('标题')
  })

  it('写入经过 document.commit（历史平面没有被绕开）', () => {
    setup([], [])
    alignSelectedPanelElements(PANEL_ID, 'left')
    const commit = find('document.commit')!
    expect(commit).toBeTruthy()
    expect(commit.document_hash_before).not.toBe(commit.document_hash_after)
  })
})

describe('readAuthority', () => {
  it('权威要么就是当前这一版，要么根本没有', () => {
    const { panel, key } = setup([], [])
    const ok = readAuthority(panel)
    expect(ok.documentVariant).toBe(key)
    expect(ok.authorityVariant).toBe(key)
    expect(ok.exact).toBe(true)

    // 换成「渲染条目声称画的是别的变体」
    useRenderStore.setState({
      byKey: { [key]: entry([{ gid: 'x', prop: 'y', value: 1 }]) },
      latest: { [FILE]: key },
    })
    const stale = readAuthority(panel)
    expect(stale.authorityVariant).toBeNull()
    expect(stale.exact).toBe(false)
    // 显示仍然有来源——那是合法的退回，不是错误
    expect(stale.displayVariant).toBe(key)
  })

  it('身份进事件时一律 hash', () => {
    const { panel, key } = setup([], [])
    const view = readAuthority(panel)
    expect(variantHash(view.documentVariant)).toMatch(/^var:[0-9a-f]{12}$/)
    expect(view.documentVariant).toBe(key)
  })
})

describe('评审 #139 的 P2：切项目要清空诊断环', () => {
  it('清空之后读不到旧项目的事件，但 seq 不重置', () => {
    setup([], [])
    recordDiagnosticEvent({
      type: 'undo.request', past_count: 0, future_count: 0, txn_open: false,
    })
    expect(readDiagnosticTrace().length).toBeGreaterThan(0)

    clearDiagnosticTrace()
    expect(readDiagnosticTrace()).toHaveLength(0)

    // seq **不重置**：编号缺口是「这里被清过」的唯一线索
    const next = recordDiagnosticEvent({
      type: 'undo.request', past_count: 0, future_count: 0, txn_open: false,
    })!
    expect(next.seq).toBeGreaterThan(1)
  })
})

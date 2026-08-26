/**
 * 几何权威不变式：**诊断 + 运行时护栏**（ADR 0016 §6，issue #131）。
 *
 * 这一档是本轮最重要的行为用例。它守的不是「trace 里有没有那条记录」，而是
 * **危险的几何写入根本没发生**——文档不变、历史不变，然后才是「trace 说清了
 * 为什么」。反过来写（先断言 trace）会让一个只记录不阻断的实现照样绿。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { commitGeometryWrite } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { renderKey, renderKeyOf, useRenderStore } from '@/store/renderStore'
import { msg } from '@/i18n'
import type { Manifest } from '@/lib/api'
import type { PanelObject } from '@/types/document'
import { __resetDiagnosticsForTests, readDiagnosticTrace } from './store'
import { __setDiagnosticSaltForTests, variantHash } from './hash'
import { readAuthority } from './authority'

const manifest = (n: number) =>
  ({
    stem: 'Fig1',
    size_mm: [100, 80],
    elements: Array.from({ length: n }, (_, i) => ({ gid: `axes_${i}`, role: 'axes' })),
  }) as unknown as Manifest

const PANEL_ID = 'p1'

function panelWith(overrides: { gid: string; prop: string; value: unknown }[]): PanelObject {
  return {
    id: PANEL_ID,
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
    overrides,
  } as unknown as PanelObject
}

/** 把一个面板放进文档，并让 renderStore 里只有 `rendered` 那一版有 manifest */
function setup(docOverrides: { gid: string; prop: string; value: unknown }[],
               renderedOverrides: { gid: string; prop: string; value: unknown }[]) {
  const panel = panelWith(docOverrides)
  useDocumentStore.setState({ doc: { ...useDocumentStore.getState().doc, objects: [panel] } })
  useDocumentStore.setState({ past: [], future: [], txn: null })

  const renderedKey = renderKey('Fig1.pdf', renderedOverrides)
  useRenderStore.setState({
    byKey: {
      [renderedKey]: {
        fileId: 'Fig1.pdf',
        rev: 1,
        manifest: manifest(2),
        svg: '<svg/>',
        status: 'ready',
        error: null,
        code: '',
        module: '',
        traceback: '',
        warnings: [],
        timings: {},
        stale: false,
        lastPatches: JSON.stringify(renderedOverrides),
        wantPatches: null,
        previewDpi: null,
      },
    },
    latest: { 'Fig1.pdf': renderedKey },
    tracked: {},
    building: {},
  })
  return { panel, renderedKey, documentKey: renderKeyOf(panel) }
}

const geomWrite = (authority: string | null) =>
  commitGeometryWrite({
    panelId: PANEL_ID,
    operation: 'align.left',
    authority,
    label: msg('alignMode.left', undefined, 'inspector'),
    patches: [{ gid: 'axes_0', prop: 'position', value: [0.1, 0.1, 0.3, 0.3] }],
    trace: {
      mode: 'left',
      selectedCount: 2,
      inputGeometry: [{ gid: 'axes_0', bbox: [0.2, 0.2, 0.3, 0.3] }],
    },
  })

const typesOf = () => readDiagnosticTrace().map((e) => e.type)
const find = (type: string) => readDiagnosticTrace().find((e) => e.type === type)

beforeEach(() => {
  __resetDiagnosticsForTests()
  __setDiagnosticSaltForTests('test-salt')
})

describe('几何权威过期时的写入', () => {
  it('被阻止：文档不变、历史不变，并留下 invariant.violation', () => {
    // document = 变体 B（刚改完 fontsize），已画好的只有变体 A
    const { renderedKey, documentKey } = setup(
      [{ gid: 'axes_0.title', prop: 'fontsize', value: 12 }],
      [],
    )
    expect(renderedKey).not.toBe(documentKey)

    const before = JSON.stringify(useDocumentStore.getState().doc)
    // 属性页量到的几何来自 A（panelRender 的显示回退），用户点了左对齐
    const ok = geomWrite(renderedKey)

    expect(ok).toBe(false)
    expect(JSON.stringify(useDocumentStore.getState().doc)).toBe(before)
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(useDocumentStore.getState().future).toHaveLength(0)

    // trace 说清了为什么
    expect(typesOf()).toContain('align.request')
    expect(typesOf()).toContain('align.blocked')
    expect(typesOf()).toContain('invariant.violation')
    expect(typesOf()).not.toContain('align.commit')

    const blocked = find('align.blocked')!
    expect(blocked.reason).toBe('authority_stale')
    expect(blocked.document_variant).toBe(variantHash(documentKey))
    expect(blocked.authority_variant).toBe(variantHash(renderedKey))

    const violation = find('invariant.violation')!
    expect(violation.kind).toBe('geometry_authority_mismatch')
    expect(violation.operation).toBe('align.left')
  })

  it('trace 里既有 document 也有 authority 哈希，且不含任何用户文本', () => {
    const { renderedKey } = setup(
      [{ gid: 'axes_0.title', prop: 'text', value: 'SUPER_SECRET_PAPER_TITLE_12345' }],
      [],
    )
    geomWrite(renderedKey)
    const dump = JSON.stringify(readDiagnosticTrace())
    expect(dump).not.toContain('SUPER_SECRET')
    expect(dump).not.toContain('Fig1.pdf')
    expect(dump).toMatch(/"document_variant":"var:[0-9a-f]{12}"/)
    expect(dump).toMatch(/"authority_variant":"var:[0-9a-f]{12}"/)
  })

  it('一份 manifest 都没有时同样拒绝（reason=no_manifest）', () => {
    setup([], [])
    useRenderStore.setState({ byKey: {}, latest: {} })
    expect(geomWrite(null)).toBe(false)
    expect(find('align.blocked')!.reason).toBe('no_manifest')
    expect(useDocumentStore.getState().past).toHaveLength(0)
  })

  it('面板已经不在文档里：拒绝，且三个身份如实报 null', () => {
    setup([], [])
    useDocumentStore.setState({ doc: { ...useDocumentStore.getState().doc, objects: [] } })
    expect(geomWrite('anything')).toBe(false)
    expect(find('align.blocked')!.reason).toBe('panel_missing')
    expect(find('align.blocked')!.document_variant).toBeNull()
  })
})

describe('几何权威精确时的写入', () => {
  it('放行，并按 request → commit 的顺序留下带几何的 trace', () => {
    // 已画好的那版就是文档这一版
    const { documentKey } = setup([], [])
    const ok = geomWrite(documentKey)

    expect(ok).toBe(true)
    expect(useDocumentStore.getState().past).toHaveLength(1)

    const order = typesOf()
    expect(order.indexOf('align.request')).toBeLessThan(order.indexOf('align.commit'))
    // 危险写入没发生，就不该有不变式违反
    expect(order).not.toContain('invariant.violation')
    expect(order).not.toContain('align.blocked')

    const commit = find('align.commit')!
    expect(commit.exact_authority).toBe(true)
    expect(commit.patch_count).toBe(1)
    expect(commit.move_count).toBe(0)
    expect(commit.selected_count).toBe(2)
    // bbox 数字在
    expect(commit.input_geometry).toEqual([{ gid: 'axes_0', bbox: [0.2, 0.2, 0.3, 0.3] }])
    // 文字不在
    expect(JSON.stringify(commit)).not.toContain('label')
  })

  it('写入本身也经过 document.commit（历史平面没有被绕开）', () => {
    const { documentKey } = setup([], [])
    geomWrite(documentKey)
    const commit = readDiagnosticTrace().find((e) => e.type === 'document.commit')!
    expect(commit).toBeTruthy()
    expect(commit.document_hash_before).not.toBe(commit.document_hash_after)
    expect(commit.patches).toEqual([{ gid: 'axes_0', prop: 'position' }])
  })

  it('什么都不用写时记 align.noop，不产生历史', () => {
    const { documentKey } = setup([], [])
    const ok = commitGeometryWrite({
      panelId: PANEL_ID,
      operation: 'align.left',
      authority: documentKey,
      label: msg('alignMode.left', undefined, 'inspector'),
      patches: [],
      trace: { mode: 'left', selectedCount: 0 },
    })
    expect(ok).toBe(false)
    expect(typesOf()).toEqual(['align.noop'])
    expect(useDocumentStore.getState().past).toHaveLength(0)
  })
})

describe('readAuthority', () => {
  it('三个身份分别是文档、画布上挂的那版、量几何的那版', () => {
    const { panel, renderedKey, documentKey } = setup(
      [{ gid: 'axes_0.title', prop: 'fontsize', value: 12 }],
      [],
    )
    const view = readAuthority(panel)
    expect(view.documentVariant).toBe(documentKey)
    // 自己那版还没有 SVG，显示与几何都退回该文件最近画好的那份
    expect(view.displayVariant).toBe(renderedKey)
    expect(view.authorityVariant).toBe(renderedKey)
    expect(view.exact).toBe(false)
  })
})

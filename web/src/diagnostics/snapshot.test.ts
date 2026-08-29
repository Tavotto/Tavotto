/**
 * frontend-state.json、undo/redo 竞态的可读性、以及诊断写入的开销
 * （ADR 0016 §7 / §15 / §22）。
 *
 * §22 的验收标准是「开发者不用猜」：竞态用例因此不只断言业务行为，还断言
 * **trace 把三个变体身份的变化说清楚了**——诊断能力本身要有用例守着，
 * 否则它会在某次重构里悄悄退化成一串没有信息量的记录。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { renderKey, renderKeyOf, useRenderStore, type PanelRender } from '@/store/renderStore'
import { resetPreview } from '@/store/svgPreviewStore'
import { msg } from '@/i18n'
import type { Manifest } from '@/lib/api'
import { VECTOR_PREVIEW } from '@/lib/previewBudget'
import type { PanelObject } from '@/types/document'
import { __resetDiagnosticsForTests, readDiagnosticTrace, recordDiagnosticEvent } from './store'
import { __setDiagnosticSaltForTests, docHash, variantHash } from './hash'
import { buildFrontendDiagnosticSnapshot } from './snapshot'
import { buildDiagnosticPayload } from './index'

const MANIFEST = {
  stem: 'Fig1',
  size_mm: [100, 80],
  elements: [{ gid: 'axes_0.title' }, { gid: 'axes_0.xaxis.label' }, { gid: 'axes_0' }],
} as unknown as Manifest

const ready = (overrides: unknown[]): PanelRender =>
  ({
    fileId: 'Fig1.pdf',
    rev: 1,
    manifest: MANIFEST,
    svg: '<svg/>',
    svgBytes: 6,
    svgEvicted: false,
    svgSeq: 1,
    status: 'ready',
    error: null,
    code: '',
    module: '',
    projectEnv: null,
    dependencyRepair: null,
    traceback: '',
    warnings: [],
    timings: {},
    stale: false,
    lastPatches: JSON.stringify(overrides),
    wantPatches: null,
    previewDpi: null,
    preview: VECTOR_PREVIEW,
  }) as PanelRender

const panelWith = (overrides: { gid: string; prop: string; value: unknown }[]): PanelObject =>
  ({
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
    overrides,
  }) as unknown as PanelObject

beforeEach(() => {
  __resetDiagnosticsForTests()
  __setDiagnosticSaltForTests('test-salt')
  resetPreview()
  useSelectionStore.setState({ ids: [] })
  useUiStore.setState({ elementPanelId: null, selectedGids: [] })
  useDocumentStore.setState({ past: [], future: [], txn: null })
  useRenderStore.setState({ byKey: {}, latest: {}, tracked: {}, building: {} })
})

describe('frontend state snapshot', () => {
  it('document=A、display/authority=B、事务开着、选中 3 个 gid', () => {
    const panel = panelWith([{ gid: 'axes_0.title', prop: 'fontsize', value: 12 }])
    useDocumentStore.setState({
      doc: { ...useDocumentStore.getState().doc, objects: [panel] },
      past: [{ label: msg('setProp', undefined, 'inspector'), patches: [], inverse: [] }],
      future: [],
      txn: { label: msg('moveElement', undefined, 'inspector'), patches: [], inverse: [] },
    })
    // 已画好的是「没有 override」那一版（变体 B）
    const renderedKey = renderKey('Fig1.pdf', [])
    useRenderStore.setState({ byKey: { [renderedKey]: ready([]) }, latest: { 'Fig1.pdf': renderedKey } })
    useUiStore.setState({
      elementPanelId: 'p1',
      selectedGids: ['axes_0.title', 'axes_0.xaxis.label', 'axes_0'],
    })

    const snap = buildFrontendDiagnosticSnapshot()

    expect(snap.schema_version).toBe(1)
    expect(snap.document.object_count).toBe(1)
    expect(snap.document.panel_count).toBe(1)
    expect(snap.document.history).toEqual({
      past: 1,
      future: 0,
      txn_open: true,
      txn_label_key: 'moveElement',
    })
    expect(snap.selection.selection_kind).toBe('element')
    expect(snap.selection.element_count).toBe(3)
    expect(snap.selection.element_gids).toEqual([
      'axes_0.title',
      'axes_0.xaxis.label',
      'axes_0',
    ])
    expect(snap.selection.active_panel).toMatch(/^panel:[0-9a-f]{12}$/)
    expect(snap.preview.active_sessions).toBe(0)

    expect(snap.panels).toHaveLength(1)
    const p = snap.panels[0]
    expect(p.kind).toBe('matplotlib')
    expect(p.override_count).toBe(1)
    expect(p.document_variant).toBe(variantHash(renderKeyOf(panel)))
    expect(p.display_variant).toBe(variantHash(renderedKey))
    // **关键一行**：权威要么就是文档这一版，要么根本没有——「来自别的变体的
    // 权威」这个概念本身就不该存在（ADR 0017 的 exactPanelRender）
    expect(p.authority_variant).toBeNull()
    expect(p.display_exact).toBe(false)
    expect(p.exact_manifest_available).toBe(false)
    expect(p.element_count).toBe(3)
  })

  it('权威就绪时 exact_manifest_available 为真', () => {
    const panel = panelWith([])
    useDocumentStore.setState({ doc: { ...useDocumentStore.getState().doc, objects: [panel] } })
    const key = renderKeyOf(panel)
    useRenderStore.setState({ byKey: { [key]: ready([]) }, latest: { 'Fig1.pdf': key } })
    const p = buildFrontendDiagnosticSnapshot().panels[0]
    expect(p.exact_manifest_available).toBe(true)
    expect(p.display_exact).toBe(true)
  })

  it('快照里没有文件名、面板名、图内文字', () => {
    const panel = panelWith([
      { gid: 'axes_0.title', prop: 'text', value: 'SUPER_SECRET_PAPER_TITLE_12345' },
    ])
    useDocumentStore.setState({
      doc: { ...useDocumentStore.getState().doc, name: 'SUPER_SECRET_PAPER_TITLE_12345', objects: [panel] },
    })
    const dump = JSON.stringify(buildFrontendDiagnosticSnapshot())
    expect(dump).not.toContain('SUPER_SECRET')
    expect(dump).not.toContain('Fig1.pdf')
    expect(dump).not.toContain('fig.py')
  })
})

describe('undo 竞态：align → 渲染在途 → undo → 迟到的渲染成功', () => {
  it('trace 把 document / display / authority 的变化说清楚', () => {
    const before = panelWith([])
    useDocumentStore.setState({ doc: { ...useDocumentStore.getState().doc, objects: [before] } })
    const keyA = renderKeyOf(before)
    useRenderStore.setState({ byKey: { [keyA]: ready([]) }, latest: { 'Fig1.pdf': keyA } })

    // ① 一次几何写入落进历史 → 文档变成变体 B
    useDocumentStore.getState().commit(msg('alignMode.left', undefined, 'inspector'), (d) => {
      const p = d.objects[0] as PanelObject
      p.overrides = [{ gid: 'axes_0', prop: 'position', value: [0.1, 0.1, 0.3, 0.3] }]
    })
    const keyB = renderKeyOf(useDocumentStore.getState().doc.objects[0] as PanelObject)
    expect(keyB).not.toBe(keyA)

    // ② B 的渲染在途：byKey 里 B 还没有 manifest，权威仍是 A
    useRenderStore.getState().patch(keyB, { fileId: 'Fig1.pdf', status: 'rendering' })

    // ③ 用户撤销 → 文档回到 A
    useDocumentStore.getState().undo()

    // ④ 迟到的 B 渲染成功了（用户已经不在 B 上了），并且 A 那份变体在此期间
    //    被 prune 回收了——A 既不是 live 键也不再是 latest，正是 prune 的清理对象。
    //    这一步是构造「撤销之后手上没有当前这一版的几何」的**必要条件**：
    //    A 的 manifest 还在缓存里的话，撤销回到 A 反而是精确的（那是好情况）
    useRenderStore.setState((s) => {
      const byKey = { ...s.byKey, [keyB]: ready([{ gid: 'axes_0', prop: 'position' }]) }
      delete byKey[keyA]
      return { byKey, latest: { 'Fig1.pdf': keyB } }
    })

    const trace = readDiagnosticTrace()
    const types = trace.map((e) => e.type)
    expect(types).toContain('document.commit')
    expect(types).toContain('undo.request')
    expect(types).toContain('undo.complete')

    const commit = trace.find((e) => e.type === 'document.commit')!
    const undo = trace.find((e) => e.type === 'undo.complete')!

    // 文档哈希讲得出「去了哪、又回到了哪」——撤销后必须回到 commit 之前那个状态
    expect(commit.document_hash_after).not.toBe(commit.document_hash_before)
    expect(undo.ok).toBe(true)
    expect(undo.document_hash_before).toBe(commit.document_hash_after)
    expect(undo.document_hash_after).toBe(commit.document_hash_before)
    expect(undo.past_count).toBe(0)
    expect(undo.future_count).toBe(1)

    // 此刻的快照如实说：文档是 A，而画布/权威已经被那次迟到的渲染换成了 B
    const p = buildFrontendDiagnosticSnapshot().panels[0]
    expect(p.document_variant).toBe(variantHash(keyA))
    // 画布上挂的是那份迟到的 B，但**权威是空的**：文档在 A，而 A 这一版
    // 手上没有对得上的 manifest。此刻任何几何写入都会被拒绝，这正是想要的
    expect(p.display_variant).toBe(variantHash(keyB))
    expect(p.authority_variant).toBeNull()
    expect(p.exact_manifest_available).toBe(false)
  })

  it('栈空时 undo.complete 记 ok=false，哈希前后相同', () => {
    useDocumentStore.setState({ past: [], future: [] })
    useDocumentStore.getState().undo()
    const undo = readDiagnosticTrace().find((e) => e.type === 'undo.complete')!
    expect(undo.ok).toBe(false)
    expect(undo.document_hash_before).toBe(undo.document_hash_after)
  })
})

describe('导出载荷', () => {
  it('带上 diagnostics.export 作为时间锚点，并且是脱敏后的形状', () => {
    const payload = buildDiagnosticPayload()
    const last = payload.interaction_trace.at(-1)!
    expect(last.type).toBe('diagnostics.export')
    expect(payload.frontend_state.schema_version).toBe(1)
    for (const ev of payload.interaction_trace) {
      expect(typeof ev.seq).toBe('number')
      expect(typeof ev.ts).toBe('number')
      expect(typeof ev.t_ms).toBe('number')
    }
  })
})

describe('生产方守约：前端真的发得出后端收得下的形状', () => {
  /*
   * 后端 `engine/diagnostics_frontend.py` 的判据是独立写的，两边只靠**形状**
   * 对上。所以光测「后端会拒绝坏载荷」还不够——还得测「前端真发出来的那份
   * 过得了那些判据」。少了这一半，两边可以各自绿着，而诊断包里一条事件都没有。
   *
   * 这不是假想：epoch 毫秒一度超出后端的整数上界，环里看着好好的，
   * 一到导出全部被丢——正是这一类断言把它逼出来的。
   */
  const HASH_RE = /^[a-z_]+:[0-9a-f]{8,16}$/
  const GID_RE = /^[a-z][a-z0-9_.:-]{0,63}$/
  const FIELD_RE = /^[a-z][a-z0-9_]{0,31}$/
  const TOKEN_RE = /^[A-Za-z0-9_.:-]{1,64}$/
  const MAX_TIMESTAMP = 4_000_000_000_000
  const isIdentity = (k: string) =>
    k.endsWith('_hash') ||
    k.endsWith('_variant') ||
    ['panel', 'file', 'session', 'version', 'active_panel', 'variant'].includes(k)

  const checkValue = (key: string, value: unknown, where: string) => {
    if (value === null || typeof value === 'boolean' || typeof value === 'number') return
    if (Array.isArray(value)) {
      for (const v of value) checkValue(key, v, where)
      return
    }
    if (typeof value === 'object') {
      for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
        if (k === 'gid') expect(String(v), `${where}.gid`).toMatch(GID_RE)
        else checkValue(k, v, `${where}.${k}`)
      }
      return
    }
    const text = String(value)
    if (isIdentity(key)) expect(text, `${where} 应是 hash`).toMatch(HASH_RE)
    else expect(text, `${where} 应是短技术标识`).toMatch(TOKEN_RE)
  }

  it('每条事件的字段名与取值都过得了后端那三条判据', () => {
    // 走一遍真实路径，让各类事件都进环
    const panel = panelWith([])
    useDocumentStore.setState({ doc: { ...useDocumentStore.getState().doc, objects: [panel] } })
    const key = renderKeyOf(panel)
    useRenderStore.setState({ byKey: { [key]: ready([]) }, latest: { 'Fig1.pdf': key } })
    useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.title'] })
    useDocumentStore.getState().commit(msg('setProp', undefined, 'inspector'), (d) => {
      ;(d.objects[0] as PanelObject).overrides = [
        { gid: 'axes_0.title', prop: 'fontsize', value: 12 },
      ]
    })
    useDocumentStore.getState().undo()
    useDocumentStore.getState().redo()

    const payload = buildDiagnosticPayload()
    expect(payload.interaction_trace.length).toBeGreaterThan(3)

    for (const ev of payload.interaction_trace) {
      expect(Number.isInteger(ev.seq)).toBe(true)
      expect(ev.ts).toBeLessThanOrEqual(MAX_TIMESTAMP)
      expect(ev.t_ms).toBeLessThanOrEqual(1_000_000_000)
      for (const [k, v] of Object.entries(ev)) {
        if (['seq', 'ts', 't_ms', 'type'].includes(k)) continue
        expect(k, `字段名 ${k}`).toMatch(FIELD_RE)
        checkValue(k, v, `${String(ev.type)}.${k}`)
      }
    }
  })

  it('快照里的身份字段也都是 hash，gid 都是小写形状', () => {
    const panel = panelWith([])
    useDocumentStore.setState({ doc: { ...useDocumentStore.getState().doc, objects: [panel] } })
    const key = renderKeyOf(panel)
    useRenderStore.setState({ byKey: { [key]: ready([]) }, latest: { 'Fig1.pdf': key } })
    useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.title'] })

    const snap = buildDiagnosticPayload().frontend_state
    expect(snap.document.document_hash).toMatch(HASH_RE)
    expect(snap.selection.active_panel!).toMatch(HASH_RE)
    for (const g of snap.selection.element_gids) expect(g).toMatch(GID_RE)
    for (const p of snap.panels) {
      expect(p.panel).toMatch(HASH_RE)
      expect(p.file).toMatch(HASH_RE)
      expect(p.document_variant).toMatch(HASH_RE)
      if (p.display_variant) expect(p.display_variant).toMatch(HASH_RE)
      if (p.authority_variant) expect(p.authority_variant).toMatch(HASH_RE)
    }
  })
})

describe('性能预算（ADR 0016 §15）', () => {
  it('连续 1000 条事件的总写入耗时远低于一帧预算，且内存有界', () => {
    const t0 = performance.now()
    for (let i = 0; i < 1000; i++) {
      recordDiagnosticEvent({
        type: 'document.commit',
        label_key: 'setProp',
        patch_count: 1,
        past_count: i,
        future_count: 0,
        txn_open: false,
        document_hash_before: docHash(i),
        document_hash_after: docHash(i + 1),
        patches: [{ gid: 'axes_0.title', prop: 'fontsize' }],
      })
    }
    const elapsed = performance.now() - t0
    // 1000 条**加起来**都不该到 100ms（实测在几毫秒量级）。阈值取得宽松是
    // 刻意的：这条用例要抓的是「有人往写入路径里塞了个 O(文档大小) 的操作」，
    // 不是给 CI 机器的抖动当秒表
    expect(elapsed).toBeLessThan(100)
    expect(readDiagnosticTrace()).toHaveLength(240)

    // 序列化后的体积也要落在预算里（100–300 KB）
    const bytes = new TextEncoder().encode(
      readDiagnosticTrace().map((e) => JSON.stringify(e)).join('\n'),
    ).length
    expect(bytes).toBeLessThan(300 * 1024)
  })

  it('文档摘要走结构共享：只改一个对象时不会把整份文档重新 hash', () => {
    // 一份「大」文档：500 个对象
    const objects = Array.from({ length: 500 }, (_, i) => ({
      id: `o${i}`,
      type: 'text',
      x: i,
      y: i,
      w: 10,
      h: 10,
      text: `label ${i}`,
    }))
    useDocumentStore.setState({
      doc: { ...useDocumentStore.getState().doc, objects: objects as never },
      past: [],
      future: [],
      txn: null,
    })

    // 第一次：冷的，要把 500 个对象都 hash 一遍
    const t0 = performance.now()
    useDocumentStore.getState().commit(msg('setProp', undefined, 'inspector'), (d) => {
      ;(d.objects[0] as { x: number }).x = 999
    })
    const cold = performance.now() - t0

    // 之后每次只改一个对象：其余 499 个引用没变，WeakMap 直接命中
    const t1 = performance.now()
    for (let i = 1; i <= 20; i++) {
      useDocumentStore.getState().commit(msg('setProp', undefined, 'inspector'), (d) => {
        ;(d.objects[i] as { x: number }).x = 1000 + i
      })
    }
    const warmEach = (performance.now() - t1) / 20

    // 热态单次必须明显快过冷启动那次；这条是「结构共享真的被用上了」的判据
    expect(warmEach).toBeLessThan(Math.max(cold, 1))
  })
})

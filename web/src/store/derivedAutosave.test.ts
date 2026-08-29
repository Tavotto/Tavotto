/**
 * 派生元数据同步与保存链路的关系（Prompt 06 §五，UX_CONTRACTS 不变式 A）。
 *
 * 这一份钉的是那张三行表里最难的一行：
 *
 * | 性质 | dirty | saveState | 历史 | 落盘 |
 * | 派生同步 | 置位 | **不动** | 不进 | **排队** |
 *
 * 「不动 saveState」与「照样落盘」必须同时成立。只做前者的话，用户下次打开
 * 文档面板又回到不可编辑（`script` 是存进文档的字段）；只做后者的话，一次
 * 外部文件改动会让关闭保护弹一句"有未保存的改动"——说的是用户没做过的事。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { PanelInfo } from '@/lib/api'
import type { CanvasData, PanelObject } from '@/types/document'
import { canvasToDoc } from '@/types/document'
import { applyDerivedUpdate, hasUnsavedWork, startAutosave, useDocumentStore } from './documentStore'
import { syncPanelSourceMetadata } from './panelSourceSync'

/** 假的 /api/autosave 槽位；其余请求 404 */
const disk = new Map<string, string>()
globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const m = String(url).match(/\/api\/autosave\/([^/?]+)/)
  if (!m) return new Response('{}', { status: 404 })
  const id = decodeURIComponent(m[1])
  if (init?.method === 'PUT') {
    disk.set(id, String(init.body))
    return new Response(JSON.stringify({ ok: true, saved_at: 1, revision: 'r1' }), { status: 200 })
  }
  const v = disk.get(id)
  return new Response(v ?? '{}', { status: v ? 200 : 404 })
}) as typeof fetch

const DOC_ID = 'd_derived'
const slotKey = `tavotto.autosave.${DOC_ID}`

const info = (id: string, script: string | undefined): PanelInfo => ({
  id,
  name: 'Fig1',
  folder: '.',
  kind: 'pdf',
  native_w_mm: 80,
  native_h_mm: 60,
  mtime: 1,
  ...(script ? { script } : {}),
})

const panel = (script: string | null): PanelObject => ({
  id: 'o1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 10,
  y: 20,
  w: 40,
  h: 30,
  script,
})

let stopAutosave: () => void

function seed(script: string | null): void {
  const canvas: CanvasData = {
    id: 'c1',
    name: 'Fig 1',
    page: { w: 150, h: 100 },
    objects: [panel(script)],
    guides: [],
  }
  useDocumentStore.setState({
    doc: canvasToDoc(canvas),
    documentId: DOC_ID,
    projectMeta: { id: 'p1', name: 'proj', createdAt: 1 },
    canvases: [canvas],
    activeCanvasId: 'c1',
    openTabs: ['c1'],
    canvasSessions: {},
    past: [],
    future: [],
    txn: null,
    dirty: false,
    saveState: 'clean',
    saveIssue: null,
    derivedSeq: 0,
    // 「换一份文档」而不是「编辑了一下」：不升代次的话，自动保存的订阅会把
    // 这次装载当成一次用户编辑，用例还没开始就已经 dirty 了
    loadSeq: useDocumentStore.getState().loadSeq + 1,
  })
}

const s = () => useDocumentStore.getState()

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  disk.clear()
  seed(null)
  stopAutosave = startAutosave()
  return () => {
    stopAutosave()
    vi.useRealTimers()
  }
})

describe('派生同步与保存状态', () => {
  it('置 dirty、排一次自动保存，但**不把状态推成「未保存」**', () => {
    syncPanelSourceMetadata({ 'Fig1.pdf': info('Fig1.pdf', 'fig1.py') })

    expect(s().dirty).toBe(true)
    expect(s().saveState).toBe('clean')
    expect(hasUnsavedWork(s().saveState)).toBe(false)
  })

  it('防抖窗口过去之后，本机崩溃兜底副本里带着最新的派生元数据', () => {
    syncPanelSourceMetadata({ 'Fig1.pdf': info('Fig1.pdf', 'fig1.py') })
    vi.advanceTimersByTime(1000)

    const saved = localStorage.getItem(slotKey)
    expect(saved).toBeTruthy()
    const objects = (JSON.parse(saved!) as { canvases: { objects: PanelObject[] }[] }).canvases[0]
      .objects
    expect(objects[0].script).toBe('fig1.py')
    expect(s().dirty).toBe(false)
  })

  it('不进撤销历史', () => {
    const past = [{ label: literal('之前的一步'), patches: [], inverse: [] }]
    useDocumentStore.setState({ past })
    syncPanelSourceMetadata({ 'Fig1.pdf': info('Fig1.pdf', 'fig1.py') })
    expect(s().past).toBe(past)
    expect(s().future).toEqual([])
  })

  it('用户编辑照旧推成 dirty——「不推」只对派生同步这一档生效', () => {
    s().commit(literal('挪一下'), (d) => {
      d.objects[0].x = 99
    })
    expect(s().saveState).toBe('dirty')
    expect(hasUnsavedWork(s().saveState)).toBe(true)
  })

  it('已经是 dirty 时，派生同步不会把状态降回去', () => {
    s().commit(literal('挪一下'), (d) => {
      d.objects[0].x = 99
    })
    syncPanelSourceMetadata({ 'Fig1.pdf': info('Fig1.pdf', 'fig1.py') })
    expect(s().saveState).toBe('dirty')
  })

  it('冲突未决时派生同步只写本机副本，不往磁盘上撞', () => {
    useDocumentStore.setState({
      saveState: 'conflict',
      saveIssue: { kind: 'stale', docId: DOC_ID },
    })
    syncPanelSourceMetadata({ 'Fig1.pdf': info('Fig1.pdf', 'fig1.py') })
    vi.advanceTimersByTime(1000)

    expect(s().saveState).toBe('conflict')
    expect(localStorage.getItem(slotKey)).toBeTruthy()
    expect(disk.size).toBe(0)
  })

  it('空载荷是彻底的 no-op：代次都不升', () => {
    // 写入口自己也要守住这条，不能只靠调用方先算差异——它是导出的，
    // 下一个调用方（Prompt 07 的就绪度）不一定会先算
    const before = s().doc
    applyDerivedUpdate({})
    expect(s().doc).toBe(before)
    expect(s().derivedSeq).toBe(0)
    expect(s().dirty).toBe(false)
  })

  it('无差异不排落盘：不多一个 updatedAt 去和别的标签页抢基线', () => {
    seed('fig1.py')
    syncPanelSourceMetadata({ 'Fig1.pdf': info('Fig1.pdf', 'fig1.py') })
    vi.advanceTimersByTime(1000)
    expect(s().dirty).toBe(false)
    expect(localStorage.getItem(slotKey)).toBeNull()
  })
})

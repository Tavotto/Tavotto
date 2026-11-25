/**
 * `restoreLayoutVersion` 的 schema 分派（版本链）：
 * - schema 2 单画布节点：仍然只盖当前画布、⌘Z 可退（既有路径逐字不变）；
 * - schema 3 / 4 整份项目节点：经 migrateToProject → switchDocument 换整份项目；
 * - 读不出来的项目正文：返回 false，当前文档零改动。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { emptyProject } from '@/types/document'
import type { FigureDocument, PanelObject, ProjectDocument, TextObject } from '@/types/document'
import { projectFileSnapshot, setProjectFile, useDocumentStore, saveNowWithResult, setWindowHold } from './documentStore'
import { fetchAutosave } from '@/lib/api'
import { openRecentDocument, restoreLayoutVersion } from './actions'

const text = (id: string, value: string): TextObject => ({
  id, type: 'text', text: value, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

const chartPanel = (id: string): PanelObject => ({
  id, type: 'panel', fileId: 'input.svg', fileKind: 'svg',
  nativeW: 10, nativeH: 10, x: 0, y: 0, w: 10, h: 10,
  overrides: [], chart: null as unknown as PanelObject['chart'],
})

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const s = () => useDocumentStore.getState()

const twoCanvasProject = (): ProjectDocument => {
  const pd = emptyProject()
  pd.project = { id: 'p_restore', name: '当前的' }
  pd.canvases[0] = {
    id: 'c_a', name: 'A', page: { w: 150, h: 100 },
    objects: [text('now_a', '现在 A')], guides: [],
  }
  pd.canvases.push({
    id: 'c_b', name: 'B', page: { w: 120, h: 90 },
    objects: [text('now_b', '现在 B')], guides: [],
  })
  pd.activeCanvasId = 'c_a'
  return pd
}

const projectVersion = (schema: 3 | 4, extra?: (pd: ProjectDocument) => void): ProjectDocument => {
  const pd = emptyProject()
  pd.project = { id: 'p_restore', name: '版本里的项目' }
  pd.schema = schema
  pd.canvases[0] = {
    id: 'c_a', name: 'A 版', page: { w: 200, h: 120 },
    objects: [text('ver_a', '版本 A')], guides: [],
  }
  pd.canvases.push({
    id: 'c_b', name: 'B 版', page: { w: 100, h: 80 },
    objects: [text('ver_b', '版本 B')], guides: [],
  })
  pd.activeCanvasId = 'c_b'
  extra?.(pd)
  return pd
}

beforeEach(async () => {
  localStorage.clear()
  setCurrentProjectId(null)
  await s().switchDocument(twoCanvasProject(), 'd_restore')
})

afterEach(() => { setWindowHold(null); setCurrentProjectId(null); vi.unstubAllGlobals() })

describe('restoreLayoutVersion：schema 分派', () => {
  it('整份项目还原为空画布后，同一槽位重开仍然是空画布', async () => {
    await saveNowWithResult()
    let stored = s().buildProject()
    vi.stubGlobal('fetch', vi.fn(async (url: string, opts?: RequestInit) => {
      if (String(url).includes('/api/autosave/d_restore')) {
        if (opts?.method === 'PUT') stored = JSON.parse(opts.body as string)
        return new Response(JSON.stringify(opts?.method === 'PUT' ? { ok: true } : stored), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response('{}', { status: 200 })
    }))
    const empty = emptyProject()
    expect(await restoreLayoutVersion(literal('恢复空画布'), empty)).toBe(true)
    expect(s().documentId).toBe('d_restore')
    expect(stored.canvases).toHaveLength(1)
    expect(stored.canvases[0].objects).toEqual([])
    const reopened = await fetchAutosave('d_restore')
    expect(reopened).not.toBeNull()
    await s().switchDocument(reopened!.doc as ProjectDocument, 'd_restore')
    expect(s().doc.objects).toEqual([])
  })

  it('直接还原动作也不能绕过只读锁', async () => {
    setWindowHold({ role: 'contested', docId: 'd_restore' })
    const before = s().doc
    expect(await restoreLayoutVersion(literal('restore'), projectVersion(3))).toBe(false)
    expect(s().doc).toBe(before)
    expect(s().windowHold?.role).toBe('contested')
  })
  it('schema 2 单画布节点：只盖当前画布，别的画布不动，⌘Z 退回（既有路径）', async () => {
    const version: FigureDocument = {
      schema: 2,
      name: '单画布版',
      page: { w: 180, h: 90 },
      objects: [text('ver', '单画布')],
      guides: [],
    }
    await restoreLayoutVersion(literal('恢复单画布'), version)
    expect(s().doc.name).toBe('单画布版')
    expect(s().doc.objects.map((o) => o.id)).toEqual(['ver'])
    // 非激活画布原样
    expect(s().canvases.find((c) => c.id === 'c_b')?.objects.map((o) => o.id)).toEqual(['now_b'])
    // 既有路径是一条历史：撤销一步回到恢复前
    expect(s().canUndo()).toBe(true)
    s().undo()
    expect(s().doc.name).toBe('A')
    expect(s().doc.objects.map((o) => o.id)).toEqual(['now_a'])
  })

  it('schema 3 整份项目节点：换整份项目，documentId 不变，所有画布都换成那一版', async () => {
    const restored = await restoreLayoutVersion(literal('恢复项目'), projectVersion(3))
    expect(restored).toBe(true)
    expect(s().documentId).toBe('d_restore')
    const pd = s().buildProject()
    expect(pd.project.name).toBe('版本里的项目')
    expect(pd.schema).toBe(3)
    expect(pd.canvases.map((c) => c.id)).toEqual(['c_a', 'c_b'])
    expect(pd.canvases.map((c) => c.objects.map((o) => o.id))).toEqual([['ver_a'], ['ver_b']])
    expect(pd.activeCanvasId).toBe('c_b')
    expect(s().doc.name).toBe('B 版')
    // 换整份项目不是一次 commit：撤销栈清空，退回靠「恢复前」节点
    expect(s().canUndo()).toBe(false)
  })

  it('schema 4 整份项目节点：图表面板原样换回来（不降 schema、不丢 chart）', async () => {
    const version = projectVersion(4, (pd) => {
      pd.canvases[1].objects = [chartPanel('p_chart')]
    })
    const restored = await restoreLayoutVersion(literal('恢复图表项目'), version)
    expect(restored).toBe(true)
    const pd = s().buildProject()
    expect(pd.schema).toBe(4)
    const panel = pd.canvases[1].objects[0] as PanelObject
    expect(panel.id).toBe('p_chart')
    expect(panel).toHaveProperty('chart')
  })

  it('项目节点恢复后，已绑定的项目文件标脏并推进编辑代次（⌘S 不会以为已经存过）', async () => {
    setCurrentProjectId('p_restore')
    setProjectFile({
      projectId: 'p_restore', name: '排版', file: 'tavottofile/排版.json',
      revision: null, dirty: false,
    })
    // 模拟「保存已经在飞」：快照在恢复之前取，回来时用它判「途中改没改过」。
    // 只改 dirty 不推进代次的话，这里会判 false，回执把 dirty 洗回 false。
    const editedSinceSave = projectFileSnapshot()
    const restored = await restoreLayoutVersion(literal('恢复项目'), projectVersion(3))
    expect(restored).toBe(true)
    expect(s().projectFile).toMatchObject({ dirty: true })
    expect(editedSinceSave()).toBe(true)
  })

  it('读不出来的项目正文：返回 false，当前文档一个字节都不动', async () => {
    const broken = { schema: 3, canvases: [] } as unknown as ProjectDocument
    const restored = await restoreLayoutVersion(literal('恢复坏项目'), broken)
    expect(restored).toBe(false)
    expect(s().documentId).toBe('d_restore')
    expect(s().doc.objects.map((o) => o.id)).toEqual(['now_a'])
    expect(s().canvases.find((c) => c.id === 'c_b')?.objects.map((o) => o.id)).toEqual(['now_b'])
  })
})

it('最近打开浏览器副本时，磁盘附件损坏的 422 不得变成无 revision PUT', async () => {
  await saveNowWithResult()
  const local = twoCanvasProject()
  localStorage.setItem('tavotto.autosave.d_unread_attachment', JSON.stringify(local))
  const puts: RequestInit[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: unknown, opts?: RequestInit) => {
    if (String(url).includes('/api/autosave/d_unread_attachment')) {
      if (opts?.method === 'PUT') puts.push(opts)
      return new Response(JSON.stringify({ code: 'chart_source_mismatch' }), { status: 422 })
    }
    return new Response('{}', { status: 200 })
  }))
  await openRecentDocument('d_unread_attachment')
  await saveNowWithResult()
  expect(s().documentId).toBe('d_unread_attachment')
  expect(s().saveState).toBe('save_error')
  expect(puts).toEqual([])
  expect(localStorage.getItem('tavotto.autosave.d_unread_attachment')).not.toBeNull()
})

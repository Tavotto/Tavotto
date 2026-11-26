/**
 * 自动保存写进项目的前端侧（ADR 0211 第二期 PR 2）：开关、节奏（本机 1 s / 项目 5 s 停手 / 30 s 最长）、
 * 绑定的建立与推进、冲突不覆盖、只读一行灰字。开关关着时与改造前逐字相同（不带 `project_write`、不起定时器）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { PROJECT_MAX_MS, PROJECT_QUIET_MS, setProjectWriteCapability } from '@/lib/autosaveProject'
import { forgetProjectFilesInSession, readProjectFile, writeProjectFile } from '@/lib/projectFile'
import { setCurrentProjectId } from '@/lib/session'
import type { CanvasData, PanelObject } from '@/types/document'
import { canvasToDoc } from '@/types/document'
import {
  flushAutosave,
  setProjectLayoutConflictHandler,
  startAutosave,
  useDocumentStore,
} from './documentStore'

const DOC_ID = 'd_project'
const puts: { url: string; spec: { name: string | null; base: string | null } | null }[] = []
let reply: Record<string, unknown> = {}
globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  if (!/\/api\/autosave\//.test(u)) return new Response('{}', { status: 404 })
  if (init?.method === 'PUT') {
    const q = new URL(u, 'http://x').searchParams.get('project_write')
    puts.push({ url: u, spec: q ? JSON.parse(q) : null })
    return new Response(JSON.stringify({ ok: true, saved_at: 1, revision: 'r1', ...reply }), { status: 200 })
  }
  return new Response('{}', { status: 404 })
}) as typeof fetch

const panel = (): PanelObject => ({
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
  script: null,
})

function seed(): void {
  const canvas: CanvasData = { id: 'c1', name: 'Fig 1', page: { w: 150, h: 100 }, objects: [panel()], guides: [] }
  useDocumentStore.setState({
    doc: canvasToDoc(canvas),
    documentId: DOC_ID,
    projectMeta: { id: 'p1', name: '排版一', createdAt: 1 },
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
    projectFile: null,
    projectReadOnlyDoc: null,
    loadSeq: useDocumentStore.getState().loadSeq + 1,
  })
}

const edit = (x = 99) =>
  useDocumentStore.getState().commit(literal('挪一下'), (d) => {
    d.objects[0].x = x
  })
const settle = () => vi.advanceTimersByTimeAsync(10)
const s = () => useDocumentStore.getState()

let stop: () => void
beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  forgetProjectFilesInSession()
  puts.length = 0
  reply = {}
  setCurrentProjectId('pj1')
  seed()
  stop = startAutosave()
})
afterEach(() => {
  stop()
  setProjectWriteCapability(false)
  setProjectLayoutConflictHandler(null)
  vi.useRealTimers()
})

describe('开关关着：与改造前一字不差', () => {
  it('不带 project_write；1 s 的自动保存照旧', async () => {
    edit()
    await vi.advanceTimersByTimeAsync(1000)
    expect(puts).toHaveLength(1)
    expect(puts[0].spec).toBeNull()
    // 之后 5 s / 30 s 都没有额外的写盘
    await vi.advanceTimersByTimeAsync(PROJECT_MAX_MS + 1000)
    expect(puts).toHaveLength(1)
  })
})

describe('开关开着：节奏', () => {
  beforeEach(() => setProjectWriteCapability(true))

  it('1 s 的那一笔只写本机；停手满 5 s 才顺带写项目', async () => {
    edit()
    await vi.advanceTimersByTimeAsync(1000)
    expect(puts.map((p) => p.spec)).toEqual([null])
    await vi.advanceTimersByTimeAsync(PROJECT_QUIET_MS - 1000)
    await settle()
    expect(puts).toHaveLength(2)
    expect(puts[1].spec).toEqual({ name: null, base: null })
  })

  it('一直在改停不下来：最长 30 s 强制写一次项目', async () => {
    for (let t = 0; t < PROJECT_MAX_MS; t += 3000) {
      edit(t)
      await vi.advanceTimersByTimeAsync(3000)
    }
    await settle()
    const withProject = puts.filter((p) => p.spec !== null)
    expect(withProject).toHaveLength(1)
  })

  it('手动保存、离开页面的冲刷顺带写项目', async () => {
    edit()
    flushAutosave()
    await settle()
    expect(puts.at(-1)?.spec).not.toBeNull()
  })

  it('第一次写成：按后端起的名字绑定，基线是它回的修订号；之后的写带着绑定', async () => {
    reply = { project_layout: { state: 'saved', name: '排版一', file: 'tavottofile/排版一.json', revision: 'rev1' } }
    edit()
    flushAutosave()
    await settle()
    expect(readProjectFile(DOC_ID)).toMatchObject({ name: '排版一', file: 'tavottofile/排版一.json', revision: 'rev1', dirty: false })
    edit(5)
    flushAutosave()
    await settle()
    expect(puts.at(-1)?.spec).toEqual({ name: '排版一', base: 'rev1' })
  })

  it('写的途中又改过：项目里那份已经落后，绑定仍是 dirty', async () => {
    reply = { project_layout: { state: 'saved', name: '排版一', file: 'tavottofile/排版一.json', revision: 'rev1' } }
    edit()
    flushAutosave()
    edit(7) // 排队的那份已经定了，这一笔不在里面
    await settle()
    expect(readProjectFile(DOC_ID)?.dirty).toBe(true)
  })

  it('写的途中被另存为改绑到了别的文件：这次的回执只属于旧文件，不把新绑定改回去', async () => {
    writeProjectFile(DOC_ID, { projectId: 'pj1', name: 'b', file: 'tavottofile/b.json', revision: 'rb', dirty: false })
    reply = { project_layout: { state: 'saved', name: 'a', file: 'tavottofile/a.json', revision: 'ra' } }
    edit()
    flushAutosave()
    await settle()
    expect(readProjectFile(DOC_ID)).toMatchObject({ name: 'b', revision: 'rb' })
  })

  it('外部修改：不覆盖、进冲突岔口一次；岔口没裁决前不再带着旧基线去撞', async () => {
    writeProjectFile(DOC_ID, { projectId: 'pj1', name: '排版一', file: 'tavottofile/排版一.json', revision: 'old', dirty: false })
    const seen: string[] = []
    setProjectLayoutConflictHandler((id, c) => seen.push(`${id}:${c.revision}`))
    reply = { project_layout: { state: 'external_change', name: '排版一', revision: 'theirs', summary: null } }
    edit()
    flushAutosave()
    await settle()
    expect(seen).toEqual([`${DOC_ID}:theirs`])
    edit(3)
    flushAutosave()
    await settle()
    expect(puts.at(-1)?.spec).toBeNull() // 基线没动：不再撞
    // 裁决之后绑定的基线变了：恢复写项目
    writeProjectFile(DOC_ID, { projectId: 'pj1', name: '排版一', file: 'tavottofile/排版一.json', revision: 'theirs', dirty: false })
    reply = {}
    edit(4)
    flushAutosave()
    await settle()
    expect(puts.at(-1)?.spec).toEqual({ name: '排版一', base: 'theirs' })
  })

  it('只读：记下这份、顶栏说一句灰字；下一次写成就清', async () => {
    reply = { project_layout: { state: 'read_only' } }
    edit()
    flushAutosave()
    await settle()
    expect(s().projectReadOnlyDoc).toBe(DOC_ID)
    reply = { project_layout: { state: 'saved', name: 'a', file: 'tavottofile/a.json', revision: 'r' } }
    edit(2)
    flushAutosave()
    await settle()
    expect(s().projectReadOnlyDoc).toBeNull()
  })

  it('瞬时失败 / 跳过：什么都不改、不打扰', async () => {
    for (const state of ['busy', 'skipped', 'refused', 'failed']) {
      reply = { project_layout: { state } }
      edit(Math.random() * 100)
      flushAutosave()
      await settle()
    }
    expect(s().projectReadOnlyDoc).toBeNull()
    expect(readProjectFile(DOC_ID)).toBeNull()
  })

  it('没有归属项目 / 只读窗口：不写项目', async () => {
    setCurrentProjectId(null)
    edit()
    flushAutosave()
    await settle()
    expect(puts.at(-1)?.spec).toBeNull()
  })
})

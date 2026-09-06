/**
 * 切项目要回到**那个项目上次开着的文档**（审计 T02）。
 *
 * 改造前：`resetForNewProject()` 换上一份空白 fig_layout 之后没有人再把用户
 * 上次在那个项目里排的版换回来——「返回 Tutorial 后先出现空白 fig_layout，
 * 需再从最近文档载入」。这里守四件事：
 *   1. 有内容的文档会被记在当前项目名下；空白不记；
 *   2. 切回项目时按记录读自动保存槽位并换回去；
 *   3. 读不回来时**说出来**（`lastDocumentIssue`），横幅上的重试能成功；
 *   4. 调用方自带 `prepareDocument`（教程）时不动那份记录。
 */
import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { readProjectDocument } from '@/lib/projectDocs'
import { setCurrentProjectId } from '@/lib/session'
import { emptyProject, type ProjectDocument, type TextObject } from '@/types/document'
import { flushAutosave, useDocumentStore } from './documentStore'
import { useProjectStore } from './projectStore'

const text = (id: string, t: string): TextObject => ({
  id, type: 'text', text: t, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

/** 模拟后端：项目按路径给 id；自动保存槽位按 id 存（忽略 pj） */
const diskSlots = new Map<string, string>()
const PROJECT_IDS: Record<string, string> = { '/figs/a': 'p_a', '/figs/b': 'p_b' }
globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  const m = u.match(/\/api\/autosave\/([^/?]+)/)
  if (m) {
    const id = decodeURIComponent(m[1])
    if (init?.method === 'PUT') {
      diskSlots.set(id, String(init.body))
      return new Response(JSON.stringify({ ok: true, saved_at: 1, revision: 'r1' }), { status: 200 })
    }
    if (init?.method === 'DELETE') {
      diskSlots.delete(id)
      return new Response('{"ok":true}', { status: 200 })
    }
    const v = diskSlots.get(id)
    return new Response(v ?? '{}', {
      status: v ? 200 : 404,
      headers: v ? { 'X-Tavotto-Revision': 'r1' } : undefined,
    })
  }
  if (u.includes('/api/projects/recent')) return new Response('{"recent":[]}', { status: 200 })
  if (u.includes('/api/projects/open')) {
    const path = (JSON.parse(String(init?.body)) as { path: string }).path
    return new Response(
      JSON.stringify({ open: true, id: PROJECT_IDS[path], figures_dir: path, name: path.slice(6) }),
      { status: 200 },
    )
  }
  if (u.includes('/api/projects')) return new Response('{"projects":[],"default":null}', { status: 200 })
  if (u.includes('/api/panels')) return new Response('{"figures_dir":"/figs","panels":[]}', { status: 200 })
  return new Response('{}', { status: 200 })
}) as typeof fetch

const s = () => useDocumentStore.getState()
const p = () => useProjectStore.getState()

/** 在当前项目里造一份有内容的文档并落盘（flush 是同步排队，PUT 要等一拍） */
async function makeContentDoc(id: string, name: string) {
  const pd: ProjectDocument = { ...emptyProject(), project: { id: 'x', name } }
  await s().switchDocument(pd, id)
  s().commit(literal('加一段字'), (d) => {
    d.objects.push(text('t1', 'hello'))
  })
  flushAutosave()
  await new Promise((r) => setTimeout(r, 30))
}

beforeEach(() => {
  localStorage.clear()
  diskSlots.clear()
  setCurrentProjectId('p_a')
  useProjectStore.setState({
    phase: 'open',
    project: { open: true, id: 'p_a', figures_dir: '/figs/a' },
    recent: [],
    opened: [],
    lastDocumentIssue: null,
  })
})

afterEach(() => {
  setCurrentProjectId(null)
})

describe('项目 → 上次文档的记录', () => {
  it('有内容的文档记在当前项目名下；换成空白文档不覆盖它', async () => {
    await makeContentDoc('d_a', 'Fig A')
    expect(readProjectDocument('p_a')).toEqual({ id: 'd_a', name: 'Fig A' })
    await s().switchDocument(emptyProject(), 'd_blank')
    expect(readProjectDocument('p_a')?.id).toBe('d_a')
  })

  it('改名跟着更新', async () => {
    await makeContentDoc('d_a', 'Fig A')
    s().renameProject('Fig A2')
    expect(readProjectDocument('p_a')?.name).toBe('Fig A2')
  })
})

describe('切项目', () => {
  it('切走再切回来，落在那个项目上次开着的文档上', async () => {
    await makeContentDoc('d_a', 'Fig A')
    await p().open('/figs/b')
    expect(s().documentId).not.toBe('d_a')
    expect(s().doc.objects).toHaveLength(0)
    expect(p().lastDocumentIssue).toBeNull()
    // 切回：记录在、槽位在 → 直接换回去，一个横幅都不弹
    await p().open('/figs/a')
    expect(s().documentId).toBe('d_a')
    expect(s().doc.objects.map((o) => o.id)).toEqual(['t1'])
    expect(p().project?.id).toBe('p_a')
    expect(p().phase).toBe('open')
    expect(p().lastDocumentIssue).toBeNull()
  })

  it('记录在、槽位读不回来：说出名字并给重试；重试成功后横幅收起', async () => {
    await makeContentDoc('d_a', 'Fig A')
    await p().open('/figs/b')
    // 磁盘与本机副本都没了（比如换了浏览器、清了数据）
    const disk = diskSlots.get('d_a')!
    diskSlots.delete('d_a')
    localStorage.removeItem('tavotto.autosave.d_a')
    await p().open('/figs/a')
    expect(s().doc.objects).toHaveLength(0)
    expect(p().lastDocumentIssue).toEqual({ id: 'd_a', name: 'Fig A' })
    // 第一次重试仍然失败：横幅留着
    expect(await p().openLastDocument()).toBe(false)
    expect(p().lastDocumentIssue).not.toBeNull()
    // 槽位回来了（后端恢复 / 网盘同步到了）：重试成功
    diskSlots.set('d_a', disk)
    expect(await p().openLastDocument()).toBe(true)
    expect(s().documentId).toBe('d_a')
    expect(p().lastDocumentIssue).toBeNull()
  })

  it('「知道了」收起横幅', async () => {
    useProjectStore.setState({ lastDocumentIssue: { id: 'd_x', name: 'X' } })
    p().dismissLastDocumentIssue()
    expect(p().lastDocumentIssue).toBeNull()
  })

  it('调用方自带 prepareDocument（教程）时不按记录换', async () => {
    await makeContentDoc('d_a', 'Fig A')
    await p().open('/figs/b')
    await p().adoptOpenedProject(
      { open: true, id: 'p_a', figures_dir: '/figs/a' },
      {
        prepareDocument: async () => {
          await s().switchDocument(emptyProject(), 'd_tutorial')
        },
      },
    )
    expect(s().documentId).toBe('d_tutorial')
    expect(p().lastDocumentIssue).toBeNull()
  })

  it('没有记录的项目照旧开空白文档', async () => {
    await p().open('/figs/b')
    expect(s().doc.objects).toHaveLength(0)
    expect(p().lastDocumentIssue).toBeNull()
  })
})

/**
 * 「最近文档」索引里记下所属项目（审计 T04）。
 *
 * 索引 `tavotto.docIndex` 是**跨项目共用一份**的（localStorage 一个键），
 * 改造前条目里没有任何项目信息，于是别的项目的文档混在菜单里、看不出来。
 *
 * 记的是**写下那一刻**的项目：项目后来改名不追认，那条旧记录说的是当时的事。
 */
import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { setCurrentProjectLabel } from '@/lib/projectLabel'
import { setCurrentProjectId } from '@/lib/session'
import { emptyProject, type ProjectDocument, type TextObject } from '@/types/document'
import { flushAutosave, useDocumentStore, type RecentDoc } from './documentStore'
import { useProjectStore } from './projectStore'

const PROJECT_IDS: Record<string, string> = { '/figs/a': 'p_a', '/figs/b': 'p_b' }
globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  if (u.includes('/api/projects/recent')) return new Response('{"recent":[]}', { status: 200 })
  if (u.includes('/api/projects/open')) {
    const path = (JSON.parse(String(init?.body)) as { path: string }).path
    return new Response(
      JSON.stringify({
        open: true,
        id: PROJECT_IDS[path],
        figures_dir: path,
        name: path === '/figs/a' ? 'kinetics' : 'roughness',
      }),
      { status: 200 },
    )
  }
  if (u.includes('/api/panels')) {
    return new Response('{"figures_dir":"/figs","panels":[]}', { status: 200 })
  }
  return new Response(JSON.stringify({ ok: true, saved_at: 1, revision: 'r1' }), { status: 200 })
}) as typeof fetch

const text = (id: string): TextObject => ({
  id, type: 'text', text: 'hello', sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

const s = () => useDocumentStore.getState()

const index = (): RecentDoc[] => JSON.parse(localStorage.getItem('tavotto.docIndex') || '[]')

/** 在当前项目里造一份有内容的文档并落一次索引（空白文档从不入索引） */
async function save(docId: string, name: string) {
  const pd: ProjectDocument = { ...emptyProject(), project: { id: 'x', name } }
  await s().switchDocument(pd, docId)
  s().commit(literal('加一段字'), (d) => {
    d.objects.push(text('t1'))
  })
  expect(flushAutosave()).toBe('saved')
}

beforeEach(() => {
  localStorage.clear()
  setCurrentProjectId('p_a')
  setCurrentProjectLabel('kinetics')
})

afterEach(() => {
  setCurrentProjectId(null)
  setCurrentProjectLabel(null)
})

describe('最近文档的项目归属', () => {
  it('记下写这一条时开着的项目 id 与它当时的名字', async () => {
    await save('d1', 'Fig A')
    expect(index()[0]).toMatchObject({ id: 'd1', projectId: 'p_a', projectName: 'kinetics' })
  })

  it('换项目再存：两条各自记着自己的项目', async () => {
    await save('d1', 'Fig A')
    setCurrentProjectId('p_b')
    setCurrentProjectLabel('roughness')
    await save('d2', 'Fig B')
    const byId = Object.fromEntries(index().map((e) => [e.id, e]))
    expect(byId.d1.projectId).toBe('p_a')
    expect(byId.d2.projectId).toBe('p_b')
    expect(byId.d1.projectName).toBe('kinetics')
    expect(byId.d2.projectName).toBe('roughness')
  })

  it('没打开项目时两个字段都不写——那是「不知道」，不是空字符串', async () => {
    setCurrentProjectId(null)
    setCurrentProjectLabel(null)
    await save('d1', 'Fig A')
    const e = index()[0]
    expect(e.id).toBe('d1')
    expect('projectId' in e).toBe(false)
    expect('projectName' in e).toBe(false)
  })

  it('项目有 id 但名字还不知道时只记 id，不写一个空名字', async () => {
    setCurrentProjectLabel(null)
    await save('d1', 'Fig A')
    const e = index()[0]
    expect(e.projectId).toBe('p_a')
    expect('projectName' in e).toBe(false)
  })
})

/**
 * 真实的切项目顺序：**先认领新项目，再换空白文档**，而换文档第一句就是把
 * 旧文档冲刷落盘。归属若在落盘那一刻现问，旧项目最后编辑的那份文档会被记进
 * 新项目——菜单里于是看不出它属于谁，而这正是这条改动要修的东西。
 */
describe('切项目那一刻的归属', () => {
  it('切走时旧文档那条仍记着旧项目', async () => {
    useProjectStore.setState({
      phase: 'open',
      project: { open: true, id: 'p_a', figures_dir: '/figs/a', name: 'kinetics' },
      recent: [],
      opened: [],
    })
    await save('d_a', 'Fig A')
    // 切到 B：adoptOpenedProject 先 setCurrentProjectId('p_b')，再换空白文档
    await useProjectStore.getState().open('/figs/b')
    const byId = Object.fromEntries(index().map((e) => [e.id, e]))
    expect(byId.d_a.projectId, '旧文档不该被记成新项目的').toBe('p_a')
    expect(byId.d_a.projectName).toBe('kinetics')
  })
})

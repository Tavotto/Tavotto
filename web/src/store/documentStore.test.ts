import { beforeEach, describe, expect, it } from 'vitest'
import { emptyProject } from '@/types/document'
import type { TextObject } from '@/types/document'
import {
  flushAutosave,
  readAutosaveDoc,
  useDocumentStore,
} from './documentStore'

const text = (id: string, t: string): TextObject => ({
  id, type: 'text', text: t, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

/** 模拟后端 /api/autosave 槽位（PUT/GET/DELETE），其余请求 404 */
const diskSlots = new Map<string, string>()
globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const m = String(url).match(/\/api\/autosave\/([^/?]+)/)
  if (m) {
    const id = decodeURIComponent(m[1])
    if (init?.method === 'PUT') {
      diskSlots.set(id, String(init.body))
      return new Response('{"ok":true}', { status: 200 })
    }
    if (init?.method === 'DELETE') {
      diskSlots.delete(id)
      return new Response('{"ok":true}', { status: 200 })
    }
    const v = diskSlots.get(id)
    return new Response(v ?? '{}', { status: v ? 200 : 404 })
  }
  return new Response('{}', { status: 404 })
}) as typeof fetch

const tick = () => new Promise((r) => setTimeout(r, 10))

const reset = async () => {
  localStorage.clear()
  diskSlots.clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_test')
}

describe('多画布数据层', () => {
  beforeEach(reset)

  it('addCanvas 新建并切换；undo 栈按画布隔离', () => {
    const s = () => useDocumentStore.getState()
    s().commit('加字A', (d) => {
      d.objects.push(text('t1', 'A'))
    })
    const firstId = s().activeCanvasId
    expect(s().past).toHaveLength(1)

    const secondId = s().addCanvas()
    expect(s().activeCanvasId).toBe(secondId)
    expect(s().doc.objects).toHaveLength(0)
    expect(s().past).toHaveLength(0) // 新画布是干净的撤销栈

    s().commit('加字B', (d) => {
      d.objects.push(text('t2', 'B'))
    })
    s().switchCanvas(firstId)
    expect(s().doc.objects.map((o) => o.id)).toEqual(['t1'])
    expect(s().past.map((e) => e.label)).toEqual(['加字A'])
    expect(s().undo()).toBe('加字A')
    expect(s().doc.objects).toHaveLength(0)

    s().switchCanvas(secondId)
    expect(s().doc.objects.map((o) => o.id)).toEqual(['t2'])
    expect(s().past.map((e) => e.label)).toEqual(['加字B'])
  })

  it('buildProject 汇总激活画布的最新内容', () => {
    const s = () => useDocumentStore.getState()
    s().commit('加字', (d) => {
      d.objects.push(text('t1', 'x'))
    })
    const pd = s().buildProject()
    expect(pd.schema).toBe(3)
    expect(pd.canvases[0].objects).toHaveLength(1)
    expect(pd.activeCanvasId).toBe(s().activeCanvasId)
  })

  it('duplicateCanvas 换新全部对象/成组 id', () => {
    const s = () => useDocumentStore.getState()
    s().commit('加组', (d) => {
      d.objects.push({ ...text('t1', 'x'), groupId: 'g1' })
      d.objects.push({ ...text('t2', 'y'), groupId: 'g1' })
      d.layoutGroups = [{ id: 'g1', kind: 'row', order: ['t1', 't2'], gap: 4, align: 'center' }]
    })
    const nid = s().duplicateCanvas(s().activeCanvasId)!
    const copy = s().canvases.find((c) => c.id === nid)!
    expect(copy.objects).toHaveLength(2)
    expect(copy.objects.map((o) => o.id)).not.toContain('t1')
    expect(copy.layoutGroups).toHaveLength(1)
    expect(copy.layoutGroups![0].id).not.toBe('g1')
    expect(copy.layoutGroups![0].order).toEqual(copy.objects.map((o) => o.id))
    expect(copy.objects[0].groupId).toBe(copy.layoutGroups![0].id)
  })

  it('deleteCanvas 守住最后一张；删除激活画布切到邻居', () => {
    const s = () => useDocumentStore.getState()
    expect(s().deleteCanvas(s().activeCanvasId)).toBe(false)
    const secondId = s().addCanvas()
    expect(s().deleteCanvas(secondId)).toBe(true)
    expect(s().canvases).toHaveLength(1)
  })

  it('reorderCanvases 移动显示顺序', () => {
    const s = () => useDocumentStore.getState()
    const first = s().activeCanvasId
    const second = s().addCanvas()
    s().reorderCanvases(1, 0)
    expect(s().canvases.map((c) => c.id)).toEqual([second, first])
  })

  it('标签：打开/关闭/重排；关闭激活标签切到邻居；最后一个不可关', () => {
    const s = () => useDocumentStore.getState()
    const first = s().activeCanvasId
    expect(s().openTabs).toEqual([first])
    expect(s().closeCanvasTab(first)).toBe(false)

    const second = s().addCanvas()
    const third = s().addCanvas()
    expect(s().openTabs).toEqual([first, second, third])

    s().reorderTabs(2, 0)
    expect(s().openTabs).toEqual([third, first, second])

    // 关闭激活标签（third）→ 切到邻居 first；画布仍在
    expect(s().activeCanvasId).toBe(third)
    expect(s().closeCanvasTab(third)).toBe(true)
    expect(s().activeCanvasId).toBe(first)
    expect(s().openTabs).toEqual([first, second])
    expect(s().canvases.map((c) => c.id)).toContain(third)
  })

  it('标签按 documentId 持久化，switchDocument 恢复', async () => {
    const s = () => useDocumentStore.getState()
    const second = s().addCanvas()
    const pd = s().buildProject()
    const docId = s().documentId
    // 换走再换回：openTabs 从本机恢复
    await s().switchDocument(emptyProject(), 'd_other')
    expect(s().openTabs).toHaveLength(1)
    await s().switchDocument(pd, docId)
    expect(s().openTabs).toEqual([pd.canvases[0].id, second])
  })

  it('deleteCanvas 一并关掉对应标签', () => {
    const s = () => useDocumentStore.getState()
    const second = s().addCanvas()
    expect(s().openTabs).toContain(second)
    s().deleteCanvas(second)
    expect(s().openTabs).not.toContain(second)
  })

  it('自动保存：磁盘落 schema 3，成功后本机副本清空', async () => {
    const s = () => useDocumentStore.getState()
    s().commit('加字', (d) => {
      d.objects.push(text('t1', 'x'))
    })
    expect(flushAutosave()).toBe('saved')
    await tick()
    const disk = JSON.parse(diskSlots.get(s().documentId)!)
    expect(disk.schema).toBe(3)
    expect(disk.canvases[0].objects[0].id).toBe('t1')
    // 磁盘写成功 → localStorage 不再保存文档主体
    expect(localStorage.getItem(`magplot.autosave.${s().documentId}`)).toBeNull()
  })

  it('schema 2 旧本机槽位读取时自动迁移并转正到磁盘', async () => {
    localStorage.setItem(
      'magplot.autosave.d_old',
      JSON.stringify({ schema: 2, name: 'old', page: { w: 80, h: 60 },
                       objects: [text('t9', 'legacy')], guides: [] }),
    )
    const pd = (await readAutosaveDoc('d_old'))!
    expect(pd.schema).toBe(3)
    expect(pd.canvases[0].objects[0].id).toBe('t9')
    expect(pd.canvases[0].page).toEqual({ w: 80, h: 60 })
    await tick()
    expect(JSON.parse(diskSlots.get('d_old')!).schema).toBe(3)
  })

  it('磁盘与本机副本并存时取 updatedAt 更新的一份', async () => {
    const older = { ...emptyProject(), updatedAt: 100 }
    const newer = { ...emptyProject(), updatedAt: 200 }
    diskSlots.set('d_x', JSON.stringify(older))
    localStorage.setItem('magplot.autosave.d_x', JSON.stringify(newer))
    const pd = (await readAutosaveDoc('d_x'))!
    expect(pd.updatedAt).toBe(200)
  })
})

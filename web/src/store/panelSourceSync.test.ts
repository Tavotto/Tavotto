/**
 * PanelObject 的外部派生元数据同步（Prompt 06 §五）。
 *
 * 被测的那句话是：**原地**换掉磁盘/registry 说了算的那几个字段，用户的东西
 * 一个字节不动。所以正向用例（script 补上了没有）与负向用例（几何、crop、
 * overrides、成组、撤销栈原样）同等重要——只有正向的话，一个"把整个对象换成
 * 新建的"的实现照样全绿。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { PanelInfo } from '@/lib/api'
import type { CanvasData, CanvasObject, PanelObject } from '@/types/document'
import { canvasToDoc } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { syncPanelSourceMetadata } from './panelSourceSync'

const info = (id: string, over: Partial<PanelInfo> = {}): PanelInfo => ({
  id,
  name: id.replace(/\.[^.]+$/, ''),
  folder: '.',
  kind: 'pdf',
  native_w_mm: 80,
  native_h_mm: 60,
  mtime: 1,
  ...over,
})

const byId = (list: PanelInfo[]): Record<string, PanelInfo> =>
  Object.fromEntries(list.map((p) => [p.id, p]))

const panelObj = (id: string, fileId: string, over: Partial<PanelObject> = {}): PanelObject => ({
  id,
  type: 'panel',
  fileId,
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 10,
  y: 20,
  w: 40,
  h: 30,
  ...over,
})

/** 装一份两画布文档；第一张是激活画布 */
function seed(first: CanvasObject[], second: CanvasObject[] = []): void {
  const canvases: CanvasData[] = [
    { id: 'c1', name: 'Fig 1', page: { w: 150, h: 100 }, objects: first, guides: [] },
    { id: 'c2', name: 'Fig 2', page: { w: 150, h: 100 }, objects: second, guides: [] },
  ]
  useDocumentStore.setState({
    doc: canvasToDoc(canvases[0]),
    canvases,
    activeCanvasId: 'c1',
    openTabs: ['c1'],
    canvasSessions: {},
    past: [],
    future: [],
    txn: null,
    dirty: false,
    saveState: 'clean',
    derivedSeq: 0,
  })
}

const s = () => useDocumentStore.getState()
const panelAt = (i: number) => s().doc.objects[i] as PanelObject

beforeEach(() => {
  useDocumentStore.setState({ documentId: 'doc-sync-test' })
  seed([])
})

describe('升级：script null → 有值', () => {
  it('原地补上 script，双击入口据此打开', () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: null })])
    const r = syncPanelSourceMetadata(byId([info('Fig1.pdf', { script: 'fig1.py' })]))

    expect(panelAt(0).script).toBe('fig1.py')
    expect(r.upgraded).toEqual(['o1'])
    expect(r.changed).toEqual(['o1'])
    expect(r.downgraded).toEqual([])
    expect(r.staleFileIds).toEqual(['Fig1.pdf'])
    expect(r.droppedFileIds).toEqual([])
  })

  it('老文档里 script 字段缺席，与显式 null 同义（不该每轮都判成"变了"）', () => {
    seed([panelObj('o1', 'Fig1.pdf')])
    expect(syncPanelSourceMetadata(byId([info('Fig1.pdf')])).changed).toEqual([])
    expect(s().derivedSeq).toBe(0)
  })
})

describe('降级：script → null', () => {
  it('原地抹掉 script 并报进 downgraded / droppedFileIds', () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: 'fig1.py' })])
    const r = syncPanelSourceMetadata(byId([info('Fig1.pdf')]))

    expect(panelAt(0).script).toBeNull()
    expect(r.downgraded).toEqual(['o1'])
    expect(r.droppedFileIds).toEqual(['Fig1.pdf'])
    expect(r.staleFileIds).toEqual([])
  })

  it('overrides 一条都不删——源关系恢复之后它们还要用', () => {
    const overrides = [{ gid: 'axes_0', prop: 'title', value: 'hi' }]
    seed([panelObj('o1', 'Fig1.pdf', { script: 'fig1.py', overrides })])
    syncPanelSourceMetadata(byId([info('Fig1.pdf')]))
    expect(panelAt(0).overrides).toEqual(overrides)
  })
})

describe('其它派生字段', () => {
  it('cost 更新', () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: 'fig1.py', cost: 'light' })])
    const r = syncPanelSourceMetadata(
      byId([info('Fig1.pdf', { script: 'fig1.py', cost: 'heavy' })]),
    )
    expect(panelAt(0).cost).toBe('heavy')
    expect(r.changed).toEqual(['o1'])
    expect(r.upgraded).toEqual([])
    expect(r.downgraded).toEqual([])
  })

  it('从来没有脚本的面板：改了派生字段也不进重建名单，也不算降级', () => {
    seed([panelObj('o1', 'shot.png', { fileKind: 'raster', script: null, pxW: 100 })])
    const r = syncPanelSourceMetadata(
      byId([info('shot.png', { kind: 'raster', px_w: 1200 })]),
    )
    expect(r.changed).toEqual(['o1'])
    expect(panelAt(0).pxW).toBe(1200)
    // 标成 tracked 等于告诉显示层"走引擎产物"，而它没有脚本可跑
    expect(r.staleFileIds).toEqual([])
    expect(r.droppedFileIds).toEqual([])
    expect(r.downgraded).toEqual([])
  })

  it('位图像素尺寸与载体类型跟着磁盘走', () => {
    seed([panelObj('o1', 'shot.png', { fileKind: 'pdf', pxW: 100 })])
    syncPanelSourceMetadata(byId([info('shot.png', { kind: 'raster', px_w: 1200 })]))
    expect(panelAt(0).fileKind).toBe('raster')
    expect(panelAt(0).pxW).toBe(1200)
  })

  it('nativeW / nativeH 不是派生字段：图幅的权威是渲染回来的 manifest', () => {
    seed([panelObj('o1', 'Fig1.pdf', { nativeW: 80, nativeH: 60 })])
    syncPanelSourceMetadata(
      byId([info('Fig1.pdf', { native_w_mm: 999, native_h_mm: 777, script: 'fig1.py' })]),
    )
    expect(panelAt(0).nativeW).toBe(80)
    expect(panelAt(0).nativeH).toBe(60)
  })
})

describe('同 fileId 的多个实例', () => {
  it('画布上的每一个副本都升级，非激活画布上的也算', () => {
    seed(
      [panelObj('o1', 'Fig1.pdf', { script: null }), panelObj('o2', 'Fig1.pdf', { script: null })],
      [panelObj('o3', 'Fig1.pdf', { script: null })],
    )
    const r = syncPanelSourceMetadata(byId([info('Fig1.pdf', { script: 'fig1.py' })]))

    expect(r.upgraded.sort()).toEqual(['o1', 'o2', 'o3'])
    expect(panelAt(0).script).toBe('fig1.py')
    expect(panelAt(1).script).toBe('fig1.py')
    expect((s().canvases[1].objects[0] as PanelObject).script).toBe('fig1.py')
    // 素材是同一个：重建的对象只有一份
    expect(r.staleFileIds).toEqual(['Fig1.pdf'])
  })

  it('激活画布只算一遍（canvases[active] 是快照，不是第二份对象）', () => {
    const p = panelObj('o1', 'Fig1.pdf', { script: null })
    seed([p])
    // 快照里也躺着同一个对象——按画布遍历两次的实现会把它报两遍
    useDocumentStore.setState({
      canvases: s().canvases.map((c) => (c.id === 'c1' ? { ...c, objects: [p] } : c)),
    })
    expect(syncPanelSourceMetadata(byId([info('Fig1.pdf', { script: 'f.py' })])).upgraded).toEqual([
      'o1',
    ])
  })
})

describe('绝不修改的东西', () => {
  it('几何、裁剪、旋转、成组、锁定、名称原样', () => {
    const crop = { x: 0.1, y: 0.2, w: 0.5, h: 0.5 }
    seed([
      panelObj('o1', 'Fig1.pdf', {
        script: null,
        x: 11,
        y: 22,
        w: 33,
        h: 44,
        crop,
        rotation: 90,
        groupId: 'g1',
        locked: true,
        hidden: true,
        name: '我的图',
        opacity: 0.5,
        flipH: true,
      }),
    ])
    syncPanelSourceMetadata(byId([info('Fig1.pdf', { script: 'fig1.py' })]))
    const o = panelAt(0)
    expect([o.x, o.y, o.w, o.h]).toEqual([11, 22, 33, 44])
    expect(o.crop).toBe(crop)
    expect(o.rotation).toBe(90)
    expect(o.groupId).toBe('g1')
    expect(o.locked).toBe(true)
    expect(o.hidden).toBe(true)
    expect(o.name).toBe('我的图')
    expect(o.opacity).toBe(0.5)
    expect(o.flipH).toBe(true)
  })

  it('非面板对象与 runtime 面板一个字节不动', () => {
    const textObj = {
      id: 't1', type: 'text' as const, text: 'hi', sizePt: 9, bold: false,
      color: '#000', align: 'left' as const, x: 0, y: 0, w: 10, h: 5,
    }
    const runtime = panelObj('r1', 'runtime:show.py#show', {
      fileKind: 'runtime',
      script: 'show.py',
      source: {
        script: 'show.py', entry: '__main__', stem: 'show',
        captureSource: 'pyplot', fingerprint: 'sha256:x', sizeMm: [80, 60],
      },
    })
    seed([textObj, runtime])
    const r = syncPanelSourceMetadata(byId([]))

    expect(s().doc.objects[0]).toBe(textObj)
    expect(s().doc.objects[1]).toBe(runtime)
    // runtime 面板不在 /api/panels 里，但它**不是**「素材不见了」
    expect(r.missing).toEqual([])
    expect(r.changed).toEqual([])
  })
})

describe('缺失素材', () => {
  it('清单里没有的素材：对象原样保留，也不抹掉 script', () => {
    const o = panelObj('o1', 'Gone.pdf', { script: 'gone.py' })
    seed([o])
    const r = syncPanelSourceMetadata(byId([info('Other.pdf')]))

    expect(s().doc.objects[0]).toBe(o)
    expect(panelAt(0).script).toBe('gone.py')
    expect(r.missing).toEqual(['o1'])
    expect(r.changed).toEqual([])
    expect(r.downgraded).toEqual([])
  })

  it('只有缺失、没有差异时一次 set 都不发', () => {
    seed([panelObj('o1', 'Gone.pdf', { script: 'gone.py' })])
    const before = s().doc
    syncPanelSourceMetadata(byId([]))
    expect(s().doc).toBe(before)
    expect(s().derivedSeq).toBe(0)
    expect(s().dirty).toBe(false)
  })
})

describe('历史与保存', () => {
  it('不进撤销历史，也不清空已有的 past / future', () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: null })])
    const past = [{ label: literal('先前的一步'), patches: [], inverse: [] }]
    useDocumentStore.setState({ past, future: [] })

    syncPanelSourceMetadata(byId([info('Fig1.pdf', { script: 'fig1.py' })]))

    expect(s().past).toBe(past)
    expect(s().future).toEqual([])
    expect(s().canUndo()).toBe(true)
  })

  it('置 dirty（要落盘）但**不推 saveState**（用户没有"未保存的改动"）', () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: null })])
    syncPanelSourceMetadata(byId([info('Fig1.pdf', { script: 'fig1.py' })]))
    // dirty 是 documentStore 的订阅置的；这里只钉代次，落盘那条在
    // documentStore.derived.test.ts 里用真订阅钉
    expect(s().derivedSeq).toBe(1)
    expect(s().saveState).toBe('clean')
  })

  it('无差异 = 零改动：doc 引用不变、代次不动', () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: 'fig1.py' })])
    const before = s().doc
    const r = syncPanelSourceMetadata(byId([info('Fig1.pdf', { script: 'fig1.py' })]))
    expect(s().doc).toBe(before)
    expect(s().derivedSeq).toBe(0)
    expect(r.changed).toEqual([])
  })
})

describe('affectedIds 过滤', () => {
  it('只看给定的素材，其它面板一个字节不动', () => {
    seed([
      panelObj('o1', 'A.pdf', { script: null }),
      panelObj('o2', 'B.pdf', { script: null }),
    ])
    const r = syncPanelSourceMetadata(
      byId([info('A.pdf', { script: 'a.py' }), info('B.pdf', { script: 'b.py' })]),
      { affectedIds: ['A.pdf'] },
    )
    expect(r.upgraded).toEqual(['o1'])
    expect(panelAt(1).script).toBeNull()
  })

  it('空表 = 这条事件什么都没牵涉到，直接返回', () => {
    seed([panelObj('o1', 'A.pdf', { script: null })])
    const spy = vi.spyOn(useDocumentStore, 'setState')
    const r = syncPanelSourceMetadata(byId([info('A.pdf', { script: 'a.py' })]), {
      affectedIds: [],
    })
    expect(r.changed).toEqual([])
    expect(spy).not.toHaveBeenCalled()
    spy.mockRestore()
  })
})

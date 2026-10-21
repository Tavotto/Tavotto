import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { emptyProject, type PanelObject } from '@/types/document'
import type { PanelInfo } from '@/lib/api'
import { useAssetStore } from './assetStore'
import { startAutosave, useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'
import { useViewportStore } from './viewportStore'
import { beginCrop, duplicateSelected, enterElementEdit } from './actions'
import {
  addFigureToLayout,
  findFigurePanel,
  openFastEdit,
  startWorkspaceModeSync,
  useWorkspaceStore,
} from './workspace'
import { activateCanvas, createCanvasAndActivate } from './canvasSession'
import { fitStageNow } from './zoomToSelection'
import { subscribePruneSelection } from '@/hooks/usePruneSelection'
import { syncLoadedDocument } from './liveSync'

/**
 * 两条工作流共享同一个对象模型（Prompt 09），**模式由激活画布上的图数推出**：恰好 1 张 = 单图编辑，
 * 其余 = 多图排版。这批用例守的是：
 *
 * * 「**没有第二套东西**」：单图变多图再变回来，overrides 还在**同一个对象 id** 上、x/y/w/h 一个字节没动；
 * * 切模式不进撤销栈、不置 dirty；重复「添加到画布」不叠对象；
 * * 模式是图数的函数：从素材库打开、拖入、新建项目、会话恢复、撤销 / 删除 / 隐藏第二张，结果都一样。
 *
 * 每条用例都挂着真的模式订阅（`startWorkspaceModeSync`），和应用里一样。
 */

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const info = (id: string, partial: Partial<PanelInfo> = {}): PanelInfo => ({
  id,
  name: id.replace(/\.[^.]+$/, ''),
  folder: '.',
  kind: 'pdf',
  native_w_mm: 80,
  native_h_mm: 60,
  mtime: 1,
  script: 'fig.py',
  ...partial,
})

const s = () => useDocumentStore.getState()
const ws = () => useWorkspaceStore.getState()

let stopSync: () => void = () => {}
afterEach(() => stopSync())

const reset = async () => {
  stopSync()
  localStorage.clear()
  useSelectionStore.getState().clear()
  useUiStore.getState().setElementPanel(null) // 顺带清 selectedGids 与 cropTargetId
  useUiStore.setState({ editingTextId: null }) // setElementPanel 不清它，会漏到下一条用例
  ws().clear()
  useAssetStore.setState({
    panels: [info('a.pdf'), info('b.pdf')],
    byId: { 'a.pdf': info('a.pdf'), 'b.pdf': info('b.pdf') },
  })
  await s().switchDocument(emptyProject(), 'd_test')
  stopSync = startWorkspaceModeSync()
}

const panelOf = (fileId: string): PanelObject => {
  const hit = findFigurePanel(fileId)
  if (!hit) throw new Error(`文档里没有 ${fileId}`)
  return hit.panel
}

describe('打开一张图 → 单图编辑', () => {
  beforeEach(reset)

  it('打开单图直接进入 fast edit，不需要用户先配置画布', () => {
    expect(openFastEdit('a.pdf')).toBe('editing')
    expect(ws().mode).toBe('fast_edit')
    expect(ws().activePanelId).toBe(panelOf('a.pdf').id)
    // 图内编辑当场就位：用户不需要再"双击进去"一次
    expect(useUiStore.getState().elementPanelId).toBe(panelOf('a.pdf').id)
  })

  it('没有源脚本的图诚实降级：进得去工作区，但不假装能改图内元素', () => {
    useAssetStore.setState({
      byId: { 'c.png': info('c.png', { kind: 'raster', script: undefined }) },
    })
    expect(openFastEdit('c.png')).toBe('layout_only')
    expect(ws().mode).toBe('fast_edit')
    expect(useUiStore.getState().elementPanelId).toBeNull()
  })

  it('项目里没有这张图：不发明一个空面板', () => {
    expect(openFastEdit('nope.pdf')).toBe('missing')
    expect(ws().mode).toBe('layout')
    expect(s().doc.objects).toHaveLength(0)
  })

  it('再打开同一张图不会叠出第二个面板，仍是单图编辑', () => {
    openFastEdit('a.pdf')
    const id = ws().activePanelId
    openFastEdit('a.pdf')
    expect(ws().activePanelId).toBe(id)
    expect(ws().mode).toBe('fast_edit')
    expect(s().doc.objects.filter((o) => o.type === 'panel')).toHaveLength(1)
  })

  it('第二次打开另一张单图项目仍是单图编辑（此前 returnToLayout 之后再打开的图落在排版上）', async () => {
    openFastEdit('a.pdf')
    expect(ws().mode).toBe('fast_edit')
    // 换成另一个只有一张图的项目
    await s().switchDocument(emptyProject(), 'd_other')
    expect(ws().mode).toBe('layout') // 空画布
    expect(openFastEdit('b.pdf')).toBe('editing')
    expect(ws().mode).toBe('fast_edit')
    expect(ws().activePanelId).toBe(panelOf('b.pdf').id)
  })

  it('「打开」= 换看：画布上已有一张图时再打开另一张（不在任何画布上）→ 新建一张以图名命名的画布，原画布一个字节不动', () => {
    openFastEdit('a.pdf')
    const first = s().activeCanvasId
    const firstObjects = JSON.stringify(s().doc.objects)
    const canvasesBefore = s().canvases.length
    expect(openFastEdit('b.pdf')).toBe('editing')
    expect(s().canvases).toHaveLength(canvasesBefore + 1)
    expect(s().activeCanvasId).not.toBe(first)
    expect(s().canvases.find((c) => c.id === s().activeCanvasId)!.name).toBe('b') // 图名，不叫「新排版」
    expect(JSON.stringify(s().canvases.find((c) => c.id === first)!.objects)).toBe(firstObjects)
    expect(ws().mode).toBe('fast_edit')
    expect(ws().activePanelId).toBe(panelOf('b.pdf').id)
    expect(useUiStore.getState().elementPanelId).toBe(panelOf('b.pdf').id)
  })

  it('「打开」一张已单独在某张画布上的图 → 切到那张画布，仍是单图编辑，不新建画布', () => {
    openFastEdit('a.pdf')
    const first = s().activeCanvasId
    openFastEdit('b.pdf') // 在第二张画布上
    const n = s().canvases.length
    expect(openFastEdit('a.pdf')).toBe('editing')
    expect(s().activeCanvasId).toBe(first)
    expect(s().canvases).toHaveLength(n)
    expect(ws().mode).toBe('fast_edit')
    expect(ws().activePanelId).toBe(panelOf('a.pdf').id)
  })

  it('「打开」一张在多图画布上的图 → 切到那张画布，选中它并对它进图内编辑（排版子状态）', () => {
    addFigureToLayout('a.pdf')
    addFigureToLayout('b.pdf')
    const multi = s().activeCanvasId
    useAssetStore.setState({ byId: { ...useAssetStore.getState().byId, 'c.pdf': info('c.pdf') } })
    openFastEdit('c.pdf') // 离开：新画布
    expect(s().activeCanvasId).not.toBe(multi)
    expect(openFastEdit('a.pdf')).toBe('editing')
    expect(s().activeCanvasId).toBe(multi)
    expect(ws().mode).toBe('layout')
    expect(useUiStore.getState().elementPanelId).toBe(panelOf('a.pdf').id)
    expect(useSelectionStore.getState().ids).toEqual([panelOf('a.pdf').id])
  })

  it('新画布这一步的撤销：撤销把放进去的图撤掉，画布留着（空，排版）——新建画布本身全仓库都不进撤销栈', () => {
    openFastEdit('a.pdf')
    const created = openFastEdit('b.pdf')
    expect(created).toBe('editing')
    const second = s().activeCanvasId
    s().undo()
    expect(s().activeCanvasId).toBe(second)
    expect(s().doc.objects.filter((o) => o.type === 'panel')).toHaveLength(0)
    expect(ws().mode).toBe('layout')
    expect(s().canvases.some((c) => c.id === second)).toBe(true)
  })

  it('进快速编辑会把绘制工具收回 select', () => {
    useUiStore.getState().setTool('arrow')
    openFastEdit('a.pdf')
    expect(useUiStore.getState().tool).toBe('select')
  })
})

/**
 * issue #267：**双击落在「素材→脚本」关联到达之前**。
 *
 * 关联是异步来的（后端扫描 / 试运行 → `assets.changed` → `panelSourceSync`
 * 原地补 `script`）。`openFastEdit` 的"能不能进图内编辑"此前是一次**一次性
 * 判断**——判完没有人再问第二遍，于是双击早一步的用户永远进不去图内编辑，
 * 而界面上一切正常（属性页甚至会在关联到达后长出「编辑图内元素」按钮）。
 *
 * 这不是"慢"，是**判断丢了且没有恢复路径**：所以它在快机器上永远看不到、
 * 在慢机器上必然发生，表现为"偶发"。
 *
 * 三条一起钉：补进去（正向）、用户已经走开就不补（两个反向）。只钉正向的话，
 * 一个"关联一到就无条件抢进图内编辑"的实现照样全绿，而那会把界面从已经回到
 * 排版、或已经打开另一张图的用户手里抢走。
 */
describe('源脚本关联迟到（issue #267）', () => {
  beforeEach(reset)

  /** 素材清单里补上关联，然后走文档同步那条路（不发请求） */
  const scriptArrives = (fileId: string) => {
    useAssetStore.setState({
      byId: { ...useAssetStore.getState().byId, [fileId]: info(fileId, { script: 'late.py' }) },
    })
    syncLoadedDocument()
  }

  it('关联到达时把那次进入补上——用户不必自己再点一次「编辑图内元素」', () => {
    useAssetStore.setState({ byId: { 'late.pdf': info('late.pdf', { script: undefined }) } })
    expect(openFastEdit('late.pdf')).toBe('layout_only')
    expect(useUiStore.getState().elementPanelId).toBeNull()
    expect(ws().pendingElementEdit).toBe(panelOf('late.pdf').id)

    scriptArrives('late.pdf')

    expect(useUiStore.getState().elementPanelId).toBe(panelOf('late.pdf').id)
    // 待办只补一次：留着的话下一次关联事件会再抢一回
    expect(ws().pendingElementEdit).toBeNull()
  })

  it('第二张图进来、单图编辑变多图排版，就把待办作废（不留着等下一条事件来抢）', () => {
    useAssetStore.setState({ byId: { 'late.pdf': info('late.pdf', { script: undefined }), 'a.pdf': info('a.pdf') } })
    openFastEdit('late.pdf')
    expect(ws().pendingElementEdit).not.toBeNull()

    addFigureToLayout('a.pdf')

    expect(ws().mode).toBe('layout')
    expect(ws().pendingElementEdit).toBeNull()
  })

  it('排版里打开一张还没关联的图：待办记在它身上，关联到达时补进图内编辑（排版的子状态）', () => {
    useAssetStore.setState({
      byId: { 'late.pdf': info('late.pdf', { script: undefined }), 'a.pdf': info('a.pdf') },
    })
    addFigureToLayout('a.pdf')
    addFigureToLayout('late.pdf')
    expect(openFastEdit('late.pdf')).toBe('layout_only')
    expect(ws().mode).toBe('layout')
    expect(ws().pendingElementEdit).toBe(panelOf('late.pdf').id)

    scriptArrives('late.pdf')

    expect(useUiStore.getState().elementPanelId).toBe(panelOf('late.pdf').id)
  })

  /*
   * 下面两条走的是**绕过 `openFastEdit` 的入口**（直接 `enterElementEdit` /
   * 直接改选区）。第二张图要在**记下待办之前**就已经在文档里：`openFastEdit` /
   * 模式切换（`exitToLayout`）都会顺手清掉待办，用它们摆场景的话待办早就
   * 没了，守卫拆掉也不会红——本轮实测过两次，那样写的反向用例在"无条件
   * 抢进"的变异下双双存活。
   */

  /** 让 `other` 先进文档，再把待办记在 `late.pdf` 上 */
  const setUpOtherThenLatch = (): string => {
    useAssetStore.setState({
      byId: { 'late.pdf': info('late.pdf', { script: undefined }), 'a.pdf': info('a.pdf') },
    })
    addFigureToLayout('a.pdf')
    const other = panelOf('a.pdf').id
    addFigureToLayout('late.pdf') // 两张图 = 排版
    expect(openFastEdit('late.pdf')).toBe('layout_only')
    expect(ws().pendingElementEdit).toBe(panelOf('late.pdf').id)
    return other
  }

  it('用户已经在编辑别的图：迟到的关联不把图内编辑换成另一张', () => {
    const other = setUpOtherThenLatch()
    enterElementEdit(other) // 画布双击 / 「编辑图内元素」按钮：不经过 openFastEdit

    scriptArrives('late.pdf')

    expect(useUiStore.getState().elementPanelId).toBe(other)
  })

  it('用户正在裁剪这张图：迟到的关联不打断他（会清掉 cropTargetId）', () => {
    useAssetStore.setState({ byId: { 'late.pdf': info('late.pdf', { script: undefined }) } })
    openFastEdit('late.pdf')
    const id = panelOf('late.pdf').id
    beginCrop(id) // 属性页的「裁剪」/ 浮动条 / 双击 / Enter 共用的唯一入口

    scriptArrives('late.pdf')

    // 补进图内编辑会调 setElementPanel()，而它清 cropTargetId + selectedGids
    expect(useUiStore.getState().cropTargetId).toBe(id)
    expect(useUiStore.getState().elementPanelId).toBeNull()
  })

  it('用户正在改画布上的文字：迟到的关联同样不插进来', () => {
    useAssetStore.setState({ byId: { 'late.pdf': info('late.pdf', { script: undefined }) } })
    openFastEdit('late.pdf')
    useUiStore.setState({ editingTextId: 't1' })

    scriptArrives('late.pdf')

    expect(useUiStore.getState().elementPanelId).toBeNull()
  })

  it('用户已经选中了排版里别的图：迟到的关联不越过他把用户拉回去', () => {
    const other = setUpOtherThenLatch()
    useSelectionStore.getState().set([other]) // 点了另一张：不经过 openFastEdit

    scriptArrives('late.pdf')

    expect(useUiStore.getState().elementPanelId).toBeNull()
    expect(useSelectionStore.getState().ids).toEqual([other])
  })
})

describe('单图编辑 ↔ 多图排版共享同一个对象', () => {
  beforeEach(reset)

  it('单图编辑里的修改在加入画布（再加第二张）之后还在，而且还在同一个对象上', () => {
    openFastEdit('a.pdf')
    const id = panelOf('a.pdf').id
    s().commit(literal('改一个图内属性'), (d) => {
      const o = d.objects.find((x) => x.id === id) as PanelObject
      o.overrides = [{ gid: 'g1', prop: 'label', value: '甲' }]
    })

    expect(addFigureToLayout('a.pdf')).toBe('focused')
    expect(addFigureToLayout('b.pdf')).toBe('added') // 第二张进来：多图排版
    expect(ws().mode).toBe('layout')
    const after = panelOf('a.pdf')
    expect(after.id).toBe(id) // 稳定对象 id：不是复制出来的新对象
    expect(after.overrides).toEqual([{ gid: 'g1', prop: 'label', value: '甲' }])
    expect(s().doc.objects.filter((o) => o.type === 'panel')).toHaveLength(2)
  })

  it('「添加到画布」在文档里还没有这张图时才真的添加', () => {
    expect(addFigureToLayout('b.pdf')).toBe('added')
    expect(addFigureToLayout('b.pdf')).toBe('focused')
    expect(s().doc.objects.filter((o) => o.type === 'panel')).toHaveLength(1)
  })

  it('「添加到画布」真的添加（第二张）时取景整张页面并留在适应模式；只是聚焦时退出', () => {
    useViewportStore.getState().setViewRect({ left: 0, top: 0, width: 800, height: 600 })
    addFigureToLayout('a.pdf')
    useViewportStore.getState().setView({ zoom: 3, panX: -500, panY: -400 })
    addFigureToLayout('b.pdf')
    // 适应模式：之后素材抽屉收起 / 窗口缩放时按页面重算，页面保持居中
    expect(useViewportStore.getState().fitted).toBe(true)
    addFigureToLayout('b.pdf')
    expect(useViewportStore.getState().fitted).toBe(false)
  })

  it('从多图排版进图内编辑再退出：位置、尺寸、edits 全都不动，仍是多图排版', () => {
    addFigureToLayout('a.pdf')
    addFigureToLayout('b.pdf')
    const id = panelOf('a.pdf').id
    s().commit(literal('摆好位置'), (d) => {
      const o = d.objects.find((x) => x.id === id) as PanelObject
      o.x = 12
      o.y = 34
      o.w = 40 // 用户在画布上把它缩小了一半
      o.h = 30
      o.overrides = [{ gid: 'g1', prop: 'label', value: '甲' }]
    })
    const before = { ...panelOf('a.pdf') }

    openFastEdit('a.pdf')
    useUiStore.getState().setElementPanel(null) // 退出图内编辑

    const after = panelOf('a.pdf')
    expect([after.x, after.y, after.w, after.h]).toEqual([
      before.x,
      before.y,
      before.w,
      before.h,
    ])
    expect(after.overrides).toEqual(before.overrides)
    expect(ws().mode).toBe('layout')
  })
})

describe('模式由图数推出（单图 ⇄ 多图往返）', () => {
  beforeEach(reset)
  const figs = () => s().doc.objects.filter((o) => o.type === 'panel')

  it('0 张 = 空画布（排版）；1 张 = 单图编辑；2 张 = 多图排版', () => {
    expect(ws().mode).toBe('layout')
    addFigureToLayout('a.pdf')
    expect(ws().mode).toBe('fast_edit')
    expect(ws().activePanelId).toBe(panelOf('a.pdf').id)
    addFigureToLayout('b.pdf')
    expect(ws().mode).toBe('layout')
    expect(ws().activePanelId).toBeNull()
  })

  it('撤销把第二张撤掉 → 回单图编辑；重做 → 又是多图排版', () => {
    addFigureToLayout('a.pdf')
    addFigureToLayout('b.pdf')
    expect(ws().mode).toBe('layout')
    s().undo()
    expect(ws().mode).toBe('fast_edit')
    expect(ws().activePanelId).toBe(panelOf('a.pdf').id)
    s().redo()
    expect(ws().mode).toBe('layout')
  })

  it('删到只剩一张 → 回单图编辑，对着剩下的那一张；排版设置（页面尺寸）留在文档里', () => {
    s().commit(literal('页面'), (d) => {
      d.page = { w: 183, h: 120 }
    })
    addFigureToLayout('a.pdf')
    addFigureToLayout('b.pdf')
    const bId = panelOf('b.pdf').id
    s().commit(literal('删除'), (d) => {
      d.objects = d.objects.filter((o) => o.id !== panelOf('a.pdf').id)
    })
    expect(ws().mode).toBe('fast_edit')
    expect(ws().activePanelId).toBe(bId)
    expect(s().doc.page).toMatchObject({ w: 183, h: 120 })
    // 再加一张：原样回到多图排版，页面还是那个页面
    addFigureToLayout('a.pdf')
    expect(ws().mode).toBe('layout')
    expect(s().doc.page).toMatchObject({ w: 183, h: 120 })
  })

  it('隐藏的图也计数：隐藏 / 取消隐藏不改模式、不发通知；删光 → 空画布（排版）', () => {
    addFigureToLayout('a.pdf')
    addFigureToLayout('b.pdf')
    useUiStore.setState({ status: null })
    s().commit(literal('隐藏'), (d) => {
      d.objects.find((o) => o.id === panelOf('a.pdf').id)!.hidden = true
    })
    expect(ws().mode).toBe('layout')
    s().commit(literal('显示'), (d) => {
      d.objects.find((o) => o.id === panelOf('a.pdf').id)!.hidden = false
    })
    expect(ws().mode).toBe('layout')
    expect(useUiStore.getState().status).toBeNull()
    // 单图 + 把它隐藏：仍是单图编辑
    s().commit(literal('删一张'), (d) => {
      d.objects = d.objects.filter((o) => o.id !== panelOf('a.pdf').id)
    })
    expect(ws().mode).toBe('fast_edit')
    s().commit(literal('隐藏唯一一张'), (d) => {
      d.objects.find((o) => o.type === 'panel')!.hidden = true
    })
    expect(ws().mode).toBe('fast_edit')
    s().commit(literal('删光'), (d) => {
      d.objects = []
    })
    expect(ws().mode).toBe('layout')
    expect(ws().activePanelId).toBeNull()
  })

  it('标注 / 文字 / 形状不是图，不计数：一张图 + 一段文字仍是单图编辑', () => {
    addFigureToLayout('a.pdf')
    s().commit(literal('文字'), (d) => {
      d.objects.push({
        id: 't1',
        type: 'text',
        text: '图注',
        sizePt: 6,
        bold: false,
        color: '#000000',
        align: 'left',
        x: 5,
        y: 5,
        w: 20,
        h: 6,
      })
    })
    expect(ws().mode).toBe('fast_edit')
  })

  it('runtime 图与磁盘图同等计数', () => {
    useAssetStore.setState({ byId: { 'a.pdf': info('a.pdf') } })
    addFigureToLayout('a.pdf')
    s().commit(literal('runtime 图'), (d) => {
      d.objects.push({
        id: 'rt1',
        type: 'panel',
        fileId: 'rt_x',
        fileKind: 'pdf',
        nativeW: 80,
        nativeH: 60,
        overrides: [],
        x: 100,
        y: 0,
        w: 40,
        h: 30,
        runtime: true,
      } as unknown as PanelObject)
    })
    expect(ws().mode).toBe('layout')
  })

  it('单图编辑里创建副本：副本是图 = 第二张 → 多图排版；副本是标注 → 留在单图编辑', () => {
    addFigureToLayout('a.pdf')
    s().commit(literal('文字'), (d) => {
      d.objects.push({
        id: 't1', type: 'text', text: '图注', sizePt: 6, bold: false, color: '#000000', align: 'left', x: 5, y: 5, w: 20, h: 6,
      })
    })
    useSelectionStore.getState().set(['t1'])
    duplicateSelected()
    expect(s().doc.objects.filter((o) => o.type === 'text')).toHaveLength(2)
    expect(ws().mode).toBe('fast_edit')
    useSelectionStore.getState().set([panelOf('a.pdf').id])
    duplicateSelected()
    expect(s().doc.objects.filter((o) => o.type === 'panel')).toHaveLength(2)
    expect(ws().mode).toBe('layout')
  })

  it('单图 → 多图 → 单图：面板几何、对象 id、overrides、历史长度严格不变，不新增面板', () => {
    addFigureToLayout('a.pdf')
    const snap = () => {
      const p = panelOf('a.pdf')
      return JSON.stringify({ id: p.id, x: p.x, y: p.y, w: p.w, h: p.h, o: p.overrides })
    }
    const before = snap()
    const past = s().past.length
    addFigureToLayout('b.pdf')
    expect(ws().mode).toBe('layout')
    s().undo()
    expect(ws().mode).toBe('fast_edit')
    expect(snap()).toBe(before)
    expect(s().past).toHaveLength(past)
    expect(figs()).toHaveLength(1)
  })

  it('多画布各自判：一张画布单图、另一张多图，切标签即改判', () => {
    addFigureToLayout('a.pdf') // 画布 1：一张
    const first = s().activeCanvasId
    const second = createCanvasAndActivate()
    expect(ws().mode).toBe('layout') // 画布 2 是空的
    useAssetStore.setState({ byId: { ...useAssetStore.getState().byId, 'c.pdf': info('c.pdf'), 'd.pdf': info('d.pdf') } })
    addFigureToLayout('c.pdf')
    addFigureToLayout('d.pdf') // 画布 2：两张
    expect(ws().mode).toBe('layout')
    activateCanvas(first)
    expect(ws().mode).toBe('fast_edit')
    expect(ws().activePanelId).toBe(panelOf('a.pdf').id)
    activateCanvas(second)
    expect(ws().mode).toBe('layout')
  })

  it('单图变多图给一次通知「已进入多图排版」；删光不给；1 → 1 的换图不给', () => {
    const notice = () => useUiStore.getState().status
    addFigureToLayout('a.pdf')
    useUiStore.setState({ status: null })
    addFigureToLayout('b.pdf')
    expect(notice()?.key).toBe('fastEdit.enteredLayout')
    useUiStore.setState({ status: null })
    s().commit(literal('删光'), (d) => {
      d.objects = []
    })
    expect(notice()).toBeNull()
  })

  it('图不在文档里、这次打开才加进来：addedForEdit 非空；换到已在文档里的那张就没有', () => {
    openFastEdit('b.pdf')
    expect(ws().addedForEdit).toBe(panelOf('b.pdf').id)
    openFastEdit('b.pdf')
    expect(ws().addedForEdit).toBeNull() // 已在文档里：不再挂
  })

  it('会话恢复（载入一份只有一张图的文档）= 单图编辑，不需要存什么；两张图 = 排版', async () => {
    addFigureToLayout('a.pdf')
    const snapshot = structuredClone(s().doc)
    await s().switchDocument(emptyProject(), 'd_blank')
    expect(ws().mode).toBe('layout')
    await s().switchDocument(structuredClone(snapshot), 'd_restored')
    expect(ws().mode).toBe('fast_edit')
    expect(ws().activePanelId).toBe(snapshot.objects[0].id)
    const two = structuredClone(snapshot)
    two.objects.push({ ...structuredClone(snapshot.objects[0]), id: 'p_second', x: 100 })
    await s().switchDocument(two, 'd_restored2')
    expect(ws().mode).toBe('layout')
  })

  it('模式不落本机：没有 tavotto.workspace.* 的键', () => {
    addFigureToLayout('a.pdf')
    expect(Object.keys(localStorage).filter((k) => k.startsWith('tavotto.workspace.'))).toEqual([])
  })
})

describe('模式是工作区状态，不是文档', () => {
  beforeEach(reset)

  it('切模式不进撤销栈、不置 dirty', () => {
    // dirty 是**自动保存的订阅**置的（documentStore 的三档表），不是 commit
    // 自己置的——不起这个订阅的话下面那两条 dirty 判据恒真，什么都没量到
    const stopAutosave = startAutosave()
    addFigureToLayout('a.pdf')
    const past = s().past.length
    expect(s().dirty).toBe(true) // 上面那次"添加"确实是一次文档修改
    useDocumentStore.setState({ dirty: false })

    openFastEdit('a.pdf')
    openFastEdit('a.pdf')

    expect(s().past.length).toBe(past)
    expect(s().dirty).toBe(false)
    stopAutosave()
  })

  it('真的改了图内属性才进历史、才置 dirty', () => {
    const stopAutosave = startAutosave()
    openFastEdit('a.pdf')
    useDocumentStore.setState({ dirty: false })
    const past = s().past.length
    const id = panelOf('a.pdf').id
    s().commit(literal('改一个图内属性'), (d) => {
      const o = d.objects.find((x) => x.id === id) as PanelObject
      o.overrides = [{ gid: 'g1', prop: 'label', value: '甲' }]
    })
    expect(s().past.length).toBe(past + 1)
    expect(s().dirty).toBe(true)
    stopAutosave()
  })
})

describe('对象消失就改判模式', () => {
  beforeEach(reset)

  it('删掉正在单图编辑的面板 → 空画布（排版），图内编辑态一并收起', () => {
    const stop = subscribePruneSelection()
    openFastEdit('a.pdf')
    const id = ws().activePanelId!
    s().commit(literal('删除'), (d) => {
      d.objects = d.objects.filter((o) => o.id !== id)
    })
    expect(ws().mode).toBe('layout')
    expect(useUiStore.getState().elementPanelId).toBeNull()
    stop()
  })

  it('把它隐藏起来不改模式（隐藏的图也计数，2026-10-21）：仍是单图编辑，图内编辑态收起', () => {
    openFastEdit('a.pdf')
    const id = ws().activePanelId!
    s().commit(literal('隐藏'), (d) => {
      const o = d.objects.find((x) => x.id === id)!
      o.hidden = true
    })
    expect(ws().mode).toBe('fast_edit')
  })
})

describe('跨画布', () => {
  beforeEach(reset)

  it('图在另一张画布上时，打开它会先切过去，不再复制一份', () => {
    addFigureToLayout('a.pdf')
    const id = panelOf('a.pdf').id
    const first = s().activeCanvasId
    const second = s().addCanvas('版二')
    expect(s().activeCanvasId).toBe(second)

    expect(openFastEdit('a.pdf')).toBe('editing')
    expect(s().activeCanvasId).toBe(first)
    expect(ws().activePanelId).toBe(id)
    const panels = s().canvases.flatMap((c) => c.objects).filter((o) => o.type === 'panel')
    expect(panels).toHaveLength(1)
  })
})

/**
 * UI 审计 T06：素材库的「编辑原图」与「添加到画布」是**两个动作、两种后果**。
 *
 * 结构上「编辑一张还不在文档里的图」必然把它加进文档（快速编辑的对象只能是
 * 文档里的面板对象），所以这里钉的不是"不加"，而是：
 *   * 图已在文档里 → 编辑零文档改动（对象数、历史、dirty 全不动，也不弹提示）；
 *   * 图不在文档里 → **恰好** +1 个对象、**一条**历史、提示说出口、一次撤销即
 *     移除并退出快速编辑；
 *   * 添加到画布 → 恰好 +1、一次撤销即移除。
 */
describe('素材库的两个动作：编辑原图 / 添加到画布（T06）', () => {
  beforeEach(reset)
  const panelsInDoc = () => s().doc.objects.filter((o) => o.type === 'panel')

  it('编辑已经在文档里的图：不新增对象、不进历史、不置 dirty、不挂「刚加入」说明', () => {
    const stopAutosave = startAutosave()
    addFigureToLayout('a.pdf')
    useDocumentStore.setState({ dirty: false })
    const past = s().past.length

    expect(openFastEdit('a.pdf')).toBe('editing')

    expect(panelsInDoc()).toHaveLength(1)
    expect(s().past.length).toBe(past)
    expect(s().dirty).toBe(false)
    expect(ws().addedForEdit).toBeNull()
    stopAutosave()
  })

  it('编辑还不在文档里的图：恰好 +1、一条历史、提示说出口、一次撤销即移除并退出单图编辑', () => {
    const stopPrune = subscribePruneSelection()
    const stopAutosave = startAutosave()
    expect(panelsInDoc()).toHaveLength(0)
    const past = s().past.length

    expect(openFastEdit('a.pdf')).toBe('editing')

    expect(panelsInDoc()).toHaveLength(1)
    expect(s().past.length).toBe(past + 1)
    expect(s().dirty).toBe(true)
    // 那一步必须说出口：用户点的是"编辑"，文档却多了一个对象——浮动条据此常驻说明
    expect(ws().addedForEdit).toBe(panelOf('a.pdf').id)

    s().undo()
    expect(panelsInDoc()).toHaveLength(0)
    expect(ws().mode).toBe('layout')
    expect(ws().addedForEdit).toBeNull()
    expect(useUiStore.getState().elementPanelId).toBeNull()
    stopAutosave()
    stopPrune()
  })

  it('说明只属于这一次进入：第二张图进来切成多图排版即清；再进同一张（已在文档里）不再挂', () => {
    openFastEdit('a.pdf')
    expect(ws().addedForEdit).toBe(panelOf('a.pdf').id)
    addFigureToLayout('b.pdf')
    expect(ws().mode).toBe('layout')
    expect(ws().addedForEdit).toBeNull()
    openFastEdit('a.pdf')
    expect(ws().addedForEdit).toBeNull()
  })

  it('再打开另一张没在画布上的图：新画布上的单图，说明跟着换成这一张', () => {
    openFastEdit('a.pdf')
    openFastEdit('b.pdf')
    expect(ws().mode).toBe('fast_edit')
    expect(ws().addedForEdit).toBe(panelOf('b.pdf').id)
  })

  it('没有源脚本的图同样：加进来就要说，一次撤销即移除', () => {
    useAssetStore.setState({
      byId: { 'c.png': info('c.png', { kind: 'raster', script: undefined }) },
    })
    expect(openFastEdit('c.png')).toBe('layout_only')
    expect(panelsInDoc()).toHaveLength(1)
    expect(ws().addedForEdit).toBe(panelOf('c.png').id)
    s().undo()
    expect(panelsInDoc()).toHaveLength(0)
  })

  it('添加到画布：恰好 +1、一次撤销即移除', () => {
    const past = s().past.length
    expect(addFigureToLayout('b.pdf')).toBe('added')
    expect(panelsInDoc()).toHaveLength(1)
    expect(s().past.length).toBe(past + 1)
    s().undo()
    expect(panelsInDoc()).toHaveLength(0)
  })
})

/**
 * 切换模式不许把画布挪走得莫名其妙（审计 T01）：单图编辑把那张图单独摆出来、按它自己的图幅框住；
 * 第二张图进来时取景「页面 ∪ 所有图」并留在适应模式；删到只剩一张时取景剩下那张。
 *
 * 动画在 reduced-motion 下同步落终态（`lib/motion.tween`），所以这里直接量落点，不等 rAF。
 */
describe('切模式的取景', () => {
  const vp = () => useViewportStore.getState()
  const view = () => ({ zoom: vp().zoom, panX: vp().panX, panY: vp().panY })

  beforeEach(async () => {
    await reset()
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: q.includes('reduce'),
      media: q,
      onchange: null,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    }))
    // 视口尺寸为 0 时所有视口动作都是 no-op，量不到任何东西
    vp().setViewRect({ left: 0, top: 0, width: 900, height: 600 })
    vp().setPan(0, 0)
    vp().setZoomCentered(1)
  })

  it('进单图编辑框住那张图；第二张进来取景「页面 ∪ 所有图」并留在适应模式', () => {
    openFastEdit('a.pdf')
    const only = panelOf('a.pdf')
    // 单图编辑：取景的是那张图本身，不是页面
    expect(vp().zoom).toBeGreaterThan(0)
    addFigureToLayout('b.pdf')
    expect(ws().mode).toBe('layout')
    expect(vp().fitted).toBe(true)
    const page = s().doc.page
    const figs = s().doc.objects.filter((o) => o.type === 'panel')
    const x = Math.min(0, ...figs.map((o) => o.x))
    const y = Math.min(0, ...figs.map((o) => o.y))
    expect(vp().fitFrame()).toMatchObject({
      x,
      y,
      w: Math.max(page.w, ...figs.map((o) => o.x + o.w)) - x,
      h: Math.max(page.h, ...figs.map((o) => o.y + o.h)) - y,
    })
    expect(only.id).toBe(panelOf('a.pdf').id)
  })

  // #706 评审 P2：加图 action 已取景「页面 ∪ 这张图」并留在适应模式；这里再聚焦（revealRect）
  // 会退出适应模式，之后抽屉收起 / 窗口缩放 / 切标签再切回都不再按这块取景
  it('「添加到画布」新加的图（多图排版里）：留在适应模式，取景「页面 ∪ 这张图」', () => {
    const page = s().doc.page
    useAssetStore.setState({
      byId: {
        ...useAssetStore.getState().byId,
        'big.pdf': info('big.pdf', { native_w_mm: page.w * 3, native_h_mm: page.h * 3 }),
      },
    })
    addFigureToLayout('a.pdf')
    addFigureToLayout('b.pdf')
    vp().setPan(-321, 77)
    expect(addFigureToLayout('big.pdf')).toBe('added')
    const o = panelOf('big.pdf')
    expect(vp().fitted).toBe(true)
    expect(vp().fitFrame()).toMatchObject({
      x: Math.min(0, o.x),
      y: Math.min(0, o.y),
      w: Math.max(page.w, o.x + o.w) - Math.min(0, o.x),
      h: Math.max(page.h, o.y + o.h) - Math.min(0, o.y),
    })
  })

  it('删到只剩一张：取景剩下那张的矩形（不是页面）', () => {
    addFigureToLayout('a.pdf')
    addFigureToLayout('b.pdf')
    const keep = panelOf('b.pdf')
    s().commit(literal('删除'), (d) => {
      d.objects = d.objects.filter((o) => o.id === keep.id)
    })
    expect(ws().mode).toBe('fast_edit')
    expect(vp().fitFrame()).toMatchObject({ x: keep.x, y: keep.y, w: keep.w, h: keep.h })
  })

  it('删光：回到按页面取景', () => {
    addFigureToLayout('a.pdf')
    vp().setPan(-999, -999)
    s().commit(literal('删光'), (d) => {
      d.objects = []
    })
    expect(ws().mode).toBe('layout')
    expect(vp().fitFrame()).toBeNull() // 取景的是页面本身
    expect(view().panX).not.toBe(-999)
  })

  it('载入 / 切换后的瞬时取景（fitStageNow）：单图编辑对着那张图的矩形，排版对着页面', () => {
    addFigureToLayout('a.pdf')
    const only = panelOf('a.pdf')
    vp().setPan(-500, -500)
    fitStageNow()
    expect(vp().fitFrame()).toMatchObject({ x: only.x, y: only.y, w: only.w, h: only.h })
    addFigureToLayout('b.pdf')
    vp().setPan(-500, -500)
    fitStageNow()
    expect(vp().fitFrame()).toBeNull() // 排版：取景的是页面
  })

  it('切画布 / 换文档不由模式订阅取景（各有各的适配点）：视口原样', async () => {
    addFigureToLayout('a.pdf')
    addFigureToLayout('b.pdf')
    const first = s().activeCanvasId
    createCanvasAndActivate() // 空画布 2
    activateCanvas(first)
    vp().setPan(-7, -7)
    const before = view()
    // 模式没变（仍是多图排版）：订阅什么都不做
    s().commit(literal('改点别的'), (d) => {
      d.page.bg = '#eeeeee'
    })
    expect(view()).toEqual(before)
    await reset()
    expect(ws().mode).toBe('layout')
  })
})

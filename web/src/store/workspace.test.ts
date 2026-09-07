import { beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { emptyProject, type PanelObject } from '@/types/document'
import type { PanelInfo } from '@/lib/api'
import { useAssetStore } from './assetStore'
import { startAutosave, useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'
import { useViewportStore } from './viewportStore'
import { beginCrop, enterElementEdit } from './actions'
import {
  addFigureToLayout,
  findFigurePanel,
  openFastEdit,
  restoreWorkspace,
  returnToLayout,
  startWorkspacePersistence,
  useWorkspaceStore,
} from './workspace'
import { activateCanvas, createCanvasAndActivate } from './canvasSession'
import { subscribePruneSelection } from '@/hooks/usePruneSelection'
import { syncLoadedDocument } from './liveSync'

/**
 * 两条工作流共享同一个对象模型（Prompt 09）。这批用例守的是「**没有第二套
 * 东西**」这件事本身——它没法靠读代码证明，只能靠：
 *
 * * 加入画布之后 overrides 还在**同一个对象 id** 上（不是被复制走了）；
 * * 从画布进图内编辑再回来，x/y/w/h 一个字节没动；
 * * 切模式不进撤销栈、不置 dirty；
 * * 重复「添加到画布」不叠对象。
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

const reset = async () => {
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
}

const panelOf = (fileId: string): PanelObject => {
  const hit = findFigurePanel(fileId)
  if (!hit) throw new Error(`文档里没有 ${fileId}`)
  return hit.panel
}

describe('打开一张图 → 快速编辑', () => {
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

  it('再打开同一张图不会叠出第二个面板', () => {
    openFastEdit('a.pdf')
    const id = ws().activePanelId
    returnToLayout()
    openFastEdit('a.pdf')
    expect(ws().activePanelId).toBe(id)
    expect(s().doc.objects.filter((o) => o.type === 'panel')).toHaveLength(1)
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

  it('回到画布排版就把待办作废（不留着等下一条事件来抢）', () => {
    useAssetStore.setState({ byId: { 'late.pdf': info('late.pdf', { script: undefined }) } })
    openFastEdit('late.pdf')
    expect(ws().pendingElementEdit).not.toBeNull()

    returnToLayout()

    expect(ws().pendingElementEdit).toBeNull()
  })

  /*
   * 下面两条走的是**绕过 `openFastEdit` 的那两个入口**（`lib/issueFocus.ts`
   * 就是这么用的：问题面板里点一条问题 → 直接 `enterFastEdit` / 直接
   * `enterElementEdit`）。必须从这里进，而且第二张图要在**记下待办之前**就
   * 已经在文档里：`openFastEdit` / `returnToLayout` / `focusLayoutPanel`
   * （`addFigureToLayout` 走它）都会顺手清掉待办，用它们摆场景的话待办早就
   * 没了，守卫拆掉也不会红——本轮实测过两次，那样写的反向用例在"无条件
   * 抢进"的变异下双双存活。
   */

  /** 让 `other` 先进文档，再把待办记在 `late.pdf` 上 */
  const setUpOtherThenLatch = (): string => {
    useAssetStore.setState({
      byId: { 'late.pdf': info('late.pdf', { script: undefined }), 'a.pdf': info('a.pdf') },
    })
    openFastEdit('a.pdf')
    const other = panelOf('a.pdf').id
    returnToLayout()
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

  it('工作区已经切到别的图：迟到的关联不越过它把用户拉回去', () => {
    const other = setUpOtherThenLatch()
    ws().enterFastEdit(other) // issueFocus 的定位路径：不经过 openFastEdit

    scriptArrives('late.pdf')

    expect(useUiStore.getState().elementPanelId).toBeNull()
    expect(ws().activePanelId).toBe(other)
  })
})

describe('快速编辑 ↔ 画布排版共享同一个对象', () => {
  beforeEach(reset)

  it('fast edit 里的修改在加入画布之后还在，而且还在同一个对象上', () => {
    openFastEdit('a.pdf')
    const id = panelOf('a.pdf').id
    s().commit(literal('改一个图内属性'), (d) => {
      const o = d.objects.find((x) => x.id === id) as PanelObject
      o.overrides = [{ gid: 'g1', prop: 'label', value: '甲' }]
    })

    expect(addFigureToLayout('a.pdf')).toBe('focused')
    const after = panelOf('a.pdf')
    expect(after.id).toBe(id) // 稳定对象 id：不是复制出来的新对象
    expect(after.overrides).toEqual([{ gid: 'g1', prop: 'label', value: '甲' }])
    expect(s().doc.objects.filter((o) => o.type === 'panel')).toHaveLength(1)
  })

  it('「添加到画布」在文档里还没有这张图时才真的添加', () => {
    expect(addFigureToLayout('b.pdf')).toBe('added')
    expect(addFigureToLayout('b.pdf')).toBe('focused')
    expect(s().doc.objects.filter((o) => o.type === 'panel')).toHaveLength(1)
  })

  it('从画布进图内编辑再返回：位置、尺寸、edits 全都不动', () => {
    addFigureToLayout('a.pdf')
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
    returnToLayout()

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
    returnToLayout()
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

describe('刷新之后回到哪里', () => {
  beforeEach(reset)

  it('模式按 documentId 存本机，重开回到同一张图', () => {
    const stop = startWorkspacePersistence()
    openFastEdit('a.pdf')
    const id = ws().activePanelId!
    stop()

    ws().clear()
    restoreWorkspace('d_test', s().doc.objects)
    expect(ws().mode).toBe('fast_edit')
    expect(ws().activePanelId).toBe(id)
  })

  it('存着的那个对象已经不在了 → 回排版模式，而不是打开一个空工作区', () => {
    const stop = startWorkspacePersistence()
    openFastEdit('a.pdf')
    stop()

    // 文档换了一份（那个对象 id 在新文档里不存在）
    ws().clear()
    restoreWorkspace('d_test', [])
    expect(ws().mode).toBe('layout')
    expect(ws().activePanelId).toBeNull()
  })

  it('本机存着别的文档那一档时不套用到这一份上', () => {
    const stop = startWorkspacePersistence()
    openFastEdit('a.pdf')
    stop()
    ws().clear()
    restoreWorkspace('d_other', s().doc.objects)
    expect(ws().mode).toBe('layout')
  })

  it('上次停在画布排版就还是画布排版——不许"顺手"打开一张图', () => {
    addFigureToLayout('a.pdf')
    const id = panelOf('a.pdf').id
    localStorage.setItem(
      'tavotto.workspace.d_test',
      JSON.stringify({ mode: 'layout', panelId: id }),
    )
    restoreWorkspace('d_test', s().doc.objects)
    expect(ws().mode).toBe('layout')
    expect(ws().activePanelId).toBeNull()
  })

  it('本机存着一个不认识的模式值（旧版本 / 手改过）时回排版模式', () => {
    addFigureToLayout('a.pdf')
    localStorage.setItem(
      'tavotto.workspace.d_test',
      JSON.stringify({ mode: 'zen', panelId: panelOf('a.pdf').id }),
    )
    restoreWorkspace('d_test', s().doc.objects)
    expect(ws().mode).toBe('layout')
  })

  it('坏掉的 blob 不让工作区卡住', () => {
    localStorage.setItem('tavotto.workspace.d_test', '{{{')
    restoreWorkspace('d_test', s().doc.objects)
    expect(ws().mode).toBe('layout')
  })
})

describe('对象消失就退出快速编辑', () => {
  beforeEach(reset)

  it('删掉正在快速编辑的面板 → 回排版模式（与图内编辑态同一次清扫）', () => {
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

  it('把它隐藏起来同样退出——不能停在一个看不见的对象上', () => {
    const stop = subscribePruneSelection()
    openFastEdit('a.pdf')
    const id = ws().activePanelId!
    s().commit(literal('隐藏'), (d) => {
      const o = d.objects.find((x) => x.id === id)!
      o.hidden = true
    })
    expect(ws().mode).toBe('layout')
    stop()
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
    returnToLayout()
    useDocumentStore.setState({ dirty: false })
    const past = s().past.length

    expect(openFastEdit('a.pdf')).toBe('editing')

    expect(panelsInDoc()).toHaveLength(1)
    expect(s().past.length).toBe(past)
    expect(s().dirty).toBe(false)
    expect(ws().addedForEdit).toBeNull()
    stopAutosave()
  })

  it('编辑还不在文档里的图：恰好 +1、一条历史、提示说出口、一次撤销即移除并退出快速编辑', () => {
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

  it('说明只属于这一次进入：回排版即清；再进同一张（已在文档里）不再挂', () => {
    openFastEdit('a.pdf')
    expect(ws().addedForEdit).toBe(panelOf('a.pdf').id)
    returnToLayout()
    expect(ws().addedForEdit).toBeNull()
    openFastEdit('a.pdf')
    expect(ws().addedForEdit).toBeNull()
  })

  it('换到另一张（也是刚加入的）说明跟着换；换到已在文档里的那张就没有', () => {
    openFastEdit('a.pdf')
    openFastEdit('b.pdf')
    expect(ws().addedForEdit).toBe(panelOf('b.pdf').id)
    openFastEdit('a.pdf')
    expect(ws().addedForEdit).toBeNull()
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
 * 切换模式不许把画布挪走（审计 T01 验收：**切换模式时画布不意外移动**）。
 *
 * 进快速编辑一定要动视口——那一屏按图自己的图幅单独摆出来。回来时此前一律
 * `revealRect` 把那张图挪到视口中央：用户没缩放没平移，画面却换成了以某张图
 * 为中心的另一片。现在回来还原进去之前的那一片。
 *
 * 动画在 reduced-motion 下同步落终态（`lib/motion.tween`），所以这里直接量
 * 落点，不等 rAF。
 */
describe('切模式不动用户的视口', () => {
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

  it('回到画布排版时还原进快速编辑前看的那一片', () => {
    openFastEdit('a.pdf')
    addFigureToLayout('b.pdf')
    // 用户在排版上自己摆好的视角
    useWorkspaceStore.getState().exitToLayout()
    vp().setPan(-321, 77)
    vp().setZoomCentered(2)
    const before = view()

    openFastEdit('a.pdf')
    expect(view()).not.toEqual(before) // 进快速编辑确实框住了那张图

    returnToLayout()
    expect(view()).toEqual(before)
  })

  it('没记过就现算一个落点，不是什么都不做', () => {
    openFastEdit('a.pdf')
    // 会话恢复：本来就在快速编辑里，没有「进来之前」那一片
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: panelOf('a.pdf').id })
    vp().setPan(-999, -999)
    returnToLayout()
    expect(view().panX).not.toBe(-999)
  })

  it('本来就在排版上时不动视口（onboarding 的前置动作会这么调）', () => {
    openFastEdit('a.pdf')
    returnToLayout()
    vp().setPan(-42, -42)
    const before = view()
    returnToLayout()
    expect(view()).toEqual(before)
  })

  it('问题面板的定位也走同一条路：它调的是 enterFastEdit', () => {
    addFigureToLayout('a.pdf')
    vp().setPan(-150, -60)
    const before = view()
    useWorkspaceStore.getState().enterFastEdit(panelOf('a.pdf').id)
    vp().setPan(0, 0)
    returnToLayout()
    expect(view()).toEqual(before)
  })

  it('换过画布就不还原：记下的那一片属于上一张画布', () => {
    addFigureToLayout('a.pdf')
    const c1 = s().activeCanvasId
    const c2 = createCanvasAndActivate()
    activateCanvas(c1)
    vp().setPan(-200, -100)
    const onC1 = view()

    // 问题面板定位那条路：进快速编辑（记下 c1 的视角），之后用户切了画布标签
    useWorkspaceStore.getState().enterFastEdit(panelOf('a.pdf').id)
    activateCanvas(c2)
    returnToLayout()
    // 把 c1 的视角还给 c2 就是把用户送到别处
    expect(view()).not.toEqual(onC1)
  })

  it('换文档之后不把上一份文档的视角还回来', async () => {
    openFastEdit('a.pdf')
    const inFastEdit = view()
    await reset()
    vp().setViewRect({ left: 0, top: 0, width: 900, height: 600 })
    vp().setPan(-7, -7)
    const before = view()
    returnToLayout()
    expect(view()).toEqual(before)
    expect(view()).not.toEqual(inFastEdit)
  })
})

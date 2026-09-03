import { beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { emptyProject, type PanelObject } from '@/types/document'
import type { PanelInfo } from '@/lib/api'
import { useAssetStore } from './assetStore'
import { startAutosave, useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'
import { enterElementEdit } from './actions'
import {
  addFigureToLayout,
  findFigurePanel,
  openFastEdit,
  restoreWorkspace,
  returnToLayout,
  startWorkspacePersistence,
  useWorkspaceStore,
} from './workspace'
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
  useUiStore.getState().setElementPanel(null)
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
   * `enterElementEdit`）。必须从这里进——`openFastEdit` 与 `returnToLayout`
   * 自己会清掉待办，用它们做反向用例的话待办早就没了，守卫拆掉也不会红
   * （本轮实测：那样写的两条用例在"无条件抢进"的变异下双双存活）。
   */

  it('用户已经在编辑别的图：迟到的关联不把图内编辑换成另一张', () => {
    useAssetStore.setState({
      byId: { 'late.pdf': info('late.pdf', { script: undefined }), 'a.pdf': info('a.pdf') },
    })
    openFastEdit('late.pdf')
    addFigureToLayout('a.pdf')
    const other = panelOf('a.pdf').id
    enterElementEdit(other) // 画布双击 / 「编辑图内元素」按钮：不经过 openFastEdit

    scriptArrives('late.pdf')

    expect(useUiStore.getState().elementPanelId).toBe(other)
  })

  it('工作区已经切到别的图：迟到的关联不越过它把用户拉回去', () => {
    useAssetStore.setState({
      byId: { 'late.pdf': info('late.pdf', { script: undefined }), 'a.pdf': info('a.pdf') },
    })
    openFastEdit('late.pdf')
    addFigureToLayout('a.pdf')
    const other = panelOf('a.pdf').id
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

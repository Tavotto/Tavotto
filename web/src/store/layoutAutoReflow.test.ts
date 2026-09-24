/**
 * 自动重排（`startLayoutAutoReflow`）与撤销 / 重做、渲染同步的交互。
 *
 * 自动重排订阅文档，成员尺寸变了就发一条 `autoReflow` commit——commit 会清空 future。
 * 撤销 / 重做本身它认得出来、不触发；但撤销 / 重做之后渲染回来，`useEngineSync` 的图幅
 * 同步器会再 `silent` 补一次图幅（撤掉改图幅的 override、或撤掉 #543 那种由事务
 * 收尾修正并进缩放条目的图幅同步），这次尺寸变化以前被当成用户编辑：发一条 autoReflow
 * commit，future 被清空，用户看到的是「撤销了一步，然后重做按不动了」。
 *
 * 每条用例都把撤销 / 重做之后的渲染同步与 120 ms 的重排防抖跑完（`settle`）再断言。
 */
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { useEngineSync } from '@/hooks/useEngineSync'
import { seedExactRender } from '@/test/renderFixtures'
import { useRenderStore } from '@/store/renderStore'
import { useDocumentStore } from '@/store/documentStore'
import { startLayoutAutoReflow } from '@/store/actions'
import {
  emptyProject,
  type FigureDocument,
  type LayoutGroup,
  type PanelObject,
  type ProjectDocument,
} from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}

/** 40×30 mm、原生 40×30（100%）的面板 */
function panel(id: string, fileId: string, x: number): PanelObject {
  return {
    id,
    type: 'panel',
    x,
    y: 0,
    w: 40,
    h: 30,
    fileId,
    fileKind: 'pdf',
    nativeW: 40,
    nativeH: 30,
    script: null,
    overrides: [],
    groupId: 'lg',
  } as PanelObject
}

/** 一行两个面板、间距 4：A 在 0..40，B 在 44..84 */
const ROW: LayoutGroup = {
  id: 'lg',
  kind: 'row',
  order: ['pa', 'pb'],
  gap: 4,
  align: 'start',
  uniform: null,
}

const s = () => useDocumentStore.getState()
const obj = (id: string) => s().doc.objects.find((o) => o.id === id) as PanelObject
const lastLabel = () => s().past.at(-1)?.label.key
const snap = (): FigureDocument => structuredClone(s().doc)

/** 等渲染同步的 effect 与自动重排的 120 ms 防抖都跑完 */
async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 200))
  })
}
const undo = async () => {
  await act(async () => {
    s().undo()
  })
  await settle()
}
const redo = async () => {
  await act(async () => {
    s().redo()
  })
  await settle()
}
const commit = async (key: string, recipe: (d: FigureDocument) => void) => {
  await act(async () => {
    s().commit(literal(key), recipe)
  })
  await settle()
}
const seed = async (p: PanelObject, size: [number, number]) => {
  await act(async () => {
    seedExactRender(p, { stem: p.fileId, size_mm: size, elements: [] })
  })
  await settle()
}

let teardown: (() => Promise<void>) | null = null

beforeEach(() => {
  useRenderStore.getState().clear()
  useRenderStore.setState({ render: async () => {} })
})
afterEach(async () => {
  await teardown?.()
  teardown = null
})

/** 挂上同步器与自动重排；`project` 给了就按「打开文档」载入它，否则摆一行两个面板 */
async function mount(docId: string, project?: ProjectDocument) {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const Probe = () => {
    useEngineSync()
    return null
  }
  const stopReflow = startLayoutAutoReflow()
  await act(async () => {
    root.render(createElement(Probe))
  })
  teardown = async () => {
    stopReflow()
    await act(async () => {
      root.unmount()
    })
    container.remove()
  }
  if (project) {
    await act(async () => {
      await s().switchDocument(project, docId)
    })
  } else {
    await act(async () => {
      await s().switchDocument(emptyProject(), docId)
    })
    await commit('准备', (d) => {
      d.objects = [panel('pa', 'Fig1.pdf', 0), panel('pb', 'Fig2.pdf', 44)]
      d.layoutGroups = [structuredClone(ROW)]
    })
    // 两张图按原生图幅渲染回来：同步器没有要补的
    await seed(obj('pa'), [40, 30])
    await seed(obj('pb'), [40, 30])
  }
  await settle()
}

describe('用户编辑引起的尺寸变化照常自动重排、照常进历史', () => {
  it('缩放 commit：B 跟着挪到 A 的右边，重排是一条独立的历史', async () => {
    await mount('d_reflow_resize')
    const depth = s().past.length
    await commit('缩放', (d) => {
      const o = d.objects.find((x) => x.id === 'pa') as PanelObject
      o.w = 30
      o.h = 22.5
    })
    expect(obj('pb').x).toBeCloseTo(34, 6)
    expect(s().past.length).toBe(depth + 2)
    expect(lastLabel()).toBe('history.autoReflow')
  })

  it('拖手柄缩放（事务，松手落历史）：松手之后重排', async () => {
    // endTxn 不换 doc 的引用，只在栈上落一条——以前订阅在「doc 没变」处早退，整个漏掉
    await mount('d_reflow_drag')
    await act(async () => {
      s().beginTxn(literal('缩放'))
      for (const k of [0.9, 0.8, 0.75]) {
        s().txnUpdate((d) => {
          const o = d.objects.find((x) => x.id === 'pa') as PanelObject
          o.w = 40 * k
          o.h = 30 * k
        })
      }
    })
    await settle()
    // 手势进行中不重排
    expect(obj('pb').x).toBe(44)
    await act(async () => {
      s().endTxn()
    })
    await settle()
    expect(obj('pb').x).toBeCloseTo(34, 6)
    expect(lastLabel()).toBe('history.autoReflow')
  })

  it('改图幅的 override → 渲染回来 → 同步器补图幅 → 自动重排', async () => {
    await mount('d_reflow_override')
    await commit('改图幅', (d) => {
      const o = d.objects.find((x) => x.id === 'pa') as PanelObject
      o.overrides = [{ gid: 'fig', prop: 'size_mm', value: [50, 30] }]
    })
    await seed(obj('pa'), [50, 30])
    expect(obj('pa').w).toBeCloseTo(50, 6)
    expect(obj('pb').x).toBeCloseTo(54, 6)
    expect(lastLabel()).toBe('history.autoReflow')
  })

  it('撤销过一步之后的新编辑：照样重排（新 commit 清空 future，不是重做）', async () => {
    await mount('d_reflow_edit_after_undo')
    await commit('别的编辑', (d) => {
      d.name = 'renamed'
    })
    await undo()
    expect(s().future.length).toBe(1)
    await commit('缩放', (d) => {
      const o = d.objects.find((x) => x.id === 'pa') as PanelObject
      o.w = 30
      o.h = 22.5
    })
    expect(obj('pb').x).toBeCloseTo(34, 6)
    expect(lastLabel()).toBe('history.autoReflow')
  })
})

describe('撤销 / 重做与它们引起的派生同步：不清空 future、不塞条目', () => {
  it('撤掉改图幅的 override（及其重排）→ 渲染同步 → 重做两步回到撤销前', async () => {
    await mount('d_reflow_undo_override')
    await commit('改图幅', (d) => {
      const o = d.objects.find((x) => x.id === 'pa') as PanelObject
      o.overrides = [{ gid: 'fig', prop: 'size_mm', value: [50, 30] }]
    })
    await seed(obj('pa'), [50, 30])
    expect(lastLabel()).toBe('history.autoReflow')
    const edited = snap()
    const depth = s().past.length

    await undo() // 撤重排
    expect([s().past.length, s().future.length]).toEqual([depth - 1, 1])
    await undo() // 撤 override：变体键变回去，旧变体的 manifest 还在，同步器把 A 补回 40
    expect(obj('pa').w).toBeCloseTo(40, 6)
    expect([s().past.length, s().future.length]).toEqual([depth - 2, 2])

    await redo() // 重做 override：同步器把 A 补成 50——这一下以前会被重排成一条新 commit
    expect(obj('pa').w).toBeCloseTo(50, 6)
    expect([s().past.length, s().future.length]).toEqual([depth - 1, 1])
    await redo()
    expect([s().past.length, s().future.length]).toEqual([depth, 0])
    expect(s().doc).toEqual(edited)
  })

  it('撤掉 #543 那种收尾修正并进缩放的图幅同步 → 同步器再补一次 → 重做两步回到撤销前', async () => {
    await mount('d_reflow_undo_txn')
    const depth0 = s().past.length
    await act(async () => {
      s().beginTxn(literal('缩放'))
      s().txnUpdate((d) => {
        const o = d.objects.find((x) => x.id === 'pa') as PanelObject
        o.w = 30
        o.h = 22.5
      })
    })
    // 手势还没松开，改了图幅的渲染先回来了（同一个变体键，40×30 → 50×30）：事务中不写，
    // 松手时由收尾修正（registerTxnFinalizer）并进这条缩放历史
    await seed(obj('pa'), [50, 30])
    await act(async () => {
      s().endTxn()
    })
    await settle()
    expect(obj('pa').w).toBeCloseTo(37.5, 6)
    expect(obj('pb').x).toBeCloseTo(41.5, 6)
    expect(lastLabel()).toBe('history.autoReflow')
    // 收尾修正是这次手势的一部分：一条缩放 + 一条重排，没有第三条
    expect(s().past.length).toBe(depth0 + 2)
    const edited = snap()
    const depth = s().past.length

    await undo()
    await undo() // 回到事务前的旧图幅；manifest 仍是 50×30，同步器 silent 补回 50（100%）
    expect([obj('pa').nativeW, obj('pa').w]).toEqual([50, 50])
    expect([s().past.length, s().future.length]).toEqual([depth - 2, 2])

    await redo()
    expect([s().past.length, s().future.length]).toEqual([depth - 1, 1])
    await redo()
    expect([s().past.length, s().future.length]).toEqual([depth, 0])
    expect(s().doc).toEqual(edited)
  })

  it('撤销缩放：撤销本身不重排，连撤两步再重做两步回到撤销前', async () => {
    await mount('d_reflow_undo_resize')
    await commit('缩放', (d) => {
      const o = d.objects.find((x) => x.id === 'pa') as PanelObject
      o.w = 30
      o.h = 22.5
    })
    expect(lastLabel()).toBe('history.autoReflow')
    const edited = snap()
    const depth = s().past.length
    await undo() // 撤重排：B 回 44，A 仍是 30 宽——这一格就是用户当时缩放完的样子
    expect(obj('pb').x).toBe(44)
    expect([s().past.length, s().future.length]).toEqual([depth - 1, 1])
    await undo()
    expect(obj('pa').w).toBe(40)
    expect([s().past.length, s().future.length]).toEqual([depth - 2, 2])
    await redo()
    await redo()
    expect([s().past.length, s().future.length]).toEqual([depth, 0])
    expect(s().doc).toEqual(edited)
  })

  it('编辑之后 120 ms 防抖还没到就连撤两步：待发的重排作废', async () => {
    await mount('d_reflow_undo_in_debounce')
    await commit('缩放', (d) => {
      const o = d.objects.find((x) => x.id === 'pa') as PanelObject
      o.w = 30
      o.h = 22.5
    })
    expect(lastLabel()).toBe('history.autoReflow')
    const depth = s().past.length
    // 再缩一次，防抖还没到就连撤两步：落在「A 30 宽、B 还在 44」那一格（第一次缩放、重排之前）
    await act(async () => {
      s().commit(literal('缩放'), (d) => {
        const o = d.objects.find((x) => x.id === 'pa') as PanelObject
        o.w = 20
        o.h = 15
      })
      s().undo()
      s().undo()
    })
    await settle()
    expect([s().past.length, s().future.length]).toEqual([depth - 1, 2])
    expect(obj('pb').x).toBe(44)
  })

  it('防抖还没到就撤销、再重做：被作废的那次重排在重做之后补上（#558 评审）', async () => {
    await mount('d_reflow_redo_cancelled')
    const depth = s().past.length
    await act(async () => {
      s().commit(literal('缩放'), (d) => {
        const o = d.objects.find((x) => x.id === 'pa') as PanelObject
        o.w = 30
        o.h = 22.5
      })
      s().undo()
    })
    await settle()
    expect([s().past.length, s().future.length]).toEqual([depth, 1])
    expect(obj('pb').x).toBe(44)
    await redo()
    // 重做回到的是用户刚做完、重排还没来得及落的那一格：重排补上，且进历史
    expect(obj('pb').x).toBeCloseTo(34, 6)
    expect(lastLabel()).toBe('history.autoReflow')
    expect([s().past.length, s().future.length]).toEqual([depth + 2, 0])
  })

  it('改图幅 override 后防抖还没到就撤销、再重做：同步器补图幅之后照样重排', async () => {
    await mount('d_reflow_redo_cancelled_override')
    await seed(obj('pa'), [40, 30])
    await commit('改图幅', (d) => {
      const o = d.objects.find((x) => x.id === 'pa') as PanelObject
      o.overrides = [{ gid: 'fig', prop: 'size_mm', value: [50, 30] }]
    })
    const depth = s().past.length
    // 渲染回来（silent 补图幅，排上重排），防抖没到就撤销
    await act(async () => {
      seedExactRender(obj('pa'), { stem: 'Fig1.pdf', size_mm: [50, 30], elements: [] })
    })
    await act(async () => {
      s().undo()
    })
    await settle()
    expect(obj('pa').w).toBeCloseTo(40, 6)
    expect([s().past.length, s().future.length]).toEqual([depth - 1, 1])
    await redo()
    expect(obj('pa').w).toBeCloseTo(50, 6)
    expect(obj('pb').x).toBeCloseTo(54, 6)
    expect(lastLabel()).toBe('history.autoReflow')
    expect([s().past.length, s().future.length]).toEqual([depth + 1, 0])
  })

  it('撤销 / 重做之后的第一次用户编辑：照常重排', async () => {
    await mount('d_reflow_edit_after_derived')
    await commit('改图幅', (d) => {
      const o = d.objects.find((x) => x.id === 'pa') as PanelObject
      o.overrides = [{ gid: 'fig', prop: 'size_mm', value: [50, 30] }]
    })
    await seed(obj('pa'), [50, 30])
    await undo()
    await undo()
    // 撤完之后用户自己把 A 缩到 30 宽：这是编辑，B 要跟着挪到 34
    await commit('缩放', (d) => {
      const o = d.objects.find((x) => x.id === 'pa') as PanelObject
      o.w = 30
      o.h = 22.5
    })
    expect(obj('pb').x).toBeCloseTo(34, 6)
    expect(lastLabel()).toBe('history.autoReflow')
    expect(s().future.length).toBe(0)
  })
})

describe('打开文档 / 切画布', () => {
  // 两条都先做一次用户编辑再换：换之前的那一刻不是「停在历史上」，换本身得把它摆回去
  it('编辑过之后载入一份组内排布没对齐的文档：不重排、不进历史、不变脏', async () => {
    await mount('d_reflow_before_open')
    await commit('别的编辑', (d) => {
      d.name = 'renamed'
    })
    const project = emptyProject()
    const canvas = project.canvases[0]
    // 另一份文档自己的组；B 被用户拖开过（位置变化不触发重排，这就是存下来的样子）
    canvas.objects = [panel('pa', 'Fig1.pdf', 0), panel('pb', 'Fig2.pdf', 60)].map((o) => ({
      ...o,
      groupId: 'lg_open',
    }))
    canvas.layoutGroups = [{ ...structuredClone(ROW), id: 'lg_open' }]
    await act(async () => {
      await s().switchDocument(project, 'd_reflow_open')
    })
    await settle()
    expect(obj('pb').x).toBe(60)
    expect(s().past.length).toBe(0)
    expect(s().dirty).toBe(false)
  })

  it('切到另一张组内排布没对齐、自己有历史的画布：不重排、那张画布的栈不动', async () => {
    const project = emptyProject()
    const first = project.canvases[0]
    first.objects = [panel('pa', 'Fig1.pdf', 0), panel('pb', 'Fig2.pdf', 44)]
    first.layoutGroups = [structuredClone(ROW)]
    const second = structuredClone(first)
    second.id = 'c_second'
    second.name = 'second'
    // 另一张画布自己的组（id 不同）；B 被用户拖开过
    second.objects = [panel('pa', 'Fig1.pdf', 0), panel('pb', 'Fig2.pdf', 60)].map((o) => ({
      ...o,
      groupId: 'lg2',
    }))
    second.layoutGroups = [{ ...structuredClone(ROW), id: 'lg2' }]
    project.canvases.push(second)
    await mount('d_reflow_switch_canvas', project)
    const rename = (name: string) =>
      commit('别的编辑', (d) => {
        d.name = name
      })
    const switchTo = async (id: string) => {
      await act(async () => {
        s().switchCanvas(id)
      })
      await settle()
    }
    await rename('one')
    await switchTo('c_second')
    await rename('two') // 第二张画布有了自己的一条历史
    await switchTo(first.id)
    await rename('one again')
    // 换回来时 past 末位是第二张画布那条（换进来的条目不是一次新编辑）
    await switchTo('c_second')
    expect(obj('pb').x).toBe(60)
    expect([s().past.length, s().future.length]).toEqual([1, 0])
  })
})

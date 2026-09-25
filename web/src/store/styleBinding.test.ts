/**
 * 画布跟随样式（ADR 0081）的判定性用例，逐条对应用户拍板的要求：
 *
 * 1. 绑定 = 立刻把整份样式对齐到当前画布上**所有图**，一条历史；撤销连绑定一起回去；
 * 2. 绑定后改值 = 改这套样式本身（先存库），两张图同时跟着变，一条历史；撤销时库也退回；
 * 3. 内置样式只读：改值时复制一份、改绑到副本；
 * 4. 重开文档 / 渲染回来：已经合样式的图**零 commit**；
 * 5. 新加进绑定画布的图，第一次拿到 manifest 时对齐一次，单独一条历史「按样式对齐新图」，
 *    撤销之后本会话里不会被当场再对齐；
 * 6. 缩放过的图按页面 pt 换算；
 * 7. 库里那条在别处被改了（设置里保存）→ 只对齐变了的那一项；
 * 8. 恢复原样 = 清样式管得到的 override + 解绑，一条历史。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import type { ProfileRecord } from '@/lib/api'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject, type ProjectDocument } from '@/types/document'
import { startLayoutAutoReflow } from './actions'
import { useAssetStore } from './assetStore'
import { useDocumentStore } from './documentStore'
import { useInteractionStore } from './interactionStore'
import { useProfileStore } from './profileStore'
import { renderKeyOf, useRenderStore } from './renderStore'
import {
  alignNewFigures,
  bindCanvasStyle,
  editBoundStyle,
  followLibrary,
  resetStyleBindingSession,
  restoreCanvasStyle,
  startStyleBindingSync,
  whenLibraryIdle,
} from './styleBinding'
import { useUiStore } from './uiStore'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const s = () => useDocumentStore.getState()
const panelById = (id: string) => s().doc.objects.find((o) => o.id === id) as PanelObject
const ov = (id: string, gid: string, prop: string) =>
  panelById(id).overrides.find((o) => o.gid === gid && o.prop === prop)?.value

const panel = (id: string, fileId: string, w = 80): PanelObject => ({
  id,
  type: 'panel',
  fileId,
  fileKind: 'pdf',
  script: `${fileId}.py`,
  name: fileId,
  nativeW: 80,
  nativeH: 60,
  x: 0,
  y: 0,
  w,
  h: (60 * w) / 80,
  overrides: [],
})

const num = (prop: string, value: number) => ({ prop, type: 'number', value })
const manifest = (stem: string, axisLabel = 9) => ({
  stem,
  size_mm: [80, 60],
  elements: [
    { gid: 'axes_0.xlabel', role: 'axis_label', label: 'x', bbox: [0, 0, 1, 1], draggable: false, editable: [num('fontsize', axisLabel)] },
    { gid: 'axes_0.title', role: 'title', label: 't', bbox: [0, 0, 1, 1], draggable: false, editable: [num('fontsize', 9)] },
  ],
})

const record = (over: Partial<ProfileRecord> & { id: string; data: Record<string, unknown> }): ProfileRecord =>
  ({
    kind: 'style',
    schema_version: 1,
    revision: 1,
    display_name: over.id,
    name_key: '',
    version: '',
    created_at: 0,
    updated_at: 0,
    built_in: false,
    read_only: false,
    is_default: false,
    derived_from: '',
    warnings: [],
    ...over,
  }) as ProfileRecord

const USER = record({ id: 's1', display_name: '投稿用', data: { element: { axis_label: { fontsize: 10 } }, pt_basis: 'page' } })
const BUILTIN = record({
  id: 'builtin-default-style',
  display_name: '默认样式',
  built_in: true,
  read_only: true,
  is_default: true,
  data: { element: { axis_label: { fontsize: 9.5 } }, pt_basis: 'page' },
})

/** 假的样式库后端：save / duplicate / create 改内存里的清单，与真 store 的 `run` 同一种落法 */
let saved: { id: string; data: Record<string, unknown> }[] = []
function fakeLibrary(records: ProfileRecord[]) {
  saved = []
  const put = (rec: ProfileRecord) => {
    const list = useProfileStore.getState().styles
    const next = list.some((r) => r.id === rec.id) ? list.map((r) => (r.id === rec.id ? rec : r)) : [...list, rec]
    useProfileStore.setState({ styles: next })
    return rec
  }
  useProfileStore.setState({
    styles: records,
    loaded: true,
    error: null,
    save: async (_k, id, data) => {
      saved.push({ id, data })
      const cur = useProfileStore.getState().styles.find((r) => r.id === id)!
      return put({ ...cur, data, revision: cur.revision + 1 })
    },
    duplicate: async (_k, id) => {
      const src = useProfileStore.getState().styles.find((r) => r.id === id)!
      return put({ ...src, id: `${id}-copy`, display_name: `${src.display_name} 副本`, built_in: false, read_only: false, is_default: false, derived_from: id })
    },
  })
}

async function seed(panels: PanelObject[], extra: (p: ProjectDocument) => void = () => {}) {
  const project = emptyProject()
  project.canvases[0].objects = panels
  project.canvases[0].page = { w: 180, h: 80, bg: '#ffffff' }
  extra(project)
  await s().switchDocument(project, `d_${Math.random().toString(36).slice(2)}`)
  for (const p of panels) seedExactRender(p, manifest(p.fileId) as never)
}

/** 面板改了 override 之后重新挂一份与它对得上的渲染（真引擎回来那一刻的样子） */
function rerender(id: string) {
  const p = panelById(id)
  const size = p.overrides.find((o) => o.gid === 'axes_0.xlabel' && o.prop === 'fontsize')?.value
  seedExactRender(p, manifest(p.fileId, typeof size === 'number' ? size : 9) as never)
}

/** 当前画布上所有图都「渲染回来」一次（真引擎在 override 变了之后会重画；只认这一版的 manifest） */
function rerenderAll() {
  for (const o of s().doc.objects) if (o.type === 'panel') rerender(o.id)
}

let stop: (() => void) | null = null
beforeEach(() => {
  resetStyleBindingSession()
  // 渲染缓存按变体分键、跨用例常驻：上一条用例渲染过的 FigB 不能让这一条的「新图」一加进来就有 manifest
  useRenderStore.getState().clear()
  fakeLibrary([BUILTIN, USER])
  useUiStore.setState({ status: null })
})
afterEach(() => {
  stop?.()
  stop = null
})

describe('绑定 = 立刻对齐当前画布上的所有图，一条历史', () => {
  it('两张图同时对齐到 10 pt；⌘Z 一次，override 与绑定一起回去', async () => {
    await seed([panel('a', 'FigA'), panel('b', 'FigB')])
    const before = s().past.length
    bindCanvasStyle('s1')
    expect(s().past.length - before).toBe(1)
    expect(s().doc.style?.id).toBe('s1')
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(10)
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(10)
    // 已经合样式的项（标题 9 pt，样式没管）不写
    expect(panelById('a').overrides).toHaveLength(1)
    expect(s().undo()).not.toBeNull()
    expect(s().doc.style).toBeUndefined()
    expect(panelById('a').overrides).toEqual([])
    expect(panelById('b').overrides).toEqual([])
  })

  it('缩放过的图按页面 pt 换算：页面上 48 mm（× 0.6）的图写 10 / 0.6 = 16.67', async () => {
    await seed([panel('a', 'FigA', 48)])
    bindCanvasStyle('s1')
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(16.67)
  })
})

describe('绑定后改值 = 改这套样式本身，画布上所有图跟着变', () => {
  it('先存库、再一条历史对齐两张图；撤销只退画布（已脱离），库保留新值；重做回到跟随', async () => {
    await seed([panel('a', 'FigA'), panel('b', 'FigB', 40)])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    const before = s().past.length
    expect(await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 11 })).toBe(true)
    const NEW = { element: { axis_label: { fontsize: 11 } }, pt_basis: 'page' }
    expect(saved.at(-1)).toEqual({ id: 's1', data: NEW })
    expect(s().past.length - before, '改一个值 = 一条历史').toBe(1)
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(11)
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(22) // 40 / 80 = 0.5
    expect(s().doc.style?.snapshot).toEqual(NEW)

    const writes = saved.length
    s().undo()
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(10)
    expect(s().doc.style).toEqual({ id: 's1', snapshot: USER.data, detached: true })
    await whenLibraryIdle()
    expect(saved.length, '撤销不写样式库').toBe(writes)
    expect(useProfileStore.getState().styles.find((r) => r.id === 's1')?.data).toEqual(NEW)

    s().redo()
    expect(s().doc.style, '重做回到跟随状态').toEqual({ id: 's1', snapshot: NEW })
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(11)
    s().undo()
    expect(s().doc.style?.detached, '再撤销仍落在已脱离').toBe(true)
  })


  it('存库失败就一个字都不改文档', async () => {
    await seed([panel('a', 'FigA')])
    bindCanvasStyle('s1')
    useProfileStore.setState({ save: async () => null, error: { code: 'x', message: '撞车' } })
    const before = s().past.length
    expect(await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 12 })).toBe(false)
    expect(s().past.length).toBe(before)
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(10)
    expect(useUiStore.getState().statusTone).toBe('error')
  })

  it('内置样式只读：改值时复制一份、改绑到副本，状态栏说一句；内置那条原样', async () => {
    await seed([panel('a', 'FigA')])
    bindCanvasStyle(BUILTIN.id)
    rerenderAll()
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(9.5)
    await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 10.5 })
    expect(s().doc.style?.id).toBe('builtin-default-style-copy')
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(10.5)
    expect(useProfileStore.getState().styles.find((r) => r.id === BUILTIN.id)?.data).toEqual(BUILTIN.data)
    expect(useUiStore.getState().status).not.toBeNull()
  })
})

describe('不许自动改的时刻：已经合样式的图零 commit', () => {
  it('重开一份已经对齐过的绑定文档、渲染回来：一条历史都不产生', async () => {
    const a = { ...panel('a', 'FigA'), overrides: [{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 10 }] }
    await seed([a], (p) => {
      p.canvases[0].style = { id: 's1', snapshot: structuredClone(USER.data) }
    })
    stop = startStyleBindingSync()
    rerender('a')
    alignNewFigures()
    followLibrary()
    expect(s().past).toHaveLength(0)
    expect(s().dirty).toBe(false)
  })

  it('没有 override、但脚本本来就合样式的图：也是零 commit', async () => {
    await seed([panel('a', 'FigA')], (p) => {
      p.canvases[0].style = { id: 's1', snapshot: { element: { axis_label: { fontsize: 9 } } } }
    })
    fakeLibrary([record({ id: 's1', data: { element: { axis_label: { fontsize: 9 } } } })])
    stop = startStyleBindingSync()
    expect(alignNewFigures()).toBe(0)
    expect(s().past).toHaveLength(0)
  })
})

describe('缩放不触发联动（用户 2026-09-24 拍板：按比例一起缩放）', () => {
  it('把已绑定画布上的图缩到 60% 并渲染回来：不产生任何对齐 commit，override 原样，读者量到的字号随图变小', async () => {
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerender('a')
    s().commit(literal('缩放'), (d) => {
      const o = d.objects[0] as PanelObject
      o.w = 48
      o.h = 36
    })
    const before = s().past.length
    // 同会话里渲染回来，以及「重开之后」（会话记账清空）再渲染回来，两种都不写
    rerender('a')
    alignNewFigures()
    followLibrary()
    resetStyleBindingSession()
    rerender('a')
    alignNewFigures()
    followLibrary()
    expect(s().past.length - before).toBe(0)
    expect(ov('a', 'axes_0.xlabel', 'fontsize'), '脚本值不变：10 pt × 0.6 = 页面上 6 pt').toBe(10)
  })
})

describe('新加进绑定画布的图：第一次拿到 manifest 时对齐一次', () => {
  it('单独一条「按样式对齐新图」，可撤销；撤销之后本会话不会被当场再对齐', async () => {
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    s().commit(literal('加一张图'), (d) => {
      d.objects.push(panel('b', 'FigB'))
    })
    const before = s().past.length
    // 渲染回来（订阅方触发 alignNewFigures）
    seedExactRender(panelById('b'), manifest('FigB') as never)
    expect(s().past.length - before).toBe(1)
    expect(s().past.at(-1)?.label).toMatchObject({ key: 'history.alignNewFigure' })
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(10)
    expect(ov('a', 'axes_0.xlabel', 'fontsize'), '只动新图').toBe(10)

    s().undo()
    expect(panelById('b').overrides).toEqual([])
    seedExactRender(panelById('b'), manifest('FigB') as never)
    expect(panelById('b').overrides, '撤销的结果不会被渲染回来那一下冲掉').toEqual([])
    // 之后又做了一次无关编辑（future 清空、同步器补跑）：撤掉的那次对齐也不会被补回来
    s().commit(literal('无关编辑'), (d) => {
      d.page.bg = '#fafafa'
    })
    expect(panelById('b').overrides, '用户撤掉的对齐不再自己回来').toEqual([])
  })

  it('事务进行中（拖到一半）渲染回来：先不写，事务收尾时补上对齐，且不并进那个事务', async () => {
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    s().commit(literal('加一张图'), (d) => {
      d.objects.push(panel('b', 'FigB'))
    })
    s().beginTxn(literal('拖动'))
    s().txnUpdate((d) => {
      d.objects[0].x = 5
    })
    seedExactRender(panelById('b'), manifest('FigB') as never)
    expect(panelById('b').overrides, '事务里不写').toEqual([])
    const before = s().past.length
    s().endTxn()
    expect(s().past.length - before, '拖动一条 + 对齐新图一条').toBe(2)
    expect(s().past.at(-1)?.label).toMatchObject({ key: 'history.alignNewFigure' })
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(10)
  })

  it('在属性页里刻意改过的图（带着样式管得到的 override）不去碰', async () => {
    await seed([panel('a', 'FigA')])
    bindCanvasStyle('s1')
    resetStyleBindingSession()
    s().commit(literal('手改'), (d) => {
      const o = d.objects[0] as PanelObject
      o.overrides = [{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 14 }]
    })
    rerender('a')
    expect(alignNewFigures()).toBe(0)
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(14)
  })
})

describe('库里那一条在别处改了（设置里保存）', () => {
  it('回到画布时跟上，只对齐变了的那一项；一条「按样式更新」', async () => {
    await seed([panel('a', 'FigA')])
    fakeLibrary([record({ id: 's1', data: { element: { axis_label: { fontsize: 10 }, title: { fontsize: 11 } } } })])
    bindCanvasStyle('s1')
    rerenderAll()
    // 用户在属性页里把标题改成 13：这一项与这次库里的改动无关，不该被冲掉
    s().commit(literal('手改标题'), (d) => {
      const o = d.objects[0] as PanelObject
      o.overrides = o.overrides.map((x) => (x.gid === 'axes_0.title' ? { ...x, value: 13 } : x))
    })
    rerenderAll()
    const before = s().past.length
    useProfileStore.setState({
      styles: [record({ id: 's1', data: { element: { axis_label: { fontsize: 12 }, title: { fontsize: 11 } } } })],
    })
    expect(followLibrary()).toBe(true)
    expect(s().past.length - before).toBe(1)
    expect(s().past.at(-1)?.label).toMatchObject({ key: 'history.syncStyle' })
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(12)
    expect(ov('a', 'axes_0.title', 'fontsize')).toBe(13)
    expect(followLibrary(), '内容相等就不再写').toBe(false)
  })
})

describe('解绑与恢复原样', () => {
  it('不跟随样式：只解绑，图保持此刻的样子', async () => {
    await seed([panel('a', 'FigA')])
    bindCanvasStyle('s1')
    bindCanvasStyle(null)
    expect(s().doc.style).toBeUndefined()
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(10)
  })

  it('恢复原样：整张画布清掉样式管得到的 override 并解绑，一条历史；撤销全回来', async () => {
    await seed([panel('a', 'FigA'), panel('b', 'FigB')])
    bindCanvasStyle('s1')
    rerenderAll()
    s().commit(literal('改标题文字'), (d) => {
      ;(d.objects[0] as PanelObject).overrides.push({ gid: 'axes_0.title', prop: 'text', value: '新标题' })
    })
    rerenderAll()
    const before = s().past.length
    restoreCanvasStyle()
    expect(s().past.length - before).toBe(1)
    expect(s().doc.style).toBeUndefined()
    expect(panelById('a').overrides).toEqual([{ gid: 'axes_0.title', prop: 'text', value: '新标题' }])
    expect(panelById('b').overrides).toEqual([])
    s().undo()
    expect(s().doc.style?.id).toBe('s1')
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(10)
  })
})

describe('Codex #547 评审', () => {
  it('P1 异步存库期间切了画布：回来时不往此刻的画布上写（库照样存了）', async () => {
    await seed([panel('a', 'FigA')])
    bindCanvasStyle('s1')
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    useProfileStore.setState({
      save: async (_k, id, data) => {
        await gate
        const cur = useProfileStore.getState().styles.find((r) => r.id === id)!
        const rec = { ...cur, data }
        useProfileStore.setState({ styles: [rec] })
        return rec
      },
    })
    const pendingEdit = editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 12 })
    const other = s().addCanvas('第二张')
    expect(s().activeCanvasId).toBe(other)
    const before = s().past.length
    release()
    expect(await pendingEdit).toBe(false)
    expect(s().past.length).toBe(before)
    expect(s().doc.style, '新画布没有被绑上别处的样式').toBeUndefined()
  })

  it('P1 库里那条变了、但还有图没渲染出来：先不前进快照，渲染到齐时一起跟上', async () => {
    await seed([panel('a', 'FigA'), panel('b', 'FigB')])
    bindCanvasStyle('s1')
    // 模拟「刚切过来」：b 的渲染还没回来
    useRenderStore.getState().clear()
    rerender('a')
    stop = startStyleBindingSync()
    const before = s().past.length
    useProfileStore.setState({ styles: [record({ id: 's1', data: { element: { axis_label: { fontsize: 12 } }, pt_basis: 'page' } })] })
    expect(s().past.length - before, 'b 还在路上：不写').toBe(0)
    expect(s().doc.style?.snapshot).toEqual(USER.data)
    rerender('b')
    expect(s().past.length - before).toBe(1)
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(12)
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(12)
  })

  it('P1 改值那一刻某张图没有 manifest（身上带着旧样式的 override）：它渲染回来时补上这一笔', async () => {
    await seed([panel('a', 'FigA'), panel('b', 'FigB')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    // b 的渲染缓存没了（换了变体 / 被驱逐），但 override 还在
    useRenderStore.getState().clear()
    rerender('a')
    await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 13 })
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(13)
    expect(ov('b', 'axes_0.xlabel', 'fontsize'), '此刻对不上它').toBe(10)
    const before = s().past.length
    rerender('b')
    expect(s().past.length - before).toBe(1)
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(13)
  })

  it('P1 改绑那一刻某张图没有 manifest（身上带着上一套样式的 override）：它渲染回来时按新绑定补上', async () => {
    await seed([panel('a', 'FigA'), panel('b', 'FigB')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    useRenderStore.getState().clear()
    rerender('a')
    bindCanvasStyle(BUILTIN.id)
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(9.5)
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(10)
    rerender('b')
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(9.5)
  })

  it('P2 交互进行中到达的跟随：交互一结束就补上', async () => {
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    useInteractionStore.getState().begin('move')
    useProfileStore.setState({ styles: [record({ id: 's1', data: { element: { axis_label: { fontsize: 12 } }, pt_basis: 'page' } })] })
    expect(ov('a', 'axes_0.xlabel', 'fontsize'), '拖到一半不写').toBe(10)
    useInteractionStore.getState().end()
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(12)
  })

  it('P2 带 override 的图还没有 manifest 时，恢复原样先不做（不留下样式的样子又解绑）', async () => {
    await seed([panel('a', 'FigA')])
    bindCanvasStyle('s1')
    useRenderStore.getState().clear()
    const before = s().past.length
    expect(restoreCanvasStyle()).toBe(false)
    expect(s().past.length).toBe(before)
    expect(s().doc.style?.id).toBe('s1')
    rerender('a')
    expect(restoreCanvasStyle()).toBe(true)
    expect(s().doc.style).toBeUndefined()
  })

  it('P2 内置样式复制出副本后改动没存进去：删掉那份副本，库里不留用户没要过的东西', async () => {
    await seed([panel('a', 'FigA')])
    bindCanvasStyle(BUILTIN.id)
    const removed: string[] = []
    const dup = useProfileStore.getState().duplicate
    useProfileStore.setState({
      duplicate: dup,
      save: async () => null,
      remove: async (_k, id) => {
        removed.push(id)
        useProfileStore.setState({ styles: useProfileStore.getState().styles.filter((r) => r.id !== id) })
        return true
      },
    })
    expect(await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 11 })).toBe(false)
    expect(removed).toEqual(['builtin-default-style-copy'])
    expect(useProfileStore.getState().styles.map((r) => r.id)).toEqual([BUILTIN.id, 's1'])
    expect(s().doc.style?.id).toBe(BUILTIN.id)
  })
})

describe('旧样式（没有 pt_basis）第一次被编辑：升级成按页面 pt 记（2026-09-24 拍板）', () => {
  const LEGACY = record({
    id: 'old',
    display_name: '老样式',
    data: { element: { axis_label: { fontsize: 10 }, title: { fontsize: 12 } } },
  })

  it('缩放比 0.6 的图：输入 9 → 页面上就是 9；旧数字按页面 pt 整张重新对齐；标记写进库与快照；一条历史', async () => {
    fakeLibrary([LEGACY])
    await seed([panel('a', 'FigA', 48)])
    stop = startStyleBindingSync()
    bindCanvasStyle('old')
    // 旧口径：数字就是脚本值
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(10)
    expect(ov('a', 'axes_0.title', 'fontsize')).toBe(12)
    rerender('a')
    const before = s().past.length
    expect(await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 9 })).toBe(true)
    expect(s().past.length - before, '升级 + 编辑 = 一条历史').toBe(1)
    expect(ov('a', 'axes_0.xlabel', 'fontsize'), '9 / 0.6').toBe(15)
    expect(ov('a', 'axes_0.title', 'fontsize'), '旧的 12 从此是页面上的 12：12 / 0.6').toBe(20)
    expect(saved.at(-1)?.data).toEqual({ element: { axis_label: { fontsize: 9 }, title: { fontsize: 12 } }, pt_basis: 'page' })
    expect(s().doc.style?.snapshot.pt_basis).toBe('page')
    expect(useUiStore.getState().status).toMatchObject({ key: 'stylePanel.upgradedLegacy' })
  })

  it('一次撤销全部退回画布上的 override 与快照；样式库保留升级后的那一份', async () => {
    fakeLibrary([LEGACY])
    await seed([panel('a', 'FigA', 48)])
    stop = startStyleBindingSync()
    bindCanvasStyle('old')
    rerender('a')
    await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 9 })
    const upgraded = useProfileStore.getState().styles.find((r) => r.id === 'old')?.data
    expect(upgraded).toMatchObject({ pt_basis: 'page' })
    s().undo()
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(10)
    expect(ov('a', 'axes_0.title', 'fontsize')).toBe(12)
    expect(s().doc.style).toEqual({ id: 'old', snapshot: LEGACY.data, detached: true })
    await whenLibraryIdle()
    expect(useProfileStore.getState().styles.find((r) => r.id === 'old')?.data).toEqual(upgraded)
  })


  it('缩放比 1 的图：旧数字一个都不变，只有被编辑的那一项变', async () => {
    fakeLibrary([LEGACY])
    await seed([panel('a', 'FigA', 80)])
    bindCanvasStyle('old')
    rerender('a')
    await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 9 })
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(9)
    expect(ov('a', 'axes_0.title', 'fontsize')).toBe(12)
  })
})

describe('Codex #547 第二轮评审：库写入一条队列 + 代次', () => {
  const gated = () => {
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    return { gate, release }
  }

  it('P1 存库期间同一份文档被重载（id 全一样、loadSeq 前进了）：旧编辑不写进新载入的那一份', async () => {
    await seed([panel('a', 'FigA')])
    bindCanvasStyle('s1')
    rerenderAll()
    const project = s().buildProject()
    const id = s().documentId
    const { gate, release } = gated()
    const real = useProfileStore.getState().save
    let started!: () => void
    const saving = new Promise<void>((r) => (started = r))
    useProfileStore.setState({ save: async (...args) => (started(), await gate, real(...args)) })
    const edit = editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 12 })
    await saving
    await s().switchDocument(structuredClone(project), id)
    const seq = s().loadSeq
    release()
    expect(await edit).toBe(false)
    expect(s().loadSeq).toBe(seq)
    expect(ov('a', 'axes_0.xlabel', 'fontsize'), '重载进来的那一份原样').toBe(10)
    expect(s().past).toHaveLength(0)
  })

  it('P2 换绑定时欠账作废：A 的欠账不会在画布跟随 B 之后写下去', async () => {
    fakeLibrary([
      record({ id: 'A', data: { element: { axis_label: { fontsize: 11 }, title: { fontsize: 14 } }, pt_basis: 'page' } }),
      record({ id: 'B', data: { element: { axis_label: { fontsize: 8.5 } }, pt_basis: 'page' } }),
    ])
    await seed([panel('a', 'FigA'), panel('b', 'FigB')])
    stop = startStyleBindingSync()
    useRenderStore.getState().clear()
    rerender('a')
    bindCanvasStyle('A')
    bindCanvasStyle('B')
    rerender('b')
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(8.5)
    expect(ov('b', 'axes_0.title', 'fontsize'), 'A 独有的那一项不写').toBeUndefined()
  })

  it('P2 换绑定时欠账作废：A 改过之后再绑回 A，旧版 A 里有、新版 A 里已删掉的那一项不写', async () => {
    fakeLibrary([
      record({ id: 'A', data: { element: { axis_label: { fontsize: 11 }, title: { fontsize: 14 } }, pt_basis: 'page' } }),
      record({ id: 'B', data: { element: { axis_label: { fontsize: 8.5 } }, pt_basis: 'page' } }),
    ])
    await seed([panel('a', 'FigA'), panel('b', 'FigB')])
    stop = startStyleBindingSync()
    useRenderStore.getState().clear()
    rerender('a')
    bindCanvasStyle('A')
    bindCanvasStyle('B')
    // A 在别处被改过：不再管标题
    useProfileStore.setState({
      styles: [
        record({ id: 'A', data: { element: { axis_label: { fontsize: 11 } }, pt_basis: 'page' } }),
        record({ id: 'B', data: { element: { axis_label: { fontsize: 8.5 } }, pt_basis: 'page' } }),
      ],
    })
    bindCanvasStyle('A')
    rerender('b')
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(11)
    expect(ov('b', 'axes_0.title', 'fontsize'), '旧版 A 的欠账已作废').toBeUndefined()
  })

  it('P1 旧样式（没有 pt_basis）在缩放比 0.6 的图上输入 6：脚本值 10、读者量到 6，并写上 pt_basis', async () => {
    fakeLibrary([record({ id: 'old', data: { element: { axis_label: { fontsize: 10 } } } })])
    await seed([panel('a', 'FigA', 48)])
    bindCanvasStyle('old')
    rerenderAll()
    await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 6 })
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(10)
    expect(saved.at(-1)?.data).toMatchObject({ pt_basis: 'page', element: { axis_label: { fontsize: 6 } } })
  })

  it('P1 只认这张面板此刻这一版的 manifest：拿不到就走欠账，拿到新的那一刻按新 gid 对齐', async () => {
    const a = panel('a', 'FigA')
    await seed([a])
    // 文档里的 override 与已渲染的那一版对不上（脚本 / 变体换了）：只有回退的旧 manifest
    s().commit(literal('改了别的'), (d) => {
      ;(d.objects[0] as PanelObject).overrides = [{ gid: 'axes_0.title', prop: 'color', value: '#ff0000' }]
    })
    bindCanvasStyle('s1')
    expect(ov('a', 'axes_0.xlabel', 'fontsize'), '不按过期的 manifest 写').toBeUndefined()
    // 新的那一版回来了：gid 换了（xlabel → xlabel_new）
    const p = panelById('a')
    seedExactRender(p, {
      stem: 'FigA',
      size_mm: [80, 60],
      elements: [
        { gid: 'axes_0.xlabel_new', role: 'axis_label', label: 'x', bbox: [0, 0, 1, 1], draggable: false, editable: [num('fontsize', 9)] },
      ],
    } as never)
    alignNewFigures()
    expect(ov('a', 'axes_0.xlabel_new', 'fontsize')).toBe(10)
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBeUndefined()
  })

  it('P2 队列排空时补跑同步：存库期间切到另一张绑同一条样式的画布，存完它跟上', async () => {
    const project = emptyProject()
    const c1 = project.canvases[0]
    c1.objects = [panel('a', 'FigA')]
    const c2 = { ...structuredClone(c1), id: 'c2', name: '第二张', objects: [panel('b', 'FigB')] }
    c2.style = { id: 's1', snapshot: structuredClone(USER.data) }
    ;(c2.objects[0] as PanelObject).overrides = [{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 10 }]
    project.canvases.push(c2)
    await s().switchDocument(project, 'd_two')
    seedExactRender(panelById('a'), manifest('FigA') as never)
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    const { gate, release } = gated()
    const real = useProfileStore.getState().save
    let started!: () => void
    const saving = new Promise<void>((r) => (started = r))
    useProfileStore.setState({ save: async (...args) => (started(), await gate, real(...args)) })
    const edit = editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 12 })
    await saving // 存库已经发出去了，才切画布
    s().switchCanvas('c2')
    rerender('b')
    release()
    expect(await edit).toBe(false)
    await whenLibraryIdle()
    expect(ov('b', 'axes_0.xlabel', 'fontsize'), '队列排空那一刻补跑了跟随').toBe(12)
  })
})

describe('#543 定稿后：停在历史上时不自动写（不清 future）', () => {
  it('撤销了一次无关编辑（有可重做的 future）时新图渲染回来：先不对齐、future 保住；下一次编辑之后补上', async () => {
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    s().commit(literal('加一张图'), (d) => {
      d.objects.push(panel('b', 'FigB'))
    })
    s().commit(literal('无关编辑'), (d) => {
      d.page.bg = '#fafafa'
    })
    s().undo()
    expect(s().future).toHaveLength(1)
    seedExactRender(panelById('b'), manifest('FigB') as never)
    expect(panelById('b').overrides, '停在历史上：不写').toEqual([])
    expect(s().future, 'future 保住，⌘⇧Z 还能回去').toHaveLength(1)
    s().commit(literal('新的编辑'), (d) => {
      d.page.bg = '#f0f0f0'
    })
    expect(ov('b', 'axes_0.xlabel', 'fontsize'), 'future 清空之后补上对齐').toBe(10)
  })

  it('布局组里的新图按样式对齐：只有「按样式对齐新图」一条，不触发自动重排（样式不改尺寸）', async () => {
    const a = { ...panel('a', 'FigA'), groupId: 'row1' }
    const b = { ...panel('b', 'FigB'), x: 90, groupId: 'row1' }
    await seed([a], (p) => {
      p.canvases[0].layoutGroups = [{ id: 'row1', kind: 'row', order: ['a'], gap: 5, align: 'start' }]
    })
    // 前面用例留在渲染缓存里的 FigB 清掉：这张新图要到下面才「渲染回来」
    useRenderStore.getState().clear()
    rerender('a')
    const stopReflow = startLayoutAutoReflow()
    stop = startStyleBindingSync()
    try {
      bindCanvasStyle('s1')
      rerenderAll()
      s().commit(literal('加一张图'), (d) => {
        d.objects.push(b)
        d.layoutGroups![0].order.push('b')
      })
      await new Promise((r) => setTimeout(r, 200))
      const before = s().past.map((e) => e.label.key)
      seedExactRender(panelById('b'), manifest('FigB') as never)
      await new Promise((r) => setTimeout(r, 200))
      const after = s().past.map((e) => e.label.key)
      expect(after.slice(before.length)).toEqual(['history.alignNewFigure'])
    } finally {
      stopReflow()
    }
  })
})

describe('Codex #547 第三轮评审', () => {
  it('P1 跟随库的变更时有一张图渲染出错（不挡快照）：它欠着这一笔，之后渲染成功时补上', async () => {
    await seed([panel('a', 'FigA'), panel('b', 'FigB')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    // b 这一版渲染失败：没有 manifest，status 是 error（不会再「在路上」）
    const pb = panelById('b')
    useRenderStore.getState().patch(renderKeyOf(pb), {
      fileId: pb.fileId,
      rev: 2,
      manifest: null as never,
      svg: '',
      status: 'error',
      stale: false,
      lastPatches: '',
      wantPatches: JSON.stringify(pb.overrides),
    } as never)
    useProfileStore.setState({
      styles: [record({ id: 's1', data: { element: { axis_label: { fontsize: 12 } }, pt_basis: 'page' } })],
    })
    expect(ov('a', 'axes_0.xlabel', 'fontsize'), '快照照常前进，a 跟上').toBe(12)
    expect(ov('b', 'axes_0.xlabel', 'fontsize'), 'b 此刻对不上').toBe(10)
    // b 之后渲染成功了
    rerender('b')
    expect(ov('b', 'axes_0.xlabel', 'fontsize'), '欠账补上，不停在旧样式上').toBe(12)
  })
})

describe('Codex #547 第四轮评审', () => {
  const note = (id: string) => ({
    id, type: 'text', text: '注释', sizePt: 9, bold: false, color: '#000000', align: 'left', x: 0, y: 0, w: 10, h: 5,
  })

  it('P1 标注只改了字号：写下去的也只有字号，用户绑定之后手改的颜色留着', async () => {
    fakeLibrary([record({ id: 's1', data: { element: {}, annotation: { sizePt: 9, color: '#000000' }, pt_basis: 'page' } })])
    await seed([panel('a', 'FigA')], (p) => {
      p.canvases[0].objects.push(note('t1') as never)
    })
    bindCanvasStyle('s1')
    s().commit(literal('手改颜色'), (d) => {
      ;(d.objects.find((o) => o.id === 't1') as { color: string }).color = '#ff0000'
    })
    await editBoundStyle({ kind: 'annotation', prop: 'sizePt', value: 11 })
    const t1 = s().doc.objects.find((o) => o.id === 't1') as { sizePt: number; color: string }
    expect(t1.sizePt).toBe(11)
    expect(t1.color, '颜色这一项样式没变，不重新套').toBe('#ff0000')
  })

  it('P1 旧样式在设置里被存成 page 口径（数字没变）：跟随时按新口径整张重算，缩放 0.6 的图 9 → 15', async () => {
    fakeLibrary([record({ id: 'old', data: { element: { axis_label: { fontsize: 9 } } } })])
    await seed([panel('a', 'FigA', 48)])
    stop = startStyleBindingSync()
    bindCanvasStyle('old')
    rerenderAll()
    expect(ov('a', 'axes_0.xlabel', 'fontsize'), '旧口径：脚本里本来就是 9，不写').toBeUndefined()
    useProfileStore.setState({
      styles: [record({ id: 'old', data: { element: { axis_label: { fontsize: 9 } }, pt_basis: 'page' } })],
    })
    expect(ov('a', 'axes_0.xlabel', 'fontsize'), '页面上 9 pt').toBe(15)
    expect(s().doc.style?.snapshot.pt_basis).toBe('page')
  })

  it('P1 库里有一笔被挡下还没落到画布的更新时在面板里改值：那一笔也一起落下去，不被当成已同步', async () => {
    fakeLibrary([record({ id: 's1', data: { element: { axis_label: { fontsize: 10 }, title: { fontsize: 9 } }, pt_basis: 'page' } })])
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    // 停在历史上：库里标题改成 12 的那一笔被挡下
    s().commit(literal('无关'), (d) => {
      d.page.bg = '#fafafa'
    })
    s().undo()
    useProfileStore.setState({
      styles: [record({ id: 's1', data: { element: { axis_label: { fontsize: 10 }, title: { fontsize: 12 } }, pt_basis: 'page' } })],
    })
    expect(ov('a', 'axes_0.title', 'fontsize'), '被挡下').not.toBe(12)
    await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 11 })
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(11)
    expect(ov('a', 'axes_0.title', 'fontsize'), '挡下的那一笔一起落下').toBe(12)
  })
})

describe('Codex #547 第五轮评审', () => {
  it('P1 排队中的改值属于发起时绑的那一条：排队期间用户改绑了，改值先落到原来那一条、之后才改绑，新绑的那一条一个字没被改', async () => {
    fakeLibrary([USER, record({ id: 's2', data: { element: { axis_label: { fontsize: 8.5 } }, pt_basis: 'page' } })])
    await seed([panel('a', 'FigA')])
    bindCanvasStyle('s1')
    rerenderAll()
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    const real = useProfileStore.getState().save
    let started!: () => void
    const saving = new Promise<void>((r) => (started = r))
    useProfileStore.setState({ save: async (...args) => (started(), await gate, real(...args)) })
    const first = editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 12 })
    const second = editBoundStyle({ kind: 'element', role: 'title', prop: 'fontsize', value: 14 })
    await saving // 第一笔的存库已经在飞，第二笔排在它后面
    bindCanvasStyle('s2') // 排在两笔改值后面（ADR 0081 §十一：明确的绑定也进库写队列）
    release()
    expect(await first).toBe(true)
    expect(await second).toBe(true)
    await whenLibraryIdle()
    expect(s().doc.style?.id, '最后绑的是 s2').toBe('s2')
    expect(useProfileStore.getState().styles.find((r) => r.id === 's1')?.data, '两笔改值落在 s1 上').toMatchObject({
      element: { axis_label: { fontsize: 12 }, title: { fontsize: 14 } },
    })
    expect(useProfileStore.getState().styles.find((r) => r.id === 's2')?.data, 's2 一个字没被改').toEqual({
      element: { axis_label: { fontsize: 8.5 } },
      pt_basis: 'page',
    })
  })

  it('P1 同一串排队的改值里第一笔把内置样式复制成副本并改绑：后面那笔跟着落到副本上', async () => {
    await seed([panel('a', 'FigA')])
    bindCanvasStyle(BUILTIN.id)
    rerenderAll()
    const first = editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 10.5 })
    const second = editBoundStyle({ kind: 'element', role: 'title', prop: 'fontsize', value: 11 })
    expect(await first).toBe(true)
    expect(await second).toBe(true)
    expect(s().doc.style?.id).toBe('builtin-default-style-copy')
    expect(s().doc.style?.snapshot).toMatchObject({ element: { axis_label: { fontsize: 10.5 }, title: { fontsize: 11 } } })
    // 队列排空之后用户自己绑回内置、再改：不被转发到上一次的副本上（转发只对当时排着的任务有效）
    bindCanvasStyle(BUILTIN.id)
    rerenderAll()
    expect(await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 9 })).toBe(true)
    expect(s().doc.style?.id).not.toBe(BUILTIN.id)
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(9)
  })

  it('P1 输入的值恰好等于库里那笔被挡下的更新：不当成无事可做，画布落到这个值', async () => {
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    s().commit(literal('无关'), (d) => {
      d.page.bg = '#fafafa'
    })
    s().undo()
    useProfileStore.setState({
      styles: [record({ id: 's1', data: { element: { axis_label: { fontsize: 12 } }, pt_basis: 'page' } })],
    })
    expect(ov('a', 'axes_0.xlabel', 'fontsize'), '停在历史上：库的那一笔被挡下').toBe(10)
    const before = s().past.length
    expect(await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 12 })).toBe(true)
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(12)
    expect(s().past.length - before).toBe(1)
    expect(s().future).toHaveLength(0)
  })
})

describe('Codex #547 第六轮评审', () => {
  it('P1 重开一份绑定的文档：同步器起来就拉样式库清单（不必先打开样式面板 / 设置）', async () => {
    let loads = 0
    useProfileStore.setState({ loaded: false, loading: false, load: async () => void (loads += 1) })
    await seed([panel('a', 'FigA')], (p) => {
      p.canvases[0].style = { id: 's1', snapshot: structuredClone(USER.data) }
    })
    stop = startStyleBindingSync()
    expect(loads).toBe(1)
  })

  it('P1 转发只在改绑真的落进发起那张画布时才记：A 上的复制任务回来时已切到 B，B 上排着的改动照常生效', async () => {
    const project = emptyProject()
    const c1 = project.canvases[0]
    c1.objects = [panel('a', 'FigA')]
    c1.style = { id: BUILTIN.id, snapshot: structuredClone(BUILTIN.data) }
    const c2 = { ...structuredClone(c1), id: 'c2', name: '第二张', objects: [panel('b', 'FigB')] }
    project.canvases.push(c2)
    await s().switchDocument(project, 'd_redirect')
    rerender('a')
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    let started!: () => void
    const saving = new Promise<void>((r) => (started = r))
    const real = useProfileStore.getState().save
    useProfileStore.setState({ save: async (...args) => (started(), await gate, real(...args)) })
    const onA = editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 10.5 })
    await saving
    s().switchCanvas('c2')
    rerender('b')
    const onB = editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 11 })
    release()
    expect(await onA).toBe(false)
    expect(await onB, 'B 的改动没有被 A 的「转发」作废').toBe(true)
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(11)
    expect(s().doc.style?.id).not.toBe(BUILTIN.id)
  })
})

describe('Codex #547 第七轮评审', () => {
  it('P1 绑着库里已经没有的样式：连着改两次，第一次按快照新建一条并改绑，第二次顺着转发落到新的那一条上', async () => {
    fakeLibrary([])
    await seed([panel('a', 'FigA')], (p) => {
      p.canvases[0].style = { id: 'gone', snapshot: structuredClone(USER.data) }
    })
    useProfileStore.setState({
      create: async (_k, name, data) => {
        const rec = record({ id: 'new1', display_name: name, data })
        useProfileStore.setState({ styles: [...useProfileStore.getState().styles, rec] })
        return rec
      },
    })
    rerenderAll()
    const first = editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 11 })
    const second = editBoundStyle({ kind: 'element', role: 'title', prop: 'fontsize', value: 13 })
    expect(await first).toBe(true)
    expect(await second, '第二次没有被静默丢掉').toBe(true)
    expect(s().doc.style?.id).toBe('new1')
    expect(s().doc.style?.snapshot).toMatchObject({ element: { axis_label: { fontsize: 11 }, title: { fontsize: 13 } } })
  })

  it('P1 样式换掉一条已有 override 时原地改值：override 顺序不变（它的 JSON 就是渲染变体的键）', async () => {
    const a = {
      ...panel('a', 'FigA'),
      overrides: [
        { gid: 'axes_0.xlabel', prop: 'fontsize', value: 20 },
        { gid: 'axes_0.title', prop: 'text', value: '改过的标题' },
      ],
    }
    await seed([a])
    bindCanvasStyle('s1')
    expect(panelById('a').overrides).toEqual([
      { gid: 'axes_0.xlabel', prop: 'fontsize', value: 10 },
      { gid: 'axes_0.title', prop: 'text', value: '改过的标题' },
    ])
  })
})

describe('Codex #547 第八轮评审', () => {
  it('P1 复制出来的副本在等存库时被另一张画布绑上了：原处放弃时不删它', async () => {
    const project = emptyProject()
    const c1 = project.canvases[0]
    c1.objects = [panel('a', 'FigA')]
    c1.style = { id: BUILTIN.id, snapshot: structuredClone(BUILTIN.data) }
    const c2 = { ...structuredClone(c1), id: 'c2', name: '第二张', objects: [panel('b', 'FigB')] }
    delete c2.style
    project.canvases.push(c2)
    await s().switchDocument(project, 'd_adopt')
    rerender('a')
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    let started!: () => void
    const saving = new Promise<void>((r) => (started = r))
    const real = useProfileStore.getState().save
    const removed: string[] = []
    useProfileStore.setState({
      save: async (...args) => (started(), await gate, real(...args)),
      remove: async (_k, id) => (removed.push(id), true),
    })
    const onA = editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 10.5 })
    await saving // 副本已经进了共享清单，存库在等
    s().switchCanvas('c2')
    rerender('b')
    bindCanvasStyle('builtin-default-style-copy')
    release()
    expect(await onA).toBe(false)
    expect(removed, '被 c2 绑着的副本不删').toEqual([])
    expect(s().doc.style?.id).toBe('builtin-default-style-copy')
  })
})

describe('Codex #547 第九轮评审', () => {

  it('P2 样式库清单还在拉时改值：等清单回来再改原来那一条，不按快照新建一条', async () => {
    await seed([panel('a', 'FigA')], (p) => {
      p.canvases[0].style = { id: 's1', snapshot: structuredClone(USER.data) }
    })
    rerenderAll()
    let creates = 0
    useProfileStore.setState({
      styles: [],
      loaded: false,
      loading: true,
      create: async () => (creates++, null),
      load: async () => {
        await Promise.resolve()
        useProfileStore.setState({ styles: [USER], loaded: true, loading: false })
      },
    })
    expect(await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 11 })).toBe(true)
    expect(creates).toBe(0)
    expect(saved.at(-1)?.id).toBe('s1')
    expect(s().doc.style?.id).toBe('s1')
  })
})

describe('Codex #547 第十轮评审', () => {
  it('P1 欠账里的某一项后来从样式里删掉了：那张图渲染回来时不再写这一项', async () => {
    fakeLibrary([record({ id: 's1', data: { element: { axis_label: { fontsize: 10 }, title: { fontsize: 11 } }, pt_basis: 'page' } })])
    await seed([panel('a', 'FigA'), panel('b', 'FigB')])
    stop = startStyleBindingSync()
    useRenderStore.getState().clear()
    rerender('a')
    bindCanvasStyle('s1') // b 没有 manifest：欠着整份（含标题 11）
    rerender('a')
    // 库里把标题那一项删了
    useProfileStore.setState({
      styles: [record({ id: 's1', data: { element: { axis_label: { fontsize: 10 } }, pt_basis: 'page' } })],
    })
    rerender('b')
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(10)
    expect(ov('b', 'axes_0.title', 'fontsize'), '样式里已经没有的那一项不写').toBeUndefined()
  })
})

describe('Codex #547 第十一轮评审（第十二轮起换成结构性解法：绑定进库写队列）', () => {

  it('P2 已经绑着同一条、图也都合样式时再点一次绑定：不产生历史、不标脏', async () => {
    await seed([panel('a', 'FigA')])
    bindCanvasStyle('s1')
    rerenderAll()
    const before = s().past.length
    bindCanvasStyle('s1')
    expect(s().past.length).toBe(before)
  })
})

describe('Codex #547 第十三轮评审', () => {
  it('P1 欠账按 commit 之前量：写好了的图不记假账，用户在它重画回来之前手改的值不会被冲掉', async () => {
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1') // a 有精确 manifest：当场写好 10
    // 重画回来之前，用户手改了轴标题
    s().commit(literal('手改'), (d) => {
      const o = d.objects[0] as PanelObject
      o.overrides = o.overrides.map((x) => (x.gid === 'axes_0.xlabel' ? { ...x, value: 14 } : x))
    })
    rerenderAll()
    alignNewFigures()
    expect(ov('a', 'axes_0.xlabel', 'fontsize'), '手改的 14 留着').toBe(14)
  })

  it('P1 排队中的绑定在切走画布后执行不了：状态栏说一声，不静默丢掉', async () => {
    const project = emptyProject()
    const c1 = project.canvases[0]
    c1.objects = [panel('a', 'FigA')]
    const c2 = { ...structuredClone(c1), id: 'c2', name: '第二张', objects: [panel('b', 'FigB')] }
    project.canvases.push(c2)
    await s().switchDocument(project, 'd_bind_cancel')
    rerender('a')
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    let started!: () => void
    const saving = new Promise<void>((r) => (started = r))
    const real = useProfileStore.getState().save
    // 先让队列里有一个在飞的写入（另一张画布上的改值）
    bindCanvasStyle('s1')
    rerenderAll()
    useProfileStore.setState({ save: async (...args) => (started(), await gate, real(...args)) })
    void editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 12 })
    await saving
    bindCanvasStyle(BUILTIN.id) // 排在后面
    s().switchCanvas('c2')
    release()
    await whenLibraryIdle()
    await whenLibraryIdle()
    expect(useUiStore.getState().status).toMatchObject({ key: 'stylePanel.bindCancelled' })
  })
})

describe('Codex #547 第十四轮评审', () => {
  const note = (id: string, text = '注释') => ({
    id, type: 'text', text, sizePt: 9, bold: false, color: '#000000', align: 'left', x: 0, y: 0, w: 10, h: 5,
  })

  it('P2 绑定之后新加的画布标注：按样式对齐一次（单独一条历史）；绑定前就有的标注重开文档时不重排', async () => {
    fakeLibrary([record({ id: 's1', data: { element: {}, annotation: { sizePt: 11 }, pt_basis: 'page' } })])
    await seed([panel('a', 'FigA')], (p) => {
      p.canvases[0].objects.push(note('old') as never)
    })
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    const size = (id: string) => (s().doc.objects.find((o) => o.id === id) as { sizePt: number }).sizePt
    expect(size('old'), '绑定时整张画布对齐').toBe(11)
    // 用户把旧标注改回 9，再加一段新标注
    s().commit(literal('手改'), (d) => {
      ;(d.objects.find((o) => o.id === 'old') as { sizePt: number }).sizePt = 9
    })
    const before = s().past.length
    s().commit(literal('加标注'), (d) => {
      d.objects.push(note('new') as never)
    })
    expect(size('new'), '新标注按样式对齐').toBe(11)
    expect(s().past.at(-1)?.label).toMatchObject({ key: 'history.alignNewText' })
    expect(s().past.length - before, '加标注一条 + 对齐一条').toBe(2)
    expect(size('old'), '手改过的旧标注不碰').toBe(9)
  })

  it('P2 绑定之后加进来的图只带着素材的烘焙基线：不当成手改，照样对齐（与先加图再绑定同一个结果）', async () => {
    useAssetStore.setState({
      byId: {
        FigB: { id: 'FigB', baked_overrides: [{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 7 }], baked_current: true } as never,
      },
    })
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    const b = { ...panel('b', 'FigB'), overrides: [{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 7 }] }
    s().commit(literal('加一张带基线的图'), (d) => {
      d.objects.push(b)
    })
    seedExactRender(panelById('b'), manifest('FigB', 7) as never)
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(10)
  })
})

describe('Codex #547 第十五轮评审', () => {
  it('P1 副本改动存失败、而另一张画布排队要绑这个副本：不删副本，排队的绑定照常绑上', async () => {
    const project = emptyProject()
    const c1 = project.canvases[0]
    c1.objects = [panel('a', 'FigA')]
    c1.style = { id: BUILTIN.id, snapshot: structuredClone(BUILTIN.data) }
    const c2 = { ...structuredClone(c1), id: 'c2', name: '第二张', objects: [panel('b', 'FigB')] }
    delete c2.style
    project.canvases.push(c2)
    await s().switchDocument(project, 'd_failed_copy')
    rerender('a')
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    let started!: () => void
    const saving = new Promise<void>((r) => (started = r))
    const removed: string[] = []
    useProfileStore.setState({
      save: async () => (started(), await gate, null),
      remove: async (_k, id) => (removed.push(id), true),
    })
    const onA = editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 10.5 })
    await saving // 副本已经进了共享清单，改动的存库在等
    s().switchCanvas('c2')
    rerender('b')
    bindCanvasStyle('builtin-default-style-copy') // 队列里有写入：排队
    release()
    expect(await onA).toBe(false)
    await whenLibraryIdle()
    expect(removed, '排队要绑它：不删').toEqual([])
    expect(s().doc.style?.id).toBe('builtin-default-style-copy')
  })
})

describe('Codex #547 第十六轮评审', () => {
  it('P1 排队的绑定执行时目标样式已被删：什么都不动，旧绑定原样留着，并说出来', async () => {
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    // 一个在飞的写入占住队列，期间排队要绑一条随后被删掉的样式
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    useProfileStore.setState({ save: async () => (await gate, null) })
    const slow = editBoundStyle({ kind: 'element', role: 'title', prop: 'fontsize', value: 14 })
    await Promise.resolve()
    bindCanvasStyle('gone')
    release()
    await slow
    await whenLibraryIdle()
    expect(useUiStore.getState().status).toMatchObject({ key: 'stylePanel.bindMissing' })
    expect(s().doc.style?.id).toBe('s1')
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(10)
  })

})

describe('Codex #547 第十七轮评审', () => {

  it('P2 复制内置样式的改动因切走画布作废、副本要删：先说出来', async () => {
    const project = emptyProject()
    const c1 = project.canvases[0]
    c1.objects = [panel('a', 'FigA')]
    c1.style = { id: BUILTIN.id, snapshot: structuredClone(BUILTIN.data) }
    const c2 = { ...structuredClone(c1), id: 'c2', name: '第二张', objects: [panel('b', 'FigB')] }
    delete c2.style
    project.canvases.push(c2)
    await s().switchDocument(project, 'd_edit_cancel')
    rerender('a')
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    let started!: () => void
    const saving = new Promise<void>((r) => (started = r))
    const real = useProfileStore.getState().save
    useProfileStore.setState({
      save: async (...args) => (started(), await gate, real(...args)),
      remove: async () => true,
    })
    const onA = editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 10.5 })
    await saving
    s().switchCanvas('c2')
    release()
    expect(await onA).toBe(false)
    expect(useUiStore.getState().status).toMatchObject({ key: 'stylePanel.editCancelled' })
  })
})

describe('Codex #547 第十八轮评审', () => {
  it('P1 改绑排在队列里时紧接着改值：改绑落地后这一笔对不上，状态栏说出来而不是静默丢掉', async () => {
    fakeLibrary([USER, record({ id: 's2', data: { element: { axis_label: { fontsize: 8.5 } }, pt_basis: 'page' } })])
    await seed([panel('a', 'FigA')])
    bindCanvasStyle('s1')
    rerenderAll()
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    let started!: () => void
    const saving = new Promise<void>((r) => (started = r))
    const real = useProfileStore.getState().save
    useProfileStore.setState({ save: async (...args) => (started(), await gate, real(...args)) })
    void editBoundStyle({ kind: 'element', role: 'title', prop: 'fontsize', value: 14 })
    await saving
    bindCanvasStyle('s2') // 排队
    const later = editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 11 }) // 发起时还绑着 s1
    release()
    expect(await later).toBe(false)
    expect(useUiStore.getState().status).toMatchObject({ key: 'stylePanel.editCancelled' })
    expect(s().doc.style?.id).toBe('s2')
  })
})

describe('Codex #547 第十九轮评审', () => {
  /** 一次与样式无关的新编辑：清掉 future，让被「停在历史上」挡下的自动写入放行 */
  const unrelatedEdit = () =>
    s().commit(literal('改了别的'), (d) => {
      ;(d.objects[0] as PanelObject).overrides.push({ gid: 'axes_0.title', prop: 'color', value: '#ff0000' })
    })

  it.each([
    ['解绑', null],
    ['改绑到另一条', 's2'],
  ])('P1 %s再撤销：改值时还没 manifest 的那张图的欠账还在，它渲染回来时照样补上', async (_, other) => {
    fakeLibrary([USER, record({ id: 's2', data: { element: { title: { fontsize: 9 } }, pt_basis: 'page' } })])
    await seed([panel('a', 'FigA'), panel('b', 'FigB')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    // b 的 override 刚变过、这一版还没渲染出来：改值那一刻它对不上，记一笔欠账
    s().commit(literal('改了 b'), (d) => {
      ;(d.objects[1] as PanelObject).overrides.push({ gid: 'axes_0.title', prop: 'color', value: '#00ff00' })
    })
    rerender('a')
    await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 12 })
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(10)
    bindCanvasStyle(other)
    s().undo()
    expect(s().doc.style?.id).toBe('s1')
    unrelatedEdit()
    rerender('a')
    rerender('b')
    expect(ov('b', 'axes_0.xlabel', 'fontsize'), '欠着的 12 补上了').toBe(12)
  })
})

describe('Codex #547 第二十轮评审（ddc3760b）', () => {
  it('P1 已对齐的图换了素材（面板 id 不变）：新素材渲染回来时按绑定的样式对齐', async () => {
    await seed([panel('a', 'FigA'), panel('b', 'FigB')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(10)
    // 与 replacePanelAsset 同形：换文件、override 换成新素材的（这里没有基线 = 空），id 不变
    s().commit(literal('替换素材'), (d) => {
      const o = d.objects.find((x) => x.id === 'b') as PanelObject
      o.fileId = 'FigC'
      o.overrides = []
    })
    seedExactRender(panelById('b'), manifest('FigC', 8) as never)
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(10)
  })

  it('P2 素材的烘焙基线已失效（文件被外部改过）：抄进来的基线照样不算手改，新图照样对齐', async () => {
    const baked = [{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 7 }]
    useAssetStore.setState({ byId: { FigB: { id: 'FigB', baked_overrides: baked, baked_current: false } as never } })
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    s().commit(literal('加一张带失效基线的图'), (d) => {
      d.objects.push({ ...panel('b', 'FigB'), overrides: structuredClone(baked) })
    })
    seedExactRender(panelById('b'), manifest('FigB', 7) as never)
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(10)
  })
})

describe('Codex #547 最后一轮评审（964ce71f）', () => {
  it.each([
    ['一个 override 都没有', [] as PanelObject['overrides']],
    ['只带着素材的烘焙基线', [{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 7 }]],
  ])('P1 存库在飞时加进来的新图（%s）：渲染回来按整份样式对齐，不只补这一笔变化量', async (_, baked) => {
    useAssetStore.setState({ byId: { FigB: { id: 'FigB', baked_overrides: baked, baked_current: true } as never } })
    fakeLibrary([record({ id: 's1', data: { element: { axis_label: { fontsize: 10 }, title: { fontsize: 11 } }, pt_basis: 'page' } })])
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    let started!: () => void
    const saving = new Promise<void>((r) => (started = r))
    const real = useProfileStore.getState().save
    useProfileStore.setState({ save: async (...args) => (started(), await gate, real(...args)) })
    const edit = editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 12 })
    await saving
    s().commit(literal('加一张图'), (d) => {
      d.objects.push({ ...panel('b', 'FigB'), overrides: structuredClone(baked) })
    })
    release()
    expect(await edit).toBe(true)
    await whenLibraryIdle()
    rerender('b')
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(12)
    expect(ov('b', 'axes_0.title', 'fontsize'), '样式的其余部分也落上去').toBe(11)
  })
  it('P1 同一时刻没 manifest 的、手改过的图：仍只欠这一笔变化量，不拿整份样式冲掉手改', async () => {
    fakeLibrary([record({ id: 's1', data: { element: { axis_label: { fontsize: 10 }, title: { fontsize: 11 } }, pt_basis: 'page' } })])
    await seed([panel('a', 'FigA'), panel('b', 'FigB')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    // 用户在属性页里把 b 的标题改成 14：这一版还没渲染回来
    s().commit(literal('手改标题'), (d) => {
      const o = d.objects.find((x) => x.id === 'b') as PanelObject
      o.overrides = o.overrides.map((v) => (v.gid === 'axes_0.title' ? { ...v, value: 14 } : v))
    })
    rerender('a')
    await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 12 })
    rerender('b')
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(12)
    expect(ov('b', 'axes_0.title', 'fontsize')).toBe(14)
  })
})

describe('撤销只退画布、不推回样式库（ADR 0081 §十二，用户 2026-09-25 拍板）', () => {
  const unrelatedEdit = () =>
    s().commit(literal('改了别的'), (d) => {
      d.page.bg = '#fafafa'
    })
  const libraryData = (id: string) => useProfileStore.getState().styles.find((r) => r.id === id)?.data

  it('撤销改样式之后再编辑一次（future 清空）：已脱离的画布不跟随，库里的新值不会被套回来', async () => {
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 12 })
    rerenderAll()
    s().undo()
    rerenderAll()
    unrelatedEdit()
    rerenderAll()
    expect(followLibrary()).toBe(false)
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(10)
    expect(s().doc.style?.detached).toBe(true)
    expect(libraryData('s1')).toMatchObject({ element: { axis_label: { fontsize: 12 } } })
  })

  it('一张画布撤销了改样式：另一张绑同一条样式的画布照样跟上新值', async () => {
    const project = emptyProject()
    const c1 = project.canvases[0]
    c1.objects = [panel('a', 'FigA')]
    const c2 = { ...structuredClone(c1), id: 'c2', name: '第二张', objects: [panel('b', 'FigB')] }
    c2.style = { id: 's1', snapshot: structuredClone(USER.data) }
    ;(c2.objects[0] as PanelObject).overrides = [{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 10 }]
    project.canvases.push(c2)
    await s().switchDocument(project, 'd_two')
    seedExactRender(panelById('a'), manifest('FigA') as never)
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 12 })
    s().undo()
    await whenLibraryIdle()
    s().switchCanvas('c2')
    rerender('b')
    expect(ov('b', 'axes_0.xlabel', 'fontsize')).toBe(12)
    expect(s().doc.style?.detached).toBeUndefined()
  })

  it('重做回到绑定状态；库此刻又变过的话照常跟上，进历史', async () => {
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 11 })
    rerenderAll()
    s().undo()
    rerenderAll()
    // 撤销之后样式在别处又改过
    useProfileStore.setState({
      styles: [record({ id: 's1', data: { element: { axis_label: { fontsize: 13 } }, pt_basis: 'page' } })],
    })
    expect(ov('a', 'axes_0.xlabel', 'fontsize'), '脱离期间不跟').toBe(10)
    s().redo()
    rerenderAll()
    expect(s().doc.style?.detached).toBeUndefined()
    expect(ov('a', 'axes_0.xlabel', 'fontsize'), '以库里此刻的内容为准').toBe(13)
    expect(s().past.at(-1)?.label).toMatchObject({ key: 'history.syncStyle' })
  })

  it('已脱离时重新选中同一套样式：恢复跟随，按库里此刻的内容对齐，一条历史', async () => {
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 12 })
    rerenderAll()
    s().undo()
    rerenderAll()
    const before = s().past.length
    bindCanvasStyle('s1')
    expect(s().past.length - before).toBe(1)
    expect(s().doc.style?.detached).toBeUndefined()
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(12)
  })

  it('已脱离、而库里恰好又回到了画布上的值：重新选中照样恢复跟随（不被「已经合样式」的空操作挡掉）', async () => {
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    await editBoundStyle({ kind: 'element', role: 'axis_label', prop: 'fontsize', value: 12 })
    rerenderAll()
    s().undo()
    rerenderAll()
    useProfileStore.setState({ styles: [USER] })
    bindCanvasStyle('s1')
    expect(s().doc.style).toEqual({ id: 's1', snapshot: USER.data })
  })

  it('撤销一次「按样式更新」（跟随库）也脱离：之后的编辑不会让它再跟回来', async () => {
    await seed([panel('a', 'FigA')])
    stop = startStyleBindingSync()
    bindCanvasStyle('s1')
    rerenderAll()
    useProfileStore.setState({
      styles: [record({ id: 's1', data: { element: { axis_label: { fontsize: 12 } }, pt_basis: 'page' } })],
    })
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(12)
    expect(s().past.at(-1)?.label).toMatchObject({ key: 'history.syncStyle' })
    s().undo()
    rerenderAll()
    expect(s().doc.style?.detached).toBe(true)
    unrelatedEdit()
    rerenderAll()
    followLibrary()
    expect(ov('a', 'axes_0.xlabel', 'fontsize')).toBe(10)
  })
})

/**
 * 嵌套组、组内编辑、剪切蒙版（ADR 0191）走真的 store 与真的快捷键：
 * ⌘G 把子组整体收进新组（成员挪成连续一段）、⇧⌘G 只拆一层；点谁选谁的单元、双击进组、Esc 一层层退；
 * 组里有锁定成员整组不动；⌘7 建蒙版（蒙版不画、写 hidden 给旧构建）、⌥⌘7 释放、删光被裁的对象蒙版还原；
 * 导出载荷带 `clip`、蒙版不进载荷；画布上被裁的对象外面包 clip-path、组内编辑时组外变淡；旧构建改过的链按一层读。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { useKeyboard } from '@/hooks/useKeyboard'
import { LayerObjects } from '@/canvas/CanvasStage'
import { groupPathOf } from '@/lib/groupTree'
import { toExportObjects } from '@/lib/exportPayload'
import { emptyProject, type CanvasObject } from '@/types/document'
import { deleteSelected, duplicateSelected, groupMates, movableTargets, selectAllIds, setObjectsHidden, toggleHidden, ungroupSelected } from './actions'
import { useDocumentStore } from './documentStore'
import { canMakeClipMask, canReleaseClipMask, enterGroupAt, groupCommand, makeClipMask, releaseClipMask } from './groupActions'
import { isolationScope, useIsolationStore } from './isolationStore'
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

function Host() {
  useKeyboard()
  return null
}
let root: Root
let host: HTMLDivElement
beforeAll(async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<Host />))
})
afterAll(async () => {
  await act(async () => root.unmount())
  host.remove()
})

const rect = (id: string, x: number, extra: Partial<CanvasObject> = {}): CanvasObject =>
  ({ id, type: 'shape', shape: 'rect', x, y: 10, w: 20, h: 20, strokePt: 1, color: '#000000', fill: '#ff0000', ...extra }) as CanvasObject
const ellipse = (id: string, x: number, extra: Partial<CanvasObject> = {}): CanvasObject =>
  rect(id, x, { shape: 'ellipse', ...extra } as Partial<CanvasObject>)
const text = (id: string, x: number): CanvasObject =>
  ({ id, type: 'text', x, y: 10, w: 20, h: 6, text: 'A', sizePt: 8, bold: false, color: '#000000', align: 'left' }) as CanvasObject

const objs = () => useDocumentStore.getState().doc.objects
const byId = (id: string) => objs().find((o) => o.id === id)!
const order = () => objs().map((o) => o.id)
const past = () => useDocumentStore.getState().past.length

async function seed(items: CanvasObject[], select: string[] = []) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_grp_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
  useIsolationStore.getState().clear()
  useSelectionStore.getState().set(select)
}

const key = (k: string, code: string, mods: { shift?: boolean; alt?: boolean; mod?: boolean } = {}) => {
  const ev = new KeyboardEvent('keydown', {
    bubbles: true,
    cancelable: true,
    key: k,
    code,
    ctrlKey: !!mods.mod,
    shiftKey: !!mods.shift,
    altKey: !!mods.alt,
  })
  act(() => {
    window.dispatchEvent(ev)
  })
  return ev.defaultPrevented
}
const cmdG = () => key('g', 'KeyG', { mod: true })
const cmdShiftG = () => key('G', 'KeyG', { mod: true, shift: true })
const cmd7 = () => key('7', 'Digit7', { mod: true })
const cmdAlt7 = () => key('¶', 'Digit7', { mod: true, alt: true })
const esc = () => key('Escape', 'Escape')

beforeEach(() => {
  useUiStore.setState({ tool: 'select', editingTextId: null, elementPanelId: null, cropTargetId: null })
})

describe('嵌套组', () => {
  it('⌘G：两个对象成组；再把这个组和第三个对象成组 = 子组整体成为成员（两层链）', async () => {
    await seed([rect('a', 0), rect('b', 30), rect('c', 60)], ['a', 'b'])
    expect(cmdG()).toBe(true)
    const inner = byId('a').groupId!
    expect(byId('b').groupId).toBe(inner)
    useSelectionStore.getState().set(['a', 'b', 'c'])
    expect(cmdG()).toBe(true)
    const outer = byId('c').groupId!
    expect(groupPathOf(byId('a'))).toEqual([outer, inner])
    expect(groupPathOf(byId('b'))).toEqual([outer, inner])
    expect(groupPathOf(byId('c'))).toEqual([outer])
    expect(past()).toBe(2)
  })

  it('⌘G 把成员挪成连续一段，落在最上面那个的位置', async () => {
    await seed([rect('a', 0), rect('x', 10), rect('b', 30), rect('y', 40)], ['a', 'b'])
    cmdG()
    expect(order()).toEqual(['x', 'a', 'b', 'y'])
  })

  it('只有一个单元（一整个组）时 ⌘G 不消费，也不进历史', async () => {
    await seed([rect('a', 0, { groupId: 'g' }), rect('b', 30, { groupId: 'g' })], ['a', 'b'])
    expect(groupCommand('group').available()).toBe(false)
    expect(cmdG()).toBe(false)
    expect(past()).toBe(0)
  })

  it('点谁选谁的单元：顶层 = 最外层组全员；双击进组后 = 子组 / 自己；Esc 一层层退出并选中退出的组', async () => {
    const items = [
      rect('a', 0, { groupId: 'g1', groupPath: ['g0', 'g1'] }),
      rect('b', 30, { groupId: 'g1', groupPath: ['g0', 'g1'] }),
      rect('c', 60, { groupId: 'g0' }),
      rect('d', 90),
    ]
    await seed(items)
    expect(groupMates('a')).toEqual(['a', 'b', 'c'])
    expect(groupMates('d')).toEqual(['d'])
    expect(enterGroupAt('a')).toBe(true)
    expect(isolationScope()).toBe('g0')
    expect(useSelectionStore.getState().ids).toEqual(['a', 'b'])
    expect(groupMates('a')).toEqual(['a', 'b'])
    expect(groupMates('c')).toEqual(['c'])
    expect(enterGroupAt('a')).toBe(true)
    expect(isolationScope()).toBe('g1')
    expect(groupMates('a')).toEqual(['a'])
    // 已经是直接成员：双击交给对象自己（改字 / 编辑锚点…）
    expect(enterGroupAt('a')).toBe(false)
    expect(esc()).toBe(true)
    expect(isolationScope()).toBe('g0')
    expect(useSelectionStore.getState().ids).toEqual(['a', 'b'])
    esc()
    expect(isolationScope()).toBeNull()
    expect(useSelectionStore.getState().ids).toEqual(['a', 'b', 'c'])
    // 顶层 Esc 照旧清选区
    esc()
    expect(useSelectionStore.getState().ids).toEqual([])
  })

  it('组内编辑里 ⌘G：新组挂在当前这一层下面', async () => {
    await seed([rect('a', 0, { groupId: 'g0' }), rect('b', 30, { groupId: 'g0' }), rect('c', 60, { groupId: 'g0' })])
    enterGroupAt('a')
    useSelectionStore.getState().set(['a', 'b'])
    cmdG()
    const g = byId('a').groupId!
    expect(groupPathOf(byId('a'))).toEqual(['g0', g])
    expect(groupPathOf(byId('c'))).toEqual(['g0'])
  })

  it('⇧⌘G 只拆一层：外层组没了，子组还在', async () => {
    await seed(
      [rect('a', 0, { groupId: 'g1', groupPath: ['g0', 'g1'] }), rect('b', 30, { groupId: 'g1', groupPath: ['g0', 'g1'] }), rect('c', 60, { groupId: 'g0' })],
      ['a', 'b', 'c'],
    )
    expect(cmdShiftG()).toBe(true)
    expect(groupPathOf(byId('a'))).toEqual(['g1'])
    expect('groupPath' in byId('a')).toBe(false)
    expect(groupPathOf(byId('c'))).toEqual([])
  })

  it('组里任一成员锁定：整组不动（嵌套组按当前这一层的单元算）', async () => {
    await seed([
      rect('a', 0, { groupId: 'g1', groupPath: ['g0', 'g1'] }),
      rect('b', 30, { groupId: 'g1', groupPath: ['g0', 'g1'], locked: true }),
      rect('c', 60, { groupId: 'g0' }),
    ])
    expect(movableTargets(['c'])).toMatchObject({ objects: [], blockedGroups: 1 })
    enterGroupAt('c')
    expect(movableTargets(['c']).objects.map((o) => o.id)).toEqual(['c'])
    expect(movableTargets(['a']).blockedGroups).toBe(1)
  })

  it('复制嵌套组：副本整条链换新 id，不和原件粘在一起', async () => {
    await seed([rect('a', 0, { groupId: 'g1', groupPath: ['g0', 'g1'] }), rect('c', 60, { groupId: 'g0' })], ['a', 'c'])
    duplicateSelected()
    const [ca, cc] = objs().slice(2)
    const pa = groupPathOf(ca)
    expect(pa).toHaveLength(2)
    expect(pa).not.toContain('g0')
    expect(groupPathOf(cc)).toEqual([pa[0]])
  })

  it('组内编辑里复制成员：副本仍留在正在编辑的组里；子组才换新 id（Codex 线程 4614452885）', async () => {
    await seed(
      [
        rect('a', 0, { groupId: 'g1', groupPath: ['g0', 'g1'] }),
        rect('b', 30, { groupId: 'g1', groupPath: ['g0', 'g1'] }),
        rect('c', 60, { groupId: 'g0' }),
      ],
      [],
    )
    enterGroupAt('c')
    useSelectionStore.getState().set(['c'])
    duplicateSelected()
    expect(groupPathOf(objs()[3])).toEqual(['g0'])
    // 复制里面的子组：外层 g0 保留，g1 换新
    useSelectionStore.getState().set(['a', 'b'])
    duplicateSelected()
    const [ca, cb] = objs().slice(4)
    expect(groupPathOf(ca)[0]).toBe('g0')
    expect(groupPathOf(ca)[1]).not.toBe('g1')
    expect(groupPathOf(cb)).toEqual(groupPathOf(ca))
  })

  it('旧构建改过 groupId（链对不上）：按一层组读，对象一个不丢', async () => {
    await seed([rect('a', 0, { groupId: 'gOld', groupPath: ['g0', 'g1'] }), rect('b', 30, { groupId: 'gOld' })])
    expect(groupMates('a')).toEqual(['a', 'b'])
  })
})

describe('剪切蒙版', () => {
  const setup = () => seed([rect('img', 0, { w: 60, h: 60 }), text('t', 5), ellipse('m', 10, { w: 30, h: 30 })], ['img', 't', 'm'])

  it('⌘7：成一个新组，最上面的椭圆成剪切路径（写 hidden 给旧构建），一条历史；⌥⌘7 释放', async () => {
    await setup()
    expect(canMakeClipMask()).toBe(true)
    expect(cmd7()).toBe(true)
    const g = byId('m').groupId!
    expect(byId('img').groupId).toBe(g)
    expect(byId('m')).toMatchObject({ clipMask: true, hidden: true })
    expect(past()).toBe(1)
    // 导出：蒙版不画，被裁的两个都带同一条轮廓
    const out = toExportObjects(objs())
    expect(out.map((o) => o.type)).toEqual(['shape', 'text'])
    expect(out.every((o) => o.clip?.length === 1 && o.clip[0].type === 'path')).toBe(true)
    useSelectionStore.getState().set(['img', 't', 'm'])
    expect(cmdAlt7()).toBe(true)
    expect(byId('m').clipMask).toBeUndefined()
    expect(byId('m').hidden).toBeUndefined()
    expect(byId('m').groupId).toBe(g) // 组还在
    expect(toExportObjects(objs()).every((o) => !o.clip)).toBe(true)
    // 撤销回到蒙版
    useDocumentStore.getState().undo()
    expect(byId('m').clipMask).toBe(true)
  })

  it('最上面不是路径 / 封闭形状：不建，说一句；⌘7 不消费', async () => {
    await seed([rect('a', 0), text('t', 5)], ['a', 't'])
    expect(canMakeClipMask()).toBe(false)
    expect(cmd7()).toBe(false)
    makeClipMask()
    expect(past()).toBe(0)
    expect(byId('a').groupId).toBeUndefined()
  })

  it('嵌套：外层剪切组里再套一个剪切组，被裁的对象带两条轮廓（外 → 内）', async () => {
    await setup()
    cmd7()
    useSelectionStore.getState().set(['img', 't', 'm'])
    // 再加一个外层蒙版
    useDocumentStore.getState().commit(literal('加'), (d) => {
      d.objects.push(rect('m2', 0, { w: 15, h: 60 }))
    })
    useSelectionStore.getState().set([...groupMates('img'), 'm2'])
    makeClipMask()
    const img = toExportObjects(objs()).find((o) => o.type === 'shape')!
    expect(img.clip).toHaveLength(2)
    // 外层的 m2 在外（形状换成路径时按描边内缩：15 mm 宽减去 1 pt）
    expect(img.clip![0].w_mm).toBeCloseTo(15 - 25.4 / 72, 6)
  })

  it('取消成组 = 释放蒙版；删光被裁的对象 = 蒙版还原成可见对象', async () => {
    await setup()
    cmd7()
    ungroupSelected()
    expect(byId('m')).not.toHaveProperty('clipMask')
    expect(byId('m')).not.toHaveProperty('hidden')
    await setup()
    cmd7()
    useSelectionStore.getState().set(['img', 't'])
    deleteSelected()
    expect(objs().map((o) => o.id)).toEqual(['m'])
    expect(byId('m')).not.toHaveProperty('clipMask')
    expect(byId('m')).not.toHaveProperty('hidden')
  })

  it('图层树上隐藏剪切路径 = 暂时不裁（clipOff）；hidden 不动；批量显示不碰蒙版；释放时一起清掉', async () => {
    await setup()
    cmd7()
    toggleHidden('m')
    expect(byId('m')).toMatchObject({ clipMask: true, clipOff: true, hidden: true })
    let out = toExportObjects(objs())
    expect(out.map((o) => o.type)).toEqual(['shape', 'text']) // 蒙版仍不画
    expect(out.every((o) => !o.clip)).toBe(true) // 内容全露出来
    setObjectsHidden(['img', 't', 'm'], false)
    expect(byId('m').hidden).toBe(true)
    toggleHidden('m')
    expect(byId('m').clipOff).toBeUndefined()
    out = toExportObjects(objs())
    expect(out.every((o) => o.clip?.length === 1)).toBe(true)
    toggleHidden('m')
    useSelectionStore.getState().set(['img', 't', 'm'])
    expect(canReleaseClipMask()).toBe(true) // 暂时不裁的也能释放
    releaseClipMask()
    expect(byId('m')).not.toHaveProperty('clipOff')
    expect(byId('m')).not.toHaveProperty('hidden')
  })

  it('⌘A 把剪切组的蒙版一起选上：⌘A + Delete 删干净，不冒出一个还原的形状', async () => {
    await setup()
    cmd7()
    useSelectionStore.getState().set(selectAllIds(objs()))
    expect(useSelectionStore.getState().ids).toContain('m')
    deleteSelected()
    expect(objs()).toHaveLength(0)
  })

  it('孤立蒙版还原时，组里只剩它：这一层组也拿掉', async () => {
    await setup()
    cmd7()
    useSelectionStore.getState().set(['img', 't'])
    deleteSelected()
    expect(byId('m').groupId).toBeUndefined()
  })

  it('释放：在剪切组里面编辑时也能释放这一层', async () => {
    await setup()
    cmd7()
    enterGroupAt('img')
    useSelectionStore.getState().set(['img'])
    releaseClipMask()
    expect(byId('m').clipMask).toBeUndefined()
  })
})

describe('画布', () => {
  it('被裁的对象外面包 clip-path、蒙版不画；组内编辑时组外的对象变淡', async () => {
    await seed([rect('img', 0, { w: 60, h: 60 }), ellipse('m', 10, { w: 30, h: 30 }), rect('out', 100)], ['img', 'm'])
    cmd7()
    const el = document.createElement('div')
    document.body.appendChild(el)
    const r = createRoot(el)
    await act(async () => r.render(<LayerObjects objects={objs()} isolation={null} />))
    expect(el.querySelector('[data-object-id="m"]')).toBeNull()
    const wrap = el.querySelector<HTMLElement>('[data-clip-by="m"]')!
    expect(wrap.querySelector('[data-object-id="img"]')).not.toBeNull()
    expect(wrap.style.clipPath).toMatch(/^path\(nonzero, ['"]M /)
    expect(el.querySelector('[data-isolation-outside]')).toBeNull()
    const g = byId('img').groupId!
    await act(async () => r.render(<LayerObjects objects={objs()} isolation={g} />))
    const outside = el.querySelectorAll('[data-isolation-outside]')
    expect([...outside].map((n) => n.querySelector('[data-object-id]')?.getAttribute('data-object-id'))).toEqual(['out'])
    await act(async () => r.unmount())
    el.remove()
  })

  it('旧构建把蒙版取消了隐藏（hidden 没了、clipMask 还在）：照样不画、不导出，只拿来裁', async () => {
    await seed([rect('img', 0, { w: 60, h: 60, groupId: 'g' }), ellipse('m', 10, { w: 30, h: 30, groupId: 'g', clipMask: true })])
    const el = document.createElement('div')
    document.body.appendChild(el)
    const r = createRoot(el)
    await act(async () => r.render(<LayerObjects objects={objs()} isolation={null} />))
    expect(el.querySelector('[data-object-id="m"]')).toBeNull()
    expect(el.querySelector('[data-clip-by="m"] [data-object-id="img"]')).not.toBeNull()
    expect(toExportObjects(objs()).map((o) => o.type)).toEqual(['shape'])
    await act(async () => r.unmount())
    el.remove()
  })
})

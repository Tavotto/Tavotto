/**
 * 画布多选排列的 action 层（Prompt 17 补的三件事）：
 *   - 锁定对象 / 含锁定成员的组不动，但仍算进选区参照框；
 *   - 离散动作先收掉开着的连续手势，自己是独立的一条历史；
 *   - 完成后发一声本地活动信号（无用户内容）。
 * 参照语义（选区 / 画布 / 主选）与主选 = 末位 id 也在这里钉住。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { ACTIVITY_EVENT, onActivity, type ActivityDetail } from '@/lib/activity'
import {
  alignSelectedTo,
  groupSelected,
  selectionHasGroup,
  selectionHasGroupIn,
  ungroupSelected,
} from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { hasActiveGesture, registerGesture } from '@/store/gestureCoordinator'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type CanvasObject, type TextObject } from '@/types/document'

const text = (id: string, over: Partial<TextObject>): TextObject =>
  ({
    id,
    type: 'text',
    text: id,
    sizePt: 10,
    bold: false,
    color: '#000000',
    align: 'left',
    x: 0,
    y: 0,
    w: 10,
    h: 5,
    ...over,
  }) as TextObject

const objs = () => useDocumentStore.getState().doc.objects
const byId = (id: string) => objs().find((o) => o.id === id)!
const past = () => useDocumentStore.getState().past

async function seed(items: CanvasObject[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_align_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
}

const three = () => [
  text('t1', { x: 10, y: 20, w: 30, h: 8 }),
  text('t2', { x: 50, y: 40, w: 20, h: 8 }),
  text('t3', { x: 90, y: 60, w: 10, h: 8 }),
]

beforeEach(async () => {
  useUiStore.setState({ status: null })
  await seed(three())
  useSelectionStore.getState().set(['t1', 't2', 't3'])
})

afterEach(() => {
  useSelectionStore.getState().clear()
})

describe('参照语义', () => {
  it('选区：左对齐把所有对象的 x 收到选区最左；一条历史，标签是 alignWithRef', () => {
    alignSelectedTo('left', 'selection')
    expect(objs().map((o) => o.x)).toEqual([10, 10, 10])
    expect(past()).toHaveLength(1)
    expect(past()[0].label.key).toBe('history.alignWithRef')
  })
  it('画布：右对齐贴到页宽', () => {
    const pw = useDocumentStore.getState().doc.page.w
    alignSelectedTo('right', 'page')
    expect(objs().map((o) => o.x + o.w)).toEqual([pw, pw, pw])
  })
  it('主选 = 选区末位 id：它自己不动，其余对齐到它', () => {
    alignSelectedTo('left', 'primary')
    expect(byId('t3').x).toBe(90)
    expect(byId('t1').x).toBe(90)
    expect(byId('t2').x).toBe(90)
  })
  it('主选跟 ids 顺序走，不跟对象顺序走', () => {
    useSelectionStore.getState().set(['t3', 't2', 't1'])
    alignSelectedTo('left', 'primary')
    expect(byId('t1').x).toBe(10)
    expect(byId('t3').x).toBe(10)
  })
  it('等宽（主选）：其余取主选的宽，主选不动', () => {
    alignSelectedTo('samew', 'primary')
    expect(objs().map((o) => o.w)).toEqual([10, 10, 10])
  })
  it('等高（主选）：文字高度由内容决定，等高对它不生效', () => {
    useDocumentStore.getState().commit(literal('改高'), (d) => {
      const t = d.objects.find((o) => o.id === 't3')!
      t.h = 20
    })
    alignSelectedTo('sameh', 'primary')
    expect(byId('t1').h).toBe(8)
  })
  it('分布：两个对象拒绝并提示；三个对象等距', () => {
    useSelectionStore.getState().set(['t1', 't2'])
    alignSelectedTo('hdist', 'selection')
    expect(past()).toHaveLength(0)
    expect(useUiStore.getState().status?.key).toBe('status.needThreeForDistribute')
    useSelectionStore.getState().set(['t1', 't2', 't3'])
    alignSelectedTo('hdist', 'selection')
    const sorted = objs().slice().sort((a, b) => a.x - b.x)
    const gaps = [sorted[1].x - (sorted[0].x + sorted[0].w), sorted[2].x - (sorted[1].x + sorted[1].w)]
    expect(gaps[0]).toBeCloseTo(gaps[1])
    expect(sorted[0].x).toBe(10)
    expect(sorted[2].x + sorted[2].w).toBe(100)
  })
})

describe('锁定与成组', () => {
  it('锁定对象不动，但仍算进选区参照框；提示跳过了几个', async () => {
    await seed([
      text('t1', { x: 0, y: 0, w: 10, h: 5, locked: true }),
      text('t2', { x: 50, y: 40, w: 20, h: 8 }),
      text('t3', { x: 90, y: 60, w: 10, h: 8 }),
    ])
    useSelectionStore.getState().set(['t1', 't2', 't3'])
    alignSelectedTo('left', 'selection')
    expect(byId('t1').x).toBe(0)
    // 参照框含锁定的 t1（最左 = 0），其余贴到 0 而不是 50
    expect(byId('t2').x).toBe(0)
    expect(byId('t3').x).toBe(0)
    expect(past()).toHaveLength(1)
    expect(useUiStore.getState().status?.key).toBe('status.alignLockedSkipped')
    expect(useUiStore.getState().status?.values).toEqual({ count: 1 })
  })
  it('含锁定成员的组整组不动（与拖动同一判据）', async () => {
    await seed([
      text('t1', { x: 10, y: 0, w: 10, h: 5, locked: true, groupId: 'g1' }),
      text('t2', { x: 50, y: 40, w: 20, h: 8, groupId: 'g1' }),
      text('t3', { x: 90, y: 60, w: 10, h: 8 }),
    ])
    useSelectionStore.getState().set(['t1', 't2', 't3'])
    alignSelectedTo('left', 'selection')
    expect(byId('t2').x).toBe(50)
    expect(byId('t3').x).toBe(10)
  })
  it('全部锁定：不进历史，提示先解锁', async () => {
    await seed([
      text('t1', { x: 10, y: 0, w: 10, h: 5, locked: true }),
      text('t2', { x: 50, y: 40, w: 20, h: 8, locked: true }),
    ])
    useSelectionStore.getState().set(['t1', 't2'])
    alignSelectedTo('left', 'selection')
    expect(past()).toHaveLength(0)
    expect(useUiStore.getState().status?.key).toBe('status.alignAllLocked')
  })
  it('成组 / 取消成组各一条历史；selectionHasGroupIn 与 selectionHasGroup 同一判据', () => {
    expect(selectionHasGroup()).toBe(false)
    groupSelected()
    const gid = byId('t1').groupId
    expect(gid).toBeTruthy()
    expect(objs().every((o) => o.groupId === gid)).toBe(true)
    expect(selectionHasGroup()).toBe(true)
    expect(selectionHasGroupIn(objs())).toBe(true)
    ungroupSelected()
    expect(objs().every((o) => o.groupId === undefined)).toBe(true)
    expect(selectionHasGroupIn(objs())).toBe(false)
    expect(past()).toHaveLength(2)
    expect(past().map((p) => p.label.key)).toEqual(['history.group', 'history.ungroup'])
  })
})

describe('离散动作与连续手势', () => {
  it('开着的手势先被收掉：对齐是独立的一条历史，不并进上一条', () => {
    const store = useDocumentStore.getState()
    store.beginTxn(literal('改字号'))
    store.txnUpdate((d) => {
      const t = d.objects.find((o) => o.id === 't1')!
      if (t.type === 'text') t.sizePt = 12
    })
    const unregister = registerGesture(() => useDocumentStore.getState().endTxn())
    expect(hasActiveGesture()).toBe(true)
    alignSelectedTo('left', 'selection')
    expect(hasActiveGesture()).toBe(false)
    expect(past().map((p) => p.label.key)).toEqual(['literal', 'history.alignWithRef'])
    unregister()
  })
  it('成组同样先收手势', () => {
    const store = useDocumentStore.getState()
    store.beginTxn(literal('改字号'))
    store.txnUpdate((d) => {
      const t = d.objects.find((o) => o.id === 't1')!
      if (t.type === 'text') t.sizePt = 12
    })
    registerGesture(() => useDocumentStore.getState().endTxn())
    groupSelected()
    expect(past()).toHaveLength(2)
    expect(past()[1].label.key).toBe('history.group')
  })
})

describe('本地活动信号', () => {
  it('对齐 / 成组 / 取消成组各发一次，detail 只有枚举与计数', () => {
    const got: ActivityDetail[] = []
    // Session 21 起总线上还有 history.pushed / selection.changed 这类通用信号：
    // 这条用例守的是排列三件事**各发一次**，只看它们自己那三种 kind
    const off = onActivity((d) => {
      if (d.kind.startsWith('selection.') && d.kind !== 'selection.changed') got.push(d)
    })
    alignSelectedTo('top', 'page')
    groupSelected()
    ungroupSelected()
    off()
    alignSelectedTo('left', 'selection')
    expect(got).toEqual([
      { kind: 'selection.aligned', mode: 'top', ref: 'page', count: 3 },
      { kind: 'selection.grouped', count: 3 },
      { kind: 'selection.ungrouped', count: 3 },
    ])
    for (const d of got) expect(JSON.stringify(d)).not.toMatch(/t1|t2|t3/)
  })
  it('分布被拒绝（两个对象）时不发信号', () => {
    const got: ActivityDetail[] = []
    const handler = (e: Event) => got.push((e as CustomEvent<ActivityDetail>).detail)
    // 选区先摆好再监听：`set` 自己会发一声 selection.changed，那不是被测的对齐
    useSelectionStore.getState().set(['t1', 't2'])
    window.addEventListener(ACTIVITY_EVENT, handler)
    alignSelectedTo('vdist', 'selection')
    window.removeEventListener(ACTIVITY_EVENT, handler)
    expect(got).toEqual([])
  })
  it('监听者抛错不影响动作本身', () => {
    const off = onActivity(() => {
      throw new Error('boom')
    })
    // jsdom 把监听者的异常报到 window.onerror，不会打断派发方
    const errors: unknown[] = []
    const onErr = (e: ErrorEvent) => {
      errors.push(e.error)
      e.preventDefault()
    }
    window.addEventListener('error', onErr)
    alignSelectedTo('left', 'selection')
    window.removeEventListener('error', onErr)
    off()
    expect(objs().map((o) => o.x)).toEqual([10, 10, 10])
    expect(past()).toHaveLength(1)
  })
})

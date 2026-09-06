/**
 * 类型切换写进文档那一半（cap-shape-switch）：一次 commit、一条历史、撤销回到
 * 原类型且字段逐字回来、**不换 id**（选择 / 成组 / 布局组 / 锁定都不丢）、
 * 数组位置不动（数组序即 z 序）。
 *
 * 判据的主语：每一条问的都是**文档里那个对象**或**历史栈**，不是「函数被调了
 * 几次」。切换成什么样是 `lib/shapeSwitch.test.ts` 的事，这里不重复那一层。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { switchObjectKind } from '@/store/actions'
import { finishActiveGesture, hasActiveGesture, registerGesture } from '@/store/gestureCoordinator'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import {
  emptyProject,
  type ArrowObject,
  type CanvasObject,
  type LayoutGroup,
  type ShapeObject,
  type TextObject,
} from '@/types/document'

const shape = (id: string, over: Partial<ShapeObject> = {}): ShapeObject => ({
  id,
  type: 'shape',
  shape: 'rect',
  x: 10,
  y: 20,
  w: 40,
  h: 30,
  strokePt: 1.75,
  color: '#112233',
  fill: '#FFEE88',
  ...over,
})

const arrow = (id: string, over: Partial<ArrowObject> = {}): ArrowObject => ({
  id,
  type: 'arrow',
  x: 10,
  y: 20,
  w: 40,
  h: 30,
  start: { rx: 0.2, ry: 0.3 },
  end: { rx: 0.8, ry: 0.7 },
  strokePt: 1.75,
  color: '#112233',
  head: 'end',
  ...over,
})

const text = (id: string): TextObject => ({
  id,
  type: 'text',
  x: 0,
  y: 0,
  w: 20,
  h: 8,
  text: id,
  sizePt: 9,
  bold: false,
  color: '#000000',
  align: 'left',
})

const objs = () => useDocumentStore.getState().doc.objects
const byId = <T extends CanvasObject = CanvasObject>(id: string) => objs().find((o) => o.id === id) as T
const past = () => useDocumentStore.getState().past
const undo = () => useDocumentStore.getState().undo()

async function seed(items: CanvasObject[], groups?: LayoutGroup[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_kindact_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(...items)
    if (groups) d.layoutGroups = groups
  })
  useDocumentStore.setState({ past: [], future: [] })
}

beforeEach(() => {
  useSelectionStore.getState().clear()
  // 上一条用例登记的手势不能漏到下一条：`hasActiveGesture` 是模块级单例
  finishActiveGesture()
})

describe('一条 commit、一条历史', () => {
  it('单个：一条历史，标签带类型名', async () => {
    await seed([shape('s1')])
    switchObjectKind(['s1'], 'ellipse')
    expect(past()).toHaveLength(1)
    expect(past()[0].label).toMatchObject({ key: 'history.switchKind', values: { name: '椭圆' } })
  })

  it('多个：还是一条历史（不是每个对象一条）', async () => {
    await seed([shape('s1'), shape('s2'), shape('s3')])
    switchObjectKind(['s1', 's2', 's3'], 'diamond')
    expect(past()).toHaveLength(1)
    expect(objs().map((o) => (o as ShapeObject).shape)).toEqual(['diamond', 'diamond', 'diamond'])
  })

  it('目标就是当前类型：不进历史', async () => {
    await seed([shape('s1')])
    switchObjectKind(['s1'], 'rect')
    expect(past()).toHaveLength(0)
  })

  it('空 ids：不进历史', async () => {
    await seed([shape('s1')])
    switchObjectKind([], 'ellipse')
    expect(past()).toHaveLength(0)
  })

  /**
   * 「不进历史」那一条**杀不死**早退（`commit` 拿到空补丁集自己也会早退，同一条
   * 保证实现了两遍）。早退自己那份职责在这里：一次什么都没发生的点击不该收掉
   * 用户正开着的那一轮连续编辑——收掉了的话，正在拖的滑杆会当场定稿，下一次
   * 拖动变成第二条历史。
   */
  it('空操作不打断进行中的手势；真的切了才收掉它', async () => {
    await seed([shape('s1')])
    const finish = vi.fn()
    registerGesture(finish)
    switchObjectKind(['s1'], 'rect') // 就是当前类型 = 什么都没发生
    expect(hasActiveGesture()).toBe(true)
    expect(finish).not.toHaveBeenCalled()
    switchObjectKind(['s1'], 'ellipse')
    expect(finish).toHaveBeenCalledTimes(1)
    expect(hasActiveGesture()).toBe(false)
  })
})

describe('与两个入口同一条判据：不同族 / 有不参与的对象 = 整个不做', () => {
  it('矩形 + 箭头切成椭圆：两个都不动（不半切一半）', async () => {
    await seed([shape('s1'), arrow('a1')])
    switchObjectKind(['s1', 'a1'], 'ellipse')
    expect(byId<ShapeObject>('s1').shape).toBe('rect')
    expect(byId('a1').type).toBe('arrow')
    expect(past()).toHaveLength(0)
  })

  it('形状 + 文字切成椭圆：形状也不动', async () => {
    await seed([shape('s1'), text('t1')])
    switchObjectKind(['s1', 't1'], 'ellipse')
    expect(byId<ShapeObject>('s1').shape).toBe('rect')
    expect(past()).toHaveLength(0)
  })

  it('箭头切成矩形（跨族）：不动', async () => {
    await seed([arrow('a1')])
    switchObjectKind(['a1'], 'rect')
    expect(byId('a1').type).toBe('arrow')
    expect(past()).toHaveLength(0)
  })
})

describe('不换 id：选择 / 成组 / 布局组 / 锁定都不丢', () => {
  it('id 与数组位置都不动（数组序即 z 序，不能顺手提到最上层）', async () => {
    await seed([shape('bottom'), shape('mid'), shape('top')])
    switchObjectKind(['mid'], 'triangle')
    expect(objs().map((o) => o.id)).toEqual(['bottom', 'mid', 'top'])
    expect(byId<ShapeObject>('mid').shape).toBe('triangle')
  })

  it('选区不动', async () => {
    await seed([shape('s1'), shape('s2')])
    useSelectionStore.getState().set(['s1', 's2'])
    switchObjectKind(['s1', 's2'], 'brace')
    expect(useSelectionStore.getState().ids).toEqual(['s1', 's2'])
  })

  it('成组、锁定、隐藏、名字、布局固定一样不少', async () => {
    await seed([
      shape('s1', {
        groupId: 'G',
        locked: true,
        hidden: true,
        name: '我起的名字',
        layoutPinned: true,
        rotationDeg: 30,
      }),
      shape('s2', { groupId: 'G' }),
    ])
    switchObjectKind(['s1'], 'polygon')
    const after = byId('s1')
    expect(after.groupId).toBe('G')
    expect(after.locked).toBe(true)
    expect(after.hidden).toBe(true)
    expect(after.name).toBe('我起的名字')
    expect(after.layoutPinned).toBe(true)
    expect(after.rotationDeg).toBe(30)
  })

  it('布局组的 order 记的是 id：切换之后成员仍然在册', async () => {
    await seed(
      [shape('s1', { groupId: 'G' }), shape('s2', { groupId: 'G' })],
      [{ id: 'G', kind: 'row', order: ['s1', 's2'], gap: 4, align: 'start' }],
    )
    switchObjectKind(['s1', 's2'], 'ellipse')
    const g = useDocumentStore.getState().doc.layoutGroups![0]
    expect(g.order).toEqual(['s1', 's2'])
    expect(objs().filter((o) => o.groupId === 'G')).toHaveLength(2)
  })
})

describe('撤销：回到原类型，字段逐字回来', () => {
  it('矩形的圆角、多边形的边数都原样回来', async () => {
    await seed([shape('s1', { cornerRadius: 4.5 })])
    const before = byId<ShapeObject>('s1')
    switchObjectKind(['s1'], 'polygon')
    expect(byId<ShapeObject>('s1').sides).toBe(6)
    undo()
    expect(byId<ShapeObject>('s1')).toEqual(before)
  })

  it('箭头的端型原样回来（切成直线时被删掉的那三个字段）', async () => {
    await seed([arrow('a1', { headStart: 'bar', headEnd: 'open', dash: 'dotted' })])
    const before = byId<ArrowObject>('a1')
    switchObjectKind(['a1'], 'line')
    expect(byId('a1').type).toBe('shape')
    undo()
    expect(byId<ArrowObject>('a1')).toEqual(before)
  })

  it('多选切换只用撤销一次', async () => {
    await seed([shape('s1'), shape('s2')])
    switchObjectKind(['s1', 's2'], 'brace')
    undo()
    expect(objs().map((o) => (o as ShapeObject).shape)).toEqual(['rect', 'rect'])
  })
})

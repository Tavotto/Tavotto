/**
 * 类型切换的模型层（cap-shape-switch）。
 *
 * 这里只测纯函数——「能切成什么」「切完长什么样」。写进文档那一半（一次 commit、
 * 一条历史、撤销回到原类型、id 不换）在 `store/shapeSwitchActions.test.ts`，
 * 两个入口的界面在 `components/inspector/objectKindSwitch.test.tsx` 与
 * `canvas/objectContextMenu.test.tsx`。
 *
 * 判据的主语说在前面：**每一条断言问的都是「切换之后那个对象」的某个字段**，
 * 不是「函数有没有被调用」，也不是「界面上是不是出现了那个词」。
 */
import { describe, expect, it } from 'vitest'
import {
  DEFAULT_SIDES,
  SHARED_STYLE_FIELDS,
  SWITCH_FAMILY_KINDS,
  sharedSwitchKind,
  switchFamilyOf,
  switchKindOf,
  switchObject,
  switchTargets,
  type SwitchKind,
} from './shapeSwitch'
import { buildPreset, PRESET_IDS, type PresetId } from './presets'
import type {
  ArrowObject,
  CanvasObject,
  PanelObject,
  ShapeKind,
  ShapeObject,
  TextObject,
} from '@/types/document'

/* -------------------------------------------------------------------------- */
/*  样品：几何 / 共有样式 / 跟对象走的那一组全都摆上非默认值                        */
/* -------------------------------------------------------------------------- */

/**
 * **每个字段都给一个「不是默认值」的取值**：断言「原样保留」时，如果样品填的
 * 正好是缺省，实现里把它删掉了也照样绿——那条用例证明不了任何事。
 */
const BOX_GEOMETRY = { x: 12.5, y: 7.25, w: 40, h: 30 } as const

const shape = (kind: ShapeKind, over: Partial<ShapeObject> = {}): ShapeObject => ({
  id: 's1',
  type: 'shape',
  shape: kind,
  ...BOX_GEOMETRY,
  strokePt: 2.25,
  color: '#3366CC',
  fill: '#FFEE88',
  dash: 'dashed',
  rotationDeg: 37,
  name: '我起的名字',
  groupId: 'g1',
  locked: true,
  hidden: true,
  layoutPinned: true,
  ...over,
})

const arrow = (over: Partial<ArrowObject> = {}): ArrowObject => ({
  id: 'a1',
  type: 'arrow',
  ...BOX_GEOMETRY,
  start: { rx: 0.1, ry: 0.2 },
  end: { rx: 0.9, ry: 0.8 },
  strokePt: 2.25,
  color: '#3366CC',
  dash: 'dashed',
  rotationDeg: 37,
  name: '我起的名字',
  groupId: 'g1',
  locked: true,
  hidden: true,
  layoutPinned: true,
  head: 'both',
  headStart: 'bar',
  headEnd: 'open',
  ...over,
})

const text = (): TextObject => ({
  id: 't1',
  type: 'text',
  ...BOX_GEOMETRY,
  text: '标题',
  sizePt: 9,
  bold: false,
  color: '#000000',
  align: 'left',
})

const panel = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    ...BOX_GEOMETRY,
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW: 40,
    nativeH: 30,
    overrides: [],
  }) as unknown as PanelObject

/** 某个类型的样品（当前类型 = kind） */
const sample = (kind: SwitchKind): CanvasObject => (kind === 'arrow' ? arrow() : shape(kind))

const BOX: readonly SwitchKind[] = SWITCH_FAMILY_KINDS.box
const LINEAR: readonly SwitchKind[] = SWITCH_FAMILY_KINDS.linear
const ALL: readonly SwitchKind[] = [...BOX, ...LINEAR]

/** 一对「源 → 目标」（同族、互不相同）的全集 */
const PAIRS: [SwitchKind, SwitchKind][] = [BOX, LINEAR].flatMap((family) =>
  family.flatMap((from) => family.filter((to) => to !== from).map((to) => [from, to] as [SwitchKind, SwitchKind])),
)

/* -------------------------------------------------------------------------- */
/*  族与目标集合                                                                */
/* -------------------------------------------------------------------------- */

describe('族的划分与它的两张表一致', () => {
  it('每一种类型都在自己族的清单里，且只在一个清单里', () => {
    for (const kind of ALL) {
      const family = switchFamilyOf(kind)
      expect(SWITCH_FAMILY_KINDS[family]).toContain(kind)
      const other = family === 'box' ? SWITCH_FAMILY_KINDS.linear : SWITCH_FAMILY_KINDS.box
      expect(other).not.toContain(kind)
    }
  })

  it('清单里的每一种都认得出自己的族（反方向）', () => {
    for (const family of ['box', 'linear'] as const) {
      for (const kind of SWITCH_FAMILY_KINDS[family]) {
        expect(switchFamilyOf(kind)).toBe(family)
      }
    }
  })

  it('直线与箭头是同一族，矩形与它们不同族', () => {
    expect(switchFamilyOf('line')).toBe(switchFamilyOf('arrow'))
    expect(switchFamilyOf('rect')).not.toBe(switchFamilyOf('line'))
  })
})

describe('switchKindOf：谁参与切换', () => {
  it('形状报自己的 shape，箭头报 arrow', () => {
    expect(switchKindOf(shape('diamond'))).toBe('diamond')
    expect(switchKindOf(arrow())).toBe('arrow')
  })

  it('文字与面板不参与', () => {
    expect(switchKindOf(text())).toBeNull()
    expect(switchKindOf(panel())).toBeNull()
  })
})

describe('switchTargets：能切成什么', () => {
  it('单个对象给出它那一族的完整清单', () => {
    expect(switchTargets([shape('rect')])).toEqual(BOX)
    expect(switchTargets([arrow()])).toEqual(LINEAR)
    expect(switchTargets([shape('line')])).toEqual(LINEAR)
  })

  it('多选全部同族：照常给（矩形 + 椭圆）', () => {
    expect(switchTargets([shape('rect', { id: 'a' }), shape('ellipse', { id: 'b' })])).toEqual(BOX)
  })

  it('多选跨族：一个都不给', () => {
    expect(switchTargets([shape('rect'), arrow()])).toEqual([])
    expect(switchTargets([shape('rect'), shape('line')])).toEqual([])
  })

  it('选区里有文字或面板：一个都不给', () => {
    expect(switchTargets([shape('rect'), text()])).toEqual([])
    expect(switchTargets([arrow(), panel()])).toEqual([])
    expect(switchTargets([text()])).toEqual([])
  })

  it('空选区不给', () => {
    expect(switchTargets([])).toEqual([])
  })
})

describe('sharedSwitchKind：当前是哪一种', () => {
  it('取值一致时报那一种', () => {
    expect(sharedSwitchKind([shape('brace', { id: 'a' }), shape('brace', { id: 'b' })])).toBe('brace')
  })

  it('同族但取值不同 = 没有答案（不挑第一个冒充）', () => {
    expect(sharedSwitchKind([shape('rect', { id: 'a' }), shape('ellipse', { id: 'b' })])).toBeNull()
  })

  it('不参与切换的对象也没有答案', () => {
    expect(sharedSwitchKind([text()])).toBeNull()
  })
})

/* -------------------------------------------------------------------------- */
/*  switchObject：什么时候什么都不做                                             */
/* -------------------------------------------------------------------------- */

describe('switchObject：不该动的时候返回 null', () => {
  it('目标就是当前类型', () => {
    for (const kind of ALL) expect(switchObject(sample(kind), kind)).toBeNull()
  })

  it('跨族', () => {
    expect(switchObject(shape('rect'), 'arrow')).toBeNull()
    expect(switchObject(shape('rect'), 'line')).toBeNull()
    expect(switchObject(arrow(), 'ellipse')).toBeNull()
    expect(switchObject(shape('line'), 'triangle')).toBeNull()
  })

  it('文字与面板', () => {
    expect(switchObject(text(), 'rect')).toBeNull()
    expect(switchObject(panel(), 'rect')).toBeNull()
  })
})

/* -------------------------------------------------------------------------- */
/*  每一对：几何、共有样式、跟对象走的那一组                                       */
/* -------------------------------------------------------------------------- */

describe.each(PAIRS)('%s → %s', (from, to) => {
  const src = sample(from)
  const out = switchObject(src, to)!

  it('切得出来，且当前类型确实换了', () => {
    expect(out).not.toBeNull()
    expect(switchKindOf(out)).toBe(to)
  })

  it('几何一个字不动（x / y / w / h / 旋转）', () => {
    expect({ x: out.x, y: out.y, w: out.w, h: out.h }).toEqual(BOX_GEOMETRY)
    expect(out.rotationDeg).toBe(37)
  })

  it('跟对象走的那一组原样：id / 名字 / 成组 / 锁定 / 隐藏 / 布局固定', () => {
    expect(out.id).toBe(src.id)
    expect(out.name).toBe('我起的名字')
    expect(out.groupId).toBe('g1')
    expect(out.locked).toBe(true)
    expect(out.hidden).toBe(true)
    expect(out.layoutPinned).toBe(true)
  })

  it.each(SHARED_STYLE_FIELDS)('共有样式 %s 原样保留', (field) => {
    expect((out as unknown as Record<string, unknown>)[field]).toEqual(
      (src as unknown as Record<string, unknown>)[field],
    )
  })

  it('原对象一个字节没被改（纯函数）', () => {
    expect(src).toEqual(sample(from))
  })
})

/* -------------------------------------------------------------------------- */
/*  字段集合合法：目标读不到的一个都不许留                                         */
/* -------------------------------------------------------------------------- */

/**
 * 每种类型**读得到**的类型相关字段。判据就是这张表——切换之后的对象上，
 * `KIND_FIELDS` 里凡是不属于目标那一格的，必须一个都不在。
 *
 * 与实现里的 `KIND_FIELDS` 刻意分开写：那边回答「哪些字段随类型走」，这边回答
 * 「哪一种类型读哪几个」。两边都从实现里取的话就是自己验自己。
 */
const READS: Record<SwitchKind, readonly string[]> = {
  rect: ['shape', 'fill', 'fillOpacity', 'cornerRadius'],
  ellipse: ['shape', 'fill', 'fillOpacity'],
  triangle: ['shape', 'fill', 'fillOpacity'],
  diamond: ['shape', 'fill', 'fillOpacity'],
  polygon: ['shape', 'fill', 'fillOpacity', 'sides'],
  brace: ['shape', 'fill', 'fillOpacity'],
  line: ['shape', 'fill', 'fillOpacity', 'start', 'end'],
  arrow: ['start', 'end', 'head', 'headStart', 'headEnd'],
}

const ALL_KIND_FIELDS = [...new Set(Object.values(READS).flat())]

describe.each(PAIRS)('%s → %s 的字段集合合法', (from, to) => {
  const out = switchObject(sample(from), to)! as unknown as Record<string, unknown>

  it('目标读不到的类型字段一个都不在（不留脏字段）', () => {
    const stale = ALL_KIND_FIELDS.filter((f) => !READS[to].includes(f) && f in out)
    expect(stale).toEqual([])
  })
})

describe('目标类型特有的必填字段有值', () => {
  it('切成形状：fill 一定在（必填字段），从箭头来时是「无填充」', () => {
    // 箭头只能切成直线（同族），所以「从箭头来」这一档只有这一条路
    expect((switchObject(arrow(), 'line') as ShapeObject).fill).toBeNull()
    // 形状之间切换带着自己的填充与透明度
    const src = shape('rect', { fill: '#FFEE88', fillOpacity: 0.4 })
    const out = switchObject(src, 'ellipse') as ShapeObject
    expect(out.fill).toBe('#FFEE88')
    expect(out.fillOpacity).toBe(0.4)
  })

  it('切成多边形：边数给默认值（后端与前端读的是同一个数）', () => {
    expect((switchObject(shape('rect'), 'polygon') as ShapeObject).sides).toBe(DEFAULT_SIDES)
  })

  it('离开多边形：边数删掉', () => {
    const out = switchObject(shape('polygon', { sides: 9 }), 'rect') as ShapeObject
    expect('sides' in out).toBe(false)
  })

  it('离开矩形：圆角删掉（只有矩形读它）', () => {
    const out = switchObject(shape('rect', { cornerRadius: 3 }), 'ellipse') as ShapeObject
    expect('cornerRadius' in out).toBe(false)
  })

  it('切成箭头：端型给默认（起点无、终点实心三角），旧 head 字段同步', () => {
    const out = switchObject(shape('line'), 'arrow') as ArrowObject
    expect(out.headStart).toBe('none')
    expect(out.headEnd).toBe('triangle')
    expect(out.head).toBe('end')
  })

  it('离开箭头：三个端型字段全删掉', () => {
    const out = switchObject(arrow(), 'line') as ShapeObject & Record<string, unknown>
    expect('head' in out).toBe(false)
    expect('headStart' in out).toBe(false)
    expect('headEnd' in out).toBe(false)
  })

  it('切成箭头：填充与透明度删掉（箭头结构上没有它们）', () => {
    const out = switchObject(shape('line', { fill: '#FF0000', fillOpacity: 0.5 }), 'arrow') as unknown as Record<
      string,
      unknown
    >
    expect('fill' in out).toBe(false)
    expect('fillOpacity' in out).toBe(false)
  })
})

/* -------------------------------------------------------------------------- */
/*  端点：线状族里几何真的没动                                                    */
/* -------------------------------------------------------------------------- */

describe('线状族的端点', () => {
  it('直线 → 箭头：端点原样（不是退回包围盒中线）', () => {
    const src = shape('line', { start: { rx: 0.1, ry: 0.2 }, end: { rx: 0.9, ry: 0.8 } })
    const out = switchObject(src, 'arrow') as ArrowObject
    expect(out.start).toEqual({ rx: 0.1, ry: 0.2 })
    expect(out.end).toEqual({ rx: 0.9, ry: 0.8 })
  })

  it('箭头 → 直线：端点原样', () => {
    const out = switchObject(arrow(), 'line') as ShapeObject
    expect(out.start).toEqual({ rx: 0.1, ry: 0.2 })
    expect(out.end).toEqual({ rx: 0.9, ry: 0.8 })
  })

  it('没有端点的旧直线 → 箭头：补成包围盒水平中线（箭头这两个字段必填）', () => {
    const legacy = shape('line')
    delete (legacy as Partial<ShapeObject>).start
    delete (legacy as Partial<ShapeObject>).end
    const out = switchObject(legacy, 'arrow') as ArrowObject
    expect(out.start).toEqual({ rx: 0, ry: 0.5 })
    expect(out.end).toEqual({ rx: 1, ry: 0.5 })
  })

  it('端点是新对象，不与源共享引用（改一个不该动另一个）', () => {
    const src = shape('line', { start: { rx: 0.1, ry: 0.2 }, end: { rx: 0.9, ry: 0.8 } })
    const out = switchObject(src, 'arrow') as ArrowObject
    out.start.rx = 0.5
    expect(src.start!.rx).toBe(0.1)
  })
})

/* -------------------------------------------------------------------------- */
/*  往返                                                                        */
/* -------------------------------------------------------------------------- */

describe('绕一圈回到原类型', () => {
  it('矩形 → 椭圆 → 多边形 → 矩形：几何与共有样式一路没动，多边形边数没留下', () => {
    const src = shape('rect')
    const a = switchObject(src, 'ellipse')!
    const b = switchObject(a, 'polygon')!
    const c = switchObject(b, 'rect')! as ShapeObject & Record<string, unknown>
    expect(c.shape).toBe('rect')
    expect({ x: c.x, y: c.y, w: c.w, h: c.h }).toEqual(BOX_GEOMETRY)
    expect(c.strokePt).toBe(2.25)
    expect(c.color).toBe('#3366CC')
    expect(c.dash).toBe('dashed')
    expect(c.fill).toBe('#FFEE88')
    expect('sides' in c).toBe(false)
  })

  it('直线 → 箭头 → 直线：端点一路没动', () => {
    const src = shape('line', { start: { rx: 0.25, ry: 0.75 }, end: { rx: 0.75, ry: 0.25 } })
    const back = switchObject(switchObject(src, 'arrow')!, 'line')! as ShapeObject
    expect(back.start).toEqual({ rx: 0.25, ry: 0.75 })
    expect(back.end).toEqual({ rx: 0.75, ry: 0.25 })
  })
})

/* -------------------------------------------------------------------------- */
/*  科研预设                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * 预设插进来的是**普通的 arrow / shape / text 对象**（`lib/presets` 只是参数组合，
 * 没有自己的对象类型），所以它们按同一套判据参与切换——这一节把「按同一套判据」
 * 兑现成可跑的断言，而不是一句说法。
 *
 * 两个层次要分开：
 *   * **整组选中**（画布上点一下 = 选中整组）：只有全体同族的预设给切换。
 *     箭头 + 文字、矩形 + 直线这些混排的整组不给——那正是 `switchTargets` 的
 *     规则，不是预设的特例。
 *   * **单独选中一个成员**（图层树的行选的是单个对象，不扩成整组）：只要它是
 *     形状或箭头就照常给。
 */
describe('科研预设：整组按同族判、单个成员照常给', () => {
  const preset = (id: PresetId) => buildPreset(id, { x: 50, y: 50 })

  /** 整组选中时给不给切换。**写成表而不是「算一遍再比一遍」**：照着实现算出
   *  期望值的话，实现错了期望值跟着错，这条用例恒真 */
  const WHOLE_GROUP: Record<PresetId, 'box' | 'linear' | null> = {
    reversible: 'linear', // 两支箭头
    dimension: 'linear', // 箭头 + 两条界线
    scalebar: null, // 直线 + 文字
    axes: null, // 两支箭头 + 两段文字
    crystal: null, // 箭头 + 文字
    errorbar: null, // 箭头 + 文字
    magnifier: null, // 两个矩形 + 一条直线（跨族）
    callout: null, // 引线 + 文字
    braceGroup: null, // 大括号 + 文字
  }

  it('清单没漏项（新增预设时这条先红，逼着回答它属于哪一档）', () => {
    expect(Object.keys(WHOLE_GROUP).sort()).toEqual([...PRESET_IDS].sort())
  })

  it.each(PRESET_IDS)('%s：整组选中时的目标集合与表一致', (id) => {
    const family = WHOLE_GROUP[id]
    expect(switchTargets(preset(id))).toEqual(family ? SWITCH_FAMILY_KINDS[family] : [])
  })

  it.each(PRESET_IDS)('%s：单独选中任一形状 / 箭头成员，照常给它那一族', (id) => {
    for (const o of preset(id)) {
      const kind = switchKindOf(o)
      if (kind == null) continue // 文字成员不参与，与别处一样
      expect(switchTargets([o])).toEqual(SWITCH_FAMILY_KINDS[switchFamilyOf(kind)])
    }
  })

  it('预设成员切换之后仍在原来那个组里（成组不因换类型而散架）', () => {
    const [top] = preset('reversible')
    const out = switchObject(top, 'line')!
    expect(out.groupId).toBe(top.groupId)
    expect(out.id).toBe(top.id)
  })
})

/**
 * 图内对齐的离散动作语义（issue #131）。
 *
 * 用户报的是「点左对齐之后布局全乱、撤销回不去」。拆开是四件事：
 *
 *   1. 点击那一刻用的几何来自**哪个变体**——权威不在就不许算；
 *   2. 没动的元素**不许**被顺手钉上一条绝对位置 override
 *      （标题原本是 matplotlib 自动布局，钉死之后再改字号它就不会自己让位了）；
 *   3. 左对齐是**离散动作**，不许被上一轮还开着的连续编辑事务吞掉
 *      （吞掉之后一次撤销会把字号和对齐一起吐出来，看起来就是「撤销乱跳」）；
 *   4. 整组已经对齐时再点一次 = 完全的 no-op：不写 override、不进历史、
 *      不重渲染。
 *
 * 这里全部打在 `alignSelectedPanelElements` 这一个入口上——按钮不许再提交
 * React 上一轮 render 捕获的闭包。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { formatMessage, literal } from '@/i18n'
import type { Manifest, ManifestElement } from '@/lib/api'
import { alignSelectedPanelElements } from '@/store/alignAction'
import { finishActiveGesture, registerGesture } from '@/store/gestureCoordinator'
import { useDocumentStore } from '@/store/documentStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'

const renderSpy = vi.fn()

/** 可拖动文字：anchor 就是 bbox 左上角，方便直接读出「往哪挪了多少」 */
const text = (
  gid: string,
  bbox: [number, number, number, number],
): ManifestElement =>
  ({
    gid,
    role: 'text',
    label: gid,
    bbox,
    editable: [],
    draggable: true,
    anchor: [bbox[0], bbox[1]],
    drag_prop: 'pos_frac',
  }) as ManifestElement

const panel = (overrides: unknown[] = []): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 10,
    y: 20,
    w: 100,
    h: 80,
    fileId: 'f1.pdf',
    fileKind: 'pdf',
    nativeW: 200,
    nativeH: 160,
    script: 'fig.py',
    overrides,
  }) as unknown as PanelObject

/** 三个文字：t1 已在最左（0.10），t2/t3 在右边，左对齐时只有后两个该动 */
const THREE = [
  text('t1', [0.10, 0.10, 0.20, 0.05]),
  text('t2', [0.40, 0.30, 0.20, 0.05]),
  text('t3', [0.55, 0.50, 0.20, 0.05]),
]

const manifest = (elements: ManifestElement[]): Manifest => ({
  stem: 'f1',
  size_mm: [200, 160],
  elements,
})

/** 把「这一版已经精确渲染过」写进 renderStore（权威就位） */
function seedExact(p: PanelObject, m: Manifest) {
  useRenderStore.getState().patch(renderKeyOf(p), {
    fileId: p.fileId,
    rev: 1,
    manifest: m,
    svg: '<svg/>',
    status: 'ready',
    stale: false,
    lastPatches: JSON.stringify(p.overrides),
    wantPatches: JSON.stringify(p.overrides),
  })
  useRenderStore.setState((s) => ({ latest: { ...s.latest, [p.fileId]: renderKeyOf(p) } }))
}

const doc = () => useDocumentStore.getState().doc
const livePanel = () => doc().objects.find((o) => o.id === 'p1') as PanelObject
const overrideOf = (gid: string, prop: string) =>
  livePanel().overrides.find((o) => o.gid === gid && o.prop === prop)?.value

beforeEach(async () => {
  localStorage.clear()
  renderSpy.mockReset()
  useRenderStore.getState().clear()
  useRenderStore.setState({ render: renderSpy })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_align')
  useUiStore.setState({ selectedGids: [], elementPanelId: 'p1' })
})

/** 三个文字都在场、权威就位、三个都选中 */
function scene(overrides: unknown[] = []) {
  useDocumentStore.getState().commit(literal('加'), (d) => {
    d.objects.push(panel(overrides))
  })
  seedExact(livePanel(), manifest(THREE))
  useUiStore.setState({ selectedGids: ['t1', 't2', 't3'] })
}

/* ============================ A. 权威闸 ============================ */

describe('权威不在时，对齐明确拒绝——绝不拿旧变体的墨迹框硬算', () => {
  it('字号刚改完、新变体还没画回来 → 拒绝执行，什么都不留下', () => {
    scene()
    // 用户改了字号：文档变成 B，B 的渲染还在路上（A 那格仍是 latest）
    useDocumentStore.getState().commit(literal('字号'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1') as PanelObject
      p.overrides.push({ gid: 't2', prop: 'fontsize', value: 22 })
    })
    const before = structuredClone(livePanel().overrides)
    const pastBefore = useDocumentStore.getState().past.length
    renderSpy.mockReset()

    const res = alignSelectedPanelElements('p1', 'left')

    expect(res.ok).toBe(false)
    expect(res.ok === false && res.reason).toBe('syncing')
    // 不写 override、不进历史、不发渲染
    expect(livePanel().overrides).toEqual(before)
    expect(useDocumentStore.getState().past).toHaveLength(pastBefore)
    expect(renderSpy).not.toHaveBeenCalled()
    // 选区必须留着：等权威回来还要继续用
    expect(useUiStore.getState().selectedGids).toEqual(['t1', 't2', 't3'])
  })

  it('脚本被改过（markStale）之后同样拒绝', () => {
    scene()
    useRenderStore.getState().markStale(['f1.pdf'])
    const res = alignSelectedPanelElements('p1', 'left')
    expect(res.ok).toBe(false)
    expect(useDocumentStore.getState().past).toHaveLength(1) // 只有「加」那条
  })

  it('权威回来之后同一个动作就能执行', () => {
    scene()
    useRenderStore.getState().markStale(['f1.pdf'])
    expect(alignSelectedPanelElements('p1', 'left').ok).toBe(false)

    seedExact(livePanel(), manifest(THREE))
    const res = alignSelectedPanelElements('p1', 'left')
    expect(res.ok).toBe(true)
  })
})

/* ================== B. 没动的元素不许被钉死 ================== */

describe('没有视觉位移就不写 override', () => {
  it('左对齐只给真正要动的两个元素发补丁，基准元素保持自动布局', () => {
    scene()
    const res = alignSelectedPanelElements('p1', 'left')

    expect(res.ok).toBe(true)
    expect(res.ok && res.patches).toBe(2)
    // t1 本来就在最左：一条 override 都不该有（继续由 matplotlib 自动摆）
    expect(overrideOf('t1', 'pos_frac')).toBeUndefined()
    // t2 / t3 挪到 0.10
    expect(overrideOf('t2', 'pos_frac')).toEqual([0.1, 0.3])
    expect(overrideOf('t3', 'pos_frac')).toEqual([0.1, 0.5])
    expect(livePanel().overrides).toHaveLength(2)
  })

  it('整组已经左对齐时再点一次 = 完全的 no-op', () => {
    // 三个文字左边界全是 0.10
    const aligned = [
      text('t1', [0.10, 0.10, 0.20, 0.05]),
      text('t2', [0.10, 0.30, 0.20, 0.05]),
      text('t3', [0.10, 0.50, 0.20, 0.05]),
    ]
    useDocumentStore.getState().commit(literal('加'), (d) => {
      d.objects.push(panel([{ gid: 't9', prop: 'color', value: '#f00' }]))
    })
    seedExact(livePanel(), manifest(aligned))
    useUiStore.setState({ selectedGids: ['t1', 't2', 't3'] })

    const before = structuredClone(livePanel().overrides)
    const pastBefore = useDocumentStore.getState().past.length
    renderSpy.mockReset()

    const res = alignSelectedPanelElements('p1', 'left')

    expect(res.ok).toBe(false)
    expect(res.ok === false && res.reason).toBe('noop')
    // overrides 逐字不变——连顺序都不许动（键一变就是一次白渲染）
    expect(livePanel().overrides).toEqual(before)
    expect(JSON.stringify(livePanel().overrides)).toBe(JSON.stringify(before))
    expect(useDocumentStore.getState().past).toHaveLength(pastBefore)
    expect(renderSpy).not.toHaveBeenCalled()
  })

  it('已有 override 但值没变的元素不重写，数组顺序原样保留', () => {
    // t2 已经被手动摆到 0.10 了；再点左对齐，它不该被「重新写一遍」
    const m = manifest([
      text('t1', [0.10, 0.10, 0.20, 0.05]),
      text('t2', [0.40, 0.30, 0.20, 0.05]),
      text('t3', [0.55, 0.50, 0.20, 0.05]),
    ])
    useDocumentStore.getState().commit(literal('加'), (d) => {
      d.objects.push(
        panel([
          { gid: 't2', prop: 'pos_frac', value: [0.1, 0.3] },
          { gid: 'zzz', prop: 'color', value: '#00f' },
        ]),
      )
    })
    // 权威里 t2 的墨迹框已经在 0.10（override 已经生效过一轮）
    m.elements[1] = text('t2', [0.10, 0.30, 0.20, 0.05])
    seedExact(livePanel(), m)
    useUiStore.setState({ selectedGids: ['t1', 't2', 't3'] })

    const res = alignSelectedPanelElements('p1', 'left')
    expect(res.ok).toBe(true)
    expect(res.ok && res.patches).toBe(1) // 只有 t3

    const ov = livePanel().overrides
    // 原有两条保持原位原值，新的一条追加在后面
    expect(ov[0]).toEqual({ gid: 't2', prop: 'pos_frac', value: [0.1, 0.3] })
    expect(ov[1]).toEqual({ gid: 'zzz', prop: 'color', value: '#00f' })
    expect(ov[2]).toMatchObject({ gid: 't3', prop: 'pos_frac' })
  })

  it('撤销一次精确回到对齐前，重做一次精确恢复', () => {
    scene()
    alignSelectedPanelElements('p1', 'left')
    const after = structuredClone(livePanel().overrides)

    useDocumentStore.getState().undo()
    expect(livePanel().overrides).toEqual([])

    useDocumentStore.getState().redo()
    expect(livePanel().overrides).toEqual(after)
  })
})

/* ================= C. 离散动作的事务边界 ================= */

describe('左对齐是独立的一条历史，绝不并进上一轮还开着的手势', () => {
  it('属性修改与左对齐各成一条，两次撤销分别回到 B 和 A', () => {
    scene()
    const A = structuredClone(livePanel().overrides) // 编辑前：空

    // 用户批量改字号：手势事务开着（安静计时器还没到）
    let finished = 0
    const unregister = registerGesture(() => {
      finished += 1
      useDocumentStore.getState().endTxn()
    })
    useDocumentStore.getState().beginTxn(literal('改字号'))
    useDocumentStore.getState().commit(literal('改字号'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1') as PanelObject
      p.overrides.push({ gid: 't2', prop: 'fontsize', value: 22 })
    })
    const B = structuredClone(livePanel().overrides)
    // 字号变了 → 权威跟着变体走；重新给新变体喂一份精确 manifest
    seedExact(livePanel(), manifest(THREE))

    // 安静计时器还没结束就点了左对齐
    const res = alignSelectedPanelElements('p1', 'left')
    unregister()

    expect(res.ok).toBe(true)
    // 对齐动作必须先把开着的手势收掉
    expect(finished).toBe(1)
    expect(useDocumentStore.getState().txn).toBeNull()

    const C = structuredClone(livePanel().overrides)
    expect(C.length).toBe(B.length + 2)

    // 撤销一次 → B（字号还在、对齐没了）
    const label1 = useDocumentStore.getState().undo()
    expect(livePanel().overrides).toEqual(B)
    expect(formatMessage(label1)).not.toBe('改字号')

    // 再撤销一次 → A
    const label2 = useDocumentStore.getState().undo()
    expect(livePanel().overrides).toEqual(A)
    expect(formatMessage(label2)).toBe('改字号')
  })

  it('finishActiveGesture 之后不留开着的事务，重复调用是安全的', () => {
    let n = 0
    const unregister = registerGesture(() => {
      n += 1
      useDocumentStore.getState().endTxn()
    })
    useDocumentStore.getState().beginTxn(literal('手势'))
    finishActiveGesture()
    finishActiveGesture()
    expect(n).toBe(1)
    expect(useDocumentStore.getState().txn).toBeNull()
    unregister()
  })
})

/* ================= D. 点击时重新读状态 ================= */

describe('点击那一刻重新读 store，不吃 React 上一轮的闭包', () => {
  it('选区在渲染之后又变了 → 按新的选区算', () => {
    scene()
    // 组件那一轮看到的是三个；点击前用户又取消了 t3
    useUiStore.setState({ selectedGids: ['t1', 't2'] })
    const res = alignSelectedPanelElements('p1', 'left')
    expect(res.ok).toBe(true)
    expect(res.ok && res.patches).toBe(1)
    expect(overrideOf('t3', 'pos_frac')).toBeUndefined()
  })

  it('选区不足两个几何目标时不执行', () => {
    scene()
    useUiStore.setState({ selectedGids: ['t1'] })
    const res = alignSelectedPanelElements('p1', 'left')
    expect(res.ok).toBe(false)
    expect(res.ok === false && res.reason).toBe('too-few')
  })
})

/* ================= E. 非法值不许部分写入 ================= */

describe('一次多选对齐要么整批成立，要么一条都不写', () => {
  it('某个元素的 bbox 非法 → 整次取消，不部分写入', () => {
    const broken = manifest([
      text('t1', [0.10, 0.10, 0.20, 0.05]),
      text('t2', [Number.NaN, 0.30, 0.20, 0.05]),
      text('t3', [0.55, 0.50, 0.20, 0.05]),
    ])
    useDocumentStore.getState().commit(literal('加'), (d) => {
      d.objects.push(panel())
    })
    seedExact(livePanel(), broken)
    useUiStore.setState({ selectedGids: ['t1', 't2', 't3'] })

    const res = alignSelectedPanelElements('p1', 'left')
    expect(res.ok).toBe(false)
    expect(res.ok === false && res.reason).toBe('invalid')
    expect(livePanel().overrides).toEqual([])
  })
})

/* ========== F. 单选几何元素：权威缺席时控件整个收掉（#137 评审 P1） ========== */

describe('GEOMETRY_WRITE_PROPS：几何字段的初值来自 manifest，权威缺席就不许出控件', () => {
  it('把「哪些 prop 算几何写」收在一处，position / size_mm 都在表里', async () => {
    const { GEOMETRY_WRITE_PROPS } = await import('@/lib/elementGeom')
    // 判据：这个 prop 在没有 override 时要从 manifest 读初值。
    // 少一个，属性页就会在权威缺席时把上一版的数字当成这一版的可编辑初值。
    expect(GEOMETRY_WRITE_PROPS.has('position')).toBe(true)
    expect(GEOMETRY_WRITE_PROPS.has('size_mm')).toBe(true)
    expect(GEOMETRY_WRITE_PROPS.has('pos_frac')).toBe(true)
    expect(GEOMETRY_WRITE_PROPS.has('loc_frac')).toBe(true)
    expect(GEOMETRY_WRITE_PROPS.has('endpoints_frac')).toBe(true)
    // 纯样式不在表里：它们不依赖 bbox，连续调整期间照旧走局部 SVG 预览
    for (const p of ['color', 'linewidth', 'linestyle', 'alpha', 'visible', 'fontsize']) {
      expect(GEOMETRY_WRITE_PROPS.has(p)).toBe(false)
    }
  })
})

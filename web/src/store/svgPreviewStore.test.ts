/**
 * 预览平面本身：transform 不覆盖、rAF 合并、取消还原、SVG 重插后重放。
 *
 * 这几条里有三条修的是真实缺陷：
 *   * 旧实现 `setAttribute('transform', 'translate(…)')` 把 matplotlib 自己的
 *     transform 整个盖掉（`<image>` 的 `scale(1 -1) translate(…)` 就这么没的）；
 *   * pointercancel 与 pointerup 走同一条路，被系统打断的拖动会留下临时位移；
 *   * React 重新插一遍 SVG 之后预览凭空消失，刚拖完的元素弹回原位。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import {
  beginPreview,
  cancelPreview,
  commitPreview,
  findGidNode,
  findPanelSvg,
  flushPreviewFrame,
  getHistoryMode,
  previewSession,
  previewStyle,
  previewTransform,
  previewTransformOf,
  reattachPreview,
  resetPreview,
  retargetPreview,
  previewScale,
  setHistoryMode,
  settleFailedAuthority,
  queuedTranslation,
  updateQueuedTranslation,
  flushQueuedTranslation,
  prepareQueuedTranslationCommit,
  captureQueuedTranslationCommit,
  cancelQueuedTranslation,
  rebaseQueuedTranslation,
} from './svgPreviewStore'

const PANEL = 'p1'
const KEY = 'Fig1.pdf []'
const SIZE = [101.6, 76.2] as const

/** PanelView 的那层包装：data-element-svg + 内联 SVG */
function mountSvg(panelId = PANEL) {
  document.body.innerHTML = `<div data-element-svg="${panelId}">${MATPLOTLIB_SVG}</div>`
}

const gid = (g: string) => findGidNode(findPanelSvg(PANEL), g)
const tf = (g: string) => gid(g)?.getAttribute('transform') ?? null

beforeEach(() => {
  resetPreview()
  mountSvg()
})

afterEach(() => {
  resetPreview()
  setHistoryMode('gesture')
})

const begin = (key = KEY) =>
  beginPreview({ panelId: PANEL, renderKey: key, rev: 3, sizeMm: SIZE })

function mountNativeSvg(par = 'none') {
  document.body.innerHTML = `<div data-element-svg="${PANEL}">
    <svg viewBox="50 20 100 100" width="200pt" height="100pt" preserveAspectRatio="${par}">
      <g id="source"><path style="fill: none; stroke: red; stroke-width: 3"/></g>
      <g data-tavotto-native-viewport-pt="200 100" transform="matrix(0.5 0 0 0.5 50 20)">
        <g id="native" transform="translate(2 3)"><path style="fill: none; stroke: blue; stroke-width: 3"/></g>
      </g>
    </svg>
  </div>`
}

const mappedTargets = {
  axes_0: { transform: ['axes_0', 'line-a', 'line-b'], style: ['axes_0', 'line-a', 'line-b'] },
  'line-a': { transform: ['line-a'], style: ['line-a', 'line-b'] },
  'line-b': { transform: ['line-b'], style: ['line-b'] },
  errorbar: { transform: ['members_3', 'members_1', 'members_2', 'members_0'], style: ['members_3', 'members_1', 'members_2', 'members_0'] },
  empty: { transform: [], style: [] },
}
function mountMappedSvg() {
  document.body.innerHTML = `<div data-element-svg="${PANEL}"><svg viewBox="50 20 100 100">
    <g id="decoration"><path style="stroke: green; stroke-width: 3"/></g>
    <g data-tavotto-native-viewport-pt="200 100">
      <g id="axes_0" transform="translate(1 2)"/>
      <g id="line-a" transform="translate(2 3)"><path style="stroke: red; stroke-width: 3"/></g>
      <g id="members_3"><path style="stroke: black; stroke-width: 1"/></g>
      <g id="members_1"><path style="stroke: black; stroke-width: 1"/></g>
      <g id="members_2"><path style="stroke: black; stroke-width: 1"/></g>
      <g id="members_0"><path style="stroke: black; stroke-width: 3"/></g>
    </g>
    <g data-tavotto-native-viewport-pt="400 200">
      <g id="line-b" transform="translate(4 5)"><path style="stroke: blue; stroke-width: 3"/></g>
    </g>
    <g id="empty"/>
  </svg></div>`
  findPanelSvg(PANEL)!.setAttribute('data-tavotto-svg-targets', JSON.stringify(mappedTargets))
}

describe('native语义gid的多DOM目标预览', () => {
  it('split axes所有目标各用自身pt空间平移一次，装饰不动，取消逐字还原', () => {
    mountMappedSvg()
    const before = findPanelSvg(PANEL)!.outerHTML
    begin()
    previewTransform('axes_0', 0.1, 0.1)
    flushPreviewFrame()
    expect(tf('axes_0')).toBe('translate(20,10) translate(1 2)')
    expect(tf('line-a')).toBe('translate(20,10) translate(2 3)')
    expect(tf('line-b')).toBe('translate(40,20) translate(4 5)')
    expect(tf('decoration')).toBeNull()
    cancelPreview()
    expect(findPanelSvg(PANEL)!.outerHTML).toBe(before)
  })

  it('shared axes只有父节点时不重复变换其后代', () => {
    mountMappedSvg()
    gid('axes_0')!.appendChild(gid('line-a')!)
    findPanelSvg(PANEL)!.setAttribute('data-tavotto-svg-targets', JSON.stringify({ axes_0: { transform: ['axes_0'], style: ['axes_0'] } }))
    begin()
    previewTransform('axes_0', 0.1, 0)
    flushPreviewFrame()
    expect(tf('axes_0')).toBe('translate(20,0) translate(1 2)')
    expect(tf('line-a')).toBe('translate(2 3)')
    cancelPreview()
    expect(tf('axes_0')).toBe('translate(1 2)')
  })

  it('不存在主DOM gid的errorbar平移4个真实成员，但不扩展caps的局部样式语义', () => {
    mountMappedSvg()
    expect(gid('errorbar')).toBeNull()
    begin()
    previewTransform('errorbar', 0.1, 0.2)
    expect(previewStyle('errorbar', 'errorbar', 'linewidth', 6)).toBe(false)
    expect(previewStyle('errorbar', 'errorbar', 'color', '#ff0000')).toBe(false)
    flushPreviewFrame()
    for (const id of mappedTargets.errorbar.transform) expect(tf(id)).toBe('translate(20,20)')
    expect(tf('line-a')).toBe('translate(2 3)')
    cancelPreview()
    for (const id of mappedTargets.errorbar.transform) expect(tf(id)).toBeNull()
  })

  it('多目标缩放、commit后同版重插与重复reattach不会重复乘矩阵', () => {
    mountMappedSvg()
    begin()
    previewScale('axes_0', 2, 0.75, 0.25)
    flushPreviewFrame()
    commitPreview([{ gid: 'axes_0', prop: 'position', value: [0.2, 0.3] }], 'next')
    const expectedA = 'matrix(2,0,0,2,-150,-25) translate(2 3)'
    const expectedB = 'matrix(2,0,0,2,-300,-50) translate(4 5)'
    expect(tf('line-a')).toBe(expectedA)
    expect(tf('line-b')).toBe(expectedB)
    mountMappedSvg()
    reattachPreview(PANEL, KEY)
    reattachPreview(PANEL, KEY)
    expect(tf('line-a')).toBe(expectedA)
    expect(tf('line-b')).toBe(expectedB)
    cancelPreview()
    expect(tf('line-a')).toBe('translate(2 3)')
    expect(tf('line-b')).toBe('translate(4 5)')
  })

  it('axes/series先后涉及同目标，base只记一次，重放与取消不叠加旧预览', () => {
    mountMappedSvg()
    begin()
    previewTransform('axes_0', 0.1, 0)
    flushPreviewFrame()
    commitPreview([{ gid: 'axes_0', prop: 'position', value: [0.1, 0] }], 'next')
    begin()
    previewTransform('line-a', 0.2, 0)
    flushPreviewFrame()
    expect(tf('line-a')).toBe('translate(40,0) translate(2 3)')
    mountMappedSvg()
    reattachPreview(PANEL, KEY)
    expect(tf('line-a')).toBe('translate(40,0) translate(2 3)')
    expect(tf('line-b')).toBe('translate(40,0) translate(4 5)')
    cancelPreview()
    expect(tf('line-a')).toBe('translate(2 3)')
    expect(tf('line-b')).toBe('translate(4 5)')
  })

  it('同目标的后续axes/series写入按实际最后顺序重放，部分DOM重挂也不把旧预览当base', () => {
    mountMappedSvg()
    begin()
    previewTransform('axes_0', 0.1, 0)
    flushPreviewFrame()
    previewTransform('line-a', 0.2, 0)
    flushPreviewFrame()
    previewTransform('axes_0', 0.3, 0)
    flushPreviewFrame()
    expect(tf('line-a')).toBe('translate(60,0) translate(2 3)')
    gid('line-b')!.outerHTML = '<g id="line-b" transform="translate(4 5)"><path style="stroke: blue; stroke-width: 3"/></g>'
    reattachPreview(PANEL, KEY)
    expect(tf('line-a')).toBe('translate(60,0) translate(2 3)')
    expect(tf('line-b')).toBe('translate(120,0) translate(4 5)')
    reattachPreview(PANEL, KEY)
    expect(tf('line-a')).toBe('translate(60,0) translate(2 3)')
    cancelPreview()
    expect(tf('line-a')).toBe('translate(2 3)')
    expect(tf('line-b')).toBe('translate(4 5)')
  })

  it('style只改权威归属目标，取消与重插后仍保持同一份可逆账本', () => {
    mountMappedSvg()
    const before = findPanelSvg(PANEL)!.outerHTML
    begin()
    expect(previewStyle('line-a', 'line', 'linewidth', 6)).toBe(true)
    flushPreviewFrame()
    for (const id of ['line-a', 'line-b']) expect((gid(id)!.querySelector('path') as SVGElement).style.strokeWidth).toBe('6')
    expect((gid('decoration')!.querySelector('path') as SVGElement).style.strokeWidth).toBe('3')
    mountMappedSvg()
    reattachPreview(PANEL, KEY)
    for (const id of ['line-a', 'line-b']) expect((gid(id)!.querySelector('path') as SVGElement).style.strokeWidth).toBe('6')
    cancelPreview()
    expect(findPanelSvg(PANEL)!.outerHTML).toBe(before)
  })

  it('style共享目标按最后意图重放，部分重挂保留的旧叶子不丢原始style', () => {
    mountMappedSvg()
    begin()
    previewStyle('line-a', 'line', 'linewidth', 6)
    flushPreviewFrame()
    previewStyle('line-b', 'line', 'linewidth', 8)
    flushPreviewFrame()
    previewStyle('line-a', 'line', 'linewidth', 10)
    flushPreviewFrame()
    gid('line-b')!.outerHTML = '<g id="line-b" transform="translate(4 5)"><path style="stroke: blue; stroke-width: 3"/></g>'
    reattachPreview(PANEL, KEY)
    for (const id of ['line-a', 'line-b']) expect((gid(id)!.querySelector('path') as SVGElement).style.strokeWidth).toBe('10')
    cancelPreview()
    for (const id of ['line-a', 'line-b']) expect((gid(id)!.querySelector('path') as SVGElement).style.strokeWidth).toBe('3')
  })

  it('排队、准备提交、捕获与同DOM换基均处理全部目标，公开首节点仍兼容', () => {
    mountMappedSvg()
    const q = queuedTranslation(PANEL, 'axes_0')
    updateQueuedTranslation(q, [0.1, 0.1])
    flushQueuedTranslation(q)
    expect(q.node).toBe(gid('axes_0'))
    expect(tf('line-a')).toBe('translate(20,10) translate(2 3)')
    expect(tf('line-b')).toBe('translate(40,20) translate(4 5)')
    prepareQueuedTranslationCommit(q)
    expect(tf('line-a')).toBe('translate(2 3)')
    expect(tf('line-b')).toBe('translate(4 5)')
    captureQueuedTranslationCommit(q)
    updateQueuedTranslation(q, [0.2, 0])
    flushQueuedTranslation(q)
    cancelQueuedTranslation(q)
    expect(tf('line-a')).toBe('translate(2 3)')
    expect(tf('line-b')).toBe('translate(4 5)')
    updateQueuedTranslation(q, [0.1, 0])
    flushQueuedTranslation(q)
    rebaseQueuedTranslation(q)
    expect(tf('line-a')).toBe('translate(2 3)')
    expect(tf('line-b')).toBe('translate(4 5)')
  })

  it('排队pseudo没有主gid也操作全部成员，真实DOM替换后重新采集每个base', () => {
    mountMappedSvg()
    const q = queuedTranslation(PANEL, 'errorbar')
    updateQueuedTranslation(q, [0.1, 0])
    flushQueuedTranslation(q)
    expect(q.node?.id).toBe('members_3')
    for (const id of mappedTargets.errorbar.transform) expect(tf(id)).toBe('translate(20,0)')
    mountMappedSvg()
    updateQueuedTranslation(q, [0.2, 0])
    flushQueuedTranslation(q)
    for (const id of mappedTargets.errorbar.transform) expect(tf(id)).toBe('translate(40,0)')
    cancelQueuedTranslation(q)
    for (const id of mappedTargets.errorbar.transform) expect(tf(id)).toBeNull()
  })

  it('显式空目标不借同名DOM，缺失或重叠任一目标则整次transform/style不写', () => {
    mountMappedSvg()
    begin()
    previewTransform('empty', 0.1, 0)
    expect(previewStyle('empty', 'line', 'visible', false)).toBe(false)
    flushPreviewFrame()
    expect(tf('empty')).toBeNull()
    findPanelSvg(PANEL)!.setAttribute('data-tavotto-svg-targets', JSON.stringify({
      'line-a': { transform: ['line-a', 'missing'], style: ['line-a', 'line-b'] },
    }))
    const before = findPanelSvg(PANEL)!.outerHTML
    previewTransform('line-a', 0.2, 0)
    expect(previewStyle('line-a', 'line', 'linewidth', 6)).toBe(false)
    flushPreviewFrame()
    expect(findPanelSvg(PANEL)!.outerHTML).toBe(before)
    cancelPreview()
  })
})

describe('组合native图的pt坐标预览与原源坐标分开', () => {
  it.each(['none', 'xMidYMid meet', 'xMaxYMin slice'])(
    '原root缩放/非零原点/PAR=%s时，位移、缩放锚点与线宽均使用native pt', (par) => {
      mountNativeSvg(par)
      const before = findPanelSvg(PANEL)!.outerHTML
      beginPreview({ panelId: PANEL, renderKey: KEY, sizeMm: [200 * 25.4 / 72, 100 * 25.4 / 72] })
      previewTransform('native', 0.1, 0.2)
      previewTransform('source', 0.1, 0.2)
      expect(previewStyle('native', 'line', 'linewidth', 4)).toBe(true)
      expect(previewStyle('source', 'line', 'linewidth', 4)).toBe(true)
      flushPreviewFrame()
      expect(tf('native')).toBe('translate(20,20) translate(2 3)')
      expect(tf('source')).toBe('translate(10,20)')
      expect((gid('native')!.querySelector('path') as SVGElement).style.strokeWidth).toBe('4')
      expect(Number((gid('source')!.querySelector('path') as SVGElement).style.strokeWidth)).toBeCloseTo(2, 12)

      previewScale('native', 2, 0.75, 0.25)
      flushPreviewFrame()
      expect(tf('native')).toBe('matrix(2,0,0,2,-150,-25) translate(2 3)')
      cancelPreview()
      expect(findPanelSvg(PANEL)!.outerHTML).toBe(before)
    },
  )

  it('同版DOM重插后按新target重放，commit留到权威换版，取消逐字还原', () => {
    mountNativeSvg()
    begin()
    previewTransform('native', 0.1, 0)
    previewStyle('native', 'line', 'linewidth', 4)
    flushPreviewFrame()
    commitPreview([{ gid: 'native', prop: 'linewidth', value: 4 }], 'next')
    mountNativeSvg()
    reattachPreview(PANEL, KEY)
    expect(tf('native')).toBe('translate(20,0) translate(2 3)')
    expect((gid('native')!.querySelector('path') as SVGElement).style.strokeWidth).toBe('4')
    reattachPreview(PANEL, KEY)
    expect(tf('native')).toBe('translate(20,0) translate(2 3)')
    cancelPreview()
    expect(tf('native')).toBe('translate(2 3)')
    expect((gid('native')!.querySelector('path') as SVGElement).style.strokeWidth).toBe('3')

    begin()
    previewTransform('native', 0.2, 0)
    flushPreviewFrame()
    commitPreview([{ gid: 'native', prop: 'position', value: [0.2, 0] }], 'next')
    mountNativeSvg()
    reattachPreview(PANEL, 'next')
    expect(previewSession()).toBeNull()
    expect(tf('native')).toBe('translate(2 3)')
  })

  it('图例缩放即使没有前置平移，也按物理viewport计算不动点', () => {
    mountNativeSvg('xMidYMid meet')
    begin()
    previewScale('native', 1.5, 0.75, 0.25)
    flushPreviewFrame()
    expect(tf('native')).toBe('matrix(1.5,0,0,1.5,-75,-12.5) translate(2 3)')
    cancelPreview()
    expect(tf('native')).toBe('translate(2 3)')
  })

  it('排队后续位移也使用目标pt依据，提交准备与取消仍还原原始transform', () => {
    mountNativeSvg()
    const q = queuedTranslation(PANEL, 'native')
    updateQueuedTranslation(q, [0.1, 0.2])
    flushQueuedTranslation(q)
    expect(tf('native')).toBe('translate(20,20) translate(2 3)')
    prepareQueuedTranslationCommit(q)
    expect(tf('native')).toBe('translate(2 3)')
    captureQueuedTranslationCommit(q)
    updateQueuedTranslation(q, [0.2, 0])
    flushQueuedTranslation(q)
    expect(tf('native')).toBe('translate(40,0) translate(2 3)')
    cancelQueuedTranslation(q)
    expect(tf('native')).toBe('translate(2 3)')
  })

  it('坏native marker不写入猜测的位移/线宽，也不声称样式预览成功', () => {
    mountNativeSvg()
    gid('native')!.parentElement!.setAttribute('data-tavotto-native-viewport-pt', '200 NaN')
    const before = findPanelSvg(PANEL)!.outerHTML
    begin()
    previewTransform('native', 0.1, 0.2)
    expect(previewStyle('native', 'line', 'linewidth', 4)).toBe(false)
    flushPreviewFrame()
    expect(findPanelSvg(PANEL)!.outerHTML).toBe(before)
    cancelPreview()
    expect(findPanelSvg(PANEL)!.outerHTML).toBe(before)
  })
})

/* --------------------------- transform 不覆盖 ----------------------------- */

describe('临时 transform 与 matplotlib 原有 transform 共存', () => {
  it('本来没有 transform 的 <g>：只加 translate，还原后属性整条消失', () => {
    expect(tf('axes_0.lines_0')).toBeNull()
    begin()
    previewTransform('axes_0.lines_0', 0.1, 0.2)
    flushPreviewFrame()
    // viewBox 288×216 → 0.1/0.2 换算成 user unit
    expect(tf('axes_0.lines_0')).toBe(`translate(${0.1 * 288},${0.2 * 216})`)
    cancelPreview()
    expect(tf('axes_0.lines_0')).toBeNull()
  })

  it('<image> 自带 scale/translate：预览是「translate + 原始」，原始一字不动', () => {
    const base = tf('axes_0.images_0')
    expect(base).toContain('scale(1 -1)')
    begin()
    previewTransform('axes_0.images_0', 0.05, -0.1)
    flushPreviewFrame()
    expect(tf('axes_0.images_0')).toBe(`translate(${0.05 * 288},${-0.1 * 216}) ${base}`)
    cancelPreview()
    expect(tf('axes_0.images_0')).toBe(base)
  })

  it('拖一百下也永远从 base 现算，绝不字符串累加', () => {
    const base = tf('axes_0.images_0')
    begin()
    for (let i = 1; i <= 100; i++) {
      previewTransform('axes_0.images_0', i / 1000, 0)
      flushPreviewFrame()
    }
    // 最后一帧 = 0.1，而不是 1..100 累加起来的 5.05
    expect(tf('axes_0.images_0')).toBe(`translate(${0.1 * 288},0) ${base}`)
    // translate 只出现一次
    expect(tf('axes_0.images_0')!.match(/translate/g)!.length).toBe(
      (base!.match(/translate/g)?.length ?? 0) + 1,
    )
    cancelPreview()
    expect(tf('axes_0.images_0')).toBe(base)
  })
})

/* ------------------------------- rAF 合并 --------------------------------- */

describe('rAF 合并：一百次 pointermove 不等于一百次 DOM 写入', () => {
  it('攒下的操作只在 flush 时落一次，按 gid 只保留最后一个值', () => {
    begin()
    for (let i = 0; i < 100; i++) previewTransform('axes_0.lines_0', i / 100, 0)
    // 还没 flush：DOM 上什么都没有
    expect(tf('axes_0.lines_0')).toBeNull()
    flushPreviewFrame()
    expect(tf('axes_0.lines_0')).toBe(`translate(${(99 / 100) * 288},0)`)
    // 一百次 move 记在计时里，真正落地的帧数是 1
    const t = previewSession()!.timing
    expect(t.preview_move_count).toBe(100)
    expect(t.preview_frame_count).toBe(1)
    expect(t.preview_first_frame).not.toBeNull()
  })
})

/* -------------------------------- 取消 ------------------------------------ */

describe('取消：DOM 还原干净，什么痕迹都不留', () => {
  it('平移 + 样式一起取消，SVG 逐字节回到原样', () => {
    const before = document.querySelector('svg')!.outerHTML
    begin()
    previewTransform('axes_0.lines_0', 0.2, 0.1)
    previewStyle('axes_0.lines_0', 'line', 'color', '#ff0000')
    previewStyle('axes_0.barseries_0.bar_0', 'bar', 'facecolor', '#00ff00')
    flushPreviewFrame()
    expect(document.querySelector('svg')!.outerHTML).not.toBe(before)
    cancelPreview()
    expect(document.querySelector('svg')!.outerHTML).toBe(before)
    expect(previewSession()).toBeNull()
  })

  it('取消后再开一轮，base 采的是真正的原始值（不是上一轮的预览值）', () => {
    begin()
    previewTransform('axes_0.lines_0', 0.3, 0)
    flushPreviewFrame()
    cancelPreview()

    begin()
    previewTransform('axes_0.lines_0', 0.1, 0)
    flushPreviewFrame()
    expect(tf('axes_0.lines_0')).toBe(`translate(${0.1 * 288},0)`)
    cancelPreview()
    expect(tf('axes_0.lines_0')).toBeNull()
  })
})

/* ------------------------- 提交后预览继续挂着 ------------------------------ */

describe('提交：预览挂到权威 SVG 换上来为止', () => {
  it('commit 之后 DOM 上的预览一动不动（否则元素会先弹回原位再跳到新位）', () => {
    begin()
    previewTransform('axes_0.lines_0', 0.2, 0)
    flushPreviewFrame()
    commitPreview([{ gid: 'axes_0.lines_0', prop: 'pos_frac', value: [1, 2] }], 'Fig1.pdf [新]')
    expect(tf('axes_0.lines_0')).toBe(`translate(${0.2 * 288},0)`)
    expect(previewSession()!.pendingCommit).toHaveLength(1)
    expect(previewSession()!.timing.commit_start).not.toBeNull()
  })

  it('等到的正是那一版 → 会话收工，计时补上 commit→权威 的耗时', () => {
    begin()
    previewTransform('axes_0.lines_0', 0.2, 0)
    flushPreviewFrame()
    commitPreview([], 'Fig1.pdf [新]')
    // 权威 SVG 换上来（React 换掉 innerHTML）
    mountSvg()
    reattachPreview(PANEL, 'Fig1.pdf [新]')
    expect(previewSession()).toBeNull()
    expect(previewTransformOf(PANEL, 'axes_0.lines_0')).toBeNull()
  })

  it('换上来的是**别的**版本（另一个变体 / 脚本重建）→ 预览静默作废', () => {
    begin()
    previewTransform('axes_0.lines_0', 0.2, 0)
    flushPreviewFrame()
    commitPreview([], 'Fig1.pdf [新]')
    mountSvg()
    reattachPreview(PANEL, 'Fig1.pdf [完全不相干]')
    expect(previewTransformOf(PANEL, 'axes_0.lines_0')).toBeNull()
    expect(tf('axes_0.lines_0')).toBeNull()
  })
})

/* --------------------------- React 重插 SVG ------------------------------- */

describe('SVG 被 React 重新插入', () => {
  it('还是同一版 → 把挂起的预览重放上去（平移与样式都要回来）', () => {
    begin()
    previewTransform('axes_0.lines_0', 0.25, 0.1)
    previewStyle('axes_0.lines_0', 'line', 'color', '#ff0000')
    flushPreviewFrame()
    commitPreview([], 'Fig1.pdf [新]')

    // React 重挂：同一份 SVG 文本被重新插入，DOM 节点全换了新的
    mountSvg()
    expect(tf('axes_0.lines_0')).toBeNull()
    reattachPreview(PANEL, KEY)
    expect(tf('axes_0.lines_0')).toBe(`translate(${0.25 * 288},${0.1 * 216})`)
    expect(
      (gid('axes_0.lines_0')!.querySelector('path') as SVGElement).style.getPropertyValue('stroke'),
    ).not.toBe('')
  })

  it('DOM 其实没换（组件只是重跑了一遍）→ 什么都不做，位移绝不翻倍', () => {
    const base = tf('axes_0.images_0')
    begin()
    previewTransform('axes_0.images_0', 0.1, 0)
    flushPreviewFrame()
    const applied = tf('axes_0.images_0')
    expect(applied).toBe(`translate(${0.1 * 288},0) ${base}`)
    // 每写一条 override 都会让 PanelView 重跑一遍；这里连叫五次。
    // 若此时重新采 base，采到的就是「已经挪过的位置」——位移翻倍，且再也
    // 还原不回 matplotlib 给的那份
    for (let i = 0; i < 5; i++) reattachPreview(PANEL, KEY)
    expect(tf('axes_0.images_0')).toBe(applied)
    cancelPreview()
    expect(tf('axes_0.images_0')).toBe(base)
  })

  it('重放之后取消，仍然能还原到 matplotlib 的原样', () => {
    const base = tf('axes_0.images_0')
    begin()
    previewTransform('axes_0.images_0', 0.2, 0)
    flushPreviewFrame()
    mountSvg()
    reattachPreview(PANEL, KEY)
    expect(tf('axes_0.images_0')).toBe(`translate(${0.2 * 288},0) ${base}`)
    cancelPreview()
    expect(tf('axes_0.images_0')).toBe(base)
  })
})

/* ------------------------------ gid 查不到 -------------------------------- */

describe('gid 在 SVG 里不存在（manifest 的伪元素）', () => {
  it('误差棒 / 柱形系列 / 刻度组的 gid 确实不在 SVG 里', () => {
    expect(gid('axes_0.errorbar_1')).toBeNull()
    expect(gid('axes_0.barseries_0')).toBeNull()
    expect(gid('axes_0.xticks')).toBeNull()
  })

  it('平移不抛错、不动别人；位移仍记着（覆盖层照常跟随）', () => {
    const before = document.querySelector('svg')!.outerHTML
    begin()
    expect(() => {
      previewTransform('axes_0.errorbar_1', 0.2, 0.2)
      flushPreviewFrame()
    }).not.toThrow()
    expect(document.querySelector('svg')!.outerHTML).toBe(before)
    expect(previewTransformOf(PANEL, 'axes_0.errorbar_1')).toEqual([0.2, 0.2])
    cancelPreview()
  })

  it('样式预览**回 false**：调用方据此原路走后端，而不是等一个永远不来的预览', () => {
    const before = document.querySelector('svg')!.outerHTML
    begin()
    // 光看能力表 errorbar.color 是「支持」的，但这一版 SVG 里没有这个 gid。
    // 回 true 的话调用方会把渲染降成 'none'，用户整轮什么都看不到——
    // 比改动前（每次都发后端）还糟
    expect(previewStyle('axes_0.errorbar_1', 'errorbar', 'color', '#ff0000')).toBe(false)
    expect(previewStyle('axes_0.barseries_0', 'bar_series', 'facecolor', '#ff0000')).toBe(false)
    flushPreviewFrame()
    expect(document.querySelector('svg')!.outerHTML).toBe(before)
    cancelPreview()
  })

  it('面板不在 DOM 里（没进编辑态）时样式预览也回 false', () => {
    document.body.innerHTML = ''
    begin()
    expect(previewStyle('axes_0.lines_0', 'line', 'color', '#ff0000')).toBe(false)
    cancelPreview()
  })

  it('面板整个不在 DOM 里（没进编辑态）也不抛错', () => {
    document.body.innerHTML = ''
    begin()
    expect(() => {
      previewTransform('axes_0.lines_0', 0.1, 0.1)
      previewStyle('axes_0.lines_0', 'line', 'color', '#ff0000')
      flushPreviewFrame()
      cancelPreview()
    }).not.toThrow()
  })
})

/* ------------------------------ 能力表把关 -------------------------------- */

describe('previewStyle 是白名单的守门人', () => {
  it('不在能力表里的字段直接回 false（调用方据此走后端）', () => {
    begin()
    expect(previewStyle('text', 'text', 'fontsize', 12)).toBe(false)
    expect(previewStyle('axes_0', 'axes', 'position', [0, 0, 1, 1])).toBe(false)
    expect(previewStyle('axes_0.images_0', 'image', 'alpha', 0.5)).toBe(false)
    cancelPreview()
  })

  it('没有会话时一律 false：预览不能凭空发生在任何手势之外', () => {
    expect(previewStyle('axes_0.lines_0', 'line', 'color', '#ff0000')).toBe(false)
  })
})

/* ------------------------------ 会话交班 ---------------------------------- */

describe('一轮没结束又开下一轮', () => {
  it('已提交的那轮交班：预览留着（等权威渲染），新会话另起', () => {
    begin()
    previewTransform('axes_0.lines_0', 0.2, 0)
    flushPreviewFrame()
    commitPreview([], 'Fig1.pdf [新]')
    const first = previewSession()!.id

    begin()
    expect(previewSession()!.id).not.toBe(first)
    // 上一轮拖完的元素不能弹回原位
    expect(tf('axes_0.lines_0')).toBe(`translate(${0.2 * 288},0)`)
  })

  it('没提交就被顶掉的那轮：必须还原——看得见却撤销不了是最坏的状态', () => {
    begin()
    previewTransform('axes_0.lines_0', 0.2, 0)
    flushPreviewFrame()
    begin()
    expect(tf('axes_0.lines_0')).toBeNull()
  })
})

/* ------------------------------ 历史粒度 ---------------------------------- */

describe('historyMode', () => {
  it('默认 gesture；可切到 granular（只改事务边界，不改渲染策略）', () => {
    expect(getHistoryMode()).toBe('gesture')
    setHistoryMode('granular')
    expect(getHistoryMode()).toBe('granular')
    begin()
    expect(previewSession()!.historyMode).toBe('granular')
    cancelPreview()
  })
})

/* ------------------------------ 渲染失败收尾 ------------------------------ */

describe('权威渲染失败', () => {
  it('会话就地收尾，但预览留在画布上（文档里已经是用户要的值）', () => {
    begin()
    previewTransform('axes_0.lines_0', 0.2, 0)
    flushPreviewFrame()
    commitPreview([], 'Fig1.pdf [新]')
    const applied = tf('axes_0.lines_0')

    settleFailedAuthority(PANEL)
    expect(previewSession()).toBeNull()
    // 撤掉预览会让画布与属性页各说各话；失败由角标表达
    expect(tf('axes_0.lines_0')).toBe(applied)

    // 收尾之后能正常开下一轮（不会被卡住）
    begin()
    previewTransform('axes_0.lines_0', 0.4, 0)
    flushPreviewFrame()
    expect(previewSession()).not.toBeNull()
  })
})

/* ------------------- 缩放预览与改挂（图例整体缩放，2026-09-25） ------------------- */

describe('previewScale：绕不动点的等比缩放，同样前置于原始变换', () => {
  it('写成 matrix(s,0,0,s,e,f)，不动点换算到 user unit；取消还原', () => {
    begin()
    previewScale('axes_0.lines_0', 1.5, 0.2, 0.4)
    flushPreviewFrame()
    const e = 0.2 * 288 * (1 - 1.5)
    const f = 0.4 * 216 * (1 - 1.5)
    expect(tf('axes_0.lines_0')).toBe(`matrix(1.5,0,0,1.5,${e},${f})`)
    cancelPreview()
    expect(tf('axes_0.lines_0')).toBeNull()
  })

  it('SVG 被原样重插：连同倍数一起重放', () => {
    begin()
    previewScale('axes_0.lines_0', 2, 0, 0)
    flushPreviewFrame()
    mountSvg()
    reattachPreview(PANEL, KEY)
    expect(tf('axes_0.lines_0')).toBe('matrix(2,0,0,2,0,0)')
  })
})

describe('retargetPreview：把在等的预览改挂到中间那一版上', () => {
  const FIRST = 'Fig1.pdf [第一版]'
  const FINAL = 'Fig1.pdf [补正后]'

  it('中间那一版换进 DOM：只重放平移 δ；补正后那一版到了才收工', () => {
    begin()
    previewScale('axes_0.lines_0', 0.8, 0.1, 0.1)
    flushPreviewFrame()
    commitPreview([], FIRST)
    expect(retargetPreview(PANEL, FIRST, { 'axes_0.lines_0': [0.01, -0.02] }, FINAL)).toBe(true)

    mountSvg() // 中间那一版的 DOM：图例大小已对，只差位置
    reattachPreview(PANEL, FIRST)
    expect(tf('axes_0.lines_0')).toBe(`translate(${0.01 * 288},${-0.02 * 216})`)
    expect(previewSession()).not.toBeNull()

    mountSvg()
    reattachPreview(PANEL, FINAL)
    expect(previewSession()).toBeNull()
  })

  it('没有在途会话：什么都不改，返回 false', () => {
    expect(retargetPreview(PANEL, FIRST, {}, FINAL)).toBe(false)
  })
})

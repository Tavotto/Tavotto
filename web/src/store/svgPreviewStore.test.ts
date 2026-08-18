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
  setHistoryMode,
  settleFailedAuthority,
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

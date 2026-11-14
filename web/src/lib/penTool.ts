import { lock45, type MmNode, type MmSubpath, type XY } from './pathEdit'

/**
 * 钢笔工具（ADR 0185 §五 M1）——**纯状态机**：指针 / 按键事件进，新状态 + 预览 + 命令出。
 * 不碰 DOM、不碰 store；画布那一层（`canvas/penController`）只负责把指针坐标换成页面 mm、把预览交给预览平面
 * （`svgPreviewStore`）、把「完成」命令变成**一次** commit。
 *
 * Illustrator 的手感：
 * - 点一下 = 加尖角点；按下后拖 = 加平滑点并拉出手柄（出手柄跟着指针、入手柄对称）；拖的时候按 ⌥ = 只动出手柄
 *   （拆开，变尖角）。
 * - 点到起点（≥ 2 个点之后）= 闭合并完成。
 * - Enter / Esc / 换工具 / 别的离散动作 = 结束成开放路径；不足两个点就什么都不留。
 */

export type PenState =
  | { kind: 'idle' }
  | {
      kind: 'drawing'
      nodes: MmNode[]
      /** 按着鼠标的这一下：落在第几个点上、按下的位置、拖没拖过、是不是在起点上按的（= 闭合） */
      /**
       * 按着鼠标的这一下：落在第几个点上、按下的位置、拖没拖过、是不是在起点上按的（= 闭合）、
       * 是不是续画时按在原路径端点上的那一下（`resume`：拖它只拉出手柄、不动原来那一侧）
       */
      press: { index: number; at: XY; dragged: boolean; closing: boolean; resume?: boolean } | null
      /** 没按着时指针在哪（橡皮筋预览线的终点） */
      cursor: XY | null
    }

export type PenEvent =
  | { type: 'down'; at: XY; shift?: boolean }
  | { type: 'move'; at: XY; alt?: boolean; shift?: boolean }
  /** 续画（钢笔点在一条开放路径的端点上）：从这些点接着画，最后一个点是被点中的端点 */
  | { type: 'resume'; nodes: MmNode[] }
  | { type: 'up'; at: XY }
  | { type: 'finish' }
  | { type: 'cancel' }

/** 完成一条路径：交给画布层落成一个 `path` 对象 */
export interface PenCommit {
  closed: boolean
  nodes: MmNode[]
}

export interface PenOptions {
  /** 按下后挪多远才算「拖」（mm，画布层按屏幕像素换算） */
  dragTolMm: number
  /** 离起点多近算「点到起点」（mm） */
  closeTolMm: number
}

export const PEN_IDLE: PenState = { kind: 'idle' }

const corner = (p: XY): MmNode => ({ p, in: p, out: p, smooth: false })
const dist = (a: XY, b: XY) => Math.hypot(a[0] - b[0], a[1] - b[1])

/** 正在画的那条 + 橡皮筋 + 「再点就闭合」提示：给预览平面画的东西（页面 mm）；idle = null */
export function penPreview(
  s: PenState,
  opts: PenOptions,
): { subpath: MmSubpath; rubber: [XY, XY] | null; closeHint: boolean } | null {
  if (s.kind !== 'drawing') return null
  const last = s.nodes[s.nodes.length - 1]
  const hover = !s.press && s.cursor ? s.cursor : null
  return {
    subpath: { closed: !!s.press?.closing, nodes: s.nodes },
    rubber: hover ? [last.p, hover] : null,
    closeHint: !!hover && wouldClose(s, hover, opts),
  }
}

/** 这一下按在起点上会不会闭合 */
export function wouldClose(s: PenState, at: XY, opts: PenOptions): boolean {
  return s.kind === 'drawing' && s.nodes.length >= 2 && dist(at, s.nodes[0].p) <= opts.closeTolMm
}

function finish(s: PenState): { state: PenState; commit?: PenCommit } {
  if (s.kind !== 'drawing' || s.nodes.length < 2) return { state: PEN_IDLE }
  return { state: PEN_IDLE, commit: { closed: false, nodes: s.nodes } }
}

/** 状态转移（纯函数；不改入参） */
export function penReduce(s: PenState, ev: PenEvent, opts: PenOptions): { state: PenState; commit?: PenCommit } {
  switch (ev.type) {
    case 'down': {
      if (s.kind === 'idle') {
        return {
          state: { kind: 'drawing', nodes: [corner(ev.at)], press: { index: 0, at: ev.at, dragged: false, closing: false }, cursor: null },
        }
      }
      if (s.press) return { state: s } // 已经按着（多指 / 漏了 up）：忽略
      // ⇧：新点相对上一个点锁在 45° 的倍数上
      const at = ev.shift ? lock45(s.nodes[s.nodes.length - 1].p, ev.at) : ev.at
      if (wouldClose(s, ev.at, opts)) {
        return { state: { ...s, press: { index: 0, at: s.nodes[0].p, dragged: false, closing: true }, cursor: null } }
      }
      const nodes = [...s.nodes, corner(at)]
      return { state: { ...s, nodes, press: { index: nodes.length - 1, at, dragged: false, closing: false }, cursor: null } }
    }
    case 'move': {
      if (s.kind !== 'drawing') return { state: s }
      if (!s.press) {
        const last = s.nodes[s.nodes.length - 1].p
        return { state: { ...s, cursor: ev.shift ? lock45(last, ev.at) : ev.at } }
      }
      const { press } = s
      if (!press.dragged && dist(ev.at, press.at) <= opts.dragTolMm) return { state: s }
      const nodes = s.nodes.slice()
      const n = nodes[press.index]
      const p = n.p
      // ⇧：手柄方向锁在 45° 的倍数上
      const h = ev.shift ? lock45(p, ev.at) : ev.at
      nodes[press.index] =
        ev.alt || press.resume
          ? { p, in: n.in, out: h, smooth: false }
          : { p, in: [2 * p[0] - h[0], 2 * p[1] - h[1]], out: h, smooth: true }
      return { state: { ...s, nodes, press: { ...press, dragged: true } } }
    }
    case 'up': {
      if (s.kind !== 'drawing' || !s.press) return { state: s }
      if (s.press.closing) return { state: PEN_IDLE, commit: { closed: true, nodes: s.nodes } }
      return { state: { ...s, press: null, cursor: ev.at } }
    }
    case 'resume': {
      if (s.kind !== 'idle' || ev.nodes.length < 2) return { state: s }
      const last = ev.nodes[ev.nodes.length - 1]
      return {
        state: {
          kind: 'drawing',
          nodes: ev.nodes,
          press: { index: ev.nodes.length - 1, at: last.p, dragged: false, closing: false, resume: true },
          cursor: null,
        },
      }
    }
    case 'finish':
      return finish(s)
    case 'cancel':
      return { state: PEN_IDLE }
  }
}

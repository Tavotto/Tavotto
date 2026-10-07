import { createContext, useState, type FocusEvent, type ReactNode } from 'react'
import type { ProblemDrill } from '@/lib/problemList'

/*
 * 问题树的共用件（行组件在 `ProblemTreeRows.tsx`、分桶节点在 `ProblemCards.tsx`、面板在 `ProblemPanel.tsx`）。
 * 常量、hook 与 context 单独一个文件：与组件同文件会让 fast refresh 整页重载。
 */

/**
 * 一组默认只展开这么多行；再多的收进「显示其余 N 项」。
 * 23 条几乎一样的「字号低于绝对下限」逐条铺开，用户看到的是一面墙，而不是
 * 「一个问题、23 个对象、一颗修复」。
 */
export const PREVIEW_ROWS = 5
/** 只差一两条就不值得折：「显示其余 1 项」比直接列出来更啰嗦 */
export const MIN_HIDDEN_ROWS = 3

/** 尾随格的宽（88px）与名字那一侧给它让出的位置（88 + 行外边距） */
export const TRAIL_W = 'w-22'
export const TRAIL_PAD = 'pr-23'

/**
 * 「这一行此刻热不热」：指针在行或尾随格上 / 焦点在两者之内。行按钮与尾随格是兄弟节点（按钮套按钮读不出来），
 * 两个都摊上同一份 props。
 */
export function useHot() {
  const [hover, setHover] = useState(false)
  const [focus, setFocus] = useState(false)
  const bind = {
    onPointerEnter: () => setHover(true),
    onPointerLeave: () => setHover(false),
    onFocus: () => setFocus(true),
    onBlur: (e: FocusEvent) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocus(false)
    },
  }
  return { hot: hover || focus, bind }
}

/**
 * 面板把「哪一支开着」与「一支里面列什么」交给节点：开合状态与逐组清单都住在 `ProblemPanel`
 * （游标、折叠、「显示其余」要跨节点算），节点只管自己这一行。
 */
export interface ProblemTreeCtx {
  /** `dflt`：用户没碰过这一支时它开不开（拆成子图的图头默认开，其余默认收） */
  isOpen: (drill: ProblemDrill, dflt: boolean) => boolean
  toggle: (drill: ProblemDrill, dflt: boolean) => void
  /** 这一支里面的逐组清单（规则行 + 对象行），`depth` 是规则行的缩进层 */
  body: (drill: ProblemDrill, depth: number) => ReactNode
  activeCanvasId: string
}

export const ProblemTreeContext = createContext<ProblemTreeCtx | null>(null)


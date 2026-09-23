import { useMemo } from 'react'

/**
 * `dangerouslySetInnerHTML` 的那个 `{ __html }` 对象，**按字符串内容**复用同一个引用。
 *
 * React 19 判断这条属性变没变用的是 `===`，比的是对象不是字符串（react-dom 19.2
 * `updateProperties`：`nextProp === lastProp` 才跳过）。每次渲染现写 `{ __html: s }` 就是每次
 * 一个新对象 → 每次重渲都把整段 HTML 重新赋给 `innerHTML`：内容一个字没变，DOM 却整个重建。
 *
 * 画布上这有两个代价（2026-09-24 用户报「松手先弹回去、顿一下才到新位置」实测）：
 * 1. 拖动的预览位移是直接写在 SVG 节点上的（`svgPreviewStore`），松手提交 override 让面板重渲，
 *    innerHTML 被原样重写，预览位移随旧节点一起消失——元素弹回原位，等权威 SVG 到了才跳到新位置；
 *    而 SVG 字符串没变，负责重挂预览的 effect（依赖 `svgHtml`）也不会重跑；
 * 2. 每次重渲都把几百 KB 的 SVG 重新解析一遍。
 */
export function useHtmlMarkup(html: string | null | undefined): { __html: string } | undefined {
  return useMemo(() => (html == null ? undefined : { __html: html }), [html])
}

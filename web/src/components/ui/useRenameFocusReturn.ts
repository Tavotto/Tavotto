import { useCallback, useEffect, useRef, type RefObject } from 'react'

/**
 * 行内改名框收起后的焦点归还（Codex #833）——文档名、画布页签、画布列表行、图层行共用这一份判据。
 *
 * - 用 **Enter / Esc** 收起：框一卸载焦点就掉到 body 上，键盘用户得从头 Tab，所以还给 `target`
 *   （打开改名的那个按钮 / 页签 / 行）。
 * - **点了别处**收起（失焦提交）：不还——焦点已经在用户点的那个输入框 / 钮上了，抢回来会让接下来的
 *   打字和快捷键落进页签条 / 列表里。点在空白处（焦点落到 body）同样不还：那也是用户的选择。
 *
 * 判据的主语是「这一次收起是不是键盘发起的」，不是 `relatedTarget`（Enter 里程序化 `blur()` 与点空白
 * 处的 relatedTarget 都是 null，分不开）。用法：Enter / Esc 的处理里先调返回的 `markKeyFinish()`，
 * 再收框；`editing` 变成 false 的那次提交之后焦点落到 `target` 上。
 */
export function useRenameFocusReturn(
  editing: boolean,
  target: RefObject<HTMLElement | null>,
): () => void {
  const viaKey = useRef(false)
  useEffect(() => {
    if (editing) return
    const refocus = viaKey.current
    viaKey.current = false
    if (refocus) target.current?.focus()
  }, [editing, target])
  return useCallback(() => {
    viaKey.current = true
  }, [])
}

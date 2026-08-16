import { useEffect, useLayoutEffect, useRef } from 'react'
import { MM_PER_PT } from '@/lib/units'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, worldToMm } from '@/store/viewportStore'
import type { TextObject } from '@/types/document'

/**
 * 文字对象：字体固定为文档字体（Times / 宋体），与 UI 字体分离。
 * 高度由内容撑开后回写到文档，但不进 undo —— 它是渲染派生值。
 */
export function TextView({ obj }: { obj: TextObject }) {
  const editing = useUiStore((s) => s.editingTextId === obj.id)
  const setEditingText = useUiStore((s) => s.setEditingText)
  const ref = useRef<HTMLDivElement>(null)
  const heightRef = useRef(obj.h)
  heightRef.current = obj.h

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => {
      const hMm = worldToMm(el.offsetHeight)
      if (hMm > 0 && Math.abs(hMm - heightRef.current) > 0.05) {
        useDocumentStore.getState().silent((d) => {
          const o = d.objects.find((x) => x.id === obj.id)
          if (o) o.h = hMm
        })
      }
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [obj.id])

  // 进入编辑态时 React 已把子节点清空，这里接管 DOM 内容避免打字被重渲染覆盖
  useEffect(() => {
    if (!editing) return
    const el = ref.current
    if (!el) return
    el.innerText = obj.text
    el.focus()
    const range = document.createRange()
    range.selectNodeContents(el)
    const sel = window.getSelection()
    sel?.removeAllRanges()
    sel?.addRange(range)
    // obj.text 只在提交时变化，故意不作为依赖，防止打字过程中回灌
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing])

  const commitText = () => {
    const el = ref.current
    if (!el) return
    const text = el.innerText.replace(/\n$/, '')
    setEditingText(null)
    if (text !== obj.text) {
      useDocumentStore.getState().commit('编辑文字', (d) => {
        const o = d.objects.find((x) => x.id === obj.id)
        if (o && o.type === 'text') o.text = text
      })
    }
  }

  return (
    <div
      ref={ref}
      // plaintext-only：Enter 语义可控、粘贴不带富文本、DOM 里只有文本节点
      contentEditable={editing ? 'plaintext-only' : false}
      suppressContentEditableWarning
      spellCheck={false}
      onBlur={editing ? commitText : undefined}
      onKeyDown={
        editing
          ? (e) => {
              e.stopPropagation()
              if (e.key === 'Escape') {
                e.preventDefault()
                ref.current?.blur()
              } else if (e.key === 'Enter') {
                // 契约：Enter 提交；⌥/⌘/Ctrl+Enter 换行。
                // 不走 contentEditable 默认断行——WebKit 在行尾插的 <br>
                // 不可见，提交时又被当尾部空行裁掉，表现为「按了没反应」。
                e.preventDefault()
                if (e.altKey || e.metaKey || e.ctrlKey) {
                  document.execCommand('insertText', false, '\n')
                } else {
                  ref.current?.blur()
                }
              }
            }
          : undefined
      }
      onPointerDown={editing ? (e) => e.stopPropagation() : undefined}
      className="absolute left-0 top-0 w-full outline-none"
      style={{
        fontFamily: 'var(--font-doc)',
        fontSize: mmToWorld(obj.sizePt * MM_PER_PT),
        lineHeight: obj.lineHeight ?? 1.25,
        fontWeight: obj.bold ? 700 : 400,
        fontStyle: obj.italic ? 'italic' : 'normal',
        textDecoration: obj.underline ? 'underline' : undefined,
        color: obj.color,
        textAlign: obj.align,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        cursor: editing ? 'text' : 'inherit',
        // 背景 / 描边 / 内边距（内容盒不变：宽度扣除 padding 由 border-box 承担）
        boxSizing: 'border-box',
        padding: obj.padding ? mmToWorld(obj.padding) : undefined,
        backgroundColor: obj.bg ?? undefined,
        border: obj.borderColor
          ? `${Math.max(mmToWorld((obj.borderPt ?? 0.75) * MM_PER_PT), 0.5)}px solid ${obj.borderColor}`
          : undefined,
      }}
    >
      {editing ? null : obj.text}
    </div>
  )
}

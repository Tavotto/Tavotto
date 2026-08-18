import { useEffect, useLayoutEffect, useRef } from 'react'
import { msg } from '@/i18n'
import {
  parseRuns,
  plainText,
  SCRIPT_SIZE,
  SUB_DROP,
  SUP_RISE,
} from '@/lib/richText'
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
  const sizePx = mmToWorld(obj.sizePt * MM_PER_PT)

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
      useDocumentStore.getState().commit(msg('history.editText', undefined, 'inspector'), (d) => {
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
        fontSize: sizePx,
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
      {editing ? null : <RenderedText text={obj.text} sizePx={sizePx} />}
    </div>
  )
}

/**
 * 行内标记的渲染（上标 `^{…}` / 下标 `_{…}`）。
 *
 * 字号与基线偏移用**绝对像素**算，不用 em/百分比：`vertical-align` 的长度值
 * 是相对元素自身字号解析的，套在缩小后的 span 上会再乘一次比例，画布与
 * 导出就对不上了。常量取自 lib/richText.ts，与后端 richtext.py 同源。
 */
function RenderedText({ text, sizePx }: { text: string; sizePx: number }) {
  const runs = parseRuns(text)
  if (runs.every((r) => r.script === '')) return <>{plainText(text)}</>
  return (
    <>
      {runs.map((r, i) =>
        r.script === '' ? (
          <span key={i}>{r.text}</span>
        ) : (
          <span
            key={i}
            style={{
              fontSize: sizePx * SCRIPT_SIZE,
              // 正 = 抬高；vertical-align 的正值就是往上，故下标取负
              verticalAlign: sizePx * (r.script === 'sup' ? SUP_RISE : -SUB_DROP),
              lineHeight: 0, // 上下标不参与行盒高度，行距不被它撑开
            }}
          >
            {r.text}
          </span>
        ),
      )}
    </>
  )
}

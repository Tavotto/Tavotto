import { useEffect, useLayoutEffect, useRef } from 'react'
import { msg, t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import {
  DEFAULT_INTERPRETATION,
  interpretRuns,
  parseRuns,
  SCRIPT_SIZE,
  SUB_DROP,
  SUP_RISE,
  type TextInterpretation,
} from '@/lib/richText'
import { layerOf } from '@/lib/glyphPlan'
import { MM_PER_PT } from '@/lib/units'
import { selectionInkFor, type SelectionInk } from '@/lib/selectionInk'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, worldToMm } from '@/store/viewportStore'
import { canvasFontStack, effectiveCanvasFamily } from '@/lib/typography'
import type { TextObject } from '@/types/document'

/**
 * 纸上的墨按字实际坐着的底二选一（`lib/selectionInk`）：选中的字与编辑态的占位。类名写全（Tailwind 只扫字面量）。
 * 深底（用户把页面或文字框设成深色）用纸色，浅底用纸上的墨——两个 token 两套主题同值，不跟界面的 ink 走。
 */
const INK_ON_GROUND: Record<SelectionInk, { selection: string; placeholder: string }> = {
  'paper-ink': { selection: 'selection:text-paper-ink', placeholder: 'empty:before:text-paper-ink/65' },
  paper: { selection: 'selection:text-paper', placeholder: 'empty:before:text-paper/65' },
}

/**
 * 文字对象：字体族由对象自己选（`CANVAS_TEXT_FAMILIES` 三选一），没设过就是
 * 文档默认族（衬线 = `--font-doc`），与 UI 字体分离。
 * 高度由内容撑开后回写到文档，但不进 undo —— 它是渲染派生值。
 */
export function TextView({ obj }: { obj: TextObject }) {
  const editing = useUiStore((s) => s.editingTextId === obj.id)
  const pageBg = useDocumentStore((s) => s.doc.page.bg)
  const pageTransparent = useDocumentStore((s) => s.doc.page.transparent)
  const ink = INK_ON_GROUND[selectionInkFor(obj.bg, { bg: pageBg, transparent: pageTransparent })]
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
    // editing 在依赖里：div 带着 key 随编辑态重建（见下），必须重新 observe 新节点
  }, [obj.id, editing])

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
      // key 随编辑态变：编辑期 contentEditable 往 DOM 里写的文本节点不归
      // React 管，退出编辑时 React 只会把 <RenderedText> 插进去、不会清掉
      // 那些野节点——正文就显示两遍（键入「（a）」显示「（a）（a）」）。
      // 换 key 强制重建 div，两个方向的切换都从空 DOM 开始。
      key={editing ? 'editing' : 'static'}
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
      // 编辑时空着就写一句占位（2026-10-07 设计审计 §10.1）：此前清空之后框里什么都没有，看不出还在编辑。
      // 占位只在 ::before 里（不进 innerText、不会被提交成正文）
      data-placeholder={editing ? translate('stage.textPlaceholder', { ns: 'workspace' }) : undefined}
      className={cn(
        'absolute left-0 top-0 w-full outline-none',
        // 选中的字：全局 ::selection 换成界面的 ink——暗色里是浅灰，落在白纸上看不见；这里换纸上的墨。
        // 但纸不一定是白的：页面底色与文字框底色都由用户定，深底上换深墨又看不见（Codex P2）——按实际的底二选一
        ink.selection,
        // 占位画在同一块底上：所选墨的 65%（白纸上 ≈ 浅色里的 ink-3），不是界面的 ink-3（暗色里是浅灰，落在白纸上看不见）
        editing && cn('empty:before:pointer-events-none empty:before:content-[attr(data-placeholder)]', ink.placeholder),
      )}
      style={{
        fontFamily: canvasFontStack(effectiveCanvasFamily(obj)),
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
        // 插入点是界面的东西，不是正文的颜色：accent（与所有可编辑框同一条规矩，宪法第二十六节）
        caretColor: editing ? 'var(--color-accent)' : undefined,
        // 背景 / 描边 / 内边距（内容盒不变：宽度扣除 padding 由 border-box 承担）
        boxSizing: 'border-box',
        padding: obj.padding ? mmToWorld(obj.padding) : undefined,
        backgroundColor: obj.bg ?? undefined,
        border: obj.borderColor
          ? `${Math.max(mmToWorld((obj.borderPt ?? 0.75) * MM_PER_PT), 0.5)}px solid ${obj.borderColor}`
          : undefined,
      }}
    >
      {editing ? null : (
        <RenderedText text={obj.text} sizePx={sizePx} interpretation={obj.interpretation} />
      )}
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
function RenderedText({
  text,
  sizePx,
  interpretation,
}: {
  text: string
  sizePx: number
  interpretation?: TextInterpretation
}) {
  // 与导出读同一份判据：`layerOf` 的覆盖表就是 PDF 后端那三张脸的能力。
  // **浏览器自己的字体栈不参与这个判断**——它画得出 `⁵` 不代表导出画得出，
  // 拿它当判据的结果正是「预览好好的、导出上是个方框」。
  const runs = interpretRuns(parseRuns(text), {
    isPrimary: (cp) => layerOf(cp) === 'primary',
    isDrawable: (cp) => layerOf(cp) !== 'missing',
    mode: interpretation ?? DEFAULT_INTERPRETATION,
  })
  if (runs.every((r) => r.script === '')) return <>{runs.map((r) => r.text).join('')}</>
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

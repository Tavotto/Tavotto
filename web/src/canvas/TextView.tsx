import { useLayoutEffect, useRef, type CSSProperties } from 'react'
import { msg, t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import {
  DEFAULT_INTERPRETATION,
  interpretRuns,
  parseRuns,
  SCRIPT_SIZE,
  SUB_DROP,
  SUP_RISE,
  type TextFormat,
  type TextInterpretation,
  type TextRun,
} from '@/lib/richText'
import { layerOf } from '@/lib/glyphPlan'
import { annotationCodec } from '@/lib/richEditor'
import { RichTextField } from '@/components/ui/RichTextField'
import { MM_PER_PT } from '@/lib/units'
import { canvasSelectionFor, groundInkFor, type CanvasSelection, type SelectionInk } from '@/lib/canvasSelection'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, worldToMm } from '@/store/viewportStore'
import { canvasFontStack, effectiveCanvasFamily } from '@/lib/typography'
import type { TextObject } from '@/types/document'

/**
 * 纸上的文字选区（`lib/canvasSelection`）：不透明的选区底 + 配它的纸墨，字对选区底的对比度与用户的底色无关。
 * 类名写全（Tailwind 只扫字面量）；token 两套主题同值，不跟界面的 ink / accent 走。
 */
const SELECTION_CLASS: Record<CanvasSelection, string> = {
  light: 'selection:bg-paper-selection selection:text-paper-ink',
  deep: 'selection:bg-paper-selection-deep selection:text-paper',
}
/** 编辑态的占位落在底上本身：按底的深浅取纸墨的 65%（白纸上 ≈ 浅色里的 ink-3） */
const PLACEHOLDER_CLASS: Record<SelectionInk, string> = {
  'paper-ink': '[&_.ProseMirror[data-empty]]:before:text-paper-ink/65',
  paper: '[&_.ProseMirror[data-empty]]:before:text-paper/65',
}

/**
 * 文字对象：字体族由对象自己选（`CANVAS_TEXT_FAMILIES` 三选一），没设过就是
 * 文档默认族（衬线 = `--font-doc`），与 UI 字体分离。
 * 高度由内容撑开后回写到文档，但不进 undo —— 它是渲染派生值。
 */
export function TextView({
  obj,
  preview = false,
}: {
  obj: TextObject
  /** 仅用于结构缩略图：保留相同字形，但不测量并写回不存在的文档对象。 */
  preview?: boolean
}) {
  const editing = useUiStore((s) => s.editingTextId === obj.id)
  const pageBg = useDocumentStore((s) => s.doc.page.bg)
  const pageTransparent = useDocumentStore((s) => s.doc.page.transparent)
  const ground = { bg: pageBg, transparent: pageTransparent }
  const selectionClass = SELECTION_CLASS[canvasSelectionFor(obj.bg, ground)]
  const placeholderClass = PLACEHOLDER_CLASS[groundInkFor(obj.bg, ground)]
  const setEditingText = useUiStore((s) => s.setEditingText)
  const ref = useRef<HTMLDivElement>(null)
  const heightRef = useRef(obj.h)
  heightRef.current = obj.h
  const sizePx = mmToWorld(obj.sizePt * MM_PER_PT)

  useLayoutEffect(() => {
    // 缩略图的 ID 由 buildPreset 临时生成，不在用户文档中。
    // 之前每个 ResizeObserver 回调都会对整份 doc 做一次无效查找与 Immer produce。
    // 跳过仅文档持久化所需的高度回写，文字渲染本身完全不变。
    if (preview) return
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
  }, [obj.id, editing, preview])

  /**
   * 就地编辑（ADR 0152）：所见即所得文字框，Enter / Esc 提交，⌥/⌘/Ctrl+Enter 换行。
   * 打字期间只记在这里，失焦时整段写成**一条**历史（与改造前同一契约）；写回 LaTeX 子集并标 textFormat。
   */
  const pending = useRef<string | null>(null)
  const commitText = () => {
    const text = pending.current
    pending.current = null
    setEditingText(null)
    if (text !== null && text !== obj.text) {
      useDocumentStore.getState().commit(msg('history.editText', undefined, 'inspector'), (d) => {
        const o = d.objects.find((x) => x.id === obj.id)
        if (o && o.type === 'text') {
          o.text = text
          o.textFormat = 'latex'
        }
      })
    }
  }
  const blurEditor = () => {
    const ae = document.activeElement
    if (ae instanceof HTMLElement && ref.current?.contains(ae)) ae.blur()
    return true
  }

  return (
    <div
      // key 随编辑态变：编辑器与静态渲染各自从空 DOM 开始，ResizeObserver 也随之重挂
      // （历史：plaintext contentEditable 时代留下的野节点会让正文显示两遍）。
      key={editing ? 'editing' : 'static'}
      ref={ref}
      spellCheck={false}
      onPointerDown={editing ? (e) => e.stopPropagation() : undefined}
      className={cn(
        'absolute left-0 top-0 w-full outline-none',
        // 选区：全局 ::selection 是界面的（半透明 accent tint + 界面 ink）。纸上的底由用户定（深色页面、中灰文字框…），
        // tint 叠上去配哪种墨都可能不到 4.5:1（Codex P2 两轮）——纸上的选区自己定不透明的底与墨
        selectionClass,
        // 占位：纸墨 65%，不是界面的 ink-3（暗色里是浅灰，落在白纸上看不见）
        editing && placeholderClass,
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
      {editing ? (
        <RichTextField
          variant="inline"
          // 与属性页文字框同一个名字（「文字」）
          aria-label={translate('text.title', { ns: 'inspector' })}
          value={obj.text}
          codec={annotationCodec(obj.textFormat)}
          onChange={(text) => (pending.current = text)}
          onBlur={commitText}
          onEnter={blurEditor}
          onEscape={blurEditor}
          // 编辑时空着就写一句占位（2026-10-07 设计审计 §10.1），只在 ::before 里、不进正文
          placeholder={translate('stage.textPlaceholder', { ns: 'workspace' })}
          sizePx={sizePx}
          selectAllOnMount
          testId="canvas-text-editor"
        />
      ) : (
        <RenderedText
          text={obj.text}
          sizePx={sizePx}
          interpretation={obj.interpretation}
          format={obj.textFormat}
        />
      )}
    </div>
  )
}


/**
 * 行内标记的渲染（上标 / 下标、LaTeX 写法的行内斜体 / 粗体，ADR 0152）。
 *
 * 字号与基线偏移用**绝对像素**算，不用 em/百分比：`vertical-align` 的长度值
 * 是相对元素自身字号解析的，套在缩小后的 span 上会再乘一次比例，画布与
 * 导出就对不上了。常量取自 lib/richText.ts，与后端 richtext.py 同源。
 *
 * 公式片段在公式排版接入之前按源码原样画（`$…$` 连定界符）——与导出那一侧
 * （`typography.interpreted_pieces`）同一个约定。
 */
function RenderedText({
  text,
  sizePx,
  interpretation,
  format,
}: {
  text: string
  sizePx: number
  interpretation?: TextInterpretation
  format?: TextFormat
}) {
  // 与导出读同一份判据：`layerOf` 的覆盖表就是 PDF 后端那三张脸的能力。
  // **浏览器自己的字体栈不参与这个判断**——它画得出 `⁵` 不代表导出画得出，
  // 拿它当判据的结果正是「预览好好的、导出上是个方框」。
  const runs = interpretRuns(parseRuns(text, format), {
    isPrimary: (cp) => layerOf(cp) === 'primary',
    isDrawable: (cp) => layerOf(cp) !== 'missing',
    mode: interpretation ?? DEFAULT_INTERPRETATION,
  })
  const plain = (r: TextRun) => r.script === '' && !r.italic && !r.bold && !r.math
  if (runs.every(plain)) return <>{runs.map((r) => r.text).join('')}</>
  return (
    <>
      {runs.map((r, i) => {
        if (r.math) return <span key={i}>{`$${r.text}$`}</span>
        // 行内样式只在**打开**时写：对象自己是粗 / 斜体时，片段不写 normal 去覆盖它（与导出的「或」一致）
        const style: CSSProperties = {}
        if (r.italic) style.fontStyle = 'italic'
        if (r.bold) style.fontWeight = 700
        if (r.script !== '') {
          style.fontSize = sizePx * SCRIPT_SIZE
          // 正 = 抬高；vertical-align 的正值就是往上，故下标取负
          style.verticalAlign = sizePx * (r.script === 'sup' ? SUP_RISE : -SUB_DROP)
          style.lineHeight = 0 // 上下标不参与行盒高度，行距不被它撑开
        }
        return (
          <span key={i} style={style}>
            {r.text}
          </span>
        )
      })}
    </>
  )
}

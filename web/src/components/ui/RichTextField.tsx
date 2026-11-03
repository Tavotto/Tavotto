import 'prosemirror-view/style/prosemirror.css'
import { Slice } from 'prosemirror-model'
import { TextSelection } from 'prosemirror-state'
import { EditorView, type NodeView } from 'prosemirror-view'
import type { Node as PMNode } from 'prosemirror-model'
import { createRoot, type Root } from 'react-dom/client'
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useReducer,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react'
import { useTranslation } from 'react-i18next'
import { Bold, CornerDownLeft, Italic, Subscript, Superscript } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import {
  createRichState,
  docToRuns,
  insertBreak,
  markActive,
  runsToDoc,
  toggleRichMark,
  type RichCodec,
  type RichMark,
  type SourceError,
} from '@/lib/richEditor'
import type { TextRun } from '@/lib/richText'
import { ALT, cn, combo, modKey } from '@/lib/utils'
import { Button } from './Button'
import { FIELD_BOX, FIELD_FOCUS_WITHIN } from './fieldBox'
import { MathFormula } from './MathFormula'
import { MathPreviewStrip } from './MathPreviewStrip'
import { fitTextAreaHeight } from './Input'

/** 挂着的编辑器，按内容节点查（测试与 e2e 驱动选区用；界面代码不走这里） */
const VIEWS = new WeakMap<Element, EditorView>()
export const richViewOf = (el: Element | null): EditorView | undefined =>
  el ? VIEWS.get(el) : undefined

/**
 * 把浏览器当下的选区读进编辑器状态。ProseMirror 经 `selectionchange` **异步**跟上原生选区：
 * ⇧← 选中后紧接着按 ⌘↑，keymap 看到的还是没选中的光标，上标落在空选区上、字没变（真浏览器
 * 实测，e2e 复现）。样式快捷键与工具条命令执行前先同步一次——只用公开的 `posAtDOM`。
 */
function syncDomSelection(view: EditorView): void {
  const sel = view.dom.ownerDocument.getSelection()
  if (!sel || !sel.anchorNode || !sel.focusNode) return
  if (!view.dom.contains(sel.anchorNode) || !view.dom.contains(sel.focusNode)) return
  try {
    const anchor = view.posAtDOM(sel.anchorNode, sel.anchorOffset)
    const head = view.posAtDOM(sel.focusNode, sel.focusOffset)
    const cur = view.state.selection
    if (cur.anchor === anchor && cur.head === head) return
    view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, anchor, head)))
  } catch {
    // 落在编辑器不认的节点上（公式块内部等）：保持编辑器自己的选区
  }
}

/**
 * 公式块的显示：后端排好的公式（`MathFormula`，与导出同一份结果）。只有宿主给了 `mathFamily` 时才用它：
 * 标注给三族之一，图内文字给 `face:<图上画公式的那张脸>`（ADR 0152 后续）。
 * 族 / 字号由 `get` 现读，宿主换了（字体族改了）时 `render()` 就地重画，不重建编辑器（重建会丢焦点）。
 */
class MathChipView implements NodeView {
  dom: HTMLElement
  private root: Root
  private node: PMNode
  private get: () => { family: string; sizePx: number; live: boolean }
  private all: Set<MathChipView>
  constructor(node: PMNode, get: () => { family: string; sizePx: number; live: boolean }, all: Set<MathChipView>) {
    this.node = node
    this.get = get
    this.all = all
    this.dom = document.createElement('span')
    this.dom.className = 'rt-math'
    this.dom.contentEditable = 'false'
    this.dom.dataset.rtMath = node.attrs.src as string
    this.root = createRoot(this.dom)
    all.add(this)
    this.render()
  }
  render() {
    const { family, sizePx, live } = this.get()
    // 源码模式时这份文档是隐藏的、每个键都被换一遍：不排版（否则每键一个请求），切回来再排
    if (!live) {
      this.root.render(<span>{this.node.attrs.src as string}</span>)
      return
    }
    this.root.render(<MathFormula src={this.node.attrs.src as string} family={family} sizePx={sizePx} />)
  }
  ignoreMutation() {
    return true
  }
  destroy() {
    this.all.delete(this)
    const root = this.root
    // 卸载推迟一拍：ProseMirror 在自己的更新里调 destroy，同步卸载另一个 React 根会被 React 警告
    queueMicrotask(() => root.unmount())
  }
}

/** 两串片段画出来是否一样（逐字段比，不依赖键序；可选标记缺省 = 假） */
function sameRuns(a: TextRun[], b: TextRun[]): boolean {
  return (
    a.length === b.length &&
    a.every(
      (r, i) =>
        r.text === b[i].text && r.script === b[i].script && !!r.italic === !!b[i].italic &&
        !!r.bold === !!b[i].bold && !!r.math === !!b[i].math && !!r.raw === !!b[i].raw,
    )
  )
}

/** 本组文案在 inspector:richText.* 下 */
const rt = (key: string, values?: Record<string, unknown>) =>
  translate(`richText.${key}`, { ns: 'inspector', ...(values ?? {}) })

export interface RichTextFieldHandle {
  focus(): void
  /** 全选（与 textarea 的 `select()` 同名，宿主的「选中即可打字」直接复用） */
  select(): void
  /** 当前选区上执行一条样式命令（宿主自己的按钮用；没挂上时什么都不做） */
  run(mark: RichMark | 'break'): void
}

export interface RichTextFieldProps {
  /** 存储的字符串（唯一权威）；外部改了（撤销、换选中对象）编辑框跟着换 */
  value: string
  codec: RichCodec
  /** 只在用户真的改了内容时调用，参数是规范形字符串 */
  onChange: (text: string) => void
  onFocus?: () => void
  onBlur?: () => void
  /** 裸 Enter：返回 true = 宿主处理了（提交 / 关闭）；缺省 = 换行 */
  onEnter?: () => boolean
  onEscape?: () => boolean
  /**
   * `field`：属性栏里带框、带工具条的多行文字框（与 `TextArea` 同一副框）。
   * `inline`：画布上就地编辑，没有框和工具条，样子由宿主的 `style` 决定。
   */
  variant?: 'field' | 'inline'
  'aria-label': string
  placeholder?: string
  /** 正文字号（px）：上下标的绝对字号与基线由它算（`--rt-size`） */
  sizePx?: number
  /**
   * 公式的排版族：给了就把公式块画成排好的公式、并在源码模式下方出预览条（ADR 0152 PR 4）。
   * 标注传画布字体族（`serif` / `sans-serif` / `monospace`）；图内文字传 `face:<图上画公式的那张脸>`；不给显示源码。
   */
  mathFamily?: string
  /** 工具条右侧宿主自己的按钮（大小写菜单等） */
  extra?: ReactNode
  /** 框最多长到几行再滚动（`field`） */
  maxRows?: number
  autoFocus?: boolean
  /** 挂上时全选（画布上双击进入编辑） */
  selectAllOnMount?: boolean
  className?: string
  style?: CSSProperties
  contentClassName?: string
  /** 测试与 e2e 的稳定锚点 */
  testId?: string
}

/**
 * 所见即所得文字框（ADR 0152）：斜体 / 粗体 / 上下标在框里直接显示，按钮与快捷键作用在选区上。
 * 存储仍是字符串——见 `lib/richEditor.ts`。
 */
export const RichTextField = forwardRef<RichTextFieldHandle, RichTextFieldProps>(function RichTextField(
  {
    value,
    codec,
    onChange,
    onFocus,
    onBlur,
    onEnter,
    onEscape,
    variant = 'field',
    'aria-label': ariaLabel,
    placeholder,
    sizePx,
    mathFamily,
    extra,
    maxRows = 6,
    autoFocus,
    selectAllOnMount,
    className,
    style,
    contentClassName,
    testId,
  },
  ref,
) {
  useTranslation('inspector')
  const host = useRef<HTMLDivElement>(null)
  const viewRef = useRef<EditorView | null>(null)
  // 宿主的回调每次渲染都可能是新的：键位与事务处理只读 ref，不随它重建编辑器
  const cb = useRef({ codec, onChange, onEnter, onEscape, onFocus, onBlur })
  cb.current = { codec, onChange, onEnter, onEscape, onFocus, onBlur }
  /** 上一次由编辑框写出去的字符串：外部 value 等于它就不回灌（不打断光标） */
  const emitted = useRef(value)
  /**
   * 编辑框（所见即所得或源码框）刚写出去的那一串——宿主随即可能换写法：第一次改老标注标 textFormat。
   * 认出这次回声就不重读：所见即所得里重读会把光标甩到末尾，源码框里会改写用户正在打的字。只在**紧接着的那次渲染**
   * 有效（下面无依赖的 effect 每次渲染后清掉）：同一串字的写法变化若来自别处（换选中了一个字符串相同、
   * 写法不同的标注），它已经被清掉，照常重读（Codex #1492 复核 P1）
   */
  const ownEcho = useRef<string | null>(null)
  const [, bump] = useReducer((n: number) => n + 1, 0)
  /**
   * LaTeX 模式（ADR 0152 PR 3，只有 `field` 形态有）：直接改存着的字符串（标注显示 LaTeX 子集的规范形）。
   * 两种模式改的是同一个字符串；源码有结构错误时不许切回，错在哪一行字说清。
   */
  const [mode, setMode] = useState<'rich' | 'latex'>('rich')
  const [source, setSource] = useState('')
  const [sourceError, setSourceError] = useState<SourceError | null>(null)
  const sourceRef = useRef<HTMLTextAreaElement>(null)
  const enterLatex = useRef<() => void>(() => {})
  /** 源码框的光标位置：预览条跟着光标所在行 */
  const [caret, setCaret] = useState(0)
  /** 挂着的公式块视图与它们现读的族 / 字号（见 `MathChipView`） */
  const chips = useRef(new Set<MathChipView>())
  const mathProps = useRef({ family: mathFamily ?? '', sizePx: sizePx ?? 13, live: true })

  useLayoutEffect(() => {
    if (!host.current) return
    let pending = false
    /** 此刻是否持有焦点：带着焦点被卸载时浏览器的 blur 不一定来得及触发（见下方 cleanup） */
    let focused = false
    const flush = (view: EditorView) => {
      pending = false
      const runs = docToRuns(view.state.doc)
      const text = cb.current.codec.serialize(runs)
      // 写法表达不了的样式（图内文字里的中文斜体等）写回时会被丢掉：框里也要跟着丢，
      // 不然框里是斜体、图上是正体——所见即所得就是假的。字不变，只换样式，选区原位保留
      const codec = cb.current.codec
      const back = (codec.reparse ?? codec.parse)(text)
      if (!sameRuns(back, runs)) {
        const { from, to } = view.state.selection
        const tr = view.state.tr.replaceWith(0, view.state.doc.content.size, runsToDoc(back).content)
        const max = tr.doc.content.size
        tr.setSelection(TextSelection.create(tr.doc, Math.min(from, max), Math.min(to, max)))
        view.dispatch(tr.setMeta('rt-external', true).setMeta('addToHistory', false))
      }
      if (text === emitted.current) return
      emitted.current = text
      ownEcho.current = text
      cb.current.onChange(text)
    }
    const view = new EditorView(host.current, {
      state: createRichState(cb.current.codec.parse(value), {
        onEnter: () => cb.current.onEnter?.() ?? false,
        onEscape: () => cb.current.onEscape?.() ?? false,
      }),
      ...(mathFamily
        ? { nodeViews: { math: (node: PMNode) => new MathChipView(node, () => mathProps.current, chips.current) } }
        : {}),
      attributes: (state) => ({
        role: 'textbox',
        'aria-multiline': 'true',
        'aria-label': ariaLabel,
        'data-rich-text': variant,
        ...(placeholder ? { 'data-placeholder': placeholder } : {}),
        ...(state.doc.content.size === 0 ? { 'data-empty': '' } : {}),
      }),
      // 粘贴：认得出公式（`$…$` / `\(…\)`……）或样式命令就按片段放进来；认不出交给默认粘贴，一字不改。
      // 自己复制出去的（带 `data-rt` 标签）走默认的 HTML 读入，样式原样
      handlePaste: (v, event, slice) => {
        const html = event.clipboardData?.getData('text/html') ?? ''
        if (html.includes('data-rt')) return false
        const text =
          event.clipboardData?.getData('text/plain') || slice.content.textBetween(0, slice.content.size, '\n')
        const runs = text ? cb.current.codec.pasteRuns?.(text) : null
        if (!runs) return false
        v.dispatch(v.state.tr.replaceSelection(new Slice(runsToDoc(runs).content, 0, 0)).scrollIntoView())
        return true
      },
      // 双击公式块：切到 LaTeX 模式改源码（画布上就地编辑没有源码模式，什么都不做）
      handleDoubleClickOn: (_v, _pos, node) => {
        if (node.type.name !== 'math' || variant !== 'field') return false
        enterLatex.current()
        return true
      },
      dispatchTransaction(tr) {
        const v = viewRef.current
        if (!v) return
        v.updateState(v.state.apply(tr))
        bump()
        if (!tr.docChanged || tr.getMeta('rt-external')) return
        // 输入法组字期间不写回：每个拼音字母都写一次文档，撤销历史就碎成一地
        if (v.composing) pending = true
        else flush(v)
      },
      handleDOMEvents: {
        compositionend: (v) => {
          queueMicrotask(() => pending && flush(v))
          return false
        },
        focus: () => {
          focused = true
          cb.current.onFocus?.()
          return false
        },
        blur: (v) => {
          focused = false
          if (pending) flush(v)
          cb.current.onBlur?.()
          return false
        },
        // 键盘事件留在框里：画布与全局快捷键（删除对象、方向键微调……）不该在打字时触发。
        // 宿主没接 Esc 的（快捷编辑弹层）放 Esc 出去：关弹层由外层统一接管
        keydown: (v, e) => {
          if (!(e.key === 'Escape' && !cb.current.onEscape)) e.stopPropagation()
          if (e.metaKey || e.ctrlKey) syncDomSelection(v)
          return false
        },
      },
    })
    viewRef.current = view
    VIEWS.set(view.dom, view)
    if (selectAllOnMount) {
      const { tr, doc } = view.state
      view.dispatch(tr.setSelection(TextSelection.create(doc, 0, doc.content.size)))
    }
    if (autoFocus || selectAllOnMount) view.focus()
    return () => {
      // 带着焦点被卸载（点画布空白取消选择 / 退出就地编辑：宿主先于浏览器移焦把我们摘掉）时，
      // 浏览器不保证再给一次 blur。宿主在 onFocus 开的事务、画布就地编辑待写的内容都靠 onBlur 收尾——
      // 这里补上，保证「任何卸载路径之前，打开的文字编辑事务先提交关闭」（QA-WIN-14 / QA-WIN-28）
      if (focused) {
        focused = false
        if (pending) flush(view)
        cb.current.onBlur?.()
      }
      viewRef.current = null
      view.destroy()
    }
    // 编辑器只挂一次；value 的变化走下面的同步，回调走 ref
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 外部改了字符串（撤销 / 重做、换选中对象、大小写菜单）或换了写法：整段换掉，不进编辑框历史。
  // 写法变了字符串不变也要重读——两个标注字符串相同、一个旧标记一个 LaTeX 子集，画出来不同（Codex #1489）
  const parsedWith = useRef(codec)
  useEffect(() => {
    const view = viewRef.current
    const valueChanged = value !== emitted.current
    if (!view || (!valueChanged && codec === parsedWith.current)) return
    emitted.current = value
    parsedWith.current = codec
    // 自己刚写出去的回声：文档已经是对的（所见即所得本来就是它；源码模式在 editSource 里同步过）
    if (!valueChanged && ownEcho.current === value) return
    // 外部真改了字符串（撤销 / 重做、换选中对象）：源码框换成**新值经 codec 读写后的规范形**——
    // 老标注的原文是旧标记，直接塞进源码框会在切回时被当 LaTeX 读（Codex #1492 P1）
    setSource(cb.current.codec.serialize(cb.current.codec.parse(value)))
    const doc = runsToDoc(cb.current.codec.parse(value))
    const tr = view.state.tr.replaceWith(0, view.state.doc.content.size, doc.content)
    view.dispatch(tr.setMeta('rt-external', true).setMeta('addToHistory', false))
  }, [value, codec])
  mathProps.current = { family: mathFamily ?? '', sizePx: sizePx ?? 13, live: mode === 'rich' }
  // 公式的族 / 字号变了（图内文字改了字体族，公式字体集跟着换）：已挂着的公式块就地重画
  useEffect(() => {
    for (const c of chips.current) c.render()
  }, [mathFamily, sizePx, mode])
  // 回声只认紧接着的那次渲染（这条 effect 排在上面那条之后，同一次提交里后跑）
  useEffect(() => {
    ownEcho.current = null
  })

  const run = (mark: RichMark | 'break') => {
    const view = viewRef.current
    if (!view) return
    // 先聚焦再改：宿主在 onFocus 里开编辑事务，改动要落进那个事务（无事务的更新会被文档层丢弃）
    view.focus()
    syncDomSelection(view)
    if (mark === 'break') insertBreak(view.state, view.dispatch)
    else toggleRichMark(mark)(view.state, view.dispatch)
  }
  const selectAll = () => {
    const view = viewRef.current
    if (!view) return
    const { doc, tr } = view.state
    view.dispatch(tr.setSelection(TextSelection.create(doc, 0, doc.content.size)))
  }
  useImperativeHandle(ref, () => ({ focus: () => viewRef.current?.focus(), select: selectAll, run }))

  enterLatex.current = () => {
    const view = viewRef.current
    // 标注：老标注也显示成 LaTeX 子集的规范形；不改就不写回（只有源码框的输入才写）
    setSource(view ? codec.serialize(docToRuns(view.state.doc)) : value)
    setSourceError(null)
    setMode('latex')
    requestAnimationFrame(() => sourceRef.current?.focus())
  }
  const leaveLatex = () => {
    const err = codec.validate?.(source) ?? null
    setSourceError(err)
    if (err) {
      sourceRef.current?.focus()
      return
    }
    // 编辑框文档在源码输入时已逐次同步（见 editSource），这里只换回显示
    setMode('rich')
    requestAnimationFrame(() => viewRef.current?.focus())
  }
  const editSource = (next: string) => {
    setSource(next)
    if (sourceError) setSourceError(codec.validate?.(next) ?? null)
    // 源码的每次改动也落进编辑框的文档与它的撤销历史：切回所见即所得后 ⌘Z 撤得掉刚才在源码里的改动
    // （Codex #1492）。标 rt-external：写出去的是用户打的原文，不让 flush 再按规范形写一遍
    const view = viewRef.current
    if (view) {
      const doc = runsToDoc((codec.reparse ?? codec.parse)(next))
      view.dispatch(
        view.state.tr.replaceWith(0, view.state.doc.content.size, doc.content).setMeta('rt-external', true),
      )
    }
    if (next === emitted.current) return
    emitted.current = next
    ownEcho.current = next
    cb.current.onChange(next)
  }
  useLayoutEffect(() => {
    if (mode === 'latex' && sourceRef.current) fitTextAreaHeight(sourceRef.current, maxRows)
  }, [mode, source, maxRows])

  const active = (mark: RichMark) => (viewRef.current ? markActive(viewRef.current.state, mark) : false)
  const vars = { '--rt-size': sizePx ? `${sizePx}px` : undefined } as CSSProperties

  const content = (
    <div
      ref={host}
      data-testid={testId}
      className={cn(
        // ProseMirror 的内容节点是 host 的子节点：样式经后代选择器给到它
        '[&_.ProseMirror]:outline-none [&_.ProseMirror]:whitespace-pre-wrap [&_.ProseMirror]:break-words',
        // 空着时的占位只在 ::before 里，不进正文
        '[&_.ProseMirror[data-empty]]:before:pointer-events-none',
        // 占位的颜色：框里用界面的 ink-3；画布上就地编辑由宿主按纸的底色给（白纸上的浅灰在深色页面上看不见）
        variant === 'field' && '[&_.ProseMirror[data-empty]]:before:text-ink-3',
        '[&_.ProseMirror[data-empty]]:before:content-[attr(data-placeholder)]',
        // 公式块：整块是一个对象
        '[&_.rt-math]:rounded-sm [&_.rt-math]:bg-accent-subtle [&_.rt-math]:px-0.5',
        contentClassName,
      )}
      style={variant === 'field' ? { ...vars, maxHeight: `${maxRows * 1.625 + 0.75}em`, overflowY: 'auto' } : vars}
    />
  )

  if (variant === 'inline') {
    return (
      <div className={className} style={{ ...style, ...vars }}>
        {content}
      </div>
    )
  }

  const tool = (mark: RichMark, icon: ReactNode, key: string, hotkey: string) => (
    <Button
      size="icon-xs"
      data-rich-action={mark}
      aria-pressed={active(mark)}
      className={cn('text-ink-2', active(mark) && 'bg-accent-subtle text-accent')}
      // 按下不抢焦点：选区要留在框里，命令才知道作用在哪段字上
      onPointerDown={(e) => e.preventDefault()}
      onClick={() => run(mark)}
      aria-label={rt(key)}
      title={rt(`${key}Title`, { key: hotkey })}
    >
      {icon}
    </Button>
  )

  const latex = mode === 'latex'
  return (
    <div
      className={cn('relative w-full min-w-0 text-ink', FIELD_BOX, FIELD_FOCUS_WITHIN, className)}
      style={style}
      data-rich-mode={mode}
    >
      <div className={cn('px-2 pt-1.5 pb-7 leading-relaxed', latex && 'hidden')}>{content}</div>
      {latex && (
        <div className="px-2 pt-1.5 pb-7">
          <textarea
            ref={sourceRef}
            aria-label={ariaLabel}
            data-rich-source
            rows={1}
            spellCheck={false}
            value={source}
            onChange={(e) => {
              setCaret(e.target.selectionStart)
              editSource(e.target.value)
            }}
            onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
            onFocus={() => cb.current.onFocus?.()}
            onBlur={() => cb.current.onBlur?.()}
            onKeyDown={(e) => {
              if (!(e.key === 'Escape' && !cb.current.onEscape)) e.stopPropagation()
              if (e.key === 'Escape' && cb.current.onEscape?.()) e.preventDefault()
              // Enter 的契约与所见即所得一致：宿主接了 Enter（提交）就提交；⌥/⌘/Ctrl+Enter 换行
              if (e.key === 'Enter' && !(e.altKey || e.metaKey || e.ctrlKey || e.shiftKey) && cb.current.onEnter?.()) {
                e.preventDefault()
              }
            }}
            className="block w-full min-w-0 resize-none bg-transparent font-mono text-sm leading-relaxed text-ink outline-none"
          />
          {mathFamily && (
            <MathPreviewStrip
              source={source}
              caret={caret}
              codec={codec}
              family={mathFamily}
              sizePx={(sizePx ?? 13) + 3}
              label={rt('preview')}
            />
          )}
          {sourceError && (
            <p data-rich-source-error className="mt-1 text-xs text-danger" role="alert">
              {rt(sourceError.code === 'brace' ? 'errorBrace' : 'errorDollar', { at: sourceError.at })}
            </p>
          )}
        </div>
      )}
      <div className="absolute bottom-1 left-1 flex items-center">
        <Button
          size="icon-xs"
          data-rich-action="mode"
          active={latex}
          aria-pressed={latex}
          // 与右边那排图标钮同一档高度；字比图标宽，宽度随字
          className={cn('w-auto px-1 font-mono text-xs', !latex && 'text-ink-3')}
          onPointerDown={(e) => e.preventDefault()}
          onClick={() => (latex ? leaveLatex() : enterLatex.current())}
          title={rt(latex ? 'modeToRichTitle' : 'modeToLatexTitle')}
          aria-label={rt('modeLatex')}
        >
          {rt('modeShort')}
        </Button>
      </div>
      <div className="absolute bottom-1 right-1 flex items-center gap-0.5">
        {!latex && (
          <>
            {tool('bold', <Bold size={ICON_SIZE.sm} />, 'bold', modKey('B'))}
            {tool('italic', <Italic size={ICON_SIZE.sm} />, 'italic', modKey('I'))}
            {tool('sup', <Superscript size={ICON_SIZE.sm} />, 'sup', modKey('↑'))}
            {tool('sub', <Subscript size={ICON_SIZE.sm} />, 'sub', modKey('↓'))}
            <Button
              size="icon-xs"
              data-rich-action="break"
              className="text-ink-2"
              onPointerDown={(e) => e.preventDefault()}
              onClick={() => run('break')}
              aria-label={rt('newline')}
              title={rt('newlineTitle', { alt: combo(ALT, '⏎'), mod: modKey('⏎') })}
            >
              <CornerDownLeft size={ICON_SIZE.sm} />
            </Button>
          </>
        )}
        {extra}
      </div>
    </div>
  )
})

import 'prosemirror-view/style/prosemirror.css'
import { TextSelection } from 'prosemirror-state'
import { EditorView } from 'prosemirror-view'
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useReducer,
  useRef,
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
} from '@/lib/richEditor'
import type { TextRun } from '@/lib/richText'
import { ALT, cn, combo, modKey } from '@/lib/utils'
import { Button } from './Button'
import { FIELD_BOX, FIELD_FOCUS_WITHIN } from './fieldBox'

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
  const [, bump] = useReducer((n: number) => n + 1, 0)

  useLayoutEffect(() => {
    if (!host.current) return
    let pending = false
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
      cb.current.onChange(text)
    }
    const view = new EditorView(host.current, {
      state: createRichState(cb.current.codec.parse(value), {
        onEnter: () => cb.current.onEnter?.() ?? false,
        onEscape: () => cb.current.onEscape?.() ?? false,
      }),
      attributes: (state) => ({
        role: 'textbox',
        'aria-multiline': 'true',
        'aria-label': ariaLabel,
        'data-rich-text': variant,
        ...(placeholder ? { 'data-placeholder': placeholder } : {}),
        ...(state.doc.content.size === 0 ? { 'data-empty': '' } : {}),
      }),
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
          cb.current.onFocus?.()
          return false
        },
        blur: (v) => {
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
      viewRef.current = null
      view.destroy()
    }
    // 编辑器只挂一次；value 的变化走下面的同步，回调走 ref
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 外部改了字符串（撤销 / 重做、换选中对象、大小写菜单）：整段换掉，不进编辑框历史
  useEffect(() => {
    const view = viewRef.current
    if (!view || value === emitted.current) return
    emitted.current = value
    const doc = runsToDoc(cb.current.codec.parse(value))
    const tr = view.state.tr.replaceWith(0, view.state.doc.content.size, doc.content)
    view.dispatch(tr.setMeta('rt-external', true).setMeta('addToHistory', false))
  }, [value])

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
        // 公式块：PR 4 之前显示源码，整块是一个对象
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

  return (
    <div
      className={cn('relative w-full min-w-0 text-ink', FIELD_BOX, FIELD_FOCUS_WITHIN, className)}
      style={style}
    >
      <div className="px-2 pt-1.5 pb-7 leading-relaxed">{content}</div>
      <div className="absolute bottom-1 right-1 flex items-center gap-0.5">
        {tool('bold', <Bold size={ICON_SIZE.xs} />, 'bold', modKey('B'))}
        {tool('italic', <Italic size={ICON_SIZE.xs} />, 'italic', modKey('I'))}
        {tool('sup', <Superscript size={ICON_SIZE.xs} />, 'sup', modKey('↑'))}
        {tool('sub', <Subscript size={ICON_SIZE.xs} />, 'sub', modKey('↓'))}
        <Button
          size="icon-xs"
          data-rich-action="break"
          className="text-ink-2"
          onPointerDown={(e) => e.preventDefault()}
          onClick={() => run('break')}
          aria-label={rt('newline')}
          title={rt('newlineTitle', { alt: combo(ALT, '⏎'), mod: modKey('⏎') })}
        >
          <CornerDownLeft size={ICON_SIZE.xs} />
        </Button>
        {extra}
      </div>
    </div>
  )
})


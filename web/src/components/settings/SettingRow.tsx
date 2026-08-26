import { useRef, useState, type ReactNode } from 'react'
import { CircleQuestionMark, TriangleAlert } from 'lucide-react'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { Popover } from '../ui/Popover'

/**
 * 设置页的基础构件。
 *
 * 修改前每个分区都长成 `<Row/> <p>说明</p> <p>更多说明</p>`：控件与解释文字
 * 视觉权重接近，整页读起来像说明书而不是设置（见
 * `docs/ux/img/ux-consistency-pass/before/zh-1440-settings-about.png`）。
 *
 * 本轮的分工：
 *   * `SettingRow`  —— 标签 + 控件 + 可选的一句状态摘要 + 可选帮助；
 *   * `HelpTip`     —— 解释性内容的唯一落点（小问号）；
 *   * `InlineWarning` —— **只**给写源文件 / 清数据 / 隐私授权 / 当前错误 /
 *     缺件 / 不可逆操作，普通说明不许伪装成警告；
 *   * `DiagnosticDisclosure` —— 路径 / 版本 / 包清单 / 原始状态码，默认折叠。
 */

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 一个设置分区：小标题 + 若干行。分区之间靠留白与一条极淡的线分层 */
export function SettingSection({
  title,
  children,
  className,
}: {
  title?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={cn('flex flex-col gap-1.5', className)}>
      {title != null && (
        <h3 className="text-xs font-medium uppercase tracking-[.06em] text-ink-3">{title}</h3>
      )}
      {children}
    </section>
  )
}

/**
 * 一行设置。行高、标签列宽、对齐在这里统一——修改前每个分区各写各的
 * `<label className="flex min-h-7 …">`，换个分区标签列就差几个像素。
 */
export function SettingRow({
  label,
  help,
  helpLabel,
  status,
  children,
  danger,
  labelWidth = 112,
}: {
  label: ReactNode
  /** 解释性内容。给了就在标签后放一个小问号，**不在行下再堆一段** */
  help?: ReactNode
  /** 问号的可达名；缺省用「关于<标签>」 */
  helpLabel?: string
  /** 一句话的现状摘要（「已开启只读」这类），跟在控件后面，不是解释 */
  status?: ReactNode
  children: ReactNode
  /** 这一行的当前状态有真实副作用（只读模式、写源文件…） */
  danger?: boolean
  labelWidth?: number
}) {
  const labelText = typeof label === 'string' ? label : ''
  return (
    <div className="flex min-h-7 items-center gap-2">
      <span
        style={{ width: labelWidth }}
        className={cn('flex shrink-0 items-center gap-1 text-xs', danger ? 'text-ink' : 'text-ink-2')}
      >
        <span className="min-w-0 truncate" title={labelText || undefined}>
          {label}
        </span>
        {help != null && (
          <HelpTip label={helpLabel ?? st('helpAbout', { label: labelText })}>{help}</HelpTip>
        )}
      </span>
      <span className="flex min-w-0 flex-1 items-center gap-2">{children}</span>
      {status != null && (
        <span className="min-w-0 shrink truncate text-right text-xs text-ink-3">{status}</span>
      )}
    </div>
  )
}

/**
 * 小问号。**四种触发方式都要真的能用**：鼠标悬停、键盘聚焦、点击、触摸。
 *
 * 实现是**一个** Radix Popover（不是 Tooltip 套 Popover）：
 *   * 悬停 / 聚焦 → 开（`keepFocus` 阻止焦点被搬进浮层，否则鼠标划过一个
 *     问号就会抢走键盘焦点，Tab 顺序当场错乱）；
 *   * 点击 → 开关（触屏上点击是唯一手势）；
 *   * Esc / 点外面 → 关（Radix 自带，挂在 document 上）。
 *
 * 只有一层浮层，所以不会出现嵌套焦点陷阱；内容仍可 Tab 进去点里面的链接。
 */
export function HelpTip({
  label,
  children,
  width = 260,
}: {
  label: string
  children: ReactNode
  width?: number
}) {
  const [open, setOpen] = useState(false)
  const closeTimer = useRef<number | undefined>(undefined)
  const cancelClose = () => {
    window.clearTimeout(closeTimer.current)
    closeTimer.current = undefined
  }
  // 指针离开后留一点时间：鼠标从问号移到气泡上的路径不该把它关掉
  const scheduleClose = () => {
    cancelClose()
    closeTimer.current = window.setTimeout(() => setOpen(false), 220)
  }
  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      width={width}
      side="top"
      align="start"
      keepFocus
      ariaLabel={label}
      trigger={
        <button
          type="button"
          aria-label={label}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          onPointerEnter={(e) => {
            // 触屏的 pointerenter 与 click 会连着来；只让鼠标走悬停这条路
            if (e.pointerType !== 'mouse') return
            cancelClose()
            setOpen(true)
          }}
          onPointerLeave={(e) => {
            if (e.pointerType !== 'mouse') return
            scheduleClose()
          }}
          onFocus={() => {
            cancelClose()
            setOpen(true)
          }}
          onBlur={scheduleClose}
          className={cn(
            'flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-ink-3',
            'outline-none transition-colors hover:text-ink-2 focus-visible:focus-ring',
          )}
        >
          <CircleQuestionMark size={12} aria-hidden />
        </button>
      }
    >
      <div
        onPointerEnter={cancelClose}
        onPointerLeave={scheduleClose}
        className="flex flex-col gap-1.5 text-xs leading-relaxed text-ink-2"
      >
        {children}
      </div>
    </Popover>
  )
}

/**
 * 行内警示。**只**给：写回源文件、清除数据、隐私授权、不可逆操作、当前错误、
 * 环境缺件、当前设置产生的重要副作用。
 *
 * 普通说明不许用这个壳——把「语言选完立刻生效」画成警告，用户就学会了
 * 忽略所有警告，真正的那条也一起被忽略。
 */
export function InlineWarning({
  children,
  tone = 'warn',
}: {
  children: ReactNode
  /** warn：需要留意的副作用；danger：错误或不可逆 */
  tone?: 'warn' | 'danger'
}) {
  return (
    <p
      role={tone === 'danger' ? 'alert' : undefined}
      className={cn(
        'flex items-start gap-1.5 rounded-sm px-2 py-1.5 text-xs leading-relaxed',
        tone === 'danger' ? 'bg-danger/[.07] text-danger' : 'bg-ink/[.045] text-ink-2',
      )}
    >
      <TriangleAlert size={12} className="mt-px shrink-0" aria-hidden />
      <span className="min-w-0">{children}</span>
    </p>
  )
}

/**
 * 诊断折叠区：解释器路径、Python / matplotlib 版本、CLI 路径、包清单、
 * 日志、原始状态码。**默认折叠**——它们是排障材料，不是首屏信息。
 */
export function DiagnosticDisclosure({
  title,
  action,
  children,
  defaultOpen = false,
}: {
  title: string
  /** 折叠头右侧的动作（导出诊断包…），不随展开消失 */
  action?: ReactNode
  children: ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className={cn(
            'flex h-7 min-w-0 flex-1 items-center gap-1 rounded-sm text-left text-xs',
            'text-ink-2 outline-none hover:text-ink focus-visible:focus-ring',
          )}
        >
          <Chevron open={open} />
          <span className="font-medium">{title}</span>
        </button>
        {action}
      </div>
      {open && <div className="flex flex-col gap-1 border-l border-border pl-2">{children}</div>}
    </div>
  )
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      width="11"
      height="11"
      viewBox="0 0 11 11"
      aria-hidden
      className={cn('shrink-0 transition-transform', open && 'rotate-90')}
    >
      <path d="M4 2.5 L7.5 5.5 L4 8.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  )
}

/**
 * 一条诊断项：名字在左、值在右（等宽、可换行、长路径给 title）。
 * 值是**诊断数据**（路径 / 版本），刻意不翻译。
 */
export function DiagnosticItem({
  name,
  value,
  ok,
}: {
  name: ReactNode
  value: ReactNode
  /** 给了才画状态点；只是信息条目就不画 */
  ok?: boolean
}) {
  const text = typeof value === 'string' ? value : undefined
  return (
    <div className="flex items-start gap-1.5">
      {ok !== undefined && (
        <span
          aria-hidden
          className={cn(
            'mt-1 h-1.5 w-1.5 shrink-0 rounded-full',
            ok ? 'bg-ink-3' : 'bg-danger',
          )}
        />
      )}
      {ok !== undefined && (
        <span className="sr-only">{st(ok ? 'about.checkOk' : 'about.checkFail')}</span>
      )}
      <span className="shrink-0 text-xs text-ink-2">{name}</span>
      <span
        title={text}
        className="min-w-0 flex-1 break-all text-right font-mono text-xs text-ink-3"
      >
        {value}
      </span>
    </div>
  )
}

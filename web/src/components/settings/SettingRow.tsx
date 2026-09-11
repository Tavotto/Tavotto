import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { ChevronRight, CircleQuestionMark, TriangleAlert } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
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

/**
 * 控件列的宽度（px）。normal 内容宽 640 里标题列拿剩下的约 380：标题 + 一行说明
 * 够放，控件（下拉 / 开关 / 一颗按钮 / 数字框）从同一条竖线起排。样式 / 规范页
 * 的只读摘要行也用它，两种模式在同一位置来回切换时整列不跳（`settingsDisclosure.test`）。
 */
export const SETTING_CONTROL_WIDTH = 240
/** 行的网格：标题列弹性、控件列定宽。值通过 CSS 变量给，摘要行共用同一份 */
export const settingRowGrid = 'grid-cols-[minmax(0,1fr)_var(--setting-control)]'
export const settingControlStyle = {
  '--setting-control': `${SETTING_CONTROL_WIDTH}px`,
} as CSSProperties

/**
 * 一个设置分区：小标题 + 可选一句说明 + 若干行。
 *
 * 分区**不是卡片**：靠上下留白与 type-section 小标题分层，相邻两行之间一根
 * hairline（只在两个 `SettingRow` 相邻时画；行与警示条 / 折叠区之间不画）。
 * 分区之间的间距由外壳的内容容器统一给（`SettingsDialog`），分区自己不带外边距，
 * 所以哪一页都不会比别的页更稀或更挤。
 */
export function SettingSection({
  title,
  description,
  children,
  className,
}: {
  title?: ReactNode
  /** 分区级的一句说明（这一组设置管什么）；行级的说明写在行上 */
  description?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section
      className={cn(
        'flex flex-col',
        '[&>[data-setting-row]+[data-setting-row]]:border-t [&>[data-setting-row]+[data-setting-row]]:border-border',
        className,
      )}
    >
      {(title != null || description != null) && (
        <header className="mb-1 flex flex-col gap-0.5">
          {title != null && <h3 className="type-section">{title}</h3>}
          {description != null && <p className="type-caption">{description}</p>}
        </header>
      )}
      {children}
    </section>
  )
}

/**
 * 一行设置（Visual Consolidation Session 5 定下的形态）：
 *
 * ```text
 * 界面语言                                     [ 简体中文        ▾ ]
 * 设置 Tavotto 使用的界面语言
 * ```
 *
 *   * 左列：**标题**（type-body，ink）+ 可选的一句**说明**（type-caption）+ 可选的
 *     一句**现状**（`status`，type-meta：「当前窗口只能固定一侧」这类只在成立时给）；
 *   * 右列：**控件列定宽** `SETTING_CONTROL_WIDTH`，控件从同一条竖线起排、左起对齐，
 *     开关 / 下拉 / 按钮 / 数字框哪一种都落在同一列——标签不漂、控件不漂；
 *   * 行高稳定：`normal` 最小 48px（控件 28 + 上下各 10），`compact` 最小 32px
 *     （密集的字段清单用，不放说明）；相邻行之间一根 hairline 由 `SettingSection` 画；
 *   * `control="fill"`：控件要整行宽（路径输入框那种）时控件落到标题下一行，
 *     不再挤在 240 里。
 *
 * **标签先说结果，说明是可选的第二行**（2026-09-11 组件工作台设计包删掉了全部
 * 行级说明；这一槽是 Session 5 按标准 SettingRow 重新开的，要不要写、写什么由页面
 * 级 Session 定）。`help`（小问号）只留给真有歧义、且说明里带链接 / 按钮的少数几处。
 *
 * `controlId` 让标签成为真正的 `<label>`：点标签文字 = 点开关。
 *
 * **`<label htmlFor>` 不负责给 `<button>` 取名。** HTML-AAM 给 `button` 的取名
 * 方式是「name from content」，`Toggle` 那颗按钮的内容只有两个装饰用的 `<span>`
 * ——chromium 大方地把标签文字算进去了，webkit 按规范办事，于是同一颗开关在
 * chromium 上有名字、在 webkit 上是 `button-name` critical（#299，Windows 腿）。
 * 所以标签自己带一个稳定 id（`settingRowLabelId(controlId)`），控件用
 * `aria-labelledby` 指着它：名字与看见的那行字**是同一份**，不会分叉。
 */
/** 这一行标签的 id：控件用 `aria-labelledby` 指它，名字就是看见的那行字 */
export const settingRowLabelId = (controlId: string) => `${controlId}-label`

export function SettingRow({
  label,
  description,
  help,
  helpLabel,
  status,
  children,
  danger,
  controlId,
  density = 'normal',
  control = 'fixed',
}: {
  label: ReactNode
  /** 一句说明（这项设置管什么）。可选；不写就只有标题一行 */
  description?: ReactNode
  /** 解释性内容。给了就在标签后放一个小问号，**不在行下再堆一段** */
  help?: ReactNode
  /** 问号的可达名；缺省用「关于<标签>」 */
  helpLabel?: string
  /** 一句话的现状摘要（「当前窗口只能固定一侧」这类），只在那个状态成立时给 */
  status?: ReactNode
  children: ReactNode
  /** 这一行的当前状态有真实副作用（只读模式、写源文件…）：落 `data-danger`，
   *  视觉上不再加重标签——警示语义由行下的 `InlineWarning` 承担 */
  danger?: boolean
  /** 控件的 id：给了标签就是 `<label htmlFor>`，点文字等于点控件 */
  controlId?: string
  /** normal 48px（默认）/ compact 32px（密集字段清单，不放说明） */
  density?: 'normal' | 'compact'
  /** fixed：控件落在定宽的控件列；fill：控件整行宽，落到标题下一行 */
  control?: 'fixed' | 'fill'
}) {
  const labelText = typeof label === 'string' ? label : ''
  const LabelTag = controlId ? 'label' : 'span'
  const fill = control === 'fill'
  const compact = density === 'compact'
  return (
    <div
      data-setting-row
      data-density={density}
      data-danger={danger || undefined}
      style={settingControlStyle}
      className={cn(
        'grid items-center gap-x-6',
        fill ? 'grid-cols-1 gap-y-1.5' : settingRowGrid,
        compact ? 'min-h-8 py-0.5' : 'min-h-12 py-2.5',
      )}
    >
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="flex items-center gap-1">
          <LabelTag
            id={controlId ? settingRowLabelId(controlId) : undefined}
            htmlFor={controlId}
            className={cn('min-w-0 break-words text-sm leading-5 text-ink', controlId && 'cursor-pointer')}
          >
            {label}
          </LabelTag>
          {help != null && (
            <span className="flex h-5 items-center">
              <HelpTip label={helpLabel ?? st('helpAbout', { label: labelText })}>{help}</HelpTip>
            </span>
          )}
        </span>
        {description != null && <span className="type-caption break-words">{description}</span>}
        {status != null && <span className="type-meta break-words">{status}</span>}
      </div>
      <div className="flex min-h-7 min-w-0 items-center gap-2">{children}</div>
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
  /**
   * 关掉之后要不要无视紧接着的那次 focus。
   *
   * Esc 关闭时 Radix 会把焦点**还给触发按钮**，那是一次真实的 focus 事件——
   * 而「聚焦即展开」会立刻把它又打开，用户按 Esc 像没反应。这不是测试的
   * 问题，是产品缺陷（键盘用户必然撞上；鼠标点开时因为焦点本来就不在按钮上，
   * 表现为偶发，我的用例里三轮红一轮）。
   *
   * 用一个「等到焦点真的离开过再恢复」的闸，不用计时器——计时器只是把
   * 这场赛跑挪到另一个刻度上。
   */
  const ignoreNextFocus = useRef(false)
  const cancelClose = () => {
    window.clearTimeout(closeTimer.current)
    closeTimer.current = undefined
  }
  // 卸载时收掉悬着的计时器：切分区 / 关对话框都会在延迟关闭的 220ms 之内发生
  useEffect(() => () => window.clearTimeout(closeTimer.current), [])
  // 指针离开后留一点时间：鼠标从问号移到气泡上的路径不该把它关掉
  const scheduleClose = () => {
    cancelClose()
    closeTimer.current = window.setTimeout(() => setOpen(false), 220)
  }
  return (
    <Popover
      open={open}
      onOpenChange={(v) => {
        if (!v) ignoreNextFocus.current = true
        setOpen(v)
      }}
      width={width}
      side="top"
      align="start"
      keepFocus
      ariaLabel={label}
      trigger={
        <button
          type="button"
          // 结构性标记：「这一页还有几个问号」的判据认它，不去猜可达名的前缀
          data-help-tip
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
            // 刚被 Esc / 点外面关掉，这次 focus 是 Radix 把焦点还回来，不是用户 Tab 过来
            if (ignoreNextFocus.current) {
              ignoreNextFocus.current = false
              return
            }
            cancelClose()
            setOpen(true)
          }}
          onBlur={() => {
            // 焦点真的离开过 → 下次 Tab 回来该正常展开
            ignoreNextFocus.current = false
            scheduleClose()
          }}
          className={cn(
            'flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-ink-3',
            'outline-none transition-colors hover:text-ink-2 focus-visible:focus-ring',
          )}
        >
          <CircleQuestionMark size={ICON_SIZE.sm} aria-hidden />
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
      <TriangleAlert size={ICON_SIZE.sm} className="mt-px shrink-0" aria-hidden />
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
          <ChevronRight
            size={ICON_SIZE.xs}
            aria-hidden
            className={cn('transition-transform', open && 'rotate-90')}
          />
          <span className="font-medium">{title}</span>
        </button>
        {action}
      </div>
      {open && <div className="flex flex-col gap-1 pl-2">{children}</div>}
    </div>
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

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { Reveal } from '../ui/Field'
import { ChevronRight, CircleQuestionMark, TriangleAlert } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { Notice, type StatusTone } from '../ui/Notice'
import { Popover } from '../ui/Popover'
import { useInFieldGroup } from '../ui/fieldGroupContext'

/**
 * 设置页的基础构件。
 *
 * 修改前每个分区都长成 `<Row/> <p>说明</p> <p>更多说明</p>`：控件与解释文字
 * 视觉权重接近，整页读起来像说明书而不是设置（见
 * `docs/ux/img/ux-consistency-pass/before/zh-1440-settings-about.png`）。
 *
 * 分工（2026-10-07 设计审计 §9.1 起，分组是 `ui/FormSection` + `ui/FieldGroup`，这里只管行）：
 *   * `SettingRow`  —— 标签 + 控件 + 可选的一句说明 / 现状 + 可选帮助 + 可选的整行宽 fill 行（`below`）；
 *   * `SettingValueRow` —— 只读的「名字 + 值」（样式 / 规范页的只读摘要，与 `SettingRow` 同一份网格）；
 *   * `GroupNotice` —— 组里的说明条（`ui/Notice`，作为组内最后一行）：**只**给写源文件 / 清数据 /
 *     隐私授权 / 当前错误 / 缺件 / 不可逆操作；「当前状态有副作用」的一句低调提醒走行上的 `description`；
 *   * `HelpTip`     —— 解释性内容的唯一落点（小问号）；
 *   * `DiagnosticDisclosure` —— 路径 / 版本 / 包清单 / 原始状态码，默认折叠；`variant="row"` 是组里
 *     「原地展开的一行」（改图助手详情的高级设置、包管理的背景材料）。
 *
 * `InlineWarning` 只剩设置以外的几处对话框在用（待它们各自那一期迁到 `ui/Notice`），设置页不再用。
 */

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 控件列的宽度（px）。内容列 680、组内左右各 16 的内边距之后，标题列拿剩下的约 380：标题 + 一行说明
 * 够放，控件（下拉 / 开关 / 一颗按钮 / 数字框）贴同一条右缘。样式 / 规范页的只读摘要行（`SettingValueRow`）
 * 也用它，两种模式在同一位置来回切换时整列不跳（`settingsDisclosure.test`）。
 */
export const SETTING_CONTROL_WIDTH = 240
/** 行的网格：标题列弹性、控件列定宽。值通过 CSS 变量给，摘要行共用同一份 */
export const settingRowGrid = 'grid-cols-[minmax(0,1fr)_var(--setting-control)]'
export const settingControlStyle = {
  '--setting-control': `${SETTING_CONTROL_WIDTH}px`,
} as CSSProperties

/**
 * 一行设置（Visual Consolidation Session 5 定下的网格，Session 6 定下的对齐）：
 *
 * ```text
 * 界面语言                                     [ 简体中文        ▾ ]
 * 设置 Tavotto 使用的界面语言
 * ```
 *
 *   * 左列：**标题**（13 / ink）+ 可选的一句**说明**（type-caption 12 / ink-3）+ 可选的
 *     一句**现状**（`status`，12 / ink-2 / 等宽数字：「当前窗口只能固定一侧」这类只在成立时给；
 *     **只放文字**——按钮、路径展开、编辑器一律进 `below`，2026-10-07 设计审计 §9.1）
 *     + 可选的**示意图**（`illustration`，说明下方的小型辅助预览）；
 *   * 右列：**控件列定宽** `SETTING_CONTROL_WIDTH`，控件从同一条竖线起排、左起对齐，
 *     开关 / 下拉 / 按钮 / 数字框哪一种都落在同一列——标签不漂、控件不漂；
 *   * **控件对齐标题行，不对齐整行中线**：标题行是一个 28px 的盒（与控件同高），
 *     行本身 `items-start`。只有标题时两者天然同一条中线；多了说明 / 示意图时开关
 *     仍与标题并排，而不是漂到说明与示意图之间的某个高度上（Session 6 之前
 *     「拖动时一同移动关联对象」那一行就是这样：开关 + 问号 + 示意图挤在同一条基线上）；
 *   * 行高稳定：`normal` 最小 48px（控件 28 + 上下各 10），`compact` 最小 32px
 *     （密集的字段清单用，不放说明）；行坐在 `FieldGroup` 里时上下 12 / 左右 16 的内边距与
 *     行间 hairline 都由组给（compact 行自己收成上下 6）；
 *   * `control="fill"`：控件要整行宽（路径输入框那种）时控件落到标题下一行，
 *     不再挤在 240 里。
 *
 * **标签先说结果，说明是可选的第二行**（2026-09-11 组件工作台设计包删掉了全部
 * 行级说明；这一槽是 Session 5 按标准 SettingRow 重新开的，页面级 Session 6 只在
 * 「标题读不出后果」的行上补了一句）。`help`（小问号）只留给真有歧义、且说明里带
 * 链接 / 按钮的少数几处。
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
  illustration,
  below,
  children,
  controlId,
  density = 'normal',
  control = 'fixed',
  layout = 'default',
  ...rest
}: {
  label: ReactNode
  /** 一句说明（这项设置管什么）。可选；不写就只有标题一行 */
  description?: ReactNode
  /** 解释性内容。给了就在标签后放一个小问号，**不在行下再堆一段** */
  help?: ReactNode
  /** 问号的可达名；缺省用「关于<标签>」 */
  helpLabel?: string
  /** 一句话的现状摘要（「当前窗口只能固定一侧」这类），只在那个状态成立时给。**只放文字** */
  status?: ReactNode
  /** 说明下方的小型辅助示意（关联对象那张关系图）。帮助理解用，不是装饰 */
  illustration?: ReactNode
  /**
   * 整行宽的 fill 行（跨两列、落在标题与控件下面）：完整路径、目录编辑器、预览这类要宽度、又属于
   * 这一行的东西（2026-10-07 设计审计 §9.1：此前塞在 `status` 里，打开时整行跳 36px）。
   */
  below?: ReactNode
  /** 控件；纯现状行（自动保存那种没有开关可调的）不给，控件列留空 */
  children?: ReactNode
  /** 控件的 id：给了标签就是 `<label htmlFor>`，点文字等于点控件。**控件必须一直挂着**（指向未挂载的输入是坏标签） */
  controlId?: string
  /** normal 48px（默认）/ compact 32px（密集字段清单，不放说明） */
  density?: 'normal' | 'compact'
  /** fixed：控件落在定宽的控件列；fill：控件整行宽，落到标题下一行 */
  control?: 'fixed' | 'fill'
  /**
   * default：标题列弹性 + 控件列定宽 240、控件贴右缘；balanced：两列 4 : 6、控件从控件列左缘起排
   * （格式复选框、库选择、安装表单这类控件本身就宽、或是一组控件的行；2026-10-07 设计审计 §9.1）
   */
  layout?: 'default' | 'balanced'
} & Record<`data-${string}`, string | number | boolean | undefined>) {
  const labelText = typeof label === 'string' ? label : ''
  const LabelTag = controlId ? 'label' : 'span'
  const fill = control === 'fill'
  const compact = density === 'compact'
  const balanced = layout === 'balanced' && !fill
  // 坐在 FieldGroup 里：上下 12 / 左右 16 的内边距由组给（index.css 的 [data-ui-field-group] 规则），行只管最小高；
  // compact 的字段清单在组里收成上下 6（utilities 层的 py 盖得过组的 12），一列十几行不至于拉成一屏半
  const grouped = useInFieldGroup()
  return (
    <div
      {...rest}
      data-setting-row
      data-density={density}
      data-layout={balanced ? 'balanced' : undefined}
      style={settingControlStyle}
      className={cn(
        'grid items-start gap-x-6',
        fill ? 'grid-cols-1 gap-y-1.5' : balanced ? 'grid-cols-[minmax(0,4fr)_minmax(0,6fr)]' : settingRowGrid,
        compact ? cn('min-h-8', grouped ? 'py-1.5' : 'py-0.5') : grouped ? 'min-h-12' : 'min-h-12 py-2.5',
      )}
    >
      <div className="flex min-w-0 flex-col">
        {/* 标题行是一个与控件同高的 28px 盒：控件对齐它，不对齐整行中线 */}
        <span className="flex min-h-7 items-center gap-1">
          <LabelTag
            id={controlId ? settingRowLabelId(controlId) : undefined}
            htmlFor={controlId}
            className="min-w-0 break-words text-base leading-5 text-ink"
          >
            {label}
          </LabelTag>
          {help != null && (
            <HelpTip label={helpLabel ?? st('helpAbout', { label: labelText })}>{help}</HelpTip>
          )}
        </span>
        {/* 说明 / 现状 / 示意图是标题下的**同一段**：负边距只收一次（把整段贴住 28px 的标题盒），
            段内各行之间是正的 gap。此前三段各自 `-mt-1`，说明一折成两行，现状那一段就被拽进
            说明的最后一行里——关于页「匿名用量统计」的两句话曾经重叠 4px（全面打磨 D02） */}
        {(description != null || status != null || illustration != null) && (
          <span className="-mt-1 flex min-w-0 flex-col gap-0.5">
            {description != null && <span className="type-caption break-words">{description}</span>}
            {/* 现状 12 / ink-2 / 等宽数字：比说明深一档——它说的是「此刻」，说明说的是「一向」 */}
            {status != null && (
              <span data-setting-status className="break-words text-sm text-ink-2 tabular-nums">
                {status}
              </span>
            )}
            {illustration != null && <span className="mt-1 flex">{illustration}</span>}
          </span>
        )}
      </div>
      {/* 控件贴列右缘（2026-09-15 打磨批次 A，用户拍板）：定宽列里左起对齐会把控件漂在页面中间、
          右侧空一大片；整行宽的 fill 形态不在此列，照旧铺满 */}
      <div className={cn('flex min-h-7 min-w-0 items-center gap-2', !fill && !balanced && 'justify-end justify-self-end')}>
        {children}
      </div>
      {below != null && (
        <div data-setting-below className="col-span-full min-w-0 pt-2">
          {below}
        </div>
      )}
    </div>
  )
}

/**
 * 只读的「名字 + 值」一行（样式 / 规范页内置那份的规则摘要）：**刻意不是一排 disabled 的控件**——整页禁用的
 * 输入看起来像「我的表单坏了」，而它其实是「这份是内置的、想改先复制一份」（审计 T41 / T42）。与 `SettingRow`
 * 同一份网格、同一档 compact 行高：「摘要 ↔ 输入框」两种模式在同一位置来回切换时，值与输入框从同一条竖线起排
 * （`settingsDisclosure.test` 量它）。此前样式页与规范页各写了一份同名的私有组件（2026-10-07 设计审计 §9.1 合并）。
 */
export function SettingValueRow({
  label,
  value,
  ...rest
}: { label: string; value: string } & Record<`data-${string}`, string | number | boolean | undefined>) {
  const grouped = useInFieldGroup()
  return (
    <div
      {...rest}
      data-summary-row
      style={settingControlStyle}
      className={cn('grid min-h-8 items-center gap-x-6 text-base', grouped ? 'py-1.5' : 'py-0.5', settingRowGrid)}
    >
      <span className="min-w-0 truncate text-ink" title={label}>
        {label}
      </span>
      <span className="min-w-0 justify-self-end truncate text-sm text-ink-2 tabular-nums" title={value}>
        {value}
      </span>
    </div>
  )
}

/**
 * 组里的说明条：`ui/Notice` 包一层组行（行的 12 / 16 内边距由组给，Notice 自己不贴组边）。作为组内**最后一行**
 * （2026-10-07 设计审计 §9.1：`InlineWarning` → 锚点派生的 `Notice`）。
 */
export function GroupNotice({
  tone = 'warn',
  title,
  action,
  children,
  ...rest
}: {
  tone?: StatusTone
  title?: ReactNode
  action?: ReactNode
  children?: ReactNode
} & Record<`data-${string}`, string | number | boolean | undefined>) {
  return (
    <div {...rest} data-group-notice className="min-w-0">
      <Notice tone={tone} title={title} action={action}>
        {children}
      </Notice>
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
          // 20px 档的图标钮（与 Button icon-xs 同形，这里保留手写是因为它挂着自己的指针 / 焦点处理）：
          // 它坐在标题行里，28px 的钮会把标题行撑出节奏
          className={cn(
            'flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-ink-3',
            'outline-none transition-colors duration-fast hover:bg-surface-hover hover:text-ink',
            'focus-visible:focus-ring',
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
        tone === 'danger' ? 'bg-danger-surface text-danger-content' : 'bg-surface-hover text-ink-2',
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
 *
 * 两种形态，DOM 同一种骨架（`根 > div > button[aria-expanded]`，e2e 的 `[data-agent-fold] > div > button` 认它）：
 *   * `inline`（缺省）：28 高的 chevron 小折叠头，坐在一段内容里；
 *   * `row`：坐在 `FieldGroup` 里的**一行**——13px 的名字、行尾 `value`（当前值 / 项数，收起时也看得见）
 *     + chevron，点整行原地展开（2026-10-07 设计审计 §9.1：改图助手详情的「高级」、包管理的背景材料、
 *     隐私的数据清单都是这一种，与 `ui/SummaryRow` 同一个读法）。
 */
export function DiagnosticDisclosure({
  title,
  action,
  value,
  children,
  defaultOpen = false,
  variant = 'inline',
  ...rest
}: {
  title: string
  /** 折叠头右侧的**动作**（复制日志…），在按钮之外，不随展开消失 */
  action?: ReactNode
  /** 折叠头右侧的**值**（当前来源 / 项数），在按钮里（只放文字），不随展开消失 */
  value?: ReactNode
  children: ReactNode
  defaultOpen?: boolean
  variant?: 'inline' | 'row'
} & Record<`data-${string}`, string | number | boolean | undefined>) {
  const [open, setOpen] = useState(defaultOpen)
  const row = variant === 'row'
  return (
    <div {...rest} data-disclosure={variant} className={cn('flex min-w-0 flex-col', row ? 'gap-0' : 'gap-1.5')}>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className={cn(
            'flex min-w-0 flex-1 items-center text-left outline-none focus-visible:focus-ring',
            row
              ? 'min-h-7 gap-2 rounded-sm text-base text-ink'
              : 'h-7 gap-1 rounded-sm text-xs text-ink-2 hover:text-ink',
          )}
        >
          {!row && (
            <ChevronRight
              size={ICON_SIZE.xs}
              aria-hidden
              className={cn('transition-transform', open && 'rotate-90')}
            />
          )}
          <span className={cn('min-w-0 truncate', !row && 'font-medium')}>{title}</span>
          {value != null && (
            <span className="ml-auto min-w-0 max-w-[50%] shrink-0 truncate text-right text-sm text-ink-2 tabular-nums">
              {value}
            </span>
          )}
          {row && (
            <ChevronRight
              size={ICON_SIZE.xs}
              aria-hidden
              className={cn('shrink-0 text-ink-3 transition-transform', value == null && 'ml-auto', open && 'rotate-90')}
            />
          )}
        </button>
        {action}
      </div>
      {/* `grid-cols-[minmax(0,1fr)]`：`Reveal` 是一个 grid，隐式列是 `auto`——而 grid 项的
          `min-width` 默认按 min-content 算，里面有一行 `truncate`（`white-space: nowrap`）的
          长路径时，那一列就撑到整条路径的宽度，越过内容列右缘被外层裁掉（编码 Agent 详情的
          「找过这些位置」实测如此）。把列钉成 `minmax(0,1fr)` 之后它才会缩，`truncate` 也才生效。
          行方向的 `grid-template-rows` 由展开动画接管，两者不冲突。 */}
      <Reveal open={open} className="min-w-0 grid-cols-[minmax(0,1fr)]">
        <div className={cn('flex min-w-0 flex-col gap-1', row ? 'pt-2' : 'pl-2')}>{children}</div>
      </Reveal>
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
  ...rest
}: {
  name: ReactNode
  value: ReactNode
  /** 给了才画状态点；只是信息条目就不画 */
  ok?: boolean
} & Record<`data-${string}`, string | number | boolean | undefined>) {
  const text = typeof value === 'string' ? value : undefined
  return (
    <div {...rest} className="flex items-start gap-1.5">
      {ok !== undefined && (
        <span
          aria-hidden
          className={cn(
            'mt-1 h-1.5 w-1.5 shrink-0 rounded-full',
            // 正常是 ok 绿（锚点作圆点 ≥3:1），不是灰——灰点读不出「通过」（2026-10-07 设计审计 §9.1）
            ok ? 'bg-ok' : 'bg-danger',
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

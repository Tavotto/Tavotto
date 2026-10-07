import * as RD from '@radix-ui/react-dialog'
import { t } from '@/i18n'
import { X } from './icons'
import { ICON_SIZE } from './Icon'
import {
  isValidElement,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
  type RefObject,
} from 'react'
import { cn } from '@/lib/utils'
import { IconButton } from './Button'
import { focusOrigin, holdFocusReturn, releaseFocusReturn } from './focusOrigin'

/**
 * 宽度五档（2026-10-07 设计审计 §10.2：此前 8 种宽度）：sm 400 · md 480 · lg 560 · xl 760 · shell 1000。
 * `shell` 同时意味着 `chrome="shell"`（设置那种自己管布局的窗口）。
 */
export type DialogSize = 'sm' | 'md' | 'lg' | 'xl' | 'shell'

const WIDTH: Record<DialogSize, number> = { sm: 400, md: 480, lg: 560, xl: 760, shell: 1000 }

/**
 * 页脚三槽（2026-10-07 设计审计 §10.2）：`[start] …… [secondary] [primary]`。
 *   start      左侧：破坏性的另一条路（「不保存」，`danger-tinted`）或次要入口（「直接运行」）
 *   secondary  右侧主按钮左边：「取消」「关闭」「稍后」
 *   primary    最右：唯一一颗主动作（动词 + 宾语）
 * 页脚按钮一律 32px（`size="lg"`）——约定，不由外壳改写。
 */
export interface DialogFooterSlots {
  start?: ReactNode
  secondary?: ReactNode
  primary?: ReactNode
}

const isSlots = (f: unknown): f is DialogFooterSlots =>
  f != null &&
  typeof f === 'object' &&
  !isValidElement(f) &&
  !Array.isArray(f) &&
  ('start' in f || 'secondary' in f || 'primary' in f)

/**
 * 遮罩只由**最底下**那个开着的对话框画（2026-10-07 设计审计 §10.2：嵌套两层遮罩叠成约 51% 暗）。
 * 模块级的栈：开着且没被 `covered` 的对话框按打开顺序登记，栈底那个画遮罩，其余的遮罩透明
 * （元素还在——Radix 的模态点外面判定仍落在它上面）。被 covered 的那层先退栈，盖着它的那层就成了栈底。
 */
const scrimStack: symbol[] = []
const scrimListeners = new Set<() => void>()
const scrimSubscribe = (fn: () => void) => {
  scrimListeners.add(fn)
  return () => scrimListeners.delete(fn)
}
const scrimBottom = () => scrimStack[0] ?? null
const scrimEmit = () => scrimListeners.forEach((fn) => fn())

function useScrimOwner(active: boolean): boolean {
  const [id] = useState(() => Symbol('dialog'))
  useLayoutEffect(() => {
    if (!active) return
    scrimStack.push(id)
    scrimEmit()
    return () => {
      const i = scrimStack.indexOf(id)
      if (i >= 0) scrimStack.splice(i, 1)
      scrimEmit()
    }
  }, [active, id])
  const bottom = useSyncExternalStore(scrimSubscribe, scrimBottom, scrimBottom)
  // 还没登记上的那一帧（或没开）按「自己画」算：单个对话框打开时不闪一帧无遮罩
  return bottom === null || bottom === id
}

interface DialogProps {
  open: boolean
  onOpenChange: (v: boolean) => void
  title: ReactNode
  description?: ReactNode
  children: ReactNode
  /**
   * 页脚。`{ start, secondary, primary }` 三槽（推荐，见 `DialogFooterSlots`），或旧写法的一段 ReactNode
   * （整段右对齐）。正文会滚时页脚吸在底边、变成毛玻璃，内容从它底下滑过（`data-scrolled`）。
   */
  footer?: ReactNode | DialogFooterSlots
  /**
   * 正文与脚部之间一块**不随正文滚**的状态区（进度 / 冲突 / 拒绝 / 结果）。给「点了脚部
   * 按钮之后的回应」用：放在可滚正文的最末尾的话，正文一长它就在视口外，用户点完
   * 什么都没看见（2026-10-07 设计审计 §10.2 P0）。自己有高度上限，超了在区内滚。
   * 不给（或给 null / false）就不占位。
   */
  status?: ReactNode
  size?: DialogSize
  /**
   * 特殊场合才用；常规尺寸走 size。只给 `shell` 那种自己管布局的大窗口（版本预览按窗口比例：
   * `"min(1200px, 80vw)"`）——字符串是 CSS 长度，窗口缩放时跟着变
   */
  width?: number | string
  /**
   * 固定高度（CSS 长度）。给「内容随分区变化」的外壳（设置）用：外框不随
   * 内容高低跳动，内容区自己滚。不给就是按内容撑高、上限 86vh 的老行为。
   */
  height?: string
  /**
   * 有不可中断的操作在跑：标记 aria-busy，并挡住 Esc / 点外面 / 右上角关闭。
   * 破坏性写入（写回原始文件、历史恢复）中途被关掉会让用户以为已取消，其实没有。
   */
  busy?: boolean
  /** 与 busy 分开：不忙但也不许随手关（例如必须做出选择的确认框） */
  blockDismiss?: boolean
  /**
   * Esc 的**安全答案**（2026-10-07 设计审计 §10.2：Esc 永远是安全答案）。给了它，Esc 就调它——
   * 即使 `blockDismiss`（点外面仍然不算回答）；`busy` 时 Esc 什么都不做。确认框的 Esc = 取消、
   * 关窗三选一的 Esc = 取消。真正的闸门（NativeConfirm、ScriptInput）不给它。
   */
  onEscape?: () => void
  /**
   * 被主对话框栈里更靠上的那个盖着（`uiStore.dialogStack`，审计 T35）：整层
   * 不可见，但**不卸载**——表单状态、滚动位置与打开子步骤的那颗按钮都还在，
   * 栈顶关掉后原样回来、焦点回到那颗按钮。遮罩也一起藏：屏幕上只有一层遮罩、
   * 一个右上角 ×。Radix 自己会把它 aria-hidden，焦点圈与 Esc 都归栈顶。
   */
  covered?: boolean
  /**
   * 稳定锚点：落在 `RD.Content` 上的 `data-dialog="<anchor>"`。e2e 要指代
   * **某一个具体的对话框**时认它——`[role=dialog]` 在这个应用里有五个产出点
   * （本组件、快速编辑、onboarding coachmark、版本面板、playground），
   * `querySelector('[role=dialog]')` 拿到的是「文档里排在最前的那个」，
   * 不是你想要的那个（issue #307）。不给就只落一个空的 `data-dialog`，
   * 仍然把「共用对话框外壳」这一类与上面那四个区分开。
   */
  anchor?: string
  /**
   * 外壳形态。`default`：标题 + 可滚的正文（带内边距）+ 脚部，绝大多数对话框。
   * `shell`：给「左导航 + 右内容」这种自己管布局与滚动的窗口（设置）——标题栏
   * 收成 44px 一条、下面一根 hairline，正文**不带内边距也不滚**，子树自己铺满、
   * 自己决定哪一列滚。
   * `palette`：命令面板（2026-10-07 设计审计 §10.1）——钉在视口上方 18vh、标题只给读屏、没有右上角 ×
   * （Esc / 点外面就是关）、正文不带内边距也不滚（输入行与结果列表自己排）。焦点陷阱与归还照旧由本外壳给。
   */
  chrome?: 'default' | 'shell' | 'palette'
  /**
   * 打开时焦点落在哪个控件上。**默认落在容器上**（第一下 Tab 进正文第一个控件，
   * 2026-09-14 审计 S1）；只给「默认动作必须是安全的那一个」的对话框用——排版
   * 时间线的预览（ADR 0101）默认焦点在「关闭」上，回车不会误触恢复。
   */
  initialFocusRef?: RefObject<HTMLElement | null>
}

/**
 * 共用对话框外壳（宪法第五节 Dialog、第二十六节）：rounded-panel 16、标题 type-title 15 / 600、
 * 说明 13 / ink-2、正文 type-reading 13 / 1.6、页脚三槽 32px、栈底才画遮罩、Esc = `onEscape` 给的安全答案。
 *
 * **常驻挂载**：调用方不要写 `if (!x) return null` 再把 `open` 写死成 true——那样只有进场、没有退场
 * （Radix 的 Presence 要等 animationend 才卸载内容）。写法是对话框常驻、`open={!!x}`，正文读一个
 * ref 里保留的**最后一份载荷**（关的那 90ms 里 x 已经是 null，正文仍要画得出来）；多步流程是一个
 * Dialog 换正文，不是两个 Dialog 交接。
 */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  status,
  size = 'md',
  width,
  height,
  busy = false,
  blockDismiss = false,
  onEscape,
  covered = false,
  anchor,
  chrome = 'default',
  initialFocusRef,
}: DialogProps) {
  const palette = chrome === 'palette'
  // 命令面板与 shell 一样自己管正文的排版与滚动
  const shell = chrome === 'shell' || size === 'shell' || palette
  const locked = busy || blockDismiss
  const ownsScrim = useScrimOwner(open && !covered)
  const slots = isSlots(footer) ? footer : null
  const hasStatus = status != null && status !== false
  const hasFooter = slots ? !!(slots.start || slots.secondary || slots.primary) : footer != null && footer !== false
  // 正文还有没滚到的部分：页脚变毛玻璃（只在默认外壳里；shell 的正文自己管滚动）
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const [scrolled, setScrolled] = useState(false)
  const footerRef = useRef<HTMLDivElement | null>(null)
  const [footerH, setFooterH] = useState(0)
  const measure = useCallback(() => {
    const el = bodyRef.current
    setScrolled(!!el && el.scrollHeight - el.clientHeight - el.scrollTop > 1)
    setFooterH(footerRef.current?.offsetHeight ?? 0)
  }, [])
  useEffect(() => {
    const el = bodyRef.current
    if (!open || !el || shell || !hasFooter || hasStatus) return
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    if (el.firstElementChild) ro.observe(el.firstElementChild)
    if (footerRef.current) ro.observe(footerRef.current)
    return () => ro.disconnect()
  }, [open, shell, hasFooter, hasStatus, measure])
  // 本仓库的对话框全部由 store 驱动、没有 Radix Trigger：关闭时 Radix 找不到
  // 触发元素，焦点会掉回 body——键盘用户按 Esc 后不知道自己在哪（审计 P1-09）。
  // 在 Radix 挪焦点**之前**（onOpenAutoFocus）记下打开前的焦点，关闭时还回去。
  const restoreTo = useRef<HTMLElement | null>(null)
  // 打开时焦点落在对话框容器本身（2026-09-14 审计 S1，用户拍板）。Radix 默认把焦点
  // 给内容里第一个可聚焦元素——而标题栏的关闭钮在 DOM 里排在正文前面，于是导出 /
  // 设置 / 快捷键三个对话框打开后第一下 Enter 都是「关闭」，读屏先念「关闭，按钮」。
  // 容器带 tabIndex=-1（Radix 自己给的），焦点停在它上面：读屏念标题与说明，
  // 用户再 Tab 进第一个控件；Tab 顺序不变，关闭钮仍在标题栏里。
  const contentRef = useRef<HTMLDivElement | null>(null)
  // 关上的那一刻登记退场后的归还目标（见 `ui/focusOrigin`）；关闭归还跑完或再次打开时撤销
  const [owner] = useState(() => Symbol('dialog-return'))
  useLayoutEffect(() => {
    if (open) releaseFocusReturn(owner)
    else if (restoreTo.current) holdFocusReturn(owner, restoreTo.current)
  }, [open, owner])
  useEffect(() => () => releaseFocusReturn(owner), [owner])
  // 关上的那一刻就把焦点还给打开者，不等退场。Radix 的归还（下面的 onCloseAutoFocus）要等 Presence
  // 卸掉内容、再隔一个 setTimeout 才跑：那之间焦点要么还在一层已关的（`data-state=closed`、正淡出的）
  // 对话框里，要么已经摔在 body 上——键盘用户这时按键落空，忙的机器上这个窗口能拉到几十毫秒
  // （CI 满载时命令面板的归还用例就在这里撞见 body）。用被动 effect 而不是 layout effect：Radix 的
  // 焦点陷阱（trapped={open}）在被动 effect 里才撤，早一步挪出去会被陷阱的 focusout 拽回来。
  // 交接守卫与 onCloseAutoFocus 同一个判据：焦点已经在这层之外一个连着的非 body 元素上，是有意交接，不动它。
  // 之后 onCloseAutoFocus 照常跑，看到焦点已在层外就什么都不做；打开者已卸掉的回退仍归它管。
  useEffect(() => {
    if (open) return
    const el = restoreTo.current
    if (!el?.isConnected) return
    const now = document.activeElement
    const inLayer = !!(now && contentRef.current?.contains(now))
    const lost = !(now instanceof HTMLElement) || now === document.body || !now.isConnected
    if (inLayer || lost) el.focus()
  }, [open])

  const hasBody = children != null && children !== false
  // 浮动页脚：默认外壳、有正文、没有 status 区时，页脚往上叠进正文的底边（负 margin = 自己的高度），正文底部
  // 留出同样高的内边距——滚动时内容从页脚底下滑过、滚到底时最后一行停在页脚上方。页脚**永远**是 Content 的
  // 同一个孩子（不在正文里进进出出）：status 出现 / 消失时它不重挂载，按钮上的焦点不会丢。
  const floating = !shell && !hasStatus && hasBody && hasFooter
  const footerEl = (
    <div
      ref={footerRef}
      data-dialog-footer
      data-floating={floating || undefined}
      data-scrolled={(floating && scrolled) || undefined}
      // 底色在 index.css 的 [data-dialog-footer] 规则里（白底 ↔ 毛玻璃两态，含 reduced-transparency）
      className={cn('flex shrink-0 items-center gap-2 px-5 pb-4 pt-3', floating && 'relative z-sticky')}
      style={floating && footerH ? { marginTop: -footerH } : undefined}
    >
      {slots ? (
        <>
          {slots.start}
          <span className="flex-1" />
          {slots.secondary}
          {slots.primary}
        </>
      ) : (
        <div className="flex flex-1 items-center justify-end gap-2">{footer as ReactNode}</div>
      )}
    </div>
  )

  return (
    <RD.Root open={open} onOpenChange={(v) => (locked && !v ? undefined : onOpenChange(v))}>
      <RD.Portal>
        <RD.Overlay
          className={cn(
            // 30%、不模糊：OpenAI 30% / shadcn 50%，两家都不做玻璃（2026-09-15 审计 B13）。
            // 只有栈底的对话框画遮罩，叠着的那层遮罩透明（见 useScrimOwner）。压暗用 shadow 色（不是 ink：
            // ink 在暗色里是浅色，遮罩会变成提亮）
            'fixed inset-0 z-overlay',
            ownsScrim ? 'bg-shadow/30' : 'bg-transparent',
            'data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out',
            covered && 'invisible',
          )}
          data-dialog-scrim={ownsScrim || undefined}
        />
        <RD.Content
          ref={contentRef}
          style={{ width: width ?? WIDTH[size], ...(height ? { height } : {}) }}
          aria-busy={busy || undefined}
          data-dialog={anchor ?? ''}
          data-covered={covered || undefined}
          onKeyDown={(e) => e.stopPropagation()}
          onOpenAutoFocus={(e) => {
            // 从正在退场的命令面板里打开的：记面板的打开者，不记那颗马上消失的输入框
            restoreTo.current = focusOrigin()
            e.preventDefault()
            ;(initialFocusRef?.current ?? contentRef.current)?.focus({ preventScroll: true })
          }}
          onCloseAutoFocus={(e) => {
            releaseFocusReturn(owner)
            // 焦点已经被交给了这层之外的一个元素（命令面板的命令打开了命名小框 / 另一个对话框）：
            // 那是有意的交接，不抢回来——否则小框的 onBlur 当场把它关掉，命令一闪而过（Codex #833）。
            // Esc / 取消 / 点外面关的那种，焦点原本在这层里，层卸掉后落在 body：照旧还给打开者
            const now = document.activeElement
            if (
              now instanceof HTMLElement &&
              now !== document.body &&
              now.isConnected &&
              !contentRef.current?.contains(now)
            ) {
              e.preventDefault()
              return
            }
            const el = restoreTo.current
            if (el?.isConnected) {
              e.preventDefault()
              el.focus()
              return
            }
            // 记下的节点在对话框开着期间被 React 重渲染换掉了（issue #37 的
            // 纯键盘 E2E 在 WebKit 上实测撞见）：先找 aria-label 相同的重生
            // 节点——那就是「同一个控件的新实例」；再不行退回顶栏第一个按钮。
            // 无论如何不把焦点摔到 body：键盘用户会当场失去位置。
            const label = el?.getAttribute('aria-label')
            const twin = label
              ? document.querySelector<HTMLElement>(`[aria-label="${CSS.escape(label)}"]`)
              : null
            const fallback =
              twin ?? document.querySelector<HTMLElement>('header button, [role="toolbar"] button')
            if (fallback) {
              e.preventDefault()
              fallback.focus()
            }
          }}
          onEscapeKeyDown={(e) => {
            if (busy) return e.preventDefault()
            if (onEscape) {
              e.preventDefault()
              onEscape()
              return
            }
            if (locked) e.preventDefault()
          }}
          onInteractOutside={(e) => locked && e.preventDefault()}
          className={cn(
            'fixed left-1/2 z-dialog max-h-[86vh] max-w-[calc(100vw-2rem)] -translate-x-1/2',
            palette ? 'top-[18vh]' : 'top-1/2 -translate-y-1/2',
            'flex flex-col overflow-hidden rounded-panel bg-surface shadow-dialog',
            // 容器是初始焦点的落点：对话框自己的出现就是位置线索，不再套一圈焦点环
            'outline-none',
            // 退场靠 Radix 的 Presence 保活（它会等 animationend）——**不要**改成条件
            // 渲染，那样只有进场、没有退场，浮层会「淡入之后瞬间消失」
            'data-[state=open]:animate-pop-in data-[state=closed]:animate-pop-out',
            covered && 'invisible',
          )}
        >
          <div
            className={cn(
              'flex gap-3',
              palette && 'sr-only',
              // 右侧给关闭钮留位：它画在右上角，但 DOM 排在最后（见下）
              shell
                ? 'h-11 shrink-0 items-center border-b border-border pl-4 pr-12'
                : 'items-start pb-1 pl-5 pr-12 pt-4',
            )}
          >
            <div className="min-w-0">
              <RD.Title className="type-title">{title}</RD.Title>
              {/* 说明与正文同一个阅读字号（13 / ink-2，2026-10-07 设计审计 §10.2）：此前 12 / ink-3，
                  比它下面的正文还轻一档，读着像脚注 */}
              {description && (
                // div 而不是 Radix 默认的 p：说明槽也可以是一段结构（导出对话框把「要导的是什么」的对象头放在这里）
                <RD.Description asChild>
                  <div className="mt-1 text-base leading-[1.5] text-ink-2">{description}</div>
                </RD.Description>
              )}
            </div>
          </div>
          {/* 没有正文（标题已说完）就不摆这块留白。正文是阅读面：type-reading 13 / 1.6
              （子元素自己写了字号的照旧）。浮动页脚时正文底部留出页脚的高度（见 floating） */}
          {hasBody && (
            <div
              ref={bodyRef}
              onScroll={shell ? undefined : measure}
              className={cn(
                'min-h-0 flex-1',
                shell ? 'flex flex-col overflow-hidden' : 'type-reading overflow-y-auto px-5 py-3',
              )}
              style={floating && footerH ? { paddingBottom: footerH + 4, scrollPaddingBottom: footerH } : undefined}
            >
              {children}
            </div>
          )}
          {hasStatus && (
            <div
              data-dialog-status
              className="max-h-[30vh] shrink-0 overflow-y-auto border-t border-border px-5 pb-2 pt-3"
            >
              {status}
            </div>
          )}
          {hasFooter && footerEl}
          {!locked && !palette && (
            <RD.Close asChild>
              {/* `data-dialog-close` 是关闭按钮的稳定锚点：aria-label 是
                  本地化文案（`actions.close`），换语言就选不中——e2e 里
                  `[aria-label=关闭]` 是明文禁止的写法（issue #307）。
                  标题栏里已经说明了这是什么对话框，关闭钮不再挂气泡。
                  **DOM 排在正文与脚部之后、视觉钉在右上角**：初始焦点在容器上，
                  第一下 Tab 应该进正文第一个控件，而不是先路过关闭钮（2026-09-14
                  审计 S1）；Shift+Tab 或走到末尾仍能到它，Esc 照旧。 */}
              <IconButton
                data-dialog-close
                label={t('actions.close')}
                tip={false}
                className={cn(
                  'absolute text-ink-3 hover:text-ink',
                  shell ? 'right-2.5 top-2' : 'right-3 top-3',
                )}
              >
                <X size={ICON_SIZE.md} />
              </IconButton>
            </RD.Close>
          )}
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  )
}

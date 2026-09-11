import * as RD from '@radix-ui/react-dialog'
import { t } from '@/i18n'
import { X } from 'lucide-react'
import { ICON_SIZE } from './Icon'
import { useRef, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { IconButton } from './Button'

export type DialogSize = 'sm' | 'md' | 'lg'

const WIDTH: Record<DialogSize, number> = { sm: 360, md: 420, lg: 560 }

interface DialogProps {
  open: boolean
  onOpenChange: (v: boolean) => void
  title: ReactNode
  description?: ReactNode
  children: ReactNode
  footer?: ReactNode
  size?: DialogSize
  /** 特殊场合才用；常规尺寸走 size */
  width?: number
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
   */
  chrome?: 'default' | 'shell'
}

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  size = 'md',
  width,
  height,
  busy = false,
  blockDismiss = false,
  covered = false,
  anchor,
  chrome = 'default',
}: DialogProps) {
  const shell = chrome === 'shell'
  const locked = busy || blockDismiss
  // 本仓库的对话框全部由 store 驱动、没有 Radix Trigger：关闭时 Radix 找不到
  // 触发元素，焦点会掉回 body——键盘用户按 Esc 后不知道自己在哪（审计 P1-09）。
  // 在 Radix 挪焦点**之前**（onOpenAutoFocus）记下打开前的焦点，关闭时还回去。
  const restoreTo = useRef<HTMLElement | null>(null)

  return (
    <RD.Root open={open} onOpenChange={(v) => (locked && !v ? undefined : onOpenChange(v))}>
      <RD.Portal>
        <RD.Overlay
          className={cn(
            'fixed inset-0 z-40 bg-ink/20 backdrop-blur-[1px]',
            'data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out',
            covered && 'invisible',
          )}
        />
        <RD.Content
          style={{ width: width ?? WIDTH[size], ...(height ? { height } : {}) }}
          aria-busy={busy || undefined}
          data-dialog={anchor ?? ''}
          data-covered={covered || undefined}
          onKeyDown={(e) => e.stopPropagation()}
          onOpenAutoFocus={() => {
            if (document.activeElement instanceof HTMLElement)
              restoreTo.current = document.activeElement
          }}
          onCloseAutoFocus={(e) => {
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
          onEscapeKeyDown={(e) => locked && e.preventDefault()}
          onInteractOutside={(e) => locked && e.preventDefault()}
          className={cn(
            'fixed left-1/2 top-1/2 z-50 max-h-[86vh] max-w-[calc(100vw-2rem)]',
            '-translate-x-1/2 -translate-y-1/2',
            'flex flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-pop',
            // 退场靠 Radix 的 Presence 保活（它会等 animationend）——**不要**改成条件
            // 渲染，那样只有进场、没有退场，浮层会「淡入之后瞬间消失」
            'data-[state=open]:animate-pop-in data-[state=closed]:animate-pop-out',
            covered && 'invisible',
          )}
        >
          <div
            className={cn(
              'flex justify-between gap-3',
              shell
                ? 'h-11 shrink-0 items-center border-b border-border px-4'
                : 'items-start px-4 pb-1 pt-3.5',
            )}
          >
            <div className="min-w-0">
              <RD.Title className="type-title">{title}</RD.Title>
              {description && (
                <RD.Description className="type-caption mt-0.5">{description}</RD.Description>
              )}
            </div>
            {!locked && (
              <RD.Close asChild>
                {/* `data-dialog-close` 是关闭按钮的稳定锚点：aria-label 是
                    本地化文案（`actions.close`），换语言就选不中——e2e 里
                    `[aria-label=关闭]` 是明文禁止的写法（issue #307）。
                    标题栏里已经说明了这是什么对话框，关闭钮不再挂气泡。 */}
                <IconButton
                  data-dialog-close
                  label={t('actions.close')}
                  tip={false}
                  className={cn('-mr-1.5 text-ink-3 hover:text-ink', !shell && '-mt-1')}
                >
                  <X size={ICON_SIZE.md} />
                </IconButton>
              </RD.Close>
            )}
          </div>
          <div
            className={cn(
              'min-h-0 flex-1',
              shell ? 'flex flex-col overflow-hidden' : 'overflow-y-auto px-4 py-3',
            )}
          >
            {children}
          </div>
          {footer && (
            <div className="flex items-center justify-end gap-2 px-4 pb-3.5 pt-1">
              {footer}
            </div>
          )}
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  )
}

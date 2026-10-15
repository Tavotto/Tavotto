import { useEffect, useRef, useState, type HTMLAttributes, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronRight, CircleAlert, CircleCheck, Info, TriangleAlert, type IconComponent } from './icons'
import { Button } from './Button'
import { Reveal } from './Field'
import { ICON_SIZE } from './Icon'
import { cn } from '@/lib/utils'

export type StatusTone = 'neutral' | 'info' | 'ok' | 'warn' | 'danger'

/** 每个语气一枚图标（形状分严重度，颜色不分：阻断圆叹号 / 警告三角 / 信息圆 / 完成对勾） */
const ICON: Record<StatusTone, IconComponent> = {
  neutral: Info,
  info: Info,
  ok: CircleCheck,
  warn: TriangleAlert,
  danger: CircleAlert,
}

/**
 * 说明条（2026-10-16 UI 2.0「错误与警告」改版）：**一行中性提示**。
 *
 * - 一块 8 圆角的 `surface-hover` 底，**无边框**；文字永远是 ink 一族（有 `title` 时 children 是次要灰字 ink-2：字符串接在
 *   主句后面同一行 ` · 原因`，节点则另起一行），不随语气染色。次要字用 ink-2 而不是 ink-3：说明条常坐在桌面灰上，
 *   ink-3 叠 5% 墨底实测 4.2:1（axe color-contrast），过不了 AA；
 * - 图标 14px 单色 ink-3；**只有 danger（需要你处理的那一档）图标染 danger 色**，warn / info / ok 同样安静，
 *   靠图标形状分严重度——它们不再是「同形换色」；
 * - 结构：图标 · 一句话 · 至多一个动作（ghost 按钮）· 可选「详情 ›」。技术信息（错误码、HTTP 状态、后端 detail、
 *   请求 id、长路径）放 `detail`：默认折叠，展开是等宽灰底块 +「复制」；
 * - 无障碍不变：danger 带 `role="alert"`，其余没有（调用方可显式传 `role`）。
 *
 * 位置规则（web/AGENTS.md「不推挤」）：说明条不往工作面板的文档流里**硬插**——一次性消息走浮动通知轨，长期状态进顶栏
 * 芯片，就地出现的要么原位预留要么包 `Reveal`。对话框里放在页脚上方（加载错误放顶部），设置组里作为组内最后一行。
 */
export function Notice({
  tone = 'neutral',
  title,
  action,
  icon,
  detail,
  detailKey,
  className,
  children,
  ...rest
}: Omit<HTMLAttributes<HTMLDivElement>, 'title'> & {
  tone?: StatusTone
  /** 一句话（ink）；与 children 同在时 children 是它下面的次要灰字 */
  title?: ReactNode
  /** 右侧至多一个动作（一颗 ghost sm 按钮） */
  action?: ReactNode
  /** 换图标；false = 不画图标 */
  icon?: IconComponent | false
  /** 技术细节：默认折叠，「详情 ›」展开，带「复制」（复制的是它渲染出来的文字） */
  detail?: ReactNode
  /**
   * 详情的身份：同一个槽位换成另一条提示时，展开 / 「已复制」状态不许沿用上一条。默认取 `title` + `detail`（都是字符串时）；
   * 它们是节点、又会被复用时由调用方传一个稳定标识。身份一变，原语自己把详情收起并清掉复制回执。
   */
  detailKey?: string
  children?: ReactNode
}) {
  const { t } = useTranslation('common')
  const Icon = icon === false ? null : (icon ?? ICON[tone])
  const [open, setOpen] = useState(false)
  const identity =
    detailKey ?? (typeof detail === 'string' ? `${typeof title === 'string' ? title : ''}\u0000${detail}` : null)
  const [seen, setSeen] = useState(identity)
  if (seen !== identity) {
    // 渲染期派生重置（React 推荐写法）：换了一条，新的一条默认收起
    setSeen(identity)
    setOpen(false)
  }
  const hasDetail = detail != null && detail !== false && detail !== ''
  return (
    <div
      role={tone === 'danger' ? 'alert' : undefined}
      {...rest}
      data-notice={tone}
      className={cn('flex flex-col rounded-md bg-surface-hover text-sm leading-5 text-ink', className)}
    >
      <div className="flex min-h-[30px] items-start gap-2 py-[5px] pl-2.5 pr-1.5">
        {Icon && (
          <Icon
            size={ICON_SIZE.sm}
            aria-hidden
            data-notice-icon={tone === 'danger' ? 'attention' : ''}
            className={cn('mt-[3px] shrink-0', tone === 'danger' ? 'text-danger' : 'text-ink-3')}
          />
        )}
        <div className="min-w-0 flex-1 break-words">
          {title != null && typeof children === 'string' ? (
            // 主句 + 一句原因：同一行（「没有发出去 · 助手服务暂时出错」），原因是次要灰字
            <p>
              {title}
              <span className="text-ink-2">
                {' · '}
                {children}
              </span>
            </p>
          ) : (
            <>
              {title != null && <p>{title}</p>}
              {children != null && <div className={title != null ? 'text-ink-2' : undefined}>{children}</div>}
            </>
          )}
        </div>
        {hasDetail && (
          <Button
            size="sm"
            className="-my-1 shrink-0 text-ink-2"
            aria-expanded={open}
            data-notice-detail-toggle
            onClick={() => setOpen((v) => !v)}
          >
            {t('notice.details')}
            <ChevronRight
              size={ICON_SIZE.xs}
              aria-hidden
              className={cn('transition-transform duration-fast', open && 'rotate-90')}
            />
          </Button>
        )}
        {action != null && <div className="-my-1 flex shrink-0 items-center gap-1">{action}</div>}
      </div>
      {hasDetail && (
        <Reveal open={open}>
          <NoticeDetail key={identity}>{detail}</NoticeDetail>
        </Reveal>
      )}
    </div>
  )
}

/** 详情块：等宽小字灰底 + 「复制」。复制的是块里渲染出来的文字，调用方不用再备一份字符串 */
function NoticeDetail({ children }: { children: ReactNode }) {
  const { t } = useTranslation('common')
  const body = useRef<HTMLDivElement>(null)
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const id = setTimeout(() => setCopied(false), 1500)
    return () => clearTimeout(id)
  }, [copied])
  return (
    <div data-notice-detail className="px-2.5 pb-2">
      <div className="flex items-start justify-between gap-2 rounded-sm bg-surface-2 py-1.5 pl-2 pr-1">
        <div ref={body} className="min-w-0 flex-1 select-text whitespace-pre-wrap break-all font-mono text-xs text-ink-3">
          {children}
        </div>
        <Button
          size="sm"
          className="-my-0.5 shrink-0 text-ink-2"
          data-notice-copy
          onClick={() => {
            void navigator.clipboard?.writeText(body.current?.textContent ?? '').then(
              () => setCopied(true),
              () => {},
            )
          }}
        >
          {copied ? t('notice.copied') : t('actions.copy')}
        </Button>
      </div>
    </div>
  )
}

/**
 * 文档流里「事后才冒出来」的说明条（刷新失败、保留上一轮结果、属性栏的说明）不硬插：包一层高度过渡，
 * 它长出来时下面的内容是顺着滑下去的，而不是瞬间跳一格（`Reveal`，grid 行 0fr → 1fr + 淡入）。
 * 收起那段里 `children` 已经没了（错误清掉了），所以退场期间留着最后一份来播完。
 * 能原位预留的就预留；这个只给「预留不了、必须就地展开」的位置。
 */
export function NoticeReveal({ open, children }: { open: boolean; children: ReactNode }) {
  const last = useRef(children)
  if (open) last.current = children
  return <Reveal open={open}>{open ? children : last.current}</Reveal>
}

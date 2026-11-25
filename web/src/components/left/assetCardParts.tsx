import { Fragment, type ReactNode } from 'react'
import { t as translate } from '@/i18n'
import { MiddleEllipsis } from '@/components/ui/MiddleEllipsis'
import { nameTail } from '@/lib/assetCardText'
import { cn } from '@/lib/utils'

/**
 * 图卡的两块共用外观：缩略图区（3:2 满幅、底边一条细线）与文字区（名字一行、元数据一行）。
 * 左栏素材卡与空画布上的选图卡都用这两块——同一张图在两处必须长得一样（11-25 用户：两处卡片不一样）。
 */

export function CardPreview({ children, empty = false }: { children: ReactNode; empty?: boolean }) {
  return (
    <div
      className={cn(
        'relative flex aspect-[3/2] items-center justify-center overflow-hidden border-b border-border/55',
        empty ? 'bg-surface-2' : 'bg-paper',
      )}
    >
      {children}
    </div>
  )
}

/** 元数据行里的一段：纯文字，或带 title（悬停看完整解释）的文字 */
type MetaPart = string | { text: string; title?: string } | null | undefined

/**
 * 卡片文字区：第一行名字（+ 一个 16px 的标记位），第二行元数据用「·」串起来、
 * 使用次数靠右。两行都是单行截断，长文件名 / 长状态不会把卡片撑高。
 * 名字是正文档（12），元数据 11：主文字与 meta 之间要有 2px 的台阶，不能只靠颜色分层
 * （宪法第六节；左栏审计 L02）。
 */
export function CardMeta({
  name,
  selected,
  marker,
  parts,
  used,
  extra,
}: {
  name: string
  selected: boolean
  marker?: ReactNode
  parts: MetaPart[]
  used: number
  extra?: ReactNode
}) {
  const shown = parts.filter((p): p is Exclude<MetaPart, null | undefined> => p != null)
  return (
    <div className="flex flex-col gap-0.5 px-1.5 py-1.5">
      <p className="flex items-center gap-1 text-sm">
        <span className={cn('min-w-0 flex-1', selected ? 'font-medium text-ink' : 'text-ink')} title={name}>
          {/* 截断时保留扩展名和结尾几位（Fig2_corr…ation.pdf）：用户靠结尾的编号与扩展名区分文件 */}
          <MiddleEllipsis text={name} tail={nameTail(name)} />
        </span>
        {marker}
      </p>
      <p className={cn('flex items-center gap-1 type-meta tabular-nums', selected && 'text-ink-2')}>
        {shown.map((part, i) => (
          <Fragment key={i}>
            {i > 0 && (
              <span aria-hidden className="shrink-0">
                ·
              </span>
            )}
            <span
              className={cn('truncate', i === 0 ? 'shrink-0' : 'min-w-0')}
              title={typeof part === 'string' ? undefined : part.title}
            >
              {typeof part === 'string' ? part : part.text}
            </span>
          </Fragment>
        ))}
        {used > 0 && (
          <span className="ml-auto shrink-0 pl-1" title={translate('assets.cardUsed', { ns: 'workspace', count: used })}>
            ×{used}
          </span>
        )}
      </p>
      {extra}
    </div>
  )
}


import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { useTranslation } from 'react-i18next'
import { RotateCcw } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { msg, t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { overrideCounts } from '@/lib/overrideCounts'
import { clearOverrides, resetOverrides } from '@/store/actions'
import type { PanelObject } from '@/types/document'
import { Menu, MenuItem } from '../ui/Menu'
import { Tip } from '../ui/Tooltip'

const el = (key: string, values?: Record<string, unknown>) =>
  translate(`element.${key}`, { ns: 'inspector', ...(values ?? {}) })

/**
 * 身份头面包屑行尾的「n 项已修改 ↺」：那颗计数徽标本身就是恢复菜单的触发器。
 *
 * 徽标回答「这个对象被 Tavotto 改了几项」，↺ 回答「怎么撤回」——同一个问题的两半，
 * 就该是同一个东西，不另起一行、不另加一颗钮（2026-09-12 用户对首版「脚本行」的
 * 回退意见：那一行把脚本名、「脚本未改动」、↺、? 摆成一整行，占地且啰嗦）。
 * 形态与标题左侧的类型徽标（`ObjectKindSwitch`）同一套：小圆角、轻 tint、xs 图标。
 *
 * 菜单两项各说各的对象与数量（审计 T32）：「恢复此元素 · n 项」只在选中了元素且它
 * 自己有修改时出现；「恢复整张图 · m 项」永远在。数字来自同一份 `overrideCounts`，
 * 按下去清掉的正是标签上写的那批。一条修改都没有时整颗徽标不出现——与字段级 ↺
 * 同一条纪律，也就没有「禁用的恢复钮」这回事。
 */
export function RestoreMenu({
  panel,
  gid,
  count,
}: {
  panel: PanelObject
  gid?: string | null
  /** 徽标上写的数：选中元素时是它自己的修改数，整张图时是面板总数（由头部算） */
  count: number
}) {
  useTranslation('inspector')
  if (count <= 0) return null
  const counts = overrideCounts(panel.overrides, gid)

  return (
    <Menu width={196} align="end" trigger={<ModifiedBadge count={count} />}>
      {gid && counts.element > 0 && (
        <MenuItem
          onSelect={() =>
            clearOverrides(
              panel.id,
              msg('element.resetElement', undefined, 'inspector'),
              panel.overrides
                .filter((o) => o.gid === gid)
                .map((o) => ({ gid: o.gid, prop: o.prop })),
            )
          }
        >
          {el('resetElementCount', { count: counts.element })}
        </MenuItem>
      )}
      <MenuItem onSelect={() => resetOverrides(panel.id)}>
        {el('resetToScriptCount', { count: counts.figure })}
      </MenuItem>
    </Menu>
  )
}

/**
 * 触发器与 `IconButton` 同一种搭法：气泡包在按钮外面，Radix 菜单触发器的属性
 * （ref / onPointerDown / aria-haspopup…）要落到真正的 `<button>` 上，所以这一层
 * 必须把它们透传下去——直接拿 `<Tip>` 当触发器的话这些属性会停在 Tip 上，菜单打不开。
 */
const ModifiedBadge = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { count: number }
>(function ModifiedBadge({ count, className, ...props }, ref) {
  return (
    <Tip label={el('restoreMenuTip')} side="bottom">
      <button
        ref={ref}
        type="button"
        data-restore-menu
        className={cn(
          'group flex shrink-0 items-center gap-1 rounded-sm bg-selected py-px pl-1 pr-0.5 text-xs text-ink',
          'outline-none transition-colors hover:bg-surface-hover focus-visible:focus-ring',
          'data-[state=open]:bg-surface-hover',
          className,
        )}
        {...props}
      >
        {el('modifiedCount', { count })}
        <RotateCcw size={ICON_SIZE.xs} className="text-ink-3 group-hover:text-ink" aria-hidden />
      </button>
    </Tip>
  )
})

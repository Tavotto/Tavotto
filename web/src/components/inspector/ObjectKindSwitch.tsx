import { ChevronDown } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import {
  sharedSwitchKind,
  switchKindLabel,
  switchKindOf,
  switchTargets,
  type SwitchKind,
} from '@/lib/shapeSwitch'
import { switchObjectKind } from '@/store/actions'
import type { CanvasObject } from '@/types/document'
import { objectTypeLabel } from '@/types/document'
import { ICON_SIZE } from '../ui/Icon'
import { Menu, MenuHeading, MenuRadioGroup, MenuRadioItem } from '../ui/Menu'
import { KIND_SWITCH_ICON } from './kindSwitchIcons'

/**
 * 属性栏对象标题里的那颗类型徽标，**兼作类型切换**。
 *
 * 徽标本来只是在说「我在改的是矩形」（审计 T01 加的）。它同时也是用户唯一
 * 看着「矩形」二字的地方——想换成椭圆时，最自然的动作就是点它。所以这里让
 * 它长出下拉：**不动版面、不多一行控件**，静态徽标与开关是同一颗。
 *
 * 三件事由 `lib/shapeSwitch` 说了算，这里一个字都不判：
 * 能不能切（`switchTargets` 返回空数组就退回静态徽标）、当前是哪一种
 * （`sharedSwitchKind`，多选取值不一致时是 **null = 多个值**，不挑第一个冒充）、
 * 切完什么样（`switchObjectKind` → `switchObject`）。右键菜单的「更改为 ›」
 * 走的是同一组函数。
 *
 * 键盘可达：Radix 的 DropdownMenu 给触发器 Enter / Space / ↓ 打开，菜单里
 * 方向键漫游、首字母跳转、Esc 收回；单选项是 `role="menuitemradio"`，当前那个
 * 带勾——**选中态不只靠颜色**。
 */
export function ObjectKindSwitch({ objs }: { objs: CanvasObject[] }) {
  useTranslation('inspector')
  const targets = switchTargets(objs)
  const current = sharedSwitchKind(objs)
  // 不可切换时徽标照旧出现：它回答的是「我在改什么」，那句话与能不能换类型无关。
  // 单选才有答案——多选混排（一张图 + 一段文字）没有一个类型可说，整颗不出现。
  if (!targets.length) {
    const one = objs.length === 1 ? objs[0] : null
    if (!one) return null
    const kind = switchKindOf(one)
    return <KindBadge>{kind ? switchKindLabel(kind) : objectTypeLabel(one.type)}</KindBadge>
  }

  const currentLabel = current ? switchKindLabel(current) : translate('mixed', { ns: 'common' })
  const ids = objs.map((o) => o.id)

  return (
    <Menu
      width={168}
      align="start"
      trigger={
        <button
          type="button"
          data-object-kind
          data-kind-switch
          aria-label={translate('kindSwitch.aria', { ns: 'inspector', name: currentLabel })}
          className={cn(
            'group flex shrink-0 items-center gap-0.5 rounded-sm bg-surface-active py-px pl-1 pr-0.5 text-xs text-ink-2',
            'outline-none transition-colors hover:bg-surface-hover hover:text-ink focus-visible:focus-ring',
            'data-[state=open]:bg-selected data-[state=open]:text-ink',
          )}
        >
          {currentLabel}
          {/* 下拉的记号只有 chevron-down（Design Constitution 第四节）：收起朝下，
              菜单展开（Radix 触发器 data-state=open）时转成朝上 */}
          <ChevronDown
            size={ICON_SIZE.xs}
            className="text-ink-3 transition-transform duration-fast group-data-[state=open]:rotate-180"
            aria-hidden
          />
        </button>
      }
    >
      <MenuHeading>{translate('kindSwitch.title', { ns: 'inspector' })}</MenuHeading>
      <MenuRadioGroup
        data-kind-switch-group
        // 取值不一致时一个都不勾（Radix 认不到任何一项就不显示 indicator）
        value={current ?? undefined}
        onValueChange={(v) => switchObjectKind(ids, v as SwitchKind)}
      >
        {targets.map((kind) => (
          <MenuRadioItem
            key={kind}
            value={kind}
            icon={KIND_SWITCH_ICON[kind]}
            data-kind-target={kind}
          >
            {switchKindLabel(kind)}
          </MenuRadioItem>
        ))}
      </MenuRadioGroup>
    </Menu>
  )
}

/** 不可切换时的静态徽标：与可切换那颗同一套底色与字号，只是不点得动 */
export function KindBadge({ children }: { children: React.ReactNode }) {
  return (
    <span data-object-kind className="shrink-0 rounded-sm bg-surface-active px-1 text-xs text-ink-2">
      {children}
    </span>
  )
}

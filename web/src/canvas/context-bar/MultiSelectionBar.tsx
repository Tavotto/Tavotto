import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, Group, SlidersHorizontal, Ungroup } from 'lucide-react'
import { t as translate } from '@/i18n'
import { captureContextBarMore, fromContextBar } from '@/lib/activityTelemetry'
import { cn } from '@/lib/utils'
import {
  ALIGN_BUTTONS,
  ALIGN_REFS,
  DISTRIBUTE_BUTTONS,
  SIZE_BUTTONS,
  type ArrangeButton,
} from '@/components/inspector/arrangeButtons'
import { Button } from '@/components/ui/Button'
import { Popover } from '@/components/ui/Popover'
import { Segmented } from '@/components/ui/Segmented'
import { Tip } from '@/components/ui/Tooltip'
import {
  alignModeLabel,
  alignRefLabel,
  alignSelectedTo,
  groupSelected,
  selectionHasGroupIn,
  ungroupSelected,
  type AlignRef,
} from '@/store/actions'
import { useArrangeStore } from '@/store/arrangeStore'
import type { CanvasObject } from '@/types/document'
import type { BarVariant } from './position'
import { openArrangeInInspector } from './openArrange'
import { Sep } from './shared'
import { qb } from './text'

/**
 * 多选（两个及以上画布对象）的浮动工具条。
 *
 * 它不是第二套排列系统：每个按钮只发意图，落地全部走 `store/actions`
 * （`alignSelectedTo` / `groupSelected` / `ungroupSelected`）——与右侧 `ArrangeSection`
 * 同一个函数、同一条历史文案、同一份锁定 / 成组判据；参照读 `arrangeStore`，
 * 两个入口切的是同一个值。按钮表（图标 / 顺序 / 最少对象数）也从 `ArrangeSection`
 * 取，不再抄一份。
 *
 * 宽度不够（`variant === 'compact'`）时压成三个弹层入口 + 成组 + 更多。
 */
const ar = (key: string, values?: Record<string, unknown>) =>
  translate(`arrange.${key}`, { ns: 'inspector', ...(values ?? {}) })

export function MultiSelectionBar({
  objs,
  variant,
}: {
  objs: CanvasObject[]
  variant: BarVariant
}) {
  useTranslation('workspace')
  useTranslation('inspector')
  const count = objs.length
  const ref = useArrangeStore((s) => s.alignRef)
  const grouped = selectionHasGroupIn(objs)

  const countEl = (
    <span
      data-selection-count={count}
      title={qb('primaryHint')}
      className="whitespace-nowrap px-1 text-ink-2"
    >
      {qb('selectedCount', { count })}
    </span>
  )

  if (variant === 'compact') {
    return (
      <>
        {countEl}
        <Sep />
        <MenuPopover label={qb('alignMenu')} width={232} testId="align">
          <RefPicker />
          <AlignRow modes={ALIGN_BUTTONS} refName={ref} count={count} />
        </MenuPopover>
        <MenuPopover label={qb('distributeMenu')} width={120} testId="distribute">
          <AlignRow modes={DISTRIBUTE_BUTTONS} refName={ref} count={count} />
        </MenuPopover>
        <MenuPopover label={qb('sizeMenu')} width={120} testId="size">
          <AlignRow modes={SIZE_BUTTONS} refName={ref} count={count} />
        </MenuPopover>
        <Sep />
        <GroupButtons grouped={grouped} />
        <MoreButton count={count} />
      </>
    )
  }

  return (
    <>
      {countEl}
      <Sep />
      <RefPicker />
      <Sep />
      <AlignRow modes={ALIGN_BUTTONS} refName={ref} count={count} />
      <Sep />
      <AlignRow modes={DISTRIBUTE_BUTTONS} refName={ref} count={count} />
      <AlignRow modes={SIZE_BUTTONS} refName={ref} count={count} />
      <Sep />
      <GroupButtons grouped={grouped} />
      <MoreButton count={count} />
    </>
  )
}

/** 参照三选一：与 ArrangeSection 共用 `arrangeStore`，这边切了那边当场就是新值 */
function RefPicker() {
  const ref = useArrangeStore((s) => s.alignRef)
  const setRef = useArrangeStore((s) => s.setAlignRef)
  return (
    <div data-align-ref-picker className="shrink-0">
      <Segmented<AlignRef>
        size="sm"
        tone="quiet"
        ariaLabel={ar('refLabel')}
        value={ref}
        onChange={setRef}
        items={ALIGN_REFS.map((r) => ({
          value: r,
          label: alignRefLabel(r),
          tip: ar(`refTip.${r}`),
        }))}
      />
    </div>
  )
}

/**
 * 一排排列按钮。分布需要三个对象：不够时按钮**仍可聚焦、tooltip 仍说得出原因**
 * （原生 `disabled` 不发 pointer 事件，说明会一起消失），只是点了不动。
 */
function AlignRow({
  modes,
  refName,
  count,
}: {
  modes: readonly ArrangeButton[]
  refName: AlignRef
  count: number
}) {
  return (
    <div className="flex items-center gap-0.5">
      {modes.map(({ mode, icon: Icon, tipKey, min }) => {
        const name = alignModeLabel(mode)
        const blocked = count < min
        const tip = tipKey
          ? ar(tipKey)
          : ar('alignRelativeRef', { mode: name, ref: alignRefLabel(refName) })
        return (
          <Tip key={mode} label={tip} side="bottom">
            <Button
              size="icon-sm"
              data-align-mode={mode}
              aria-label={name}
              aria-disabled={blocked || undefined}
              className={cn(blocked && 'cursor-not-allowed opacity-35')}
              onClick={() => {
                if (blocked) return
                // 遥测只认「从浮动栏发起」的那一次（`lib/activityTelemetry`）
                fromContextBar(() => alignSelectedTo(mode, refName))
              }}
            >
              <Icon size={12} />
            </Button>
          </Tip>
        )
      })}
    </div>
  )
}

/** 成组常驻；选区里已有组时再给「取消成组」（判据 = actions 的 selectionHasGroupIn） */
function GroupButtons({ grouped }: { grouped: boolean }) {
  return (
    <>
      <Tip label={ar('groupTip')} side="bottom">
        <Button
          size="icon-sm"
          data-group-action="group"
          aria-label={ar('group')}
          onClick={() => fromContextBar(() => groupSelected())}
        >
          <Group size={12} />
        </Button>
      </Tip>
      {grouped && (
        <Tip label={ar('ungroupTip')} side="bottom">
          <Button
            size="icon-sm"
            data-group-action="ungroup"
            aria-label={ar('ungroup')}
            onClick={() => fromContextBar(() => ungroupSelected())}
          >
            <Ungroup size={12} />
          </Button>
        </Tip>
      )}
    </>
  )
}

/** 「更多」：到属性页的排列组去（间距 / 布局组 / 样式搬运都在那里） */
function MoreButton({ count }: { count: number }) {
  return (
    <Tip label={qb('moreArrangeTip')} side="bottom">
      <Button
        size="icon-sm"
        data-multi-more
        aria-label={qb('moreArrange')}
        onClick={() => {
          openArrangeInInspector()
          captureContextBarMore(count)
        }}
      >
        <SlidersHorizontal size={12} />
      </Button>
    </Tip>
  )
}

/** 窄屏下的弹层入口：文字按钮 + 下拉角，内容还是同一批按钮 */
function MenuPopover({
  label,
  width,
  testId,
  children,
}: {
  label: string
  width: number
  testId: string
  children: ReactNode
}) {
  return (
    <Popover
      width={width}
      align="start"
      trigger={
        <Button size="sm" className="gap-0.5 px-1.5" data-multi-menu={testId} aria-label={label}>
          {label}
          <ChevronDown size={11} aria-hidden />
        </Button>
      }
    >
      <div className="flex flex-col gap-1.5">{children}</div>
    </Popover>
  )
}

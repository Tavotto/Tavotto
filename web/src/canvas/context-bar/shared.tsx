import type { ReactNode } from 'react'
import { ChevronDown, SlidersHorizontal } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Button } from '@/components/ui/Button'
import { Popover } from '@/components/ui/Popover'
import { Tip } from '@/components/ui/Tooltip'
import { useUiStore } from '@/store/uiStore'
import { qb } from './text'

/**
 * 组与组之间的那道线。**它自己不带外边距**（2026-09-15 打磨 F3）：留白全部由容器的
 * `gap-1` 给，组间距因此处处是 4 + 1 + 4 = 9（宪法第三节的 8 再加线本身）。
 *
 * 此前线自己带 `mx-0.5`、容器又各给一份 gap，两份叠出来的组间距单选栏是 5、
 * 多选栏是 17——同一条浮动栏家族里三种数。空白只能有一个来源，否则改一处不算改完。
 */
export const Sep = () => <span aria-hidden className="h-4 w-px shrink-0 bg-border" />

/** 「全部属性」——工具条到属性页的固定出口 */
export function OpenInspectorButton() {
  return (
    <Tip label={qb('openInspector')} side="bottom">
      <Button
        size="icon-sm"
        aria-label={qb('openInspector')}
        onClick={() => {
          const ui = useUiStore.getState()
          ui.setRightTab('properties')
        }}
      >
        <SlidersHorizontal size={ICON_SIZE.sm} />
      </Button>
    </Tip>
  )
}

/**
 * 窄屏下的弹层入口：文字按钮 + 下拉角，内容还是同一批按钮。画布多选栏与图内多选栏
 * （ADR 0089）的压缩档共用这一份。
 */
export function MenuPopover({
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
        <Button size="md" data-multi-menu={testId} aria-label={label}>
          {label}
          <ChevronDown size={ICON_SIZE.xs} aria-hidden />
        </Button>
      }
    >
      <div className="flex flex-col gap-1.5">{children}</div>
    </Popover>
  )
}

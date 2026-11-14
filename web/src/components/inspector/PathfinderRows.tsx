import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ICON_SIZE } from '@/components/ui/Icon'
import { formatMessage, t as translate } from '@/i18n'
import { keyOf } from '@/lib/keymap'
import { loadVecops, useVecopsStore } from '@/lib/vecops/kernel'
import { useDocumentStore } from '@/store/documentStore'
import {
  blockedReason,
  getOffsetMm,
  offsetSelected,
  PATHFINDER_COMMANDS,
  setOffsetMm,
  type PathfinderCommand,
} from '@/store/pathfinderActions'
import { useSelectionStore } from '@/store/selectionStore'
import { IconButton } from '../ui/Button'
import { NumberField } from '../ui/Input'
import { PATHFINDER_COMMAND_ICONS } from '@/components/pathfinderCommandIcons'

/**
 * 属性栏里的路径查找器（ADR 0185 M2）：排列区一行摘要「路径查找器」，点开是两排图标——
 * 合并 / 减去顶层 / 相交 / 排除重叠 / 分割 / 修边 / 复合路径（选着两个以上能参与的对象时），
 * 偏移路径（带距离格）/ 轮廓化描边 / 反转方向 / 释放复合路径（各自适用时）。
 * 哪几个出现只看命令表 `PATHFINDER_COMMANDS` 的 `available()`（与右键菜单、命令面板同一份）；
 * 内核加载不了时按钮灰掉，停上去说原因。摘要行一出现就预取内核，点下去不用等下载。
 */

const SHAPE_MODES: readonly PathfinderCommand['id'][] = [
  'pathfinder-unite',
  'pathfinder-minus-front',
  'pathfinder-intersect',
  'pathfinder-exclude',
  'pathfinder-divide',
  'pathfinder-trim',
  'make-compound',
]
const PATH_OPS: readonly PathfinderCommand['id'][] = ['outline-stroke', 'simplify-path', 'reverse-path', 'release-compound']

const label = (id: PathfinderCommand['id']) => translate(`palette.commands.${id}.label`, { ns: 'dialogs' })

/** 此刻适用的路径查找器命令（订阅文档与选区，选区一变就重算） */
export function usePathfinderCommands(): PathfinderCommand[] {
  useDocumentStore((s) => s.doc.objects)
  useSelectionStore((s) => s.ids)
  useVecopsStore((s) => s.status)
  return PATHFINDER_COMMANDS.filter((c) => c.available())
}

function CommandButton({ c }: { c: PathfinderCommand }) {
  const Icon = PATHFINDER_COMMAND_ICONS[c.id]
  const reason = blockedReason(c)
  return (
    <IconButton
      data-pathfinder-command={c.id}
      label={label(c.id)}
      tip={reason ? formatMessage(reason) : true}
      shortcut={c.key ? keyOf(c.key) : undefined}
      side="left"
      disabled={!!reason}
      onClick={c.run}
    >
      <Icon size={ICON_SIZE.md} />
    </IconButton>
  )
}

export function PathfinderRows({ commands }: { commands: readonly PathfinderCommand[] }) {
  useTranslation(['inspector', 'dialogs'])
  const [offset, setOffset] = useState(getOffsetMm)
  // 摘要行一出现就在后台预取内核：真点下去时已经编译好了
  const needsKernel = commands.some((c) => c.kernel)
  useEffect(() => {
    if (needsKernel) void loadVecops()
  }, [needsKernel])
  const has = (id: PathfinderCommand['id']) => commands.find((c) => c.id === id)
  const modes = SHAPE_MODES.map(has).filter((c): c is PathfinderCommand => !!c)
  const ops = PATH_OPS.map(has).filter((c): c is PathfinderCommand => !!c)
  const offsetCmd = has('offset-path')
  const offsetBlocked = offsetCmd ? blockedReason(offsetCmd) : null
  return (
    <div className="flex flex-col gap-1" data-pathfinder-rows="">
      {modes.length > 0 && (
        <div role="toolbar" aria-label={translate('pathfinder.modes', { ns: 'inspector' })} className="flex flex-wrap items-center gap-0.5">
          {modes.map((c) => (
            <CommandButton key={c.id} c={c} />
          ))}
        </div>
      )}
      {(offsetCmd || ops.length > 0) && (
        <div role="toolbar" aria-label={translate('pathfinder.ops', { ns: 'inspector' })} className="flex flex-wrap items-center gap-0.5">
          {offsetCmd && (
            <>
              <CommandButton c={{ ...offsetCmd, run: () => void offsetSelected(offset) }} />
              <NumberField
                half
                ariaLabel={translate('pathfinder.offsetDistance', { ns: 'inspector' })}
                title={translate('pathfinder.offsetDistanceTip', { ns: 'inspector' })}
                value={offset}
                step={0.5}
                min={-50}
                max={50}
                precision={2}
                unit="mm"
                disabled={!!offsetBlocked}
                onChange={(v) => {
                  if (!Number.isFinite(v) || v === 0) return
                  setOffset(v)
                  setOffsetMm(v)
                }}
              />
            </>
          )}
          {ops.map((c) => (
            <CommandButton key={c.id} c={c} />
          ))}
        </div>
      )}
    </div>
  )
}

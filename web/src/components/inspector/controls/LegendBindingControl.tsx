import { CornerUpLeft, Link2, Unlink2 } from 'lucide-react'
import { t as translate } from '@/i18n'
import type { Manifest, ManifestElement } from '@/lib/api'
import { entryBinding, hasStyleOverride } from '@/lib/legendModel'
import { engineLabel } from '../roles/registry'
import { restoreLegendEntryFollow } from '@/store/actions'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { Button } from '../../ui/Button'

const lg = (key: string, values?: Record<string, unknown>) =>
  translate(`legend.${key}`, { ns: 'inspector', ...(values ?? {}) })

/**
 * 图例项的「与图中对象」一行（ADR 0034）。
 *
 * 状态只有两句话：**跟随图中对象**（示意线由源派生，源变它变）与
 * **自定义**（示意线自己是一份状态）。旁边给一个动作：
 *   * 跟随中 → 「改为自定义」（冻结在此刻的样子；直接改下方任一样式也会
 *     脱开，这里只是把那件事说明白）；
 *   * 自定义 → 「恢复跟随」——删掉全部示意线 override，一次撤销。
 * 另有一条「查看源对象」的链接：选中那条曲线 / 散点 / 柱系列。
 *
 * 没有源的项不会走到这里（引擎不发 `binding` 字段）。
 */
export function LegendBindingControl({
  panel,
  manifest,
  element,
  onSetCustom,
}: {
  panel: PanelObject
  manifest: Manifest | null | undefined
  element: ManifestElement
  /** 离散写入 `binding = custom`（走调用方的写入器：一条历史 + 一次渲染） */
  onSetCustom: () => void
}) {
  const binding = entryBinding(panel, element) ?? 'custom'
  const sourceGid = element.legend_entry?.source_gid
  const source = sourceGid ? manifest?.elements.find((e) => e.gid === sourceGid) : undefined
  const styled = hasStyleOverride(panel, element.gid)
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <div className="flex min-w-0 items-center gap-1.5">
        {binding === 'follow_source' ? (
          <Link2 size={12} className="shrink-0 text-ink-3" aria-hidden />
        ) : (
          <Unlink2 size={12} className="shrink-0 text-accent" aria-hidden />
        )}
        <span className="min-w-0 truncate text-xs text-ink" data-binding={binding}>
          {binding === 'follow_source' ? lg('stateFollow') : lg('stateCustom')}
        </span>
        {binding === 'follow_source' ? (
          <Button size="sm" className="ml-auto shrink-0 text-ink-2" onClick={onSetCustom}>
            {lg('makeCustom')}
          </Button>
        ) : (
          <Button
            size="sm"
            className="ml-auto shrink-0 text-ink-2"
            onClick={() => restoreLegendEntryFollow(panel.id, element)}
          >
            {lg('restoreFollow')}
          </Button>
        )}
      </div>
      <p className="text-xs leading-snug text-ink-3">
        {binding === 'follow_source'
          ? lg('followHint')
          : styled
            ? lg('customHintStyled')
            : lg('customHint')}
      </p>
      {source && (
        <Button
          size="sm"
          className="max-w-full self-start px-1.5 text-ink-2"
          onClick={() => useUiStore.getState().setSelectedGid(source.gid)}
        >
          <CornerUpLeft size={11} className="shrink-0" />
          <span className="truncate">{lg('viewSource', { label: engineLabel(source.label) })}</span>
        </Button>
      )}
    </div>
  )
}

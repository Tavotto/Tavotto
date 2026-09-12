import { CornerUpLeft, Link2, Unlink2 } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import type { Manifest, ManifestElement } from '@/lib/api'
import { entryBinding, hasStyleOverride } from '@/lib/legendModel'
import { engineLabel } from '../roles/registry'
import { restoreLegendEntryFollow } from '@/store/actions'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { Button } from '../../ui/Button'
import { Tip } from '../../ui/Tooltip'

const lg = (key: string, values?: Record<string, unknown>) =>
  translate(`legend.${key}`, { ns: 'inspector', ...(values ?? {}) })

/**
 * 图例项与图中对象的**链接**（ADR 0034；审计 T18）。
 *
 * 一行说完关系与状态：链条开关 + 「链接到：散点 “Observed”」 + 来源入口。
 * 审计点名的问题是「先用一段说明解释跟随对象，再列跟随 / 自定义 / 查看源对象」
 * ——同一件事说了三遍。现在**状态在文字里、动作在开关上、原理在提示里**：
 *
 *   * 链接中 → 点开关 = 断开（示意线冻结在此刻的样子，成为一份自己的状态）；
 *   * 已断开 → 点开关 = 恢复链接（删掉全部示意线 override，**一次撤销**，
 *     走 `store/actions.restoreLegendEntryFollow`，不在组件里逐条清）。
 *
 * 「查看源对象」两种状态下都留着：断开之后关系仍然存在（只是样式不再派生），
 * 藏起来会让人以为改这一项动到了那条曲线本身——那正是这一条的验收。
 *
 * 示意线的样式字段**只在断开后出现**（展示注册表的 `visibleWhen`，判据是
 * `binding !== 'follow_source'`，与这里读的是同一个字段值）。所以链接中不再有
 * 「改下方任一样式即断开」这条路——文案跟着改了，别让提示说一件做不到的事。
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
  const linked = binding === 'follow_source'
  const sourceGid = element.legend_entry?.source_gid
  const source = sourceGid ? manifest?.elements.find((e) => e.gid === sourceGid) : undefined
  const sourceLabel = source ? engineLabel(source.label) : null
  const styled = hasStyleOverride(panel, element.gid)

  // 状态文字：链接中说链接到谁，断开后说来源仍是谁——两种状态都答得出
  // 「这一项与图里哪个对象是一回事」
  const stateText = linked
    ? sourceLabel
      ? lg('linkedTo', { label: sourceLabel })
      : lg('stateFollow')
    : sourceLabel
      ? lg('unlinkedFrom', { label: sourceLabel })
      : lg('stateCustom')
  const toggleLabel = linked ? lg('unlinkAction') : lg('relinkAction')
  const toggleHint = linked ? lg('followHint') : styled ? lg('customHintStyled') : lg('customHint')

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <div className="flex min-w-0 items-center gap-1.5">
        <Tip label={`${toggleLabel} · ${toggleHint}`}>
          <Button
            size="icon-sm"
            aria-pressed={linked}
            aria-label={toggleLabel}
            data-binding-toggle={binding}
            className="shrink-0"
            onClick={() => (linked ? onSetCustom() : restoreLegendEntryFollow(panel.id, element))}
          >
            {/* 状态不只靠颜色：断开是另一个图形，旁边那句文字也换了 */}
            {linked ? (
              <Link2 size={ICON_SIZE.xs} className="text-ink-2" aria-hidden />
            ) : (
              <Unlink2 size={ICON_SIZE.xs} className="text-ink-3" aria-hidden />
            )}
          </Button>
        </Tip>
        <span
          className="min-w-0 flex-1 truncate text-xs text-ink"
          data-binding={binding}
          title={stateText}
        >
          {stateText}
        </span>
      </div>
      {source && (
        <Button
          size="sm"
          className="max-w-full self-start px-1.5 text-ink-2"
          onClick={() => useUiStore.getState().setSelectedGid(source.gid)}
        >
          <CornerUpLeft size={ICON_SIZE.xs} className="shrink-0" />
          <span className="truncate">{lg('viewSource', { label: engineLabel(source.label) })}</span>
        </Button>
      )}
    </div>
  )
}

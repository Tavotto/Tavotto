import { CornerUpLeft, Link2 } from 'lucide-react'
import { t as translate } from '@/i18n'
import type { Manifest, ManifestElement } from '@/lib/api'
import { useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { Tip } from '../ui/Tooltip'
import { engineLabel } from './roles/registry'

/**
 * 色条与它上色的图像 / 集合**共用一份色阶**（审计 T22 / T23）：色图、上下限
 * 在两边都能改，改哪边另一边都跟着变（引擎 `ALIAS_GROUPS`，同一份状态的两个
 * gid）。这一行把关系说出口，并给一个「选中对方」的入口——两边各显示一次
 * 色阶字段本身没有错，错的是不说它们是同一份。
 *
 * 判据只认 manifest 的 `mappable_gid`（引擎反查 `cb.mappable` 得来），不猜
 * 「两边 cmap 名字相同就是一对」。原理挂在按钮的悬停提示上（键盘也到得了），
 * 不是一段常驻说明。
 */
const el = (key: string, values?: Record<string, unknown>) =>
  translate(`element.${key}`, { ns: 'inspector', ...(values ?? {}) })

/** 色阶的另一端：色条 → 它的 mappable；图像 / 集合 → 给它上色的色条 */
export function colorScalePartner(
  manifest: Manifest,
  element: ManifestElement,
): ManifestElement | null {
  if (element.role === 'colorbar') {
    return element.mappable_gid
      ? (manifest.elements.find((e) => e.gid === element.mappable_gid) ?? null)
      : null
  }
  return manifest.elements.find((e) => e.role === 'colorbar' && e.mappable_gid === element.gid) ?? null
}

export function ColorScaleLink({
  manifest,
  element,
}: {
  manifest: Manifest
  element: ManifestElement
}) {
  const partner = colorScalePartner(manifest, element)
  if (!partner) return null
  const label = engineLabel(partner.label)
  return (
    <div
      data-color-scale-link={partner.gid}
      className="mb-2 flex min-w-0 items-center gap-1.5 rounded-sm border border-border px-2 py-1"
    >
      <Link2 size={12} className="shrink-0 text-ink-3" aria-hidden />
      <span className="min-w-0 flex-1 truncate text-xs text-ink-2" title={el('colorScaleTip')}>
        {el('colorScaleLinked', { label })}
      </span>
      <Tip label={el('colorScaleTip')}>
        <Button
          size="sm"
          className="shrink-0 text-ink-2"
          onClick={() => useUiStore.getState().setSelectedGid(partner.gid)}
          aria-label={el('selectScalePartner', { label })}
        >
          <CornerUpLeft size={11} className="shrink-0" aria-hidden />
          {el('selectScalePartnerShort')}
        </Button>
      </Tip>
    </div>
  )
}

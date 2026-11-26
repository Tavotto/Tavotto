import { useTranslation } from 'react-i18next'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Row } from '@/components/ui/Field'
import { Select } from '@/components/ui/Select'
import { Spinner } from '@/components/ui/Spinner'
import { ERROR_KINDS, figureUntouched, normScript, type ErrorKind } from '@/lib/dataFigureOrigin'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useFigureOriginStore } from '@/store/figureOriginStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useTafFlagStore } from '@/store/tafFlagStore'
import type { PanelObject } from '@/types/document'
import { INSPECTOR_LABEL_W } from './layout'

/**
 * 误差棒属性页最上面的一行「误差线 [SD ▾]」（阴影带的属性页上叫「误差带」）：数据页（「从数据到图」，实验开关 `ai.taf.enabled`）画出的图，误差线是 Tavotto 用重复值**算出来的**时，
 * 在这里换成 SD / SEM / 95% CI 重画一版。选项与标签逐字沿用数据页右栏的 `dataFigureV2.confirm.*`。
 *
 * 出现条件缺一不显示（属性页与没有这一行时逐字相同）：开关开着；这块面板是本次会话里用数据页画的（`figureOriginStore` 记着来历）；
 * 误差线是算出来的、单文件（登记时已判）；图没被改过（`lib/dataFigureOrigin.figureUntouched`——换图带不走手改）。
 * 换的过程中下拉立刻显示新值、转灰、禁用，状态槽里转圈；失败 / 被取代 / 换了项目静默回到原值（store 清掉 `switching`）。
 */
export function ErrorKindRow({ panel, band = false }: { panel: PanelObject; band?: boolean }) {
  const { t } = useTranslation('ai')
  const enabled = useTafFlagStore((s) => s.enabled)
  const key = panel.script ? normScript(panel.script) : ''
  const origin = useFigureOriginStore((s) => (key ? s.figures[key] : undefined))
  const switching = useFigureOriginStore((s) => (s.switching?.script === key ? s.switching : null))
  const busy = useFigureOriginStore((s) => s.switching != null)
  const doc = useDocumentStore((s) => s.doc)
  const runtime = useRuntimeAssetStore((s) => s.assets?.find((a) => a.id === panel.fileId))
  const bakedCount = useAssetStore((s) => s.byId[panel.fileId]?.baked_overrides?.length ?? 0)
  if (!enabled || !origin || (band && !origin.band) || !figureUntouched(doc, panel, origin, { runtime, bakedCount })) return null
  const shown: ErrorKind = switching ? switching.kind : origin.kind
  // 点中的是阴影带时这一行叫「误差带」（与数据页候选「均值线 + 误差带」同名），误差棒上仍叫「误差线」
  const label = t(band ? 'dataFigureV2.confirm.errorBand' : 'dataFigureV2.confirm.error')
  return (
    <Row
      label={label}
      labelWidth={INSPECTOR_LABEL_W}
      className="mb-1.5"
      status={switching ? <Spinner size={ICON_SIZE.xs} aria-hidden className="text-ink-3" /> : undefined}
    >
      <div data-error-kind className="flex min-w-0 flex-1" aria-busy={switching ? true : undefined}>
        <Select<ErrorKind>
          value={shown}
          disabled={busy}
          ariaLabel={label}
          onChange={(k) => void useFigureOriginStore.getState().switchKind(key, k)}
          options={ERROR_KINDS.map((k) => ({ value: k, label: t(`dataFigureV2.confirm.option.${k}`) }))}
          title={t(`dataFigureV2.confirm.option.${shown}`)}
        />
      </div>
    </Row>
  )
}

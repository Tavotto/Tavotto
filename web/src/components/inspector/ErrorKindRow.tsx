import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Row } from '@/components/ui/Field'
import { Select } from '@/components/ui/Select'
import { Spinner } from '@/components/ui/Spinner'
import { ERROR_KINDS, figureUntouched, readPanelOrigin, type ErrorKind } from '@/lib/dataFigureOrigin'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useFigureOriginStore } from '@/store/figureOriginStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useTafFlagStore } from '@/store/tafFlagStore'
import type { PanelObject } from '@/types/document'
import { INSPECTOR_LABEL_W } from './layout'

/**
 * 误差棒 / 阴影带属性页最上面的一行「误差线 [SD ▾]」：数据页（「从数据到图」，实验开关 `ai.taf.enabled`）画出的图，误差线是 Tavotto 用重复值**算出来的**时，
 * 在这里换成 SD / SEM / 95% CI 重画一版。选项与标签逐字沿用数据页右栏的 `dataFigureV2.confirm.*`。
 *
 * 来历在面板自己身上（`PanelObject.dataOrigin`，随项目文档落盘，关掉项目再打开仍在；`lib/dataFigureOrigin.readPanelOrigin` 严格读，读不出 = 没有）。
 * 出现条件缺一不显示（属性页与没有这一行时逐字相同）：开关开着；面板上有读得出的来历；图没被改过（`figureUntouched`——换图带不走手改）；
 * **数据文件还是画这张图时的那一份**（去磁盘核一次摘要，核对完成前也不出；文件不在 / 读不了 / 变了都不出，全程静默）。
 * 换的过程中下拉立刻显示新值、转灰、禁用，状态槽里转圈；失败 / 被取代 / 换了项目静默回到原值（store 清掉 `switching`）。
 */
export function ErrorKindRow({ panel, band = false }: { panel: PanelObject; band?: boolean }) {
  const { t } = useTranslation('ai')
  const enabled = useTafFlagStore((s) => s.enabled)
  const origin = readPanelOrigin(panel)
  const dataPath = origin?.file.path ?? null
  const digest = useFigureOriginStore((s) => (dataPath ? s.digests[dataPath] : undefined))
  const switching = useFigureOriginStore((s) => (s.switching?.panelId === panel.id ? s.switching : null))
  const busy = useFigureOriginStore((s) => s.switching != null)
  const doc = useDocumentStore((s) => s.doc)
  const runtime = useRuntimeAssetStore((s) => s.assets?.find((a) => a.id === panel.fileId))
  const assetsLoaded = useRuntimeAssetStore((s) => s.assets !== null)
  const bakedCount = useAssetStore((s) => s.byId[panel.fileId]?.baked_overrides?.length ?? 0)
  const wanted = enabled && !!origin && (!band || origin.band)
  // 重开项目后素材清单可能还没取过（素材库没开过）：「图没被改过」要看它，取一次
  useEffect(() => {
    if (wanted && !assetsLoaded) void useRuntimeAssetStore.getState().loadAssets()
  }, [wanted, assetsLoaded])
  // 每次这一行要出现时都去磁盘核一次数据（回来前保留上一次的结果，不闪）
  useEffect(() => {
    if (wanted && dataPath) void useFigureOriginStore.getState().checkData(dataPath)
  }, [wanted, dataPath])
  if (!wanted || !origin || !figureUntouched(doc, panel, origin, { runtime, bakedCount })) return null
  if (typeof digest !== 'string' || digest !== origin.dataSha256) return null
  const shown: ErrorKind = switching ? switching.kind : origin.kind
  return (
    <Row
      label={t('dataFigureV2.confirm.error')}
      labelWidth={INSPECTOR_LABEL_W}
      className="mb-1.5"
      status={switching ? <Spinner size={ICON_SIZE.xs} aria-hidden className="text-ink-3" /> : undefined}
    >
      <div data-error-kind className="flex min-w-0 flex-1" aria-busy={switching ? true : undefined}>
        <Select<ErrorKind>
          value={shown}
          disabled={busy}
          ariaLabel={t('dataFigureV2.confirm.error')}
          onChange={(k) => void useFigureOriginStore.getState().switchKind(panel.id, k)}
          options={ERROR_KINDS.map((k) => ({ value: k, label: t(`dataFigureV2.confirm.option.${k}`) }))}
          title={t(`dataFigureV2.confirm.option.${shown}`)}
        />
      </div>
    </Row>
  )
}

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Dialog } from '@/components/ui/Dialog'
import { Button } from '@/components/ui/Button'
import { TextArea, TextInput } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Notice } from '@/components/ui/Notice'
import { msg } from '@/i18n/message'
import { formatTime } from '@/i18n/format'
import { fileUrl } from '@/lib/api'
import { applyComputedFrameDraft } from '@/lib/chartFrame'
import { takeProjectCheckpoint } from '@/lib/timelineCheckpoint'
import { finishActiveGesture } from '@/store/gestureCoordinator'
import { chartBindConfirmReady, chartBindPanelUnchanged, useChartBindStore } from '@/store/chartBindStore'
import type { ChartSeriesForm } from '@/store/chartBindStore'
import { useDocumentStore } from '@/store/documentStore'

export function ChartBindDialog() {
  const { t } = useTranslation('workspace')
  const s = useChartBindStore()
  const ready = chartBindConfirmReady(s)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  useEffect(() => () => useChartBindStore.getState().close(), [])

  const confirm = async () => {
    const current = useChartBindStore.getState()
    if (!chartBindConfirmReady(current) || !current.panelId || !current.result) return
    const panel = useDocumentStore.getState().doc.objects.find(o => o.id === current.panelId)
    if (panel?.type !== 'panel' || !chartBindPanelUnchanged(current, panel)) return
    setConfirmError(null)
    finishActiveGesture()
    try {
      // 首次写 chart = 这份排版原地升级 schema 3→4：升级前整份项目先落一份可退回的备份
      // 节点（规则与 ADR 0182 §3）；备份没落定就不得写图表。已有 chart 的重绑不升级、免备份。
      if (!panel.chart) {
        await takeProjectCheckpoint({
          auto: true,
          name: t('chartBind.beforeUpgrade', { time: formatTime(Date.now()) }),
          programName: true,
          allowEmpty: true,
        })
        // await 之后上下文可能已变（L-123）：面板被编辑过就放弃这次确认
        const panelAfter = useDocumentStore.getState().doc.objects.find(o => o.id === current.panelId)
        if (panelAfter?.type !== 'panel' || !chartBindPanelUnchanged(current, panelAfter)) return
      }
      const chart = { model: current.result.model, binding: current.result.binding }
      const overrides = current.resultRender ? structuredClone(current.resultRender.overrides) : null
      const frame = current.resultFrame
      // 一次 commit = 一条历史：model/binding/overrides 与 /2 的显示框原点、原生尺寸、
      // 面板几何在同一事务采纳（ADR 0182 §2）；采纳原语失败时整个 produce 回滚，零写入
      useDocumentStore.getState().commit(msg('chartBind.apply', undefined, 'workspace'), d => {
        const index = d.objects.findIndex(o => o.id === current.panelId)
        const target = d.objects[index]
        if (target?.type === 'panel' && target.fileId === current.file) {
          target.chart = chart
          if (overrides) target.overrides = overrides
        }
        if (frame && index >= 0) applyComputedFrameDraft(d, index, current.panelId, frame, frame.inputIdentity)
      }, { overrides: 'restored' })
    } catch (err) {
      setConfirmError(t('chartBind.confirmFailed', { error: String(err) }))
      return
    }
    current.close()
  }
  const kinds: { value: ChartSeriesForm['kind'], label: string }[] = [
    { value: 'line', label: t('chartBind.kind.line') },
    { value: 'scatter', label: t('chartBind.kind.scatter') },
    { value: 'bar', label: t('chartBind.kind.bar') },
    { value: 'errorbar', label: t('chartBind.kind.errorbar') },
    { value: 'image', label: t('chartBind.kind.image') },
  ]
  const scaleOptions = [
    { value: 'linear' as const, label: t('chartBind.scaleLinear') },
    { value: 'log' as const, label: t('chartBind.scaleLog') },
  ]
  return (
    <Dialog open={s.open} onOpenChange={o => { if (!o) s.close() }}
      title={t('chartBind.title')} size="xl"
      footer={{
        secondary: <Button onClick={s.close}>{t('chartBind.cancel')}</Button>,
        primary: <Button data-chart-bind-action={ready ? 'confirm' : 'preview'}
          disabled={s.busy || !s.scene}
          onClick={ready ? () => { void confirm() } : () => { void s.confirmBinding() }}>
          {s.busy ? t('chartBind.generating') : ready ? t('chartBind.confirm') : t('chartBind.preview')}
        </Button>,
      }}>
      <div data-chart-bind-dialog className="space-y-4">
        <p className="text-sm text-ink-2">{t('chartBind.fileLabel')}: {s.file}</p>
        {!s.scene && s.error && <Button onClick={() => { void s.loadScene() }}>{t('chartBind.retryScene')}</Button>}
        <fieldset disabled={!s.scene} className="space-y-4">
          <fieldset>
            <legend className="type-title mb-2">{t('chartBind.rect')}</legend>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {[t('chartBind.rectX'), t('chartBind.rectY'), t('chartBind.rectWidth'), t('chartBind.rectHeight')].map((label, i) => (
                <label key={i} className="grid gap-1 text-xs text-ink-2">{label}
                  <TextInput aria-label={label} data-chart-rect={i} type="number" value={s.form.rect[i]}
                    onChange={e => { const rect = [...s.form.rect] as [number, number, number, number]; rect[i] = Number(e.target.value); s.patchForm({ rect }) }} />
                </label>
              ))}
            </div>
          </fieldset>
          <label className="grid gap-1 text-xs text-ink-2">{t('chartBind.axesNodes')}
            <TextArea aria-label={t('chartBind.axesNodes')} data-chart-axes-nodes
              value={s.form.axesNodes.join('\n')} rows={2}
              onChange={e => s.patchForm({ axesNodes: e.target.value.split(/\r?\n/).map(v => v.trim()).filter(Boolean) })} />
          </label>
          <p className="text-xs text-ink-3">{t('chartBind.axesNodesHelp')}</p>
          <div className="grid gap-3 sm:grid-cols-2">
            {(['x', 'y'] as const).filter(axis => axis !== 'x' || s.form.xKind === 'numeric').map(axis => (
              <fieldset key={axis} data-chart-axis={axis} className="space-y-2">
                <legend className="type-title">{axis === 'x' ? t('chartBind.axis.x') : t('chartBind.axis.y')}</legend>
                <Select ariaLabel={t('chartBind.scale')} value={s.form[axis].scale} options={scaleOptions}
                  onChange={scale => s.patchForm({ [axis]: { ...s.form[axis], scale } })} />
                <div className="grid grid-cols-2 gap-2">
                  {(['p1_user', 'p1_value', 'p2_user', 'p2_value'] as const).map(key => {
                    const labels = { p1_user: t('chartBind.firstPosition'), p1_value: t('chartBind.firstValue'), p2_user: t('chartBind.secondPosition'), p2_value: t('chartBind.secondValue') }
                    return <label key={key} className="grid gap-1 text-xs text-ink-2">{labels[key]}
                      <TextInput aria-label={labels[key]} data-chart-axis-field={key} type="number" value={s.form[axis][key]}
                        onChange={e => s.patchForm({ [axis]: { ...s.form[axis], [key]: Number(e.target.value) } })} />
                    </label>
                  })}
                </div>
              </fieldset>
            ))}
          </div>
          <Select ariaLabel={t('chartBind.xKind')} value={s.form.xKind}
            options={[{ value: 'numeric', label: t('chartBind.numeric') }, { value: 'categorical', label: t('chartBind.categorical') }]}
            onChange={xKind => s.patchForm({ xKind })} />
          {s.form.xKind === 'categorical' && <TextInput aria-label={t('chartBind.categoriesCol')} placeholder={t('chartBind.categoriesCol')}
            value={s.form.categoriesCol} onChange={e => s.patchForm({ categoriesCol: e.target.value })} />}
          <fieldset className="space-y-3">
            <legend className="type-title mb-2">{t('chartBind.series')}</legend>
            {s.form.series.map((sr, i) => (
              <div key={i} data-chart-series={i} className="space-y-2 border-b border-border pb-3">
                <div className="flex items-center gap-2">
                  <Select ariaLabel={t('chartBind.seriesType')} value={sr.kind} options={kinds}
                    onChange={kind => s.patchSeries(i, { kind })} />
                  <Button disabled={s.form.series.length === 1} data-chart-series-remove={i} onClick={() => s.removeSeries(i)}>{t('chartBind.removeSeries')}</Button>
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  <TextInput aria-label={t('chartBind.seriesNode')} data-chart-series-field="node" list="chart-bind-nodes"
                    value={sr.node} placeholder={t('chartBind.seriesNode')} onChange={e => s.patchSeries(i, { node: e.target.value })} />
                  {sr.kind === 'image' ? <TextInput aria-label={t('chartBind.colorbarNode')} data-chart-series-field="colorbar"
                    list="chart-bind-nodes" value={sr.cbarNode ?? ''} placeholder={t('chartBind.colorbarNode')}
                    onChange={e => s.patchSeries(i, { cbarNode: e.target.value })} /> : <>
                    <TextInput aria-label={t('chartBind.xColumn')} data-chart-series-field="x" value={sr.xCol}
                      placeholder={t('chartBind.xColumn')} onChange={e => s.patchSeries(i, { xCol: e.target.value })} />
                    <TextInput aria-label={t('chartBind.yColumn')} data-chart-series-field="y" value={sr.yCol}
                      placeholder={t('chartBind.yColumn')} onChange={e => s.patchSeries(i, { yCol: e.target.value })} />
                    {(sr.kind === 'bar' || sr.kind === 'errorbar') && <TextInput aria-label={t('chartBind.errorColumn')}
                      value={sr.yerrCol ?? ''} placeholder={t('chartBind.errorColumn')} onChange={e => s.patchSeries(i, { yerrCol: e.target.value })} />}
                  </>}
                </div>
              </div>
            ))}
            <Button data-chart-series-add onClick={s.addSeries}>{t('chartBind.addSeries')}</Button>
          </fieldset>
          <fieldset className="space-y-3">
            <legend className="type-title mb-2">{t('chartBind.legend.title')}</legend>
            {s.form.legend.map((lg, li) => (
              <div key={li} data-chart-legend={li} className="space-y-2 border-b border-border pb-3">
                <div className="flex items-center gap-2">
                  <TextInput aria-label={t('chartBind.legend.region')} data-chart-legend-node
                    list="chart-bind-nodes" value={lg.node} placeholder={t('chartBind.legend.region')}
                    onChange={e => s.patchLegend(li, { node: e.target.value })} />
                  <Button data-chart-legend-remove={li} onClick={() => s.removeLegend(li)}>{t('chartBind.legend.remove')}</Button>
                </div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {[t('chartBind.legend.rectX'), t('chartBind.legend.rectY'), t('chartBind.legend.rectWidth'), t('chartBind.legend.rectHeight')].map((label, i) => (
                    <label key={i} className="grid gap-1 text-xs text-ink-2">{label}
                      <TextInput aria-label={label} data-chart-legend-rect={i} type="number" value={lg.rect[i]}
                        onChange={e => { const rect = [...lg.rect] as [number, number, number, number]; rect[i] = Number(e.target.value); s.patchLegend(li, { rect }) }} />
                    </label>
                  ))}
                </div>
                <p className="text-xs text-ink-3">{t('chartBind.legend.entries')}</p>
                {lg.entries.map((entry, k) => (
                  <div key={k} data-chart-legend-entry={k} className="grid gap-2 sm:grid-cols-[5rem_1fr_1fr_auto]">
                    <TextInput aria-label={t('chartBind.legend.entryIndex')} data-chart-legend-entry-index type="number"
                      value={entry.index} onChange={e => s.patchLegendEntry(li, k, { index: Number(e.target.value) })} />
                    <TextInput aria-label={t('chartBind.legend.entryLabel')} data-chart-legend-entry-label
                      list="chart-bind-legend-texts" value={entry.label} placeholder={t('chartBind.legend.entryLabel')}
                      onChange={e => s.patchLegendEntry(li, k, { label: e.target.value })} />
                    <Select ariaLabel={t('chartBind.legend.follow')} value={entry.seriesNode}
                      options={[{ value: '', label: t('chartBind.legend.custom') },
                        ...s.form.series.filter(sr => sr.node).map(sr => ({ value: sr.node, label: sr.node }))]}
                      onChange={v => s.patchLegendEntry(li, k, { seriesNode: v })} />
                    <Button disabled={lg.entries.length === 1} data-chart-legend-entry-remove={k}
                      onClick={() => s.removeLegendEntry(li, k)}>{t('chartBind.legend.removeEntry')}</Button>
                  </div>
                ))}
                <Button data-chart-legend-entry-add onClick={() => s.addLegendEntry(li)}>{t('chartBind.legend.addEntry')}</Button>
              </div>
            ))}
            {s.form.legend.length === 0 && <Button data-chart-legend-add onClick={s.addLegend}>{t('chartBind.legend.add')}</Button>}
            <datalist id="chart-bind-legend-texts">
              {s.scene?.nodes.filter(n => n.text).map(n => <option key={n.id} value={n.text!}>{n.id}</option>)}
            </datalist>
          </fieldset>
          <datalist id="chart-bind-nodes">{s.scene?.nodes.map(n => <option key={n.id} value={n.id}>{n.text ?? n.tag}</option>)}</datalist>
          <label className="grid gap-1 text-xs text-ink-2">{t('chartBind.data')}
            <TextArea aria-label={t('chartBind.data')} data-chart-data rows={5} maxRows={12}
              className="font-mono" value={s.form.datasetText} placeholder={t('chartBind.dataPlaceholder')}
              onChange={e => s.patchForm({ datasetText: e.target.value })} />
          </label>
          {s.form.series.some(sr => sr.kind === 'image') && <label className="grid gap-1 text-xs text-ink-2">{t('chartBind.gridData')}
            <TextArea aria-label={t('chartBind.gridData')} data-chart-grid rows={4} className="font-mono"
              value={s.form.gridText} onChange={e => s.patchForm({ gridText: e.target.value })} />
          </label>}
        </fieldset>
        {s.draftSource === 'svg' && <Notice data-chart-restored title={t('chartBind.restoredTitle')}>
          {t('chartBind.restoredHelp')}
        </Notice>}
        {s.error && <p className="text-sm text-danger-content">{s.error.code ? `[${s.error.code}] ` : ''}{s.error.message}</p>}
        {confirmError && <p className="text-sm text-danger-content" data-chart-confirm-error>{confirmError}</p>}
        {s.previewSvg && <div data-chart-comparison className="grid gap-3 sm:grid-cols-2">
          <figure className="min-w-0"><figcaption className="text-xs text-ink-2">{t('chartBind.original')}</figcaption>
            <img alt={t('chartBind.original')} src={fileUrl(s.file!)} className="max-h-72 w-full bg-paper" />
          </figure>
          <figure className="min-w-0"><figcaption className="text-xs text-ink-2">{t('chartBind.computed')}</figcaption>
            <img alt={t('chartBind.computed')} src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(s.previewSvg)}`} className="max-h-72 w-full bg-paper" />
          </figure>
        </div>}
      </div>
    </Dialog>
  )
}

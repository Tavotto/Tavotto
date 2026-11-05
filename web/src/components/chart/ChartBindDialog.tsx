// ChartBindDialog：SVG 计算图表的绑定向导界面（ADR 0166；NEXT_PHASE R06）。
// 只编排 chartBindStore 的既有状态机；判据在后端。确认落盘走 documentStore
// 的一次 updateObject 事务（chart 字段写入面板），可撤销。
import { useEffect } from 'react'

import { Dialog } from '@/components/ui/Dialog'
import { Button } from '@/components/ui/Button'
import { TextInput } from '@/components/ui/Input'
import { msg } from '@/i18n/message'
import { t } from '@/i18n'
import { chartBindConfirmReady, useChartBindStore } from '@/store/chartBindStore'
import { updateObject } from '@/store/actions'

export function ChartBindDialog() {
  const open = useChartBindStore((s) => s.open)
  const file = useChartBindStore((s) => s.file)
  const scene = useChartBindStore((s) => s.scene)
  const form = useChartBindStore((s) => s.form)
  const result = useChartBindStore((s) => s.result)
  const previewSvg = useChartBindStore((s) => s.previewSvg)
  const busy = useChartBindStore((s) => s.busy)
  const error = useChartBindStore((s) => s.error)
  const panelId = useChartBindStore((s) => s.panelId)
  const close = useChartBindStore((s) => s.close)
  const patchForm = useChartBindStore((s) => s.patchForm)
  const patchSeries = useChartBindStore((s) => s.patchSeries)
  const runPreview = useChartBindStore((s) => s.runPreview)

  const ready = chartBindConfirmReady(useChartBindStore.getState())

  const confirm = () => {
    if (!panelId || !result || !previewSvg) return
    // 一次 documentStore 事务：chart 字段写入面板（可撤销）
    updateObject(panelId, msg('chartBind.apply', undefined, 'workspace'), (o) => {
      const panel = o as { chart?: unknown }
      panel.chart = { model: result.model, binding: result.binding }
    })
    close()
  }

  // 卸载即作废在途请求（close 递增 openSeq）
  useEffect(() => () => useChartBindStore.getState().close(), [])

  const num = (v: string) => (v === '' ? undefined : Number(v))
  const series0 = form.series[0]

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) close()
      }}
      title={t('workspace:chartBind.title')}
      size="lg"
      footer={{
        secondary: <Button onClick={close}>{t('workspace:chartBind.cancel')}</Button>,
        primary: (
          <Button onClick={confirm} disabled={!ready || busy}>
            {t('workspace:chartBind.confirm')}
          </Button>
        ),
      }}
    >
      <div className="space-y-4 p-4">
        <p className="text-sm">
          {t('workspace:chartBind.fileLabel')}: <code>{file}</code>
          {scene ? ` · ${t('workspace:chartBind.nodesLabel')}: ${scene.nodes.length}` : ''}
        </p>

        {/* 轴区（v1 数值输入；画布框选在后续批次） */}
        <fieldset>
          <legend className="text-sm">{t('workspace:chartBind.rect')}</legend>
          <div className="flex gap-2">
            {(['x', 'y', 'w', 'h'] as const).map((key, i) => (
              <TextInput
                key={key}
                type="number"
                value={String(form.rect[i])}
                onChange={(e) => {
                  const rect = [...form.rect] as [number, number, number, number]
                  rect[i] = Number(e.target.value)
                  patchForm({ rect })
                }}
              />
            ))}
          </div>
        </fieldset>

        {/* 每轴独立校准 + scale */}
        <div className="grid grid-cols-2 gap-3">
          {(['x', 'y'] as const).map((axis) => (
            <fieldset key={axis}>
              <legend className="text-sm">{t(`workspace.chartBind.axis.${axis}`)}</legend>
              <div className="space-y-1">
                <TextInput
                  type="number"
                  value={String(form[axis].p1_value)}
                  onChange={(e) =>
                    patchForm({
                      [axis]: { ...form[axis], p1_value: num(e.target.value) ?? 0 },
                    })
                  }
                />
                <TextInput
                  type="number"
                  value={String(form[axis].p2_value)}
                  onChange={(e) =>
                    patchForm({
                      [axis]: { ...form[axis], p2_value: num(e.target.value) ?? 0 },
                    })
                  }
                />
                <select
                  value={form[axis].scale}
                  onChange={(e) =>
                    patchForm({
                      [axis]: {
                        ...form[axis],
                        scale: e.target.value as 'linear' | 'log',
                      },
                    })
                  }
                >
                  <option value="linear">{t('workspace:chartBind.scaleLinear')}</option>
                  <option value="log">{t('workspace:chartBind.scaleLog')}</option>
                </select>
              </div>
            </fieldset>
          ))}
        </div>

        {/* 系列 */}
        <fieldset>
          <legend className="text-sm">{t('workspace:chartBind.series')}</legend>
          <div className="flex gap-2">
            <TextInput
              value={series0?.node ?? ''}
              onChange={(e) => patchSeries(0, { node: e.target.value })}
              placeholder={t('workspace:chartBind.seriesNode')}
            />
            <TextInput
              value={series0?.xCol ?? ''}
              onChange={(e) => patchSeries(0, { xCol: e.target.value })}
              placeholder="x"
            />
            <TextInput
              value={series0?.yCol ?? ''}
              onChange={(e) => patchSeries(0, { yCol: e.target.value })}
              placeholder="y"
            />
          </div>
        </fieldset>

        {/* 数据 */}
        <fieldset>
          <legend className="text-sm">{t('workspace:chartBind.data')}</legend>
          <textarea
            className="h-32 font-mono text-xs w-full"
            value={form.datasetText}
            onChange={(e) => patchForm({ datasetText: e.target.value })}
            placeholder={t('workspace:chartBind.dataPlaceholder')}
          />
        </fieldset>

        {/* 对照预览 */}
        {previewSvg ? (
          <div
            className="border rounded p-2 overflow-auto max-h-64"
            // 组合 SVG 由后端 svgcheck 校验过；与画布显示同一份
            dangerouslySetInnerHTML={{ __html: previewSvg }}
          />
        ) : null}

        {error ? (
          <p className="text-sm text-red-600">
            {error.code ? `[${error.code}] ` : ''}
            {error.message}
          </p>
        ) : null}

        {!previewSvg && result ? (
          <Button onClick={runPreview} disabled={busy}>
            {t('workspace:chartBind.preview')}
          </Button>
        ) : null}
      </div>
    </Dialog>
  )
}

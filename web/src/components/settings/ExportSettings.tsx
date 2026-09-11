import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { readExportDefaults, writeExportDefaults } from '@/lib/exportDefaults'
import { FORMATS, hasRaster } from '@/lib/exportRequest'
import { Select } from '../ui/Select'
import { Toggle } from '../ui/Toggle'
import { SettingRow, SettingSection, settingRowLabelId } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })
/**
 * 导出对话框自己的文案。**这里刻意读同一批 key**（`export.ppiLabel` /
 * `export.reportToggle` / `export.pdfHint` / `export.pngHint`），不在设置页
 * 另写一份同义词——审计 T43 记的正是那种分叉：设置里叫「Proof 留档」、
 * 导出对话框里叫「样式检查报告」，是同一个东西的两个名字。同一个 key 的两处
 * 渲染没法再各自演进。
 */
const ex = (key: string, values?: Record<string, unknown>) =>
  translate(`export.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 导出默认值。三项可调：默认格式、分辨率、要不要样式检查报告。
 *
 * 顺序跟着依赖关系走（与导出对话框一致）：**先格式后分辨率**。分辨率只对位图
 * 有意义，所以只选了矢量格式时那一行是停用的，并就近说明为什么——ADR 0031 的
 * 「PPI 只在有位图格式时是数字」在界面这一侧的样子。摆一个不影响任何结果的
 * 输入框，等于说了而不做。
 */
export function ExportSettings() {
  useTranslation('dialogs')
  const [defaults, setDefaults] = useState(readExportDefaults)
  const update = (patch: Partial<typeof defaults>) => setDefaults(writeExportDefaults(patch))
  const toggleFormat = (f: string) => {
    const next = defaults.formats.includes(f)
      ? defaults.formats.filter((x) => x !== f)
      : [...defaults.formats, f]
    if (next.length) update({ formats: next })
  }
  // 「有没有位图格式」的判据与导出请求同一处，不在这里另写一遍格式清单
  const raster = hasRaster(defaults.formats)
  return (
    <SettingSection>
      <SettingRow label={st('export.defaultFormats')}>
        <span className="flex items-center gap-3">
          {FORMATS.map((f) => (
            <label key={f} className="flex items-center gap-1.5 text-xs text-ink-2">
              <input
                type="checkbox"
                checked={defaults.formats.includes(f)}
                onChange={() => toggleFormat(f)}
              />
              {/* 只列格式名，「矢量 / 位图」的类型旁注按 2026-09-11 设计包去掉；
                  格式清单仍来自 `FORMATS`（唯一出处），EPS / TIFF 加进来时自动跟上 */}
              {f.toUpperCase()}
            </label>
          ))}
        </span>
      </SettingRow>
      <SettingRow
        label={ex('ppiLabel')}
        status={raster ? undefined : st('export.ppiNotForVector')}
      >
        <Select
          className="w-[120px]"
          ariaLabel={ex('ppiSelectLabel')}
          disabled={!raster}
          value={defaults.dpi}
          onChange={(v) => update({ dpi: v })}
          options={['300', '600', '900', '1200'].map((d) => ({
            value: d,
            label: translate('measure.ppi', { value: d }),
          }))}
        />
      </SettingRow>
      <SettingRow
        label={ex('reportToggle')}
        controlId="setting-export-report"
      >
        <Toggle
          aria-labelledby={settingRowLabelId('setting-export-report')}
          id="setting-export-report"
          checked={defaults.withProof}
          onChange={(v) => update({ withProof: v })}
        />
      </SettingRow>
    </SettingSection>
  )
}

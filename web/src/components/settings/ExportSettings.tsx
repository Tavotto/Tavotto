import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { onExportDefaultsHydrated, readExportDefaults, writeExportDefaults } from '@/lib/exportDefaults'
import { FORMATS, hasRaster } from '@/lib/exportRequest'
import { Select } from '../ui/Select'
import { Checkbox } from '../ui/Checkbox'
import { FieldGroup, FormSection } from '../ui/FormSection'
import { Toggle } from '../ui/Toggle'
import { SettingRow, settingRowLabelId } from './SettingRow'

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
 * **一组**（2026-10-07 设计审计 §9.1）：此前三个分区（格式 / 位图输出 / 检查）各只有一行，三个小标题比内容
 * 还多。现在是「默认导出」一组三行；顺序仍跟着依赖关系走（与导出对话框一致）：**先格式后分辨率**。
 * 分辨率只对位图有意义，所以只选了矢量格式时那一行停用，并在行内就近说明为什么——ADR 0031 的
 * 「PPI 只在有位图格式时是数字」在界面这一侧的样子。
 *
 * 格式一行是 `balanced`（4 : 6）：四个复选框放不进 240 的控件列，标签 12px（此前 11px），从控件列左缘起排。
 * `readExportDefaults` / `writeExportDefaults` 的合同一个字没动。
 */
export function ExportSettings() {
  useTranslation('dialogs')
  const [defaults, setDefaults] = useState(readExportDefaults)
  // 后端那份（#715 PR-B）在设置页开着时才取回来：跟着重读，别让页面显示本机的旧缓存
  useEffect(() => onExportDefaultsHydrated(() => setDefaults(readExportDefaults())), [])
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
    <FormSection title={st('export.sectionDefaults')}>
      <FieldGroup>
        {/* 「默认格式」这四个字已经说清了它管什么，下面不再复述一句（全面打磨 D36） */}
        <SettingRow label={st('export.defaultFormats')} layout="balanced" data-settings-anchor="export.formats">
          <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
            {FORMATS.map((f) => (
              <label key={f} data-export-format={f} className="flex h-7 items-center gap-1.5 text-sm text-ink">
                <Checkbox checked={defaults.formats.includes(f)} onChange={() => toggleFormat(f)} />
                {/* 只列格式名；格式清单来自 `FORMATS`（唯一出处），EPS / TIFF 加进来时自动跟上 */}
                {f.toUpperCase()}
              </label>
            ))}
          </span>
        </SettingRow>
        <SettingRow
          label={ex('ppiLabel')}
          // 停用的原因就在行内（宪法第五节：禁用项收不到指针事件，气泡不能是唯一的说明）
          status={raster ? undefined : st('export.ppiNotForVector')}
          data-settings-anchor="export.ppi"
          data-ppi-disabled={raster ? undefined : ''}
        >
          <Select
            className="w-full"
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
        <SettingRow label={ex('reportToggle')} controlId="setting-export-report" data-settings-anchor="export.report">
          <Toggle
            aria-labelledby={settingRowLabelId('setting-export-report')}
            id="setting-export-report"
            checked={defaults.withProof}
            onChange={(v) => update({ withProof: v })}
          />
        </SettingRow>
      </FieldGroup>
    </FormSection>
  )
}

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { readExportDefaults, writeExportDefaults } from '@/lib/exportDefaults'
import { Select } from '../ui/Select'
import { Toggle } from '../ui/Toggle'
import { SettingRow, SettingSection } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 导出默认值。可调的三项保留；「这里只是初始值」与 proof 是什么进问号。 */
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
  return (
    <SettingSection>
      <SettingRow label={st('export.defaultDpi')} help={st('export.dpiHint')}>
        <Select
          className="w-[120px]"
          ariaLabel={st('export.defaultDpi')}
          value={defaults.dpi}
          onChange={(v) => update({ dpi: v })}
          options={['300', '600', '900', '1200'].map((d) => ({
            value: d,
            label: translate('measure.dpi', { value: d }),
          }))}
        />
      </SettingRow>
      <SettingRow label={st('export.defaultFormats')} help={st('export.formatsHint')}>
        <span className="flex items-center gap-3">
          {['pdf', 'png'].map((f) => (
            <label key={f} className="flex items-center gap-1.5 text-xs text-ink-2">
              <input
                type="checkbox"
                checked={defaults.formats.includes(f)}
                onChange={() => toggleFormat(f)}
              />
              {f.toUpperCase()}
            </label>
          ))}
        </span>
      </SettingRow>
      <SettingRow
        label={st('export.proof')}
        help={st('export.proofHelp')}
        status={defaults.withProof ? st('export.proofHint') : undefined}
      >
        <Toggle
          checked={defaults.withProof}
          onChange={(v) => update({ withProof: v })}
          aria-label={st('export.proofAria')}
        />
      </SettingRow>
    </SettingSection>
  )
}

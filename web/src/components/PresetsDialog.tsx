import { useTranslation } from 'react-i18next'
import {
  insertPreset,
  insertSymbol,
  PRESET_IDS,
  presetHint,
  presetLabel,
  SYMBOLS,
} from '@/lib/presets'
import { cn } from '@/lib/utils'
import { PresetPreview } from './PresetPreview'
import { Dialog } from './ui/Dialog'

/**
 * 科研预设：组合插入既有对象（箭头/形状/文字成组）+ 常用符号。
 * 点击即插入到视口中心并关闭；全部可 ⌘Z 撤销。
 *
 * 九种预设各有一格**真实结构预览**（`PresetPreview`，与插入同一份定义）+
 * 一个短名称——不插入也分得清尺寸线与比例尺（审计 T30）。
 */
export function PresetsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation('dialogs')
  if (!open) return null
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()} title={t('presets.title')} size="md">
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-3 gap-1.5" role="list" aria-label={t('presets.title')}>
          {PRESET_IDS.map((id) => (
            <button
              key={id}
              type="button"
              role="listitem"
              data-preset={id}
              title={presetHint(id)}
              aria-label={presetLabel(id)}
              onClick={() => {
                insertPreset(id)
                onClose()
              }}
              className={cn(
                'flex flex-col items-center gap-1 rounded-sm border border-border bg-surface px-1 pb-1.5 pt-1',
                'text-xs text-ink outline-none transition-colors',
                'hover:border-border-strong hover:bg-surface-2 focus-visible:focus-ring',
              )}
            >
              <PresetPreview id={id} />
              <span className="max-w-full truncate">{presetLabel(id)}</span>
            </button>
          ))}
        </div>
        <div>
          <h3 className="mb-1 text-xs font-medium text-ink-2">{t('presets.symbolsHeading')}</h3>
          <div className="grid grid-cols-8 gap-0.5">
            {SYMBOLS.map((s) => (
              <button
                key={s}
                onClick={() => {
                  insertSymbol(s)
                  onClose()
                }}
                aria-label={t('presets.insertSymbolAria', { symbol: s })}
                className="flex h-8 items-center justify-center rounded-sm text-sm text-ink outline-none hover:bg-ink/[.055] focus-visible:focus-ring"
                style={{ fontFamily: 'var(--font-doc)' }}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      </div>
    </Dialog>
  )
}

import { useTranslation } from 'react-i18next'
import {
  insertPreset,
  insertSymbol,
  PRESET_IDS,
  presetHint,
  presetLabel,
  SYMBOLS,
} from '@/lib/presets'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'

/**
 * 科研预设：组合插入既有对象（箭头/形状/文字成组）+ 常用符号。
 * 点击即插入到视口中心并关闭；全部可 ⌘Z 撤销。
 */
export function PresetsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation('dialogs')
  if (!open) return null
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()} title={t('presets.title')} size="md">
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-1.5">
          {PRESET_IDS.map((id) => (
            <Button
              key={id}
              variant="outline"
              size="md"
              className="justify-start"
              title={presetHint(id)}
              onClick={() => {
                insertPreset(id)
                onClose()
              }}
            >
              {presetLabel(id)}
            </Button>
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

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
        {/* 列表语义走 ul/li，**不往按钮身上写 `role`**：显式 role 会替换掉
            按钮的原生语义，读屏用户遇到的就成了一个可聚焦的列表项，而不是
            一个能激活的控件。`role="list"` 是显式写的——Tailwind 的
            `list-style: none` 会让 Safari/VoiceOver 丢掉 ul 的列表语义 */}
        <ul className="grid grid-cols-3 gap-1.5" role="list" aria-label={t('presets.title')}>
          {PRESET_IDS.map((id) => (
            <li key={id} role="listitem" className="flex">
              <button
                type="button"
                data-preset={id}
                title={presetHint(id)}
                aria-label={presetLabel(id)}
                onClick={() => {
                  insertPreset(id)
                  onClose()
                }}
                /* 格子不画框（全面打磨 D33，§8）：九张 125×87 的带框卡摆在一个 420 宽的
                   对话框里，第一眼看到的是九个方框而不是九张示意图。交互语言与
                   `OptionGrid` 的样张格同一套——常态无底、hover 浮 surface-hover */
                className={cn(
                  'flex w-full flex-col items-center gap-1 rounded-sm px-1 pb-1.5 pt-1',
                  'text-xs text-ink outline-none transition-colors',
                  'hover:bg-surface-hover focus-visible:focus-ring',
                )}
              >
                <PresetPreview id={id} />
                <span className="max-w-full truncate">{presetLabel(id)}</span>
              </button>
            </li>
          ))}
        </ul>
        <div>
          {/* 小标走 type-section（全面打磨 D33）：11/500/ink-2 是自造的第七个文字角色 */}
          <h3 className="type-section mb-1">{t('presets.symbolsHeading')}</h3>
          {/* 格 32×32（与 `OptionGrid` 的样张格同一档）：此前是八等分的 47×32，
              格子被拉宽而字号没跟上，「²」「³」「⁻¹」在 12px 里几乎看不见 */}
          <div className="flex flex-wrap gap-0.5">
            {SYMBOLS.map((s) => (
              <button
                key={s}
                onClick={() => {
                  insertSymbol(s)
                  onClose()
                }}
                aria-label={t('presets.insertSymbolAria', { symbol: s })}
                className="flex h-8 w-8 items-center justify-center rounded-sm text-ink outline-none hover:bg-surface-hover focus-visible:focus-ring"
                /* 这里的字是**文档字形样张**，不是界面文字：字体与字号都跟着文档走
                   （与样张格里的线型 / 标记预览同理），所以不走 UI 的五档字阶 */
                style={{ fontFamily: 'var(--font-doc)', fontSize: 17 }}
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

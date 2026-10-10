import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  insertPreset,
  insertSymbol,
  PRESET_IDS,
  presetLabel,
  SYMBOLS,
} from '@/lib/presets'
import { cn } from '@/lib/utils'
import { PresetPreview } from './PresetPreview'
import { Card } from './ui/Card'
import { Dialog } from './ui/Dialog'

/** 预设格每行几张：方向键的上下按它跨行 */
const COLS = 3

/**
 * 科研预设：组合插入既有对象（箭头/形状/文字成组）+ 常用符号。
 * 点击即插入到视口中心并关闭；全部可 ⌘Z 撤销。
 *
 * 九种预设各有一格**真实结构预览**（`PresetPreview`，与插入同一份定义）+
 * 一个短名称——不插入也分得清尺寸线与比例尺（审计 T30）。
 *
 * 2026-10-07 设计审计 §10.2：格子是 `Card subtle`（浅底、hover 画一圈轮廓，不是九个方框），
 * 九格是一个**漫游焦点网格**：Tab 只停一次，方向键在格间走（上下跨一行），Home / End 到头尾。
 */
export function PresetsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation('dialogs')
  const [active, setActive] = useState(0)
  const cells = useRef<(HTMLButtonElement | null)[]>([])
  const move = (to: number) => {
    const n = PRESET_IDS.length
    const next = Math.max(0, Math.min(n - 1, to))
    setActive(next)
    cells.current[next]?.focus()
  }
  const onGridKey = (e: React.KeyboardEvent, i: number) => {
    const step: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: COLS, ArrowUp: -COLS }
    if (e.key in step) {
      e.preventDefault()
      move(i + step[e.key])
    } else if (e.key === 'Home') {
      e.preventDefault()
      move(0)
    } else if (e.key === 'End') {
      e.preventDefault()
      move(PRESET_IDS.length - 1)
    }
  }
  return (
    <Dialog
      // 常驻挂载（Dialog 的常驻写法）：有退场动画
      open={open}
      onOpenChange={(v) => !v && onClose()}
      title={t('presets.title')}
      size="md"
      anchor="presets"
    >
      <div className="flex flex-col gap-4">
        {/* 列表语义走 ul/li，**不往按钮身上写 `role`**：显式 role 会替换掉
            按钮的原生语义，读屏用户遇到的就成了一个可聚焦的列表项，而不是
            一个能激活的控件。`role="list"` 是显式写的——Tailwind 的
            `list-style: none` 会让 Safari/VoiceOver 丢掉 ul 的列表语义 */}
        <ul className={cn('grid gap-2', 'grid-cols-3')} role="list" aria-label={t('presets.title')}>
          {PRESET_IDS.map((id, i) => (
            <li key={id} role="listitem" className="flex">
              <Card appearance="subtle" padding="none" interactive className="flex w-full">
                <button
                  ref={(el) => {
                    cells.current[i] = el
                  }}
                  type="button"
                  data-preset={id}
                  aria-label={presetLabel(id)}
                  tabIndex={i === active ? 0 : -1}
                  onFocus={() => setActive(i)}
                  onKeyDown={(e) => onGridKey(e, i)}
                  onClick={() => {
                    insertPreset(id)
                    onClose()
                  }}
                  className="flex w-full flex-col items-center gap-1 rounded-lg px-1 pb-2 pt-1.5 text-sm text-ink outline-none"
                >
                  <PresetPreview id={id} />
                  <span className="max-w-full truncate">{presetLabel(id)}</span>
                </button>
              </Card>
            </li>
          ))}
        </ul>
        <div>
          <h3 className="type-section mb-1.5">{t('presets.symbolsHeading')}</h3>
          {/* 格 32×32（与 `OptionGrid` 的样张格同一档）：此前是八等分的 47×32，
              格子被拉宽而字号没跟上，「²」「³」「⁻¹」在 12px 里几乎看不见 */}
          <div className="flex flex-wrap gap-0.5">
            {SYMBOLS.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => {
                  insertSymbol(s)
                  onClose()
                }}
                aria-label={t('presets.insertSymbolAria', { symbol: s })}
                className="flex h-8 w-8 items-center justify-center rounded-md text-ink outline-none hover:bg-surface-hover focus-visible:focus-ring"
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

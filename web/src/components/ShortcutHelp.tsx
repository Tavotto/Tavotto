import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { ALT, MOD } from '@/lib/utils'
import { useUiStore } from '@/store/uiStore'
import { Dialog } from './ui/Dialog'

/**
 * 快捷键帮助（按 ? 或从 ⌘K 打开）。分组与实际实现一一对应，不列不存在的键。
 *
 * 表里只有**键位**（那是事实，不翻译）与 i18n key；说明文字走
 * `shortcuts:key.*`。有几行的「键位」本身含自然语言（方向键 / Space+拖动），
 * 那几条另走 `shortcuts:combo.*`。
 */
const sc = (key: string, values?: Record<string, unknown>) =>
  translate(key, { ns: 'shortcuts', ...(values ?? {}) })

interface Row {
  /** 直接显示的键位；与 comboKey 二选一 */
  keys?: string
  /** 键位本身要翻译时用的 key（在 shortcuts:combo.* 下） */
  comboKey?: string
  comboValues?: Record<string, unknown>
  /** 说明文字的 key（在 shortcuts:key.* 下） */
  desc: string
}

const GROUPS: { id: string; rows: Row[] }[] = [
  {
    id: 'general',
    rows: [
      { keys: `${MOD}K`, desc: 'palette' },
      { keys: `${MOD}Z / ⇧${MOD}Z`, desc: 'undoRedo' },
      { keys: `${MOD}S`, desc: 'saveDocument' },
      { keys: `⇧${MOD}S`, desc: 'saveLayout' },
      { keys: `${MOD}E`, desc: 'export' },
      { keys: '?', desc: 'help' },
    ],
  },
  {
    id: 'editing',
    rows: [
      { keys: `${MOD}A`, desc: 'selectAll' },
      { keys: `${MOD}C / ${MOD}V`, desc: 'copyPaste' },
      { keys: `${MOD}D`, desc: 'duplicate' },
      { keys: 'Delete', desc: 'delete' },
      { keys: 'Enter', desc: 'enter' },
      { keys: 'Esc', desc: 'escape' },
      { keys: `${MOD}↑ / ${MOD}↓`, desc: 'script' },
      { comboKey: 'arrowKeys', desc: 'nudge' },
      // 多选与右键：真实存在的两条手势（ObjectView 的 shift 加选、QuickEdit 菜单）
      { comboKey: 'shiftClick', desc: 'multiSelect' },
      { comboKey: 'rightClick', desc: 'quickEdit' },
    ],
  },
  {
    id: 'tutorial',
    rows: [{ keys: 'Esc', desc: 'tutorialPause' }],
  },
  {
    id: 'arrange',
    rows: [
      { keys: `${MOD}] / ${MOD}[`, desc: 'zMove' },
      { keys: `⇧${MOD}] / ⇧${MOD}[`, desc: 'zEnds' },
    ],
  },
  {
    id: 'view',
    rows: [
      { keys: `${MOD}+ / ${MOD}−`, desc: 'zoom' },
      { keys: `${MOD}0 / ${MOD}1`, desc: 'zoomPresets' },
      { comboKey: 'wheelZoom', comboValues: { mod: MOD }, desc: 'wheelZoom' },
      { comboKey: 'spaceDrag', desc: 'pan' },
    ],
  },
  {
    id: 'tools',
    rows: [
      { comboKey: 'tools', desc: 'tools' },
      { comboKey: 'altDrag', comboValues: { alt: ALT }, desc: 'freeResize' },
      { comboKey: 'newline', comboValues: { alt: ALT, mod: MOD }, desc: 'newline' },
    ],
  },
]

const keyText = (r: Row) => r.keys ?? sc(`combo.${r.comboKey}`, r.comboValues)

export function ShortcutHelp() {
  useTranslation('shortcuts')
  const open = useUiStore((s) => s.shortcutHelpOpen)
  const setOpen = useUiStore((s) => s.setShortcutHelpOpen)
  return (
    <Dialog open={open} onOpenChange={setOpen} title={sc('title')} size="md">
      <div className="flex flex-col gap-3">
        {GROUPS.map((g) => (
          <div key={g.id}>
            <h3 className="mb-1 text-xs font-medium uppercase tracking-[.06em] text-ink-3">
              {sc(`group.${g.id}`)}
            </h3>
            <ul className="flex flex-col">
              {g.rows.map((r) => (
                <li key={r.desc} className="flex h-6 items-center gap-3">
                  <span className="w-40 shrink-0 font-mono text-xs text-ink">{keyText(r)}</span>
                  <span className="min-w-0 flex-1 truncate text-xs text-ink-2">
                    {sc(`key.${r.desc}`)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Dialog>
  )
}

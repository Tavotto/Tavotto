import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { ALT, MOD } from '@/lib/utils'
import { keysOf, type KeyId } from '@/lib/keymap'
import { useUiStore } from '@/store/uiStore'
import { Dialog } from './ui/Dialog'
import { KeyCaps } from './ui/Kbd'
import { SearchInput } from './ui/SearchInput'

/**
 * 快捷键帮助（按 ? 或从 ⌘K 打开）。分组与实际实现一一对应，不列不存在的键。
 *
 * 表里只有**键位**（那是事实，不翻译）与 i18n key；说明文字走
 * `shortcuts:key.*`。有几行的「键位」本身含自然语言（方向键 / Space+拖动），
 * 那几条另走 `shortcuts:combo.*`。
 *
 * 分组按用户的任务分（文件 / 选择 / 编辑 / 排列 / 视图 / 工具，共 6 张卡；原先单列的
 * 「教程」只有一行 Esc，并进「工具」），说明整句显示、可换行、可搜索——此前一列 40px 宽的键位加一行只能截断的
 * 说明，一半的句子都读不到结尾（审计 T50）。
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

/**
 * 表里的键位**不在这里写**：`keys` 一律由 `lib/keymap` 的 `keysOf()` 取（2026-10-07 设计审计 §10.1 P2，
 * 快捷键单一来源）；只有键位本身是自然语言的那几行（方向键 / Space+拖动 / 右键）走 `comboKey`。
 */
const row = (desc: string, ...ids: KeyId[]): Row => ({ keys: keysOf(...ids), desc })

export const GROUPS: { id: string; rows: Row[] }[] = [
  {
    id: 'file',
    rows: [
      row('saveDocument', 'save'),
      row('saveLayout', 'saveAs'),
      row('timeline', 'timeline'),
      row('saveNamed', 'saveNamed'),
      row('export', 'export'),
      row('palette', 'palette'),
      row('help', 'help'),
    ],
  },
  {
    id: 'selection',
    rows: [
      row('selectAll', 'selectAll'),
      // 多选与右键：真实存在的两条手势（ObjectView 的 shift 加选、QuickEdit 菜单）
      { comboKey: 'shiftClick', desc: 'multiSelect' },
      // 重叠元素的轮换（issue #216）：⌥ 点击画布；键盘走 ⌘K 里的同名命令
      { comboKey: 'altClick', comboValues: { alt: ALT }, desc: 'cycleOverlap' },
      row('enter', 'enter'),
      row('escape', 'escape'),
    ],
  },
  {
    id: 'editing',
    rows: [
      row('undoRedo', 'undo', 'redo'),
      row('copyPaste', 'copy', 'paste'),
      row('duplicate', 'duplicate'),
      row('delete', 'delete'),
      { comboKey: 'arrowKeys', comboValues: { alt: ALT }, desc: 'nudge' },
      { comboKey: 'rightClick', desc: 'quickEdit' },
      { keys: `${MOD}↑ / ${MOD}↓`, desc: 'script' },
      { comboKey: 'newline', comboValues: { alt: ALT, mod: MOD }, desc: 'newline' },
    ],
  },
  {
    id: 'arrange',
    rows: [row('zMove', 'zUp', 'zDown'), row('zEnds', 'zTop', 'zBottom')],
  },
  {
    id: 'view',
    rows: [
      row('zoom', 'zoomIn', 'zoomOut'),
      row('zoomPresets', 'zoomActual', 'zoomFit'),
      row('zoomSelection', 'zoomSelection'),
      { comboKey: 'wheelZoom', comboValues: { mod: MOD }, desc: 'wheelZoom' },
      { comboKey: 'spaceDrag', desc: 'pan' },
    ],
  },
  {
    id: 'tools',
    rows: [
      row('tools', 'toolSelect', 'toolText', 'toolArrow', 'toolRect', 'toolEllipse', 'toolLine'),
      { comboKey: 'altDrag', comboValues: { alt: ALT }, desc: 'freeResize' },
      row('tabs', 'tabRename', 'tabClose', 'tabReorder'),
      row('tutorialPause', 'escape'),
    ],
  },
]

const keyText = (r: Row) => r.keys ?? sc(`combo.${r.comboKey}`, r.comboValues)

/** 搜索命中：键位或说明含查询串（按当前语言的成文比） */
export function filterGroups(groups: typeof GROUPS, query: string) {
  const q = query.trim().toLowerCase()
  if (!q) return groups
  return groups
    .map((g) => ({
      ...g,
      rows: g.rows.filter(
        (r) =>
          keyText(r).toLowerCase().includes(q) || sc(`key.${r.desc}`).toLowerCase().includes(q),
      ),
    }))
    .filter((g) => g.rows.length > 0)
}

export function ShortcutHelp() {
  useTranslation('shortcuts')
  const open = useUiStore((s) => s.shortcutHelpOpen)
  const setOpen = useUiStore((s) => s.setShortcutHelpOpen)
  const [query, setQuery] = useState('')
  const shown = useMemo(() => filterGroups(GROUPS, query), [query])
  // 关掉就清查询：**盯 `open` 而不是 `onOpenChange`**——Esc / 点遮罩会走那个
  // 回调，而 `?` 的开关、命令面板、其它 store 调用方直接改 `shortcutHelpOpen`，
  // 一个字都不经过它。挂在回调上的话「下次打开还停在上次的过滤结果」只在
  // 某几条关闭路径上不发生。
  useEffect(() => {
    if (!open) setQuery('')
  }, [open])
  return (
    <Dialog open={open} onOpenChange={setOpen} title={sc('title')} width={960} anchor="shortcut-help">
      <div className="flex flex-col gap-4">
        {/* 搜索框只有一种（`SearchInput`，宪法第五节）：28 高、fieldBox 的边、16px 放大镜、
            有内容才出清除钮。此前这里自己定了高 36 / 圆角 10 / surface-2 底 / 13 号字，
            四处都在原语之外（2026-09-15 打磨 K4） */}
        <SearchInput
          data-shortcut-search
          value={query}
          onValueChange={setQuery}
          placeholder={sc('search')}
          aria-label={sc('searchAria')}
        />
        <div className="flex max-h-[32rem] min-h-0 flex-col overflow-y-auto overscroll-contain">
          {shown.length === 0 && (
            <p className="type-body flex min-h-40 items-center justify-center py-7 text-center text-ink-3">
              {sc('noMatch')}
            </p>
          )}
          {/* 6 张卡：宽屏三列、窄窗两列 / 一列；卡内一行 = 说明 + 键帽（键帽多到放不下时换到说明下一行） */}
          <div className="columns-1 gap-3 sm:columns-2 lg:columns-3">
            {shown.map((g) => (
              <section
                key={g.id}
                data-shortcut-group={g.id}
                className="mb-3 break-inside-avoid rounded-md bg-surface-2 px-3.5 py-3"
              >
                <h3 className="type-section mb-1.5">{sc(`group.${g.id}`)}</h3>
                <ul className="flex flex-col">
                  {g.rows.map((r) => (
                    <li
                      key={r.desc}
                      data-shortcut-row={r.desc}
                      className="flex min-h-8 flex-wrap items-center justify-between gap-x-3 gap-y-1 py-1"
                    >
                      {/* DOM 里键位在前（测试与读屏按「键 → 说明」读），视觉上靠右 */}
                      <span data-shortcut-keys className="order-last shrink-0">
                        <KeyCaps keys={keyText(r)} />
                      </span>
                      {/* 整句显示、可换行：说明是要读的字，截断掉的那半正是它的意思 */}
                      <span
                        data-shortcut-desc
                        className="type-body min-w-[9rem] flex-1 whitespace-normal break-words text-ink-2"
                      >
                        {sc(`key.${r.desc}`)}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        </div>
      </div>
    </Dialog>
  )
}

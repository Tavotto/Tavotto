import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { ALT, MOD } from '@/lib/utils'
import { useUiStore } from '@/store/uiStore'
import { Dialog } from './ui/Dialog'
import { Kbd } from './ui/Kbd'
import { SearchInput } from './ui/SearchInput'

/**
 * 快捷键帮助（按 ? 或从 ⌘K 打开）。分组与实际实现一一对应，不列不存在的键。
 *
 * 表里只有**键位**（那是事实，不翻译）与 i18n key；说明文字走
 * `shortcuts:key.*`。有几行的「键位」本身含自然语言（方向键 / Space+拖动），
 * 那几条另走 `shortcuts:combo.*`。
 *
 * 分组按用户的任务分（文件 / 选择 / 编辑 / 排列 / 视图 / 工具 / 教程），
 * 说明整句显示、可换行、可搜索——此前一列 40px 宽的键位加一行只能截断的
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

export const GROUPS: { id: string; rows: Row[] }[] = [
  {
    id: 'file',
    rows: [
      { keys: `${MOD}S`, desc: 'saveDocument' },
      { keys: `⇧${MOD}S`, desc: 'saveLayout' },
      { keys: `${MOD}E`, desc: 'export' },
      { keys: `${MOD}K`, desc: 'palette' },
      { keys: '?', desc: 'help' },
    ],
  },
  {
    id: 'selection',
    rows: [
      { keys: `${MOD}A`, desc: 'selectAll' },
      // 多选与右键：真实存在的两条手势（ObjectView 的 shift 加选、QuickEdit 菜单）
      { comboKey: 'shiftClick', desc: 'multiSelect' },
      // 重叠元素的轮换（issue #216）：⌥ 点击画布；键盘走 ⌘K 里的同名命令
      { comboKey: 'altClick', comboValues: { alt: ALT }, desc: 'cycleOverlap' },
      { keys: 'Enter', desc: 'enter' },
      { keys: 'Esc', desc: 'escape' },
    ],
  },
  {
    id: 'editing',
    rows: [
      { keys: `${MOD}Z / ⇧${MOD}Z`, desc: 'undoRedo' },
      { keys: `${MOD}C / ${MOD}V`, desc: 'copyPaste' },
      { keys: `${MOD}D`, desc: 'duplicate' },
      { keys: 'Delete', desc: 'delete' },
      { comboKey: 'arrowKeys', comboValues: { alt: ALT }, desc: 'nudge' },
      { comboKey: 'rightClick', desc: 'quickEdit' },
      { keys: `${MOD}↑ / ${MOD}↓`, desc: 'script' },
      { comboKey: 'newline', comboValues: { alt: ALT, mod: MOD }, desc: 'newline' },
    ],
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
    ],
  },
  {
    id: 'tutorial',
    rows: [{ keys: 'Esc', desc: 'tutorialPause' }],
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
    <Dialog open={open} onOpenChange={setOpen} title={sc('title')} size="md">
      <div className="flex flex-col gap-4">
        {/* 搜索框只有一种（`SearchInput`，宪法第五节）：28 高、fieldBox 的边、16px 放大镜、
            有内容才出清除钮。此前这里自己定了高 36 / 圆角 10 / surface-2 底 / 13 号字，
            四处都在原语之外（2026-09-15 打磨 K4） */}
        <SearchInput
          value={query}
          onValueChange={setQuery}
          placeholder={sc('search')}
          aria-label={sc('searchAria')}
        />
        <div className="flex max-h-[22rem] min-h-0 flex-col gap-5 overflow-y-auto overscroll-contain">
          {shown.length === 0 && (
            <p className="type-body flex min-h-40 items-center justify-center py-7 text-center text-ink-3">
              {sc('noMatch')}
            </p>
          )}
          {shown.map((g) => (
            <div key={g.id} data-shortcut-group={g.id}>
              {/* 组头 = `type-section`（12/500/ink，宪法第六节）：此前是 11/500/ink-3 外加
                  一根延伸到行尾的线，是这一页自造的第三种分区头（2026-09-15 打磨 K5） */}
              <h3 className="type-section mb-2">
                {sc(`group.${g.id}`)}
              </h3>
              <ul className="flex flex-col">
                {g.rows.map((r) => (
                  <li
                    key={r.desc}
                    data-shortcut-row
                    className="flex min-h-8 items-center justify-between gap-5 py-1.5"
                  >
                    {/* DOM 里键位在前（测试与读屏按「键 → 说明」读），视觉上靠右 */}
                    <span className="order-last shrink-0">
                      <Kbd size="md">{keyText(r)}</Kbd>
                    </span>
                    {/* 整句显示、可换行：说明是要读的字，截断掉的那半正是它的意思 */}
                    <span className="type-body min-w-0 flex-1 whitespace-normal break-words text-ink-2">
                      {sc(`key.${r.desc}`)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
    </Dialog>
  )
}

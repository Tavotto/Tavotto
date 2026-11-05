import type { ReactNode } from 'react'
import { currentLocale, t } from '@/i18n'
import type { SelectOption } from '@/components/ui/Select'
import { fontFamilyOptionLabel } from '../roles/registry'
import { fontStackOf } from './fontStack'

const isGeneric = (v: string) => ['serif', 'sans-serif', 'monospace'].includes(v)

const NO_LABELS = {}
const ROWS = new WeakMap<readonly string[], WeakMap<object, { lang: string; rows: Map<string, SelectOption> }>>()
function rowCache(options: readonly string[], labels: Readonly<Record<string, string>> | undefined) {
  const lang = currentLocale()
  let byLabels = ROWS.get(options)
  if (!byLabels) ROWS.set(options, (byLabels = new WeakMap()))
  const key = labels ?? NO_LABELS
  let hit = byLabels.get(key)
  if (!hit || hit.lang !== lang) byLabels.set(key, (hit = { lang, rows: new Map() }))
  return hit.rows
}

/**
 * 字体下拉（图内文字）的选项与收起态：用户只挑具体字体，不出现「衬线 / 无衬线 / 等宽」。
 *
 * 脚本写的是通用族（`serif`）时，引擎发来的 `option_faces` 说明它此刻由哪张脸画：
 * - 收起态与列表里的选中项都是那张脸（「Times New Roman」）；它不在选项表里（没装、退到
 *   「DejaVu Serif」之类）就补成一行，让它能显示为选中；悬停说明它来自脚本的 `serif` 设置；
 * - 选**正是这张脸**的那一行是空操作（`onChange` 不转发：脚本保持 `serif`，没有编辑）；选别的具体
 *   字体写的是那个显式名字（跟随 rcParams 与显式字体是两种不同的选择）。
 *
 * 老引擎不发 `option_faces`、或这个通用族解析不出来时原样平铺（通用族留在列表里，值才有对应项）。
 * 只管显示：选项的 `value` 永远是引擎的原值。
 */
export function fontFamilySelectModel({
  value,
  options,
  labels,
  faces,
  extra,
  labelOf,
  onChange,
}: {
  /** 当前值；多选混合时传空串 */
  value: string
  options: readonly string[]
  labels: Readonly<Record<string, string>> | undefined
  faces: Readonly<Record<string, string>> | undefined
  /** 每个具体字体行后面的附加物（「未安装」标记） */
  extra?: (o: string) => ReactNode
  /** 具体字体行的显示名；默认 `fontFamilyOptionLabel`（调用方已有自己的口径时传入） */
  labelOf?: (o: string) => string
  onChange: (v: string) => void
}): { options: SelectOption[]; value: string; onChange: (v: string) => void; title?: string } {
  const named = labelOf ?? ((o: string) => fontFamilyOptionLabel(o, labels, options))
  const build = (o: string): SelectOption => ({
    value: o,
    // 中英文名都搜得到，不管界面是哪种语言
    searchText: [o, labels?.[o], named(o)].filter(Boolean).join(' '),
    label: (
      <span style={{ fontFamily: fontStackOf(o) }}>
        {named(o)}
        {extra?.(o)}
      </span>
    ),
  })
  // 行按 (options 引用, labels 引用, 语言) 缓存：本机有几百个族，选中文字后每次重渲染都重造几百行 JSX 与显示名，
  // 符号插入因此慢了一倍。带 `extra` / `labelOf`（调用方每次新造的函数）的不缓存。
  const rows = !extra && !labelOf ? rowCache(options, labels) : undefined
  const row = (o: string): SelectOption => {
    const hit = rows?.get(o)
    if (hit) return hit
    const made = build(o)
    rows?.set(o, made)
    return made
  }
  const face = isGeneric(value) ? faces?.[value] : undefined
  const resolved = (g: string) => !!faces?.[g]
  // 通用族行只在「它解析得出脸」时拿掉；解析不出来的（老引擎 / 没有这一族）留着，当前值才有对应项
  const kept = options.filter((o) => !isGeneric(o) || !resolved(o))
  const list = face && !kept.includes(face) ? [face, ...kept] : kept
  if (!face) return { options: list.map(row), value, onChange }
  return {
    options: list.map(row),
    value: face,
    // 选中的就是它画出来的那张脸：不是编辑，脚本里的 serif 原样保留
    onChange: (v) => {
      if (v !== face) onChange(v)
    },
    title: t('textControls.fontFromGeneric', { ns: 'inspector', generic: value }),
  }
}

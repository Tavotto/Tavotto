import type { ReactNode } from 'react'
import { t } from '@/i18n'
import type { SelectOption } from '@/components/ui/Select'
import { fontFamilyOptionLabel, optionLabel } from '../roles/registry'
import { fontStackOf } from './fontStack'

const GENERIC = ['serif', 'sans-serif', 'monospace'] as const
const isGeneric = (v: string) => (GENERIC as readonly string[]).includes(v)

/**
 * 字体下拉（图内文字）的选项与收起态——Word「主题字体」的做法：
 *
 * - 收起的字段显示**此刻真正画出来的那张脸**（脚本写 `serif`、画的是 Times New Roman → 显示
 *   「Times New Roman」），悬停说明它来自脚本的 `serif` 设置；
 * - 列表里具体字体照旧，通用族挪到末尾一个小分组「跟随全局字体族」，每行「衬线 · Times New Roman」
 *   （三行都有，解析口径与引擎渲染同一条：`option_faces`）。值是通用族时勾选落在分组里那一行。
 *
 * 只管显示：选项的 `value` 永远是引擎的原值，选了通用族行写回的仍是 `serif`，选具体字体行
 * 写的是那个字体名（这是两种不同的选择：显式字体 vs 跟随 rcParams）。老引擎不发 `option_faces`
 * （或字段里没有通用族）时原样平铺，行为不变。
 */
export function fontFamilySelectModel({
  value,
  options,
  labels,
  faces,
  extra,
  labelOf,
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
}): { options: SelectOption[]; triggerLabel?: ReactNode; title?: string } {
  const named = labelOf ?? ((o: string) => fontFamilyOptionLabel(o, labels, options))
  const row = (o: string, ...kids: ReactNode[]): SelectOption => ({
    value: o,
    label: <span style={{ fontFamily: fontStackOf(o) }}>{kids}</span>,
  })
  const hasGeneric = options.some(isGeneric)
  const hasFaces = !!faces && GENERIC.some((g) => faces[g])
  if (!hasGeneric || !hasFaces) {
    return {
      options: options.map((o) =>
        row(o, named(o), extra?.(o)),
      ),
    }
  }
  const group = t('textControls.fontGenericGroup', { ns: 'inspector' })
  const concrete = options.filter((o) => !isGeneric(o))
  const generics = options.filter(isGeneric)
  const out: SelectOption[] = [
    ...concrete.map((o) =>
      row(o, named(o), extra?.(o)),
    ),
    ...generics.map((g) => ({
      ...row(
        g,
        faces![g]
          ? t('textControls.fontGenericRow', {
              ns: 'inspector',
              generic: optionLabel('fontfamily', g),
              face: faces![g],
            })
          : optionLabel('fontfamily', g),
      ),
      group,
    })),
  ]
  const face = isGeneric(value) ? faces![value] : undefined
  return face
    ? {
        options: out,
        triggerLabel: <span style={{ fontFamily: fontStackOf(face) }}>{named(face)}</span>,
        title: t('textControls.fontFromGeneric', { ns: 'inspector', generic: value }),
      }
    : { options: out }
}

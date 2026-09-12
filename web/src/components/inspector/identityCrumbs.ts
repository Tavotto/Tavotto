import { t } from '@/i18n'

import { engineLabel } from './roles/registry'

/**
 * 属性页顶部那条面包屑：「面板 / 子图 / 元素」。
 *
 * 引擎发来的 `label` 是**中文散文**（`子图 1` / `标题 “…”`），一律过
 * `engineLabel` 换成当前语言——元素树那边一直这么做，这条面包屑曾经漏了：
 * 英文界面下一选中元素，右栏标题就冒出中文，而画面其余部分全是英文。
 * `pnpm i18n:check` 拦不住这一类：它查的是 key 与译文，而这里漏的是
 * **运行时数据**没过翻译函数，一个 key 都没少。
 *
 * 单独一个模块而不是塞在 Inspector.tsx 里：组件文件里再导出一个非组件会让
 * fast refresh 失效（oxlint 的 only-export-components），而这段逻辑正好该被
 * 单测直接盯住。
 */
export function identityCrumbs(
  panelName: string,
  axesLabel: string | undefined,
  elementLabel: string | undefined,
  selectedCount: number,
): string[] {
  return [
    panelName,
    axesLabel ? engineLabel(axesLabel) : null,
    elementLabel
      ? engineLabel(elementLabel)
      : selectedCount > 1
        ? t('elementsSelected', { ns: 'inspector', count: selectedCount })
        : null,
  ].filter(Boolean) as string[]
}

/**
 * 引擎给元素起名时把引号里的文字截到 18 个字符（`manifest._snippet`）：
 * `X 轴 “Reaction time (mi…”`。树行里那样正好，身份头的标题却不该带着引擎的省略号
 * ——完整文字就在下一行的「内容」框里，标题该用完整文字、让 CSS 按可用宽度截断
 * （2026-09-12 critique P3）。只在名字确实被截过（以 `…”` 收尾）且元素带 `text`
 * 字段时替换；引号外的角色前缀原样保留，`engineLabel` 仍认得出它。
 */
export function untruncatedLabel(label: string, text: string | undefined): string {
  if (typeof text !== 'string' || !label.endsWith('…”')) return label
  const full = text.split(/\s+/).join(' ').trim()
  if (!full) return label
  // 用回调而不是替换串：用户的文字进 `replace` 的第二个参数会被当成模板，
  // mathtext 里常见的 `$$`、`$&` 会被吃掉或换成被截的旧名（评审 P2）
  return label.replace(/“[^”]*…”$/, () => `“${full}”`)
}

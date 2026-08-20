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

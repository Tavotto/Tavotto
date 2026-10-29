import { useTranslation } from 'react-i18next'
import { CaseSensitive } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import { transformCase, type CaseMode } from '@/lib/richText'
import { Button } from '../ui/Button'
import { Menu, MenuItem } from '../ui/Menu'

/**
 * 图内文字的「大小写」菜单，坐在所见即所得文字框（`RichTextField`）工具条的最右端。
 * 属性页与右键快捷编辑共用同一份——两边各写一套，行为迟早会飘。
 *
 * 换行 / 上下标 / 粗斜体已是文字框自己的按钮（ADR 0152）；大小写要保护 `$…$` 里的公式
 * （`\alpha` 改了大小写就废），所以走 `transformCase(…, protectMath)`。
 */
/** 本组文案在 inspector:textActions.* 下 */
const ta = (key: string, values?: Record<string, unknown>) =>
  translate(`textActions.${key}`, { ns: 'inspector', ...(values ?? {}) })

export function TextCaseMenu({
  text,
  onChange,
}: {
  text: string
  /** 一次性的离散动作，宿主可以当场定稿 */
  onChange: (next: string) => void
}) {
  useTranslation('inspector')
  const changeCase = (mode: CaseMode) => onChange(transformCase(text, mode, true))
  return (
    <Menu
      align="end"
      width={140}
      trigger={
        <Button
          size="icon-xs"
          data-rich-action="case"
          className="text-ink-2"
          // 按下不抢焦点：文字框的编辑事务不该因为点了按钮就提交
          onPointerDown={(e) => e.preventDefault()}
          title={ta('caseTitle')}
          aria-label={ta('caseTitle')}
        >
          <CaseSensitive size={ICON_SIZE.xs} />
        </Button>
      }
    >
      <MenuItem onSelect={() => changeCase('upper')}>{ta('upper')}</MenuItem>
      <MenuItem onSelect={() => changeCase('lower')}>{ta('lower')}</MenuItem>
      <MenuItem onSelect={() => changeCase('title')}>{ta('titleCase')}</MenuItem>
      <MenuItem onSelect={() => changeCase('sentence')}>{ta('sentence')}</MenuItem>
    </Menu>
  )
}

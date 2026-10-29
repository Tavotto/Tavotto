import { useTranslation } from 'react-i18next'
import { CaseSensitive } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import { figureTextCodec } from '@/lib/richEditor'
import { applyCase, type CaseMode } from '@/lib/richText'
import { Button } from '../ui/Button'
import { Menu, MenuItem } from '../ui/Menu'

/**
 * 图内文字的「大小写」菜单，坐在所见即所得文字框（`RichTextField`）工具条的最右端。
 * 属性页与右键快捷编辑共用同一份——两边各写一套，行为迟早会飘。
 *
 * 换行 / 上下标 / 粗斜体已是文字框自己的按钮（ADR 0152）。大小写经 `figureTextCodec` 拆成片段：
 * 带样式的字（`$\mathit{temperature}$`）照样改，公式块（`\alpha` 改了大小写就废）原样不动（Codex #1489）。
 */

/** 图内文字的大小写：样式与公式不动，只改字 */
export function figureTextCase(text: string, mode: CaseMode): string {
  const runs = figureTextCodec.parse(text)
  const editable = runs.filter((r) => !r.math && !r.raw)
  // 整段拼起来一起改，句首 / 词首按整句判断（「Rate \mathit{k}」的 k 不是句首）；
  // 长度变了（ß → SS 之类）就退回逐段改，不错位
  const joined = editable.map((r) => r.text).join('')
  const changed = applyCase(joined, mode)
  let pos = 0
  const sameLength = changed.length === joined.length
  const next = runs.map((r) => {
    if (r.math || r.raw) return r
    const t = sameLength ? changed.slice(pos, pos + r.text.length) : applyCase(r.text, mode)
    pos += r.text.length
    return { ...r, text: t }
  })
  return figureTextCodec.serialize(next)
}
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
  const changeCase = (mode: CaseMode) => onChange(figureTextCase(text, mode))
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

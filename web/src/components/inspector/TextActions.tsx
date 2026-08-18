import type { RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { CaseSensitive, CornerDownLeft, Subscript, Superscript } from 'lucide-react'
import { t as translate } from '@/i18n'
import { toggleMathScript, transformCase, type CaseMode } from '@/lib/richText'
import { ALT, combo, modKey } from '@/lib/utils'
import { Button } from '../ui/Button'
import { Menu, MenuItem } from '../ui/Menu'

/**
 * 图内文字内容的四个动作：换行 / 上标 / 下标 / 大小写。
 * 属性页与右键弹层共用同一份——两边各写一套，行为迟早会飘。
 *
 * 上下标走 matplotlib mathtext（`cm$^{-1}$`），跟画布标注那套 `^{…}` 行内
 * 标记不是一回事；大小写要保护 `$…$` 里的公式（`\alpha` 改了大小写就废）。
 */
/** 本组文案在 inspector:textActions.* 下 */
const ta = (key: string, values?: Record<string, unknown>) =>
  translate(`textActions.${key}`, { ns: 'inspector', ...(values ?? {}) })

export function TextActionRow({
  text,
  taRef,
  onChange,
}: {
  text: string
  taRef: RefObject<HTMLTextAreaElement | null>
  /** immediate=true 表示这是一次性的离散动作，可以当场定稿 */
  onChange: (next: string, immediate: boolean) => void
}) {
  useTranslation('inspector')
  /** 改完文本把光标放回去——不复位的话每点一次按钮光标就跳到末尾 */
  const restoreCaret = (start: number, end: number) =>
    requestAnimationFrame(() => {
      const ta = taRef.current
      if (!ta) return
      ta.focus()
      ta.setSelectionRange(start, end)
    })

  const selection = () => {
    const ta = taRef.current
    return [ta?.selectionStart ?? text.length, ta?.selectionEnd ?? text.length] as const
  }

  const insertNewline = () => {
    const [s, t] = selection()
    onChange(text.slice(0, s) + '\n' + text.slice(t), false)
    restoreCaret(s + 1, s + 1)
  }

  const wrapMath = (kind: 'sup' | 'sub') => {
    const [s, t] = selection()
    const next = toggleMathScript(text, s, t, kind)
    onChange(next.text, true)
    restoreCaret(next.start, next.end)
  }

  const changeCase = (mode: CaseMode) => onChange(transformCase(text, mode, true), true)

  return (
    // 按下时一律不抢焦点：textarea 的编辑事务不该因为点了按钮就提交
    <div className="flex shrink-0 justify-end gap-0.5">
      <Button
        size="icon-sm"
        onPointerDown={(e) => e.preventDefault()}
        onClick={insertNewline}
        title={ta('newlineTitle', { alt: combo(ALT, '⏎'), mod: modKey('⏎') })}
        aria-label={ta('newline')}
      >
        <CornerDownLeft size={12} />
      </Button>
      <Button
        size="icon-sm"
        onPointerDown={(e) => e.preventDefault()}
        onClick={() => wrapMath('sup')}
        title={ta('supTitle')}
        aria-label={ta('sup')}
      >
        <Superscript size={12} />
      </Button>
      <Button
        size="icon-sm"
        onPointerDown={(e) => e.preventDefault()}
        onClick={() => wrapMath('sub')}
        title={ta('subTitle')}
        aria-label={ta('sub')}
      >
        <Subscript size={12} />
      </Button>
      <Menu
        align="end"
        width={140}
        trigger={
          <Button
            size="icon-sm"
            onPointerDown={(e) => e.preventDefault()}
            title={ta('caseTitle')}
            aria-label={ta('caseTitle')}
          >
            <CaseSensitive size={12} />
          </Button>
        }
      >
        <MenuItem onSelect={() => changeCase('upper')}>{ta('upper')}</MenuItem>
        <MenuItem onSelect={() => changeCase('lower')}>{ta('lower')}</MenuItem>
        <MenuItem onSelect={() => changeCase('title')}>{ta('titleCase')}</MenuItem>
        <MenuItem onSelect={() => changeCase('sentence')}>{ta('sentence')}</MenuItem>
      </Menu>
    </div>
  )
}

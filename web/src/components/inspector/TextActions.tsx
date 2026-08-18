import type { RefObject } from 'react'
import { CaseSensitive, CornerDownLeft, Subscript, Superscript } from 'lucide-react'
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
        title={`在光标处插入换行（${combo(ALT, '⏎')} / ${modKey('⏎')}）`}
        aria-label="插入换行"
      >
        <CornerDownLeft size={12} />
      </Button>
      <Button
        size="icon-sm"
        onPointerDown={(e) => e.preventDefault()}
        onClick={() => wrapMath('sup')}
        title="上标：选中一段再点，写成 matplotlib 公式（cm$^{-1}$）"
        aria-label="上标"
      >
        <Superscript size={12} />
      </Button>
      <Button
        size="icon-sm"
        onPointerDown={(e) => e.preventDefault()}
        onClick={() => wrapMath('sub')}
        title="下标：选中一段再点，写成 matplotlib 公式（H$_{2}$O）"
        aria-label="下标"
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
            title="大小写转换"
            aria-label="大小写转换"
          >
            <CaseSensitive size={12} />
          </Button>
        }
      >
        <MenuItem onSelect={() => changeCase('upper')}>全部大写</MenuItem>
        <MenuItem onSelect={() => changeCase('lower')}>全部小写</MenuItem>
        <MenuItem onSelect={() => changeCase('title')}>每词首字母大写</MenuItem>
        <MenuItem onSelect={() => changeCase('sentence')}>每句首字母大写</MenuItem>
      </Menu>
    </div>
  )
}

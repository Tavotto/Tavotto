import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Bold,
  CornerDownLeft,
  Italic,
  Subscript,
  Superscript,
  TextAlignCenter,
  TextAlignEnd,
  TextAlignStart,
  Underline,
} from 'lucide-react'
import { toggleScript, transformCase, type CaseMode } from '@/lib/richText'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { BASE_FONT_PT, effectivePt, round1 } from '@/lib/units'
import { ALT, combo, modKey } from '@/lib/utils'
import { updateObjects } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { panelFullSize, type PanelObject, type TextObject } from '@/types/document'
import { Button } from '../ui/Button'
import { Row, Section } from '../ui/Field'
import { ColorField, NumberField } from '../ui/Input'
import { Segmented } from '../ui/Segmented'
import { shared } from './common'

/** 本组文案 inspector:text.*，历史标签 inspector:history.* */
const tx = (key: string, values?: Record<string, unknown>) =>
  translate(`text.${key}`, { ns: 'inspector', ...(values ?? {}) })
const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'inspector')

const alignItems = () => [
  { value: 'left' as const, icon: <TextAlignStart size={13} />, tip: tx('alignLeft') },
  { value: 'center' as const, icon: <TextAlignCenter size={13} />, tip: tx('alignCenter') },
  { value: 'right' as const, icon: <TextAlignEnd size={13} />, tip: tx('alignRight') },
]

/**
 * 上下标快捷键：Mod+↑ = 上标、Mod+↓ = 下标（Mod = ⌘ / Ctrl，两边都认）。
 *
 * 带 ⌥ / ⇧ 的组合**不认领**——那些是系统自带的「按段移动 / 选到开头」，
 * 抢过来用户就没法在文本框里选词了；只有干净的 Mod+方向键才是我们的键位。
 * 单独抽出来是为了能直接对它写用例，不必为每种组合去挂一次组件。
 */
export function scriptHotkey(
  e: Pick<KeyboardEvent, 'key' | 'metaKey' | 'ctrlKey' | 'altKey' | 'shiftKey'>,
): 'sup' | 'sub' | null {
  if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return null
  if (e.key === 'ArrowUp') return 'sup'
  if (e.key === 'ArrowDown') return 'sub'
  return null
}

/** 大小写是一次性动作而不是状态：转换完就没有「当前处于大写」这回事 */
const caseItems = () => [
  { value: 'upper' as const, label: 'AA', tip: tx('caseUpper') },
  { value: 'lower' as const, label: 'aa', tip: tx('caseLower') },
  { value: 'title' as const, label: 'Aa', tip: tx('caseTitle') },
  { value: 'sentence' as const, label: 'A.', tip: tx('caseSentence') },
]

export function TextSection({ objs }: { objs: TextObject[] }) {
  useTranslation('inspector')
  const taRef = useRef<HTMLTextAreaElement>(null)
  const ids = objs.map((o) => o.id)
  const one = objs.length === 1 ? objs[0] : null
  const bold = shared(objs, (o) => (o as TextObject).bold)
  const italic = shared(objs, (o) => (o as TextObject).italic === true)
  const underline = shared(objs, (o) => (o as TextObject).underline === true)
  const align = shared(objs, (o) => (o as TextObject).align)
  const sizePt = shared(objs, (o) => (o as TextObject).sizePt)
  const color = shared(objs, (o) => (o as TextObject).color)
  const bg = shared(objs, (o) => (o as TextObject).bg ?? null)
  const borderColor = shared(objs, (o) => (o as TextObject).borderColor ?? null)

  const patch = (label: UiMessage, fn: (o: TextObject) => void) =>
    updateObjects(ids, label, (o) => {
      if (o.type === 'text') fn(o)
    })

  /**
   * 输入框是逐字符 onChange 的：不开事务的话一个字一条历史，⌘Z 一次只退一个字，
   * 长文本还会把 200 条历史上限挤爆。聚焦开事务、失焦合并成一条
   * （与 ElementInspector 的图内文字输入框同一模式）。
   */
  const beginTxn = () => useDocumentStore.getState().beginTxn(hist('editText'))
  const endTxn = () => useDocumentStore.getState().endTxn()

  /** 改完文本后把光标放回去——不复位的话每点一次按钮光标就跳到末尾。 */
  const restoreCaret = (start: number, end: number) => {
    requestAnimationFrame(() => {
      const el = taRef.current
      if (!el) return
      el.focus()
      el.setSelectionRange(start, end)
    })
  }

  /**
   * 给选中的一段套上/去掉上下标标记（`^{…}` / `_{…}`）。
   * 没有选区就插入一对空标记并把光标放进去，可以直接开始打字。
   */
  const wrapScript = (kind: 'sup' | 'sub') => {
    if (!one) return
    const ta = taRef.current
    const start = ta?.selectionStart ?? one.text.length
    const end = ta?.selectionEnd ?? one.text.length
    const next = toggleScript(one.text, start, end, kind)
    patch(hist(kind === 'sup' ? 'setSuperscript' : 'setSubscript'), (o) => (o.text = next.text))
    restoreCaret(next.start, next.end)
  }

  /** 大小写：直接改文本内容（可撤销），不新增字段，导出零改动。 */
  const applyCase = (mode: CaseMode) =>
    patch(hist('transformCase'), (o) => (o.text = transformCase(o.text, mode)))

  /** 在光标处插入换行；textarea 没聚焦过就接在末尾。补丁重渲染后恢复光标。 */
  const insertNewline = () => {
    if (!one) return
    const ta = taRef.current
    const start = ta?.selectionStart ?? one.text.length
    const end = ta?.selectionEnd ?? one.text.length
    const next = one.text.slice(0, start) + '\n' + one.text.slice(end)
    patch(hist('insertNewline'), (o) => (o.text = next))
    requestAnimationFrame(() => {
      const el = taRef.current
      if (el) {
        el.focus()
        el.setSelectionRange(start + 1, start + 1)
      }
    })
  }

  return (
    <Section title={tx('title')}>
      {one && (
        <textarea
          ref={taRef}
          value={one.text}
          rows={2}
          onFocus={beginTxn}
          onBlur={endTxn}
          onChange={(e) => patch(hist('editText'), (o) => (o.text = e.target.value))}
          onKeyDown={(e) => {
            e.stopPropagation()
            const kind = scriptHotkey(e)
            if (!kind) return
            // 不拦默认行为的话光标会先跳到上一行/末行，标记插进去人就找不着它了
            e.preventDefault()
            wrapScript(kind)
          }}
          onDoubleClick={() => useUiStore.getState().setEditingText(one.id)}
          placeholder={tx('placeholder')}
          className="mb-1 w-full resize-none rounded-sm border border-border bg-surface px-1.5 py-1 text-xs leading-relaxed text-ink outline-none transition-colors placeholder:text-ink-3 hover:border-border-strong focus:border-accent"
          style={{ fontFamily: 'var(--font-doc)' }}
        />
      )}
      {one && (
        <div className="mb-2 flex justify-end">
          <Button
            size="sm"
            onClick={insertNewline}
            title={tx('newlineTitle', { alt: combo(ALT, '⏎'), mod: modKey('⏎') })}
          >
            <CornerDownLeft size={12} />
            {tx('insertNewline')}
          </Button>
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        <Row label={tx('fontSize')}>
          <NumberField
            value={sizePt ?? 10}
            mixed={sizePt === undefined}
            step={0.5}
            min={3}
            max={96}
            suffix="pt"
            onChange={(v) => patch(hist('setFontSize'), (o) => (o.sizePt = v))}
          />
          <Button
            size="icon"
            active={bold === true}
            onClick={() => patch(hist('toggleBold'), (o) => (o.bold = !bold))}
            aria-label={tx('bold')}
          >
            <Bold size={13} />
          </Button>
          <Button
            size="icon"
            active={italic === true}
            onClick={() => patch(hist('toggleItalic'), (o) => (o.italic = !italic))}
            aria-label={tx('italic')}
          >
            <Italic size={13} />
          </Button>
          <Button
            size="icon"
            active={underline === true}
            onClick={() =>
              patch(hist('toggleUnderline'), (o) => {
                if (underline) delete o.underline
                else o.underline = true
              })
            }
            aria-label={tx('underline')}
          >
            <Underline size={13} />
          </Button>
          <Button
            size="icon"
            disabled={!one}
            onClick={() => wrapScript('sup')}
            aria-label={tx('superscript')}
            title={tx('superscriptTitle', { key: modKey('↑') })}
          >
            <Superscript size={13} />
          </Button>
          <Button
            size="icon"
            disabled={!one}
            onClick={() => wrapScript('sub')}
            aria-label={tx('subscript')}
            title={tx('subscriptTitle', { key: modKey('↓') })}
          >
            <Subscript size={13} />
          </Button>
        </Row>
        <Row label={tx('case')}>
          <Segmented
            value={null}
            onChange={(v) => applyCase(v)}
            items={caseItems()}
            className="w-full"
          />
        </Row>
        {one && (
          <MatchFigureSize
            text={one}
            onMatch={(v) => patch(hist('matchFigureSize'), (o) => (o.sizePt = v))}
          />
        )}
        <Row label={tx('align')}>
          <Segmented
            value={align ?? null}
            onChange={(v) => patch(hist('setAlign'), (o) => (o.align = v))}
            items={alignItems()}
            className="w-full"
          />
        </Row>
        <Row label={tx('color')}>
          <ColorField
            value={color ?? '#000000'}
            onChange={(v) => patch(hist('setTextColor'), (o) => (o.color = v))}
          />
        </Row>
        <Row label={tx('lineHeight')}>
          <NumberField
            value={shared(objs, (o) => (o as TextObject).lineHeight ?? 1.25) ?? 1.25}
            step={0.05}
            min={0.8}
            max={3}
            precision={2}
            onChange={(v) =>
              patch(hist('setLineHeight'), (o) => {
                if (Math.abs(v - 1.25) < 0.001) delete o.lineHeight
                else o.lineHeight = v
              })
            }
          />
        </Row>
        <Row label={tx('background')}>
          {bg ? (
            <>
              <ColorField
                value={bg}
                onChange={(v) => patch(hist('setTextBg'), (o) => (o.bg = v))}
              />
              <Button
                size="icon"
                onClick={() => patch(hist('clearTextBg'), (o) => delete o.bg)}
                aria-label={tx('clearBackground')}
              >
                <span className="text-xs text-ink-3">{tx('none')}</span>
              </Button>
            </>
          ) : (
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={() =>
                patch(hist('addTextBg'), (o) => {
                  o.bg = '#FFFFFF'
                  if (o.padding == null) o.padding = 1
                })
              }
            >
              {tx('addBackground')}
            </Button>
          )}
        </Row>
        <Row label={tx('border')}>
          {borderColor ? (
            <>
              <ColorField
                value={borderColor}
                onChange={(v) => patch(hist('setTextBorder'), (o) => (o.borderColor = v))}
              />
              <Button
                size="icon"
                onClick={() =>
                  patch(hist('clearTextBorder'), (o) => {
                    delete o.borderColor
                    delete o.borderPt
                  })
                }
                aria-label={tx('clearBorder')}
              >
                <span className="text-xs text-ink-3">{tx('none')}</span>
              </Button>
            </>
          ) : (
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={() =>
                patch(hist('addTextBorder'), (o) => {
                  o.borderColor = '#1B1B18'
                  if (o.padding == null) o.padding = 1
                })
              }
            >
              {tx('addBorder')}
            </Button>
          )}
        </Row>
        {(bg || borderColor) && (
          <Row label={tx('padding')}>
            <NumberField
              value={shared(objs, (o) => (o as TextObject).padding ?? 0) ?? 0}
              step={0.5}
              min={0}
              max={10}
              precision={1}
              suffix="mm"
              onChange={(v) =>
                patch(hist('setPadding'), (o) => {
                  if (v > 0) o.padding = v
                  else delete o.padding
                })
              }
            />
          </Row>
        )}
      </div>
    </Section>
  )
}

/**
 * 标注字号是页面绝对值，图内文字的字号在面板缩放后会跟着缩放——
 * 「都设 9pt 但看上去不一样大」的根源。给出标注所压面板的等效正文字号，
 * 一键把标注对齐到它，视觉上就和图内正文一样大。
 */
function MatchFigureSize({
  text,
  onMatch,
}: {
  text: TextObject
  onMatch: (sizePt: number) => void
}) {
  useTranslation('inspector')
  // 取与标注重叠面积最大的面板；同面积取更晚（更上层）的
  const panel = useDocumentStore((s) => {
    let best: PanelObject | null = null
    let bestArea = 0
    for (const o of s.doc.objects) {
      if (o.type !== 'panel' || o.hidden) continue
      const w = Math.min(text.x + text.w, o.x + o.w) - Math.max(text.x, o.x)
      const h = Math.min(text.y + text.h, o.y + o.h) - Math.max(text.y, o.y)
      const area = Math.max(0, w) * Math.max(0, h)
      if (area > 0 && area >= bestArea) {
        bestArea = area
        best = o
      }
    }
    return best
  })
  if (!panel) return null

  const scale = panelFullSize(panel).w / panel.nativeW
  const eff = round1(effectivePt(panelFullSize(panel).w, panel.nativeW))
  if (Math.abs(eff - text.sizePt) < 0.05) return null

  return (
    <p
      className="text-xs leading-relaxed text-ink-3"
      title={tx('matchTitle', {
        panel: panel.name ?? panel.fileId,
        scale: Math.round(scale * 100),
        base: BASE_FONT_PT,
        eff,
      })}
    >
      {tx('matchHint', { eff })}
      <button
        onClick={() => onMatch(eff)}
        className="ml-1.5 text-accent underline-offset-2 hover:underline"
      >
        {tx('matchAction')}
      </button>
    </p>
  )
}

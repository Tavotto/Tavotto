import { useRef } from 'react'
import {
  Bold,
  CornerDownLeft,
  Italic,
  TextAlignCenter,
  TextAlignEnd,
  TextAlignStart,
  Underline,
} from 'lucide-react'
import { BASE_FONT_PT, effectivePt, round1 } from '@/lib/units'
import { updateObjects } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { panelFullSize, type PanelObject, type TextObject } from '@/types/document'
import { Button } from '../ui/Button'
import { Row, Section } from '../ui/Field'
import { ColorField, NumberField } from '../ui/Input'
import { Segmented } from '../ui/Segmented'
import { shared } from './common'

const ALIGN_ITEMS = [
  { value: 'left' as const, icon: <TextAlignStart size={13} />, tip: '左对齐' },
  { value: 'center' as const, icon: <TextAlignCenter size={13} />, tip: '居中' },
  { value: 'right' as const, icon: <TextAlignEnd size={13} />, tip: '右对齐' },
]

export function TextSection({ objs }: { objs: TextObject[] }) {
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

  const patch = (label: string, fn: (o: TextObject) => void) =>
    updateObjects(ids, label, (o) => {
      if (o.type === 'text') fn(o)
    })

  /** 在光标处插入换行；textarea 没聚焦过就接在末尾。补丁重渲染后恢复光标。 */
  const insertNewline = () => {
    if (!one) return
    const ta = taRef.current
    const start = ta?.selectionStart ?? one.text.length
    const end = ta?.selectionEnd ?? one.text.length
    const next = one.text.slice(0, start) + '\n' + one.text.slice(end)
    patch('插入换行', (o) => (o.text = next))
    requestAnimationFrame(() => {
      const el = taRef.current
      if (el) {
        el.focus()
        el.setSelectionRange(start + 1, start + 1)
      }
    })
  }

  return (
    <Section title="文字">
      {one && (
        <textarea
          ref={taRef}
          value={one.text}
          rows={2}
          onChange={(e) => patch('编辑文字', (o) => (o.text = e.target.value))}
          onKeyDown={(e) => e.stopPropagation()}
          onDoubleClick={() => useUiStore.getState().setEditingText(one.id)}
          placeholder="输入文字…"
          className="mb-1 w-full resize-none rounded-sm border border-border bg-surface px-1.5 py-1 text-xs leading-relaxed text-ink outline-none transition-colors placeholder:text-ink-3 hover:border-border-strong focus:border-accent"
          style={{ fontFamily: 'var(--font-doc)' }}
        />
      )}
      {one && (
        <div className="mb-2 flex justify-end">
          <Button
            size="sm"
            onClick={insertNewline}
            title="在光标处插入换行（画布内编辑可用 ⌥⏎ / ⌘⏎）"
          >
            <CornerDownLeft size={12} />
            插入换行
          </Button>
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        <Row label="字号">
          <NumberField
            value={sizePt ?? 10}
            mixed={sizePt === undefined}
            step={0.5}
            min={3}
            max={96}
            suffix="pt"
            onChange={(v) => patch('修改字号', (o) => (o.sizePt = v))}
          />
          <Button
            size="icon"
            active={bold === true}
            onClick={() => patch('切换加粗', (o) => (o.bold = !bold))}
            aria-label="加粗"
          >
            <Bold size={13} />
          </Button>
          <Button
            size="icon"
            active={italic === true}
            onClick={() => patch('切换斜体', (o) => (o.italic = !italic))}
            aria-label="斜体（仅拉丁字形）"
          >
            <Italic size={13} />
          </Button>
          <Button
            size="icon"
            active={underline === true}
            onClick={() =>
              patch('切换下划线', (o) => {
                if (underline) delete o.underline
                else o.underline = true
              })
            }
            aria-label="下划线"
          >
            <Underline size={13} />
          </Button>
        </Row>
        {one && (
          <MatchFigureSize
            text={one}
            onMatch={(v) => patch('对齐图内字号', (o) => (o.sizePt = v))}
          />
        )}
        <Row label="对齐">
          <Segmented
            value={align ?? null}
            onChange={(v) => patch('修改对齐', (o) => (o.align = v))}
            items={ALIGN_ITEMS}
            className="w-full"
          />
        </Row>
        <Row label="颜色">
          <ColorField
            value={color ?? '#000000'}
            onChange={(v) => patch('修改文字颜色', (o) => (o.color = v))}
          />
        </Row>
        <Row label="行距">
          <NumberField
            value={shared(objs, (o) => (o as TextObject).lineHeight ?? 1.25) ?? 1.25}
            step={0.05}
            min={0.8}
            max={3}
            precision={2}
            onChange={(v) =>
              patch('修改行距', (o) => {
                if (Math.abs(v - 1.25) < 0.001) delete o.lineHeight
                else o.lineHeight = v
              })
            }
          />
        </Row>
        <Row label="背景">
          {bg ? (
            <>
              <ColorField value={bg} onChange={(v) => patch('修改文字背景', (o) => (o.bg = v))} />
              <Button
                size="icon"
                onClick={() => patch('清除文字背景', (o) => delete o.bg)}
                aria-label="清除背景"
              >
                <span className="text-xs text-ink-3">无</span>
              </Button>
            </>
          ) : (
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={() =>
                patch('添加文字背景', (o) => {
                  o.bg = '#FFFFFF'
                  if (o.padding == null) o.padding = 1
                })
              }
            >
              添加背景
            </Button>
          )}
        </Row>
        <Row label="描边">
          {borderColor ? (
            <>
              <ColorField
                value={borderColor}
                onChange={(v) => patch('修改文字描边', (o) => (o.borderColor = v))}
              />
              <Button
                size="icon"
                onClick={() =>
                  patch('清除文字描边', (o) => {
                    delete o.borderColor
                    delete o.borderPt
                  })
                }
                aria-label="清除描边"
              >
                <span className="text-xs text-ink-3">无</span>
              </Button>
            </>
          ) : (
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={() =>
                patch('添加文字描边', (o) => {
                  o.borderColor = '#1B1B18'
                  if (o.padding == null) o.padding = 1
                })
              }
            >
              添加描边
            </Button>
          )}
        </Row>
        {(bg || borderColor) && (
          <Row label="内边距">
            <NumberField
              value={shared(objs, (o) => (o as TextObject).padding ?? 0) ?? 0}
              step={0.5}
              min={0}
              max={10}
              precision={1}
              suffix="mm"
              onChange={(v) =>
                patch('修改内边距', (o) => {
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
      title={`标注字号是页面绝对值；「${panel.name ?? panel.fileId}」缩放到 ${Math.round(scale * 100)}% 后，图内 ${BASE_FONT_PT}pt 正文实际显示约 ${eff}pt。想让标注和图内文字一样大，就用这个值。`}
    >
      图内正文显示约 {eff}pt
      <button
        onClick={() => onMatch(eff)}
        className="ml-1.5 text-accent underline-offset-2 hover:underline"
      >
        对齐
      </button>
    </p>
  )
}

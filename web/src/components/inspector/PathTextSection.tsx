import { useTranslation } from 'react-i18next'
import { Bold, FlipVertical2, Italic, TextAlignCenter, TextAlignEnd, TextAlignStart } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { clampOffset } from '@/lib/pathText'
import { updateObjects } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import type { PathTextObject } from '@/types/document'
import { Button } from '../ui/Button'
import { Row, Section } from '../ui/Field'
import { ColorField, NumberField, TextInput } from '../ui/Input'
import { Segmented } from '../ui/Segmented'
import { shared } from './common'
import { INSPECTOR_LABEL_W } from './layout'

/**
 * 路径文字（ADR 0190）的属性：内容、字号 + 粗斜（一行）、颜色、字体族、对齐 + 翻转（一行）、起点。没有换行、行距、
 * 底框——一行字沿路径走，这些都没有意义。写入一律 `updateObjects`；连续输入（内容、起点）聚焦开事务、失焦一条历史。
 */

const tx = (key: string, values?: Record<string, unknown>) => translate(`text.${key}`, { ns: 'inspector', ...(values ?? {}) })
const px = (key: string) => translate(`pathText.${key}`, { ns: 'inspector' })
const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'inspector')
const FAMILIES = ['serif', 'sans-serif', 'monospace'] as const
type Align = 'start' | 'center' | 'end'

export function PathTextSection({ objs }: { objs: PathTextObject[] }) {
  useTranslation('inspector')
  const ids = objs.map((o) => o.id)
  const one = objs.length === 1 ? objs[0] : null
  const patch = (label: UiMessage, fn: (o: PathTextObject) => void) =>
    updateObjects(ids, label, (o) => {
      if (o.type === 'pathText') fn(o)
    })
  const beginTxn = (key: string) => useDocumentStore.getState().beginTxn(hist(key))
  const endTxn = () => useDocumentStore.getState().endTxn()

  const size = shared(objs, (o) => (o as PathTextObject).sizePt)
  const bold = shared(objs, (o) => (o as PathTextObject).bold)
  const italic = shared(objs, (o) => (o as PathTextObject).italic === true)
  const color = shared(objs, (o) => (o as PathTextObject).color)
  const family = shared(objs, (o) => (o as PathTextObject).fontFamily ?? 'serif')
  const align = shared(objs, (o): Align => (o as PathTextObject).textAlign ?? 'start')
  const flip = shared(objs, (o) => (o as PathTextObject).flip === true)
  const start = shared(objs, (o) => Math.round(clampOffset((o as PathTextObject).startOffset) * 1000) / 10)

  return (
    <Section title={px('title')}>
      {one && (
        <div className="mb-2">
          <TextInput
            data-path-text-content=""
            aria-label={px('title')}
            value={one.text}
            placeholder={tx('placeholder')}
            onFocus={() => beginTxn('editText')}
            onBlur={endTxn}
            onChange={(e) =>
              useDocumentStore.getState().txnUpdate((d) => {
                const o = d.objects.find((x) => x.id === one.id)
                // 一行字：换行并成空格（与后端 `pathtext.line_text` 同一条）
                if (o && o.type === 'pathText') o.text = e.target.value.replace(/\s*\n\s*/g, ' ')
              })
            }
          />
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        <Row label={tx('fontSize')} labelWidth={INSPECTOR_LABEL_W}>
          <div className="flex items-center gap-1">
            <NumberField
              half
              value={size ?? 10}
              mixed={size === undefined}
              min={2}
              max={200}
              step={0.5}
              precision={1}
              unit="pt"
              ariaLabel={tx('fontSize')}
              onChange={(v) => patch(hist('setFontSize'), (o) => (o.sizePt = v))}
            />
            <Button
              size="icon-sm"
              active={bold === true}
              aria-label={tx('bold')}
              onClick={() => patch(hist('toggleBold'), (o) => (o.bold = bold !== true))}
            >
              <Bold size={ICON_SIZE.sm} />
            </Button>
            <Button
              size="icon-sm"
              active={italic === true}
              aria-label={tx('italic')}
              onClick={() =>
                patch(hist('toggleItalic'), (o) => {
                  if (italic) delete o.italic
                  else o.italic = true
                })
              }
            >
              <Italic size={ICON_SIZE.sm} />
            </Button>
          </div>
        </Row>
        <Row label={tx('color')} labelWidth={INSPECTOR_LABEL_W}>
          <ColorField
            value={color ?? objs[0].color}
            mixed={color === undefined}
            ariaLabel={tx('color')}
            onChange={(v) => patch(hist('setTextColor'), (o) => (o.color = v))}
          />
        </Row>
        <Row label={translate('prop.fontfamily', { ns: 'inspector' })} labelWidth={INSPECTOR_LABEL_W}>
          <Segmented
            ariaLabel={translate('prop.fontfamily', { ns: 'inspector' })}
            value={family ?? null}
            onChange={(v) =>
              patch(hist('setFontFamily'), (o) => {
                if (v === 'serif') delete o.fontFamily
                else o.fontFamily = v
              })
            }
            items={FAMILIES.map((f) => ({ value: f, label: translate(`enum.fontfamily.${f}`, { ns: 'inspector' }) }))}
          />
        </Row>
        <Row label={tx('align')} labelWidth={INSPECTOR_LABEL_W}>
          <div className="flex items-center gap-1">
            <Segmented<Align>
              ariaLabel={tx('align')}
              value={align ?? null}
              onChange={(v) =>
                patch(hist('setAlign'), (o) => {
                  if (v === 'start') delete o.textAlign
                  else o.textAlign = v
                })
              }
              items={[
                { value: 'start', icon: <TextAlignStart size={ICON_SIZE.sm} />, ariaLabel: px('alignStart'), tip: px('alignStart') },
                { value: 'center', icon: <TextAlignCenter size={ICON_SIZE.sm} />, ariaLabel: px('alignCenter'), tip: px('alignCenter') },
                { value: 'end', icon: <TextAlignEnd size={ICON_SIZE.sm} />, ariaLabel: px('alignEnd'), tip: px('alignEnd') },
              ]}
            />
            <Button
              size="icon-sm"
              active={flip === true}
              aria-label={px('flip')}
              title={px('flip')}
              data-path-text-flip=""
              onClick={() =>
                patch(hist('flipPathText'), (o) => {
                  if (flip) delete o.flip
                  else o.flip = true
                })
              }
            >
              <FlipVertical2 size={ICON_SIZE.sm} />
            </Button>
          </div>
        </Row>
        <Row label={px('start')} labelWidth={INSPECTOR_LABEL_W}>
          <NumberField
            half
            value={start ?? 0}
            mixed={start === undefined}
            min={0}
            max={100}
            step={1}
            precision={1}
            unit="%"
            ariaLabel={px('start')}
            dataProp="path-text-start"
            onChange={(v) =>
              patch(hist('pathTextStart'), (o) => {
                const off = Math.round(Math.min(100, Math.max(0, v)) * 100) / 10000
                if (off > 0) o.startOffset = off
                else delete o.startOffset
              })
            }
          />
        </Row>
      </div>
    </Section>
  )
}

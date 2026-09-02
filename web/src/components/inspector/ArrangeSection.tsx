import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowDownToLine,
  ArrowUpToLine,
  Clipboard,
  ClipboardPaste,
  Group,
  MoveDown,
  MoveUp,
  Ungroup,
} from 'lucide-react'
import { t as translate } from '@/i18n'
import { MOD } from '@/lib/utils'
import {
  alignModeLabel,
  alignRefLabel,
  alignSelectedTo,
  changeZOrder,
  copySelectionStyle,
  createLayoutGroup,
  dissolveLayoutGroup,
  groupSelected,
  pasteSelectionStyle,
  reflowLayoutGroup,
  selectionHasGroup,
  setSelectionSpacing,
  styleClipKind,
  toggleLayoutPinned,
  ungroupSelected,
  updateLayoutGroup,
  type ZMove,
} from '@/store/actions'
import { useArrangeStore } from '@/store/arrangeStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import type { CanvasObject, LayoutGroup } from '@/types/document'
import { Button } from '../ui/Button'
import { Disclosure, Row, Section } from '../ui/Field'
import { NumberField } from '../ui/Input'
import { Segmented } from '../ui/Segmented'
import { Toggle } from '../ui/Toggle'
import { Tip } from '../ui/Tooltip'
import {
  ALIGN_BUTTONS,
  ALIGN_REFS,
  DISTRIBUTE_BUTTONS,
  SIZE_BUTTONS,
  type ArrangeButton,
} from './arrangeButtons'
import { useSelectedObjects } from './common'

/** 本组的文案在 inspector:arrange.* 下；对齐动作名复用 inspector:alignMode.* */
const ar = (key: string, values?: Record<string, unknown>) =>
  translate(`arrange.${key}`, { ns: 'inspector', ...(values ?? {}) })

const DISTRIBUTE: readonly ArrangeButton[] = [...DISTRIBUTE_BUTTONS, ...SIZE_BUTTONS]

const ZORDER: { move: ZMove; icon: typeof MoveUp; key: string; shortcut?: string }[] = [
  { move: 'top', icon: ArrowUpToLine, key: 'zTop', shortcut: `⇧${MOD}]` },
  { move: 'up', icon: MoveUp, key: 'zUp', shortcut: `${MOD}]` },
  { move: 'down', icon: MoveDown, key: 'zDown', shortcut: `${MOD}[` },
  { move: 'bottom', icon: ArrowDownToLine, key: 'zBottom', shortcut: `⇧${MOD}[` },
]

/** 六向对齐，参照整个画布 —— 单选时唯一说得通的对齐 */
export function AlignToCanvasRow() {
  useTranslation('inspector')
  return (
    <div className="grid grid-cols-6 gap-0.5">
      {ALIGN_BUTTONS.map(({ mode, icon: Icon }) => {
        const label = ar('alignRelativeCanvas', { mode: alignModeLabel(mode) })
        return (
        <Tip key={mode} label={label} side="left">
          <Button
            size="icon"
            className="w-full"
            onClick={() => alignSelectedTo(mode, 'page')}
            aria-label={label}
          >
            <Icon size={14} />
          </Button>
        </Tip>
        )
      })}
    </div>
  )
}

/**
 * 排列：紧凑无外框工具带。单选面板只补层级（对齐已在位置组里），
 * 其他单选给「对齐到画布 + 层级」，多选给对齐 / 分布 / 层级；
 * 参照、间距、成组与样式搬运等次级项收进「更多排列」。
 */
export function ArrangeSection({
  count,
  multi = false,
  zOnly = false,
}: {
  count: number
  multi?: boolean
  zOnly?: boolean
}) {
  useTranslation('inspector')
  const [moreOpen, setMoreOpen] = useState(false)
  const zRow = (
    <div role="toolbar" aria-label={ar('zorderLabel')} className="grid grid-cols-6 gap-0.5">
      {ZORDER.map(({ move, icon: Icon, key, shortcut }) => {
        const tip = ar(key)
        return (
          <Tip key={move} label={tip} shortcut={shortcut} side="left">
            <Button
              size="icon"
              className="w-full"
              onClick={() => changeZOrder(move)}
              aria-label={tip}
            >
              <Icon size={14} />
            </Button>
          </Tip>
        )
      })}
    </div>
  )

  if (zOnly) {
    return <Section title={ar('zorderLabel')}>{zRow}</Section>
  }

  return (
    <>
      {/* `data-arrange-section`：浮动栏「更多」滚到这里；属性页没有 section 路由 */}
      <Section
        title={multi ? ar('titleMulti', { count }) : ar('title')}
        className="scroll-mt-2"
        data-arrange-section=""
      >
        <div className="flex flex-col gap-1.5">
          {multi ? <MultiAlignRows count={count} /> : <AlignToCanvasRow />}
          {zRow}
        </div>
      </Section>
      {multi && (
        <Disclosure title={ar('more')} open={moreOpen} onToggle={() => setMoreOpen((v) => !v)}>
          <MultiArrangeExtras />
        </Disclosure>
      )}
    </>
  )
}

function MultiAlignRows({ count }: { count: number }) {
  useTranslation('inspector')
  // 对齐参照与画布上的多选浮动栏共用 arrangeStore：这边切了那边当场就是新值
  const ref = useArrangeStore((s) => s.alignRef)
  const setRef = useArrangeStore((s) => s.setAlignRef)

  return (
    <>
      <Segmented
        className="w-full"
        tone="quiet"
        ariaLabel={ar('refLabel')}
        value={ref}
        onChange={setRef}
        items={ALIGN_REFS.map((r) => ({
          value: r,
          label: alignRefLabel(r),
          tip: ar(`refTip.${r}`),
        }))}
      />

      <div role="toolbar" aria-label={ar('alignToolbar')} className="grid grid-cols-6 gap-0.5">
        {ALIGN_BUTTONS.map(({ mode, icon: Icon }) => {
          const tip = alignModeLabel(mode)
          return (
            <Tip
              key={mode}
              label={ar('alignRelativeRef', { mode: tip, ref: alignRefLabel(ref) })}
              side="left"
            >
              <Button
                size="icon"
                className="w-full"
                onClick={() => alignSelectedTo(mode, ref)}
                aria-label={tip}
              >
                <Icon size={14} />
              </Button>
            </Tip>
          )
        })}
      </div>

      <div
        role="toolbar"
        aria-label={ar('distributeToolbar')}
        className="grid grid-cols-6 gap-0.5"
      >
        {DISTRIBUTE.map(({ mode, icon: Icon, tipKey, min }) => {
          const tip = tipKey ? ar(tipKey) : alignModeLabel(mode)
          return (
            <Tip
              key={mode}
              label={
                mode === 'samew' || mode === 'sameh'
                  ? ar('alignRelativeRef', { mode: tip, ref: alignRefLabel(ref) })
                  : tip
              }
              side="left"
            >
              <Button
                size="icon"
                className="w-full"
                disabled={count < min}
                onClick={() => alignSelectedTo(mode, ref)}
                aria-label={tip}
              >
                <Icon size={14} />
              </Button>
            </Tip>
          )
        })}
      </div>
    </>
  )
}

function MultiArrangeExtras() {
  useTranslation('inspector')
  const objs = useSelectedObjects()
  // 样式剪贴板不在 store 里（不属于文档），复制后自己触发一次重渲染
  const [, bump] = useState(0)
  const clip = styleClipKind()
  const grouped = selectionHasGroup()

  return (
    <div className="flex flex-col gap-1.5">
      <Row label={ar('spacing')}>
        <NumberField
          className="min-w-0 flex-1"
          prefix="H"
          suffix="mm"
          step={0.5}
          precision={1}
          value={spacingOf(objs, 'x') ?? 0}
          mixed={spacingOf(objs, 'x') === undefined}
          title={ar('spacingHTitle')}
          onChange={(v) => setSelectionSpacing('x', v)}
        />
        <NumberField
          className="min-w-0 flex-1"
          prefix="V"
          suffix="mm"
          step={0.5}
          precision={1}
          value={spacingOf(objs, 'y') ?? 0}
          mixed={spacingOf(objs, 'y') === undefined}
          title={ar('spacingVTitle')}
          onChange={(v) => setSelectionSpacing('y', v)}
        />
      </Row>

      <div className="flex gap-1.5">
        <Tip label={ar('groupTip')}>
          <Button variant="outline" size="sm" className="flex-1" onClick={groupSelected}>
            <Group size={13} />
            {ar('group')}
          </Button>
        </Tip>
        <Tip label={ar('ungroupTip')}>
          <Button
            variant="outline"
            size="sm"
            className="flex-1"
            disabled={!grouped}
            onClick={ungroupSelected}
          >
            <Ungroup size={13} />
            {ar('ungroup')}
          </Button>
        </Tip>
      </div>

      <LayoutGroupControls />

      <div className="flex gap-1.5">
        <Tip label={ar('copyStyleTip')}>
          <Button
            variant="outline"
            size="sm"
            className="flex-1"
            onClick={() => {
              copySelectionStyle()
              bump((n) => n + 1)
            }}
          >
            <Clipboard size={13} />
            {ar('copyStyle')}
          </Button>
        </Tip>
        <Tip
          label={
            clip
              ? ar('pasteStyleTip', { kind: translate(`objectType.${clip}`) })
              : ar('pasteStyleEmpty')
          }
        >
          <Button
            variant="outline"
            size="sm"
            className="flex-1"
            disabled={!clip}
            onClick={pasteSelectionStyle}
          >
            <ClipboardPaste size={13} />
            {ar('pasteStyle')}
          </Button>
        </Tip>
      </div>
    </div>
  )
}

/**
 * 结构化布局组：行 / 列 / 网格约束。创建即按阅读顺序排一次；
 * 之后改间距 / 列数 / 对齐即时重排，替换素材、改面板比例后自动归位。
 * 成员可单独「固定位置」不随重排。
 */
function LayoutGroupControls() {
  useTranslation('inspector')
  const selIds = useSelectionStore((s) => s.ids)
  const group = useDocumentStore((s): LayoutGroup | null => {
    const gs = s.doc.layoutGroups
    if (!gs?.length) return null
    return (
      gs.find((g) => s.doc.objects.some((o) => selIds.includes(o.id) && o.groupId === g.id)) ??
      null
    )
  })
  const anyPinned = useDocumentStore((s) =>
    s.doc.objects.some((o) => selIds.includes(o.id) && o.layoutPinned),
  )

  if (!group) {
    return (
      <div className="mt-0.5">
        <Row label={ar('layoutGroup')}>
          <div className="flex min-w-0 flex-1 gap-1">
            {(['row', 'col', 'grid'] as const).map((kind) => {
              const label = ar(
                kind === 'row' ? 'layoutRow' : kind === 'col' ? 'layoutCol' : 'layoutGrid',
              )
              return (
              <Tip key={kind} label={ar('createLayoutTip', { kind: label })}>
                <Button
                  variant="outline"
                  size="sm"
                  className="flex-1"
                  onClick={() => createLayoutGroup(kind)}
                >
                  {label}
                </Button>
              </Tip>
              )
            })}
          </div>
        </Row>
      </div>
    )
  }

  return (
    <div className="mt-0.5 flex flex-col gap-1.5 rounded-sm border border-border p-1.5">
      <Row label={ar('layout')}>
        <Segmented
          className="w-full"
          value={group.kind}
          onChange={(kind) => updateLayoutGroup(group.id, { kind })}
          items={[
            { value: 'row', label: ar('layoutRow') },
            { value: 'col', label: ar('layoutCol') },
            { value: 'grid', label: ar('layoutGrid') },
          ]}
        />
      </Row>
      <Row label={ar('spacing')}>
        <NumberField
          value={group.gap}
          min={0}
          max={50}
          step={0.5}
          suffix="mm"
          onChange={(gap) => updateLayoutGroup(group.id, { gap })}
        />
        {group.kind === 'grid' && (
          <NumberField
            prefix={ar('columns')}
            value={group.cols ?? 2}
            min={1}
            max={8}
            step={1}
            onChange={(cols) => updateLayoutGroup(group.id, { cols: Math.round(cols) })}
          />
        )}
      </Row>
      <Row label={ar('align')}>
        <Segmented
          className="w-full"
          tone="quiet"
          value={group.align}
          onChange={(align) => updateLayoutGroup(group.id, { align })}
          items={[
            { value: 'start', label: ar(group.kind === 'col' ? 'alignStartCol' : 'alignStartRow') },
            { value: 'center', label: ar('alignCenter') },
            { value: 'end', label: ar(group.kind === 'col' ? 'alignEndCol' : 'alignEndRow') },
          ]}
        />
      </Row>
      <Row label={ar('uniform')}>
        <Segmented
          className="w-full"
          tone="quiet"
          value={group.uniform ?? 'none'}
          onChange={(v) =>
            updateLayoutGroup(group.id, { uniform: v === 'none' ? null : (v as 'width' | 'height') })
          }
          items={[
            { value: 'none', label: ar('uniformNone') },
            { value: 'width', label: ar('uniformWidth') },
            { value: 'height', label: ar('uniformHeight') },
          ]}
        />
      </Row>
      <label className="flex items-center gap-1.5 text-xs text-ink-2">
        <Toggle checked={anyPinned} onChange={() => toggleLayoutPinned(selIds)} />
        {ar('pinMembers')}
      </label>
      <div className="flex gap-1.5">
        <Button
          variant="outline"
          size="sm"
          className="flex-1"
          title={ar('reflowTitle')}
          onClick={() => reflowLayoutGroup(group.id)}
        >
          {ar('reflow')}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="flex-1"
          title={ar('dissolveTitle')}
          onClick={() => dissolveLayoutGroup(group.id)}
        >
          {ar('dissolve')}
        </Button>
      </div>
    </div>
  )
}

/** 当前相邻间距；对象间距不一致时返回 undefined（输入框显示「多个值」） */
function spacingOf(objs: CanvasObject[], axis: 'x' | 'y'): number | undefined {
  if (objs.length < 2) return undefined
  const s = axis === 'x' ? 'w' : 'h'
  const sorted = objs.slice().sort((a, b) => a[axis] - b[axis])
  const gaps: number[] = []
  for (let i = 1; i < sorted.length; i++) {
    gaps.push(sorted[i][axis] - (sorted[i - 1][axis] + sorted[i - 1][s]))
  }
  return gaps.every((g) => Math.abs(g - gaps[0]) < 0.01) ? Math.round(gaps[0] * 100) / 100 : undefined
}

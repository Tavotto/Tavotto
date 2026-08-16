import { useState } from 'react'
import {
  AlignCenterHorizontal,
  AlignCenterVertical,
  AlignEndHorizontal,
  AlignEndVertical,
  AlignHorizontalDistributeCenter,
  AlignStartHorizontal,
  AlignStartVertical,
  AlignVerticalDistributeCenter,
  ArrowDownToLine,
  ArrowUpToLine,
  Clipboard,
  ClipboardPaste,
  Group,
  MoveDown,
  MoveUp,
  MoveHorizontal,
  MoveVertical,
  Ungroup,
} from 'lucide-react'
import type { AlignMode } from '@/lib/geometry'
import { MOD } from '@/lib/utils'
import {
  ALIGN_REF_LABEL,
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
  type AlignRef,
  type ZMove,
} from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import type { CanvasObject, LayoutGroup } from '@/types/document'
import { Button } from '../ui/Button'
import { Disclosure, Row, Section } from '../ui/Field'
import { NumberField } from '../ui/Input'
import { Segmented } from '../ui/Segmented'
import { Toggle } from '../ui/Toggle'
import { Tip } from '../ui/Tooltip'
import { useSelectedObjects } from './common'

const ALIGN: { mode: AlignMode; icon: typeof AlignStartVertical; tip: string }[] = [
  { mode: 'left', icon: AlignStartVertical, tip: '左对齐' },
  { mode: 'hcenter', icon: AlignCenterVertical, tip: '水平居中' },
  { mode: 'right', icon: AlignEndVertical, tip: '右对齐' },
  { mode: 'top', icon: AlignStartHorizontal, tip: '顶对齐' },
  { mode: 'vcenter', icon: AlignCenterHorizontal, tip: '垂直居中' },
  { mode: 'bottom', icon: AlignEndHorizontal, tip: '底对齐' },
]

const DISTRIBUTE: { mode: AlignMode; icon: typeof MoveUp; tip: string; min: number }[] = [
  { mode: 'hdist', icon: AlignHorizontalDistributeCenter, tip: '水平等距分布（≥3 个对象）', min: 3 },
  { mode: 'vdist', icon: AlignVerticalDistributeCenter, tip: '垂直等距分布（≥3 个对象）', min: 3 },
  { mode: 'samew', icon: MoveHorizontal, tip: '等宽', min: 2 },
  { mode: 'sameh', icon: MoveVertical, tip: '等高', min: 2 },
]

const ZORDER: { move: ZMove; icon: typeof MoveUp; tip: string; shortcut?: string }[] = [
  { move: 'top', icon: ArrowUpToLine, tip: '置于顶层', shortcut: `⇧${MOD}]` },
  { move: 'up', icon: MoveUp, tip: '上移一层', shortcut: `${MOD}]` },
  { move: 'down', icon: MoveDown, tip: '下移一层', shortcut: `${MOD}[` },
  { move: 'bottom', icon: ArrowDownToLine, tip: '置于底层', shortcut: `⇧${MOD}[` },
]

const REFS: AlignRef[] = ['selection', 'page', 'primary']

/** 六向对齐，参照整个画布 —— 单选时唯一说得通的对齐 */
export function AlignToCanvasRow() {
  return (
    <div className="grid grid-cols-6 gap-0.5">
      {ALIGN.map(({ mode, icon: Icon, tip }) => (
        <Tip key={mode} label={`${tip}（相对画布）`} side="left">
          <Button
            size="icon"
            className="w-full"
            onClick={() => alignSelectedTo(mode, 'page')}
            aria-label={`${tip}（相对画布）`}
          >
            <Icon size={14} />
          </Button>
        </Tip>
      ))}
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
  const [moreOpen, setMoreOpen] = useState(false)
  const zRow = (
    <div role="toolbar" aria-label="层级" className="grid grid-cols-6 gap-0.5">
      {ZORDER.map(({ move, icon: Icon, tip, shortcut }) => (
        <Tip key={move} label={tip} shortcut={shortcut} side="left">
          <Button size="icon" className="w-full" onClick={() => changeZOrder(move)} aria-label={tip}>
            <Icon size={14} />
          </Button>
        </Tip>
      ))}
    </div>
  )

  if (zOnly) {
    return (
      <Section title="层级">
        {zRow}
      </Section>
    )
  }

  return (
    <>
      <Section title={multi ? `排列 · 已选 ${count} 个` : '排列'}>
        <div className="flex flex-col gap-1.5">
          {multi ? <MultiAlignRows count={count} /> : <AlignToCanvasRow />}
          {zRow}
        </div>
      </Section>
      {multi && (
        <Disclosure title="更多排列" open={moreOpen} onToggle={() => setMoreOpen((v) => !v)}>
          <MultiArrangeExtras />
        </Disclosure>
      )}
    </>
  )
}

/** 对齐参照是模块级共享状态：对齐行与「更多排列」都要读 */
let alignRefState: AlignRef = 'selection'

function MultiAlignRows({ count }: { count: number }) {
  const [ref, setRefLocal] = useState<AlignRef>(alignRefState)
  const setRef = (r: AlignRef) => {
    alignRefState = r
    setRefLocal(r)
  }

  const refTip: Record<AlignRef, string> = {
    selection: '以选区的包围盒为基准',
    page: '以整个画布为基准',
    primary: '以最后选中的那个对象为基准，它自己不动',
  }

  return (
    <>
      <Segmented
        className="w-full"
        tone="quiet"
        value={ref}
        onChange={setRef}
        items={REFS.map((r) => ({ value: r, label: ALIGN_REF_LABEL[r], tip: refTip[r] }))}
      />

      <div role="toolbar" aria-label="对齐" className="grid grid-cols-6 gap-0.5">
        {ALIGN.map(({ mode, icon: Icon, tip }) => (
          <Tip key={mode} label={`${tip}（${ALIGN_REF_LABEL[ref]}）`} side="left">
            <Button
              size="icon"
              className="w-full"
              onClick={() => alignSelectedTo(mode, ref)}
              aria-label={tip}
            >
              <Icon size={14} />
            </Button>
          </Tip>
        ))}
      </div>

      <div role="toolbar" aria-label="分布与统一尺寸" className="grid grid-cols-6 gap-0.5">
        {DISTRIBUTE.map(({ mode, icon: Icon, tip, min }) => (
          <Tip
            key={mode}
            label={mode === 'samew' || mode === 'sameh' ? `${tip}（${ALIGN_REF_LABEL[ref]}）` : tip}
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
        ))}
      </div>
    </>
  )
}

function MultiArrangeExtras() {
  const objs = useSelectedObjects()
  // 样式剪贴板不在 store 里（不属于文档），复制后自己触发一次重渲染
  const [, bump] = useState(0)
  const clip = styleClipKind()
  const grouped = selectionHasGroup()

  return (
    <div className="flex flex-col gap-1.5">
      <Row label="间距">
        <NumberField
          className="min-w-0 flex-1"
          prefix="H"
          suffix="mm"
          step={0.5}
          precision={1}
          value={spacingOf(objs, 'x') ?? 0}
          mixed={spacingOf(objs, 'x') === undefined}
          title="水平间距：按 X 排序后依次贴齐，第一个对象不动"
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
          title="垂直间距：按 Y 排序后依次贴齐，第一个对象不动"
          onChange={(v) => setSelectionSpacing('y', v)}
        />
      </Row>

      <div className="flex gap-1.5">
        <Tip label="成组后点其中任意一个都会整组选中、整组移动">
          <Button variant="outline" size="sm" className="flex-1" onClick={groupSelected}>
            <Group size={13} />
            成组
          </Button>
        </Tip>
        <Tip label="解散选区里的组">
          <Button
            variant="outline"
            size="sm"
            className="flex-1"
            disabled={!grouped}
            onClick={ungroupSelected}
          >
            <Ungroup size={13} />
            取消成组
          </Button>
        </Tip>
      </div>

      <LayoutGroupControls />

      <div className="flex gap-1.5">
        <Tip label="从最后选中的对象取样式：面板取裁剪 / 旋转 / 不透明度，文字取字号 / 字重 / 颜色 / 对齐">
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
            复制样式
          </Button>
        </Tip>
        <Tip
          label={
            clip
              ? `粘贴到选区里的${clip === 'panel' ? '面板' : '文字'}`
              : '还没有复制过样式'
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
            粘贴样式
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
        <Row label="布局组">
          <div className="flex min-w-0 flex-1 gap-1">
            {(
              [
                ['row', '行'],
                ['col', '列'],
                ['grid', '网格'],
              ] as const
            ).map(([kind, label]) => (
              <Tip
                key={kind}
                label={`把选区变成${label}布局：固定间距自动排列，替换素材后自动重排（可撤销）`}
              >
                <Button
                  variant="outline"
                  size="sm"
                  className="flex-1"
                  onClick={() => createLayoutGroup(kind)}
                >
                  {label}
                </Button>
              </Tip>
            ))}
          </div>
        </Row>
      </div>
    )
  }

  return (
    <div className="mt-0.5 flex flex-col gap-1.5 rounded-sm border border-border p-1.5">
      <Row label="布局">
        <Segmented
          className="w-full"
          value={group.kind}
          onChange={(kind) => updateLayoutGroup(group.id, { kind })}
          items={[
            { value: 'row', label: '行' },
            { value: 'col', label: '列' },
            { value: 'grid', label: '网格' },
          ]}
        />
      </Row>
      <Row label="间距">
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
            prefix="列"
            value={group.cols ?? 2}
            min={1}
            max={8}
            step={1}
            onChange={(cols) => updateLayoutGroup(group.id, { cols: Math.round(cols) })}
          />
        )}
      </Row>
      <Row label="对齐">
        <Segmented
          className="w-full"
          tone="quiet"
          value={group.align}
          onChange={(align) => updateLayoutGroup(group.id, { align })}
          items={[
            { value: 'start', label: group.kind === 'col' ? '左' : '上' },
            { value: 'center', label: '中' },
            { value: 'end', label: group.kind === 'col' ? '右' : '下' },
          ]}
        />
      </Row>
      <Row label="统一">
        <Segmented
          className="w-full"
          tone="quiet"
          value={group.uniform ?? 'none'}
          onChange={(v) =>
            updateLayoutGroup(group.id, { uniform: v === 'none' ? null : (v as 'width' | 'height') })
          }
          items={[
            { value: 'none', label: '不动' },
            { value: 'width', label: '等宽' },
            { value: 'height', label: '等高' },
          ]}
        />
      </Row>
      <label className="flex items-center gap-1.5 text-xs text-ink-2">
        <Toggle checked={anyPinned} onChange={() => toggleLayoutPinned(selIds)} />
        固定选中成员（不随重排）
      </label>
      <div className="flex gap-1.5">
        <Button
          variant="outline"
          size="sm"
          className="flex-1"
          title="按当前约束把全部成员归位"
          onClick={() => reflowLayoutGroup(group.id)}
        >
          重新排列
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="flex-1"
          title="移除布局约束与成组，对象位置保持现状"
          onClick={() => dissolveLayoutGroup(group.id)}
        >
          解散布局
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

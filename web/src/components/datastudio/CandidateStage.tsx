import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { cn } from '@/lib/utils'
import { basisOf } from '@/lib/dataBasis'
import { previewOf, type Candidate, type ThumbState } from '@/lib/dataFigureAdapter'
import { reducedNumbers } from '@/lib/dataFigure'
import { OptionGrid } from '@/components/inspector/controls/OptionGrid'
import { FigureThumb } from './FigureThumb'

const KNOWN_CANDIDATES = [
  'points_mean', 'box_points', 'violin_points', 'ecdf_groups', 'bar_error', 'line_points', 'line_band',
  'line_units', 'points_err', 'line_err', 'scatter_fit', 'scatter', 'hist', 'ecdf', 'density', 'box_points_single',
]

/** 出图超过这么久还没回来：换一句话，让人知道没卡住 */
const SLOW_MS = 8000

/** 出图中的状态句：正常「正在画…」，超过 8 秒换成慢提示 */
export function useDrawingLine(active: boolean): string {
  const { t } = useTranslation('ai')
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    setSlow(false)
    if (!active) return
    const id = window.setTimeout(() => setSlow(true), SLOW_MS)
    return () => window.clearTimeout(id)
  }, [active])
  return t(slow ? 'dataFigureV2.working.slow' : 'dataFigureV2.working.drawing')
}

/** 切换排最多几列（合画最多 8 张候选 = 两行） */
/** 切换排里一格的宽度（px） */
const SWITCH_CELL_PX = 150

/** 主图的最大高度：容器（临时标签的内容区，`containerType: size`）高度减去标题行 / 说明 / 依据 / 切换排与间距，一屏放得下；再小也留 240px */
const STAGE_MAX_H = 'max(240px, calc(100cqh - 250px))'
/** 多一行「为什么不是这张」小字（约 20px）时主图让出这一行，免得整块比现在更高 */
const STAGE_MAX_H_WHY_NOT = 'max(240px, calc(100cqh - 270px))'

/**
 * 主图：保持图自己的宽高比（`object-contain` 的盒子 = 图本身，不拉伸、不裁切），高度随可用高度收放。
 * 还没到时是同宽的静态骨架（图一到换上去）；取不到是 `.hatch-box` 占位。出图中那一张带扫光。
 */
function StageImage({ src, onSettled, sweeping, maxH }: { src: string; onSettled: (ok: boolean) => void; sweeping: boolean; maxH: string }) {
  const [state, setState] = useState<'wait' | 'ok' | 'bad'>('wait')
  return (
    <span data-candidate-image={state} className={cn('relative mx-auto block overflow-hidden rounded-md', state === 'ok' ? 'w-fit max-w-full' : 'aspect-16/10 w-full max-w-[860px]', state === 'bad' ? 'hatch-box' : state === 'wait' ? 'bg-surface-active' : 'bg-paper')}>
      {state !== 'bad' && (
        <img
          src={src}
          alt=""
          draggable={false}
          onLoad={() => {
            setState('ok')
            onSettled(true)
          }}
          onError={() => {
            setState('bad')
            onSettled(false)
          }}
          className={cn('block max-w-full object-contain', state === 'ok' ? 'animate-fade-in' : 'absolute inset-0 size-full opacity-0')}
          style={state === 'ok' ? { maxHeight: maxH } : undefined}
        />
      )}
      {sweeping && <span aria-hidden className="pointer-events-none absolute inset-y-0 left-0 w-1/3 animate-sweep bg-linear-to-r from-transparent via-paper/70 to-transparent" />}
    </span>
  )
}

/**
 * 候选画法：**一张主图 + 一排轻量切换**（ADR 0178 修订）。切换只是预览（不发请求、不创建），点「创建可编辑图」才出图（`onCreate`）。
 *
 *  - 主图 = 选中的那一张（默认推荐的第一张）：标题 +「推荐」+ 一句说明 + 一行「依据」（`lib/dataBasis`，按后端给的数拼；没有 facts 就没有这一行）；
 *    主按钮在标题行右端。1 张候选时不显示切换排。
 *  - 切换排：小缩略图 + 标题；选中那一项浮成白底 + `shadow-thumb` + 600（与 `OptionGrid` / `Segmented` 同一副皮，无左侧竖线）；图没到的项不可选。
 *    ←→ 在项之间移动并预览，Enter / 空格 / 点击同样只预览。成对的合画候选靠 `variant` 那一行分清。
 *  - 出图期间：主图带扫光，切换项与按钮置灰。候选没出来之前（`pending`）画骨架（1 张主图 + 3 个切换项，尺寸与最终一致）。
 */
export function CandidateStage({
  items,
  thumbs,
  pending,
  drawingId,
  previewKey,
  onPreview,
  onCreate,
  onThumb,
  purposeCode,
}: {
  /** 当前看图目的（响应的 `purpose.code`）：「你选的是……」那句要写它的文案 */
  purposeCode?: string | null
  /** 还没被去掉的候选（取不到图的已经不在里面）；第一张带「推荐」 */
  items: Candidate[]
  /** 每张缩略图各自的就绪状态 */
  thumbs: Record<string, ThumbState>
  /** 候选的文字还没回来：画骨架 */
  pending?: boolean
  drawingId: string | null
  /** 预览的那一张的画法身份（`candidateIdentity`）；null = 第一张 */
  previewKey: string | null
  onPreview: (id: string) => void
  onCreate: (id: string) => void
  /** 一张图取到了 / 取不到 */
  onThumb: (id: string, ok: boolean) => void
}) {
  const { t, i18n } = useTranslation('ai')
  const known = (c: string) => KNOWN_CANDIDATES.includes(c)
  const title = (c: string) => (known(c) ? t(`dataFigureV2.candidate.${c}.title`) : t('dataFigureV2.candidate.other.title'))
  // 带误差线 / 误差带的画法：说明里写的是这张图**实际**画的那一种（用户在「需要你确认」里改过就跟着变），没有这一项的按默认的标准差说
  // 取自表里现成误差列的两种画法（`points_err` / `line_err`）说明里写列名（用户数据，只当文本），不写误差种类（`unknown` 时没法写）
  const why = (c: string, error?: string | null, column?: string) =>
    known(c) ? t(`dataFigureV2.candidate.${c}.why`, { error: t(`dataFigureV2.errorName.${error === 'sem' || error === 'ci95' ? error : 'sd'}`), column: column ?? '' }) : t('dataFigureV2.candidate.other.why')
  const variantText = (v: NonNullable<Candidate['variant']>) => (v.kind === 'split' ? t('dataFigureV2.variant.split', { column: v.column }) : t('dataFigureV2.variant.by_file'))
  // 大表的候选带 `reduced`（整表行数 / 画出的点数）：安静的一小段灰字 + 悬停说明；没少画（小表 / kept ≥ rows）就没有
  const reducedOf = (it: Candidate) => {
    const n = reducedNumbers(it.reduced, i18n.language)
    return n ? { text: t('dataFigureV2.reduced.text', n), hint: t('dataFigureV2.reduced.hint', n) } : null
  }

  if (pending) {
    return (
      <div data-candidate-stage className="mx-auto flex max-w-[860px] flex-col gap-3">
        <div data-candidate-skeleton className="flex flex-col">
          <span aria-hidden className="block h-5 w-1/4 rounded-sm bg-surface-active" />
          <FigureThumb src={null} alt="" className="mt-3 aspect-16/10" />
          <span aria-hidden className="mt-3 block h-4 w-3/5 rounded-sm bg-surface-active" />
        </div>
        <div className="flex gap-2">
          {[0, 1, 2].map((i) => (
            <div key={i} data-candidate-skeleton className="w-36 shrink-0">
              <FigureThumb src={null} alt="" className="aspect-16/10" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  const sel = previewOf(items, thumbs, previewKey)
  if (!sel) return null
  const busy = drawingId != null
  const mine = drawingId === sel.id
  const ready = thumbs[sel.id] === 'ready'
  const red = reducedOf(sel)
  const basis = basisOf(sel)
  // 算出来的误差线缺了哪几处：只放在整句的 title 里（最多 8 个，多了加「等」），灰字本身仍是一行
  // 只有一处时句子里已经点了名，不再用 title 重复一遍
  const missingTip = basis && basis.missingFor.length > 1
    ? basis.missingFor.slice(0, 8).join(t('dataFigureV2.basis.listJoin')) + (basis.missingFor.length > 8 ? t('dataFigureV2.basis.etc') : '')
    : undefined

  return (
    <div data-candidate-stage className="mx-auto flex max-w-[860px] flex-col gap-3">
      <div>
        <div className="flex items-center gap-2">
          <span data-candidate-title className="min-w-0 text-base font-medium text-ink">{title(sel.title_code)}</span>
          {sel.id === items[0]?.id && <Badge>{t('dataFigureV2.pick.recommended')}</Badge>}
          <span className="flex-1" />
          {red && (
            <span data-candidate-reduced className="shrink-0 whitespace-nowrap text-xs tabular-nums text-ink-3" title={red.hint}>
              {red.text}
            </span>
          )}
          <Button variant="primary" size="md" data-candidate-create disabled={busy || !ready} className="shrink-0" onClick={() => onCreate(sel.id)}>
            {t('dataFigureV2.draft.create')}
          </Button>
        </div>
        {sel.variant && (
          <p data-candidate-variant={sel.variant.kind} className="mt-1 truncate text-sm font-medium text-ink-2" title={variantText(sel.variant)}>
            {variantText(sel.variant)}
          </p>
        )}
      </div>
      <StageImage key={sel.thumb} src={sel.thumb} sweeping={mine} maxH={sel.why_not ? STAGE_MAX_H_WHY_NOT : STAGE_MAX_H} onSettled={(ok) => onThumb(sel.id, ok)} />
      <div>
        <p data-candidate-why className="text-sm text-ink-2">{why(sel.why_code, sel.error, sel.error_column)}</p>
        {basis && (
          <p data-candidate-basis className="mt-1 text-xs text-ink-3" title={missingTip}>
            {t('dataFigureV2.basis.line', {
              text: [
                basis.main.map((b) => t(`dataFigureV2.basis.${b.key}`, b.values)).join(t('dataFigureV2.basis.then')),
                ...[basis.errColumn, basis.skipped, basis.errMissing].filter((x) => x != null).map((b) => t(`dataFigureV2.basis.${b.key}`, b.values)),
              ].join(t('dataFigureV2.basis.join')),
            })}
          </p>
        )}
        {/* 为什么不是这张：只有当前预览的这张带 `why_not` 才出现，独立一行、不带开头、同样的小灰字；没有就不占位 */}
        {sel.why_not && (
          <p data-candidate-whynot={sel.why_not.code} className="mt-1 text-xs text-ink-3">
            {t(`dataFigureV2.whyNot.${sel.why_not.code}`, { purpose: purposeCode ? t(`dataFigureV2.purpose.${purposeCode}`) : '' })}
          </p>
        )}
      </div>
      {items.length > 1 && (
        // 切换排 = 公共的 `OptionGrid`（选中浮成白底 + 阴影 + 600、方向键漫游即预览都是它自带的）。每格竖排：小缩略图 + 画法名 + （合画）variant 一行。
        // **永远一行**：候选多了（合画最多 8 张）就在这一排里横向滚动，不换行——换行会把这块区域撑高、把主图挤小（用户 2026-11-06：新控件不许撑大原有的区域）。
        // 单格宽 150px：外层限宽并横向滚动，内层按「候选数 × 150」定宽；候选少时这一排不撑满整行
        <div data-candidate-switch className={cn('max-w-full self-start overflow-x-auto', busy && 'opacity-60')}>
          <div style={{ width: items.length * SWITCH_CELL_PX }}>
          <OptionGrid
            value={sel.id}
            ariaLabel={t('dataFigureV2.draft.switchLabel')}
            columns={items.length}
            previewHasLabel
            framed
            cellClassName="h-auto flex-col items-stretch justify-start gap-1 p-1.5 text-left text-sm"
            onChange={onPreview}
            options={items.map((it) => ({
              value: it.id,
              label: title(it.title_code),
              // 图没到的项不可选：选了也没有东西可看；出图期间整排不可选
              disabled: busy || thumbs[it.id] !== 'ready',
              preview: (
                <>
                  <FigureThumb src={it.thumb} alt="" onSettled={(ok) => onThumb(it.id, ok)} className="aspect-16/10" />
                  <span className="truncate">{title(it.title_code)}</span>
                  {it.variant && (
                    <span data-candidate-variant={it.variant.kind} className="truncate text-xs font-normal text-ink-3" title={variantText(it.variant)}>
                      {variantText(it.variant)}
                    </span>
                  )}
                </>
              ),
            }))}
          />
          </div>
        </div>
      )}
    </div>
  )
}

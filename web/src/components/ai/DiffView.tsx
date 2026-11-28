import { useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { ChevronRight, Maximize2, RotateCcw } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { TruncateMiddle } from '@/components/ui/TruncateMiddle'
import { cn } from '@/lib/utils'
import { scriptName } from '@/store/aiStore'
import { Button, IconButton } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { CopyAction } from './CopyAction'
import { parseUnifiedDiff, type DiffRow } from './transcriptModel'

const ai = (key: string, values?: Record<string, unknown>) => translate(key, { ns: 'ai', ...(values ?? {}) })

/* ------------------------------------------------------------------ 行 */

/** 增删用语义色那一对：整行淡底（-surface）；行号用 -content；左侧 4px 变更条——新增实色、删除 1px 条纹
    （不靠颜色也能区分，色盲可读）；字级高亮用 -border 那一档（比整行底深一档） */
const ROW: Record<'add' | 'del' | 'ctx', { row: string; no: string; rail: string; word: string }> = {
  add: { row: 'bg-ok-surface', no: 'text-ok-content', rail: 'bg-ok', word: 'bg-ok-border' },
  del: {
    row: 'bg-danger-surface',
    no: 'text-danger-content',
    rail: 'bg-[repeating-linear-gradient(180deg,var(--color-danger)_0_1px,transparent_1px_3px)]',
    word: 'bg-danger-border',
  },
  ctx: { row: 'bg-surface', no: 'text-ink-3', rail: '', word: '' },
}

function DiffRows({ rows, digits }: { rows: DiffRow[]; digits: number }) {
  return (
    <pre className="w-max min-w-full font-mono text-sm">
      {rows.map((r, i) => {
        if (r.kind === 'gap') {
          return (
            <div
              key={i}
              data-diff-row="gap"
              className="flex h-[22px] items-center bg-surface-2 pl-3 font-sans text-xs text-ink-3"
            >
              {ai('diff.unchanged', { count: r.count })}
            </div>
          )
        }
        const s = ROW[r.kind]
        let body: ReactNode = r.text || ' '
        if (r.pair) {
          const [a, b] = r.pair
          body = (
            <>
              {r.text.slice(0, a)}
              <span data-diff-word className={cn('rounded-xs', s.word)}>
                {r.text.slice(a, b)}
              </span>
              {r.text.slice(b)}
            </>
          )
        }
        return (
          <div key={i} data-diff-row={r.kind} className={cn('flex h-[22px] items-center', s.row)}>
            {/* 变更条与行号粘在左边：横向滚长行时仍看得到「这一行是增是删、第几行」 */}
            <span aria-hidden className={cn('sticky left-0 flex h-full shrink-0 items-stretch', s.row)}>
              <span className={cn('w-1 shrink-0', s.rail)} />
              <span
                className={cn('pl-2 pr-2.5 text-right text-xs tabular-nums leading-[22px]', s.no)}
                style={{ minWidth: `${digits + 2.5}ch` }}
              >
                {r.no}
              </span>
            </span>
            <span className="whitespace-pre pr-3 text-ink">{body}</span>
          </div>
        )
      })}
    </pre>
  )
}

/* ------------------------------------------------------------------ 卡 */

/**
 * 改了脚本 = **显著卡**（2026-10-07 设计审计 §6.2 / §6.4，宪法第十八节「2026-10-07 重做」）：1px 边框 12px 圆角；40px 文件头
 * 「图标 · **已修改** · 文件名（等宽，中段省略）· 右侧 +N −N · 竖线 · 状态 ✓」。操作（回滚 / 放大 / 复制补丁）
 * hover 或键盘聚焦时**替换** +N −N 的位置，不占常驻空间；文件头本身可点，收起 / 展开下面的行。
 * 行 22px、整行淡底、左侧 4px 粘性变更条、tabular 行号；hunk 之间是「⋯ 未改动 N 行」的分隔。
 * 放大后在 xl 对话框里看完整 diff。
 */
export function DiffView({
  diff,
  script,
  onRevert,
  fill = false,
  variant = 'card',
}: {
  diff: string
  script?: string
  /** 给了就在文件头放「回滚」（会话里的那张卡）；对话框与历史里不给 */
  onRevert?: () => void | Promise<unknown>
  /** 占满父容器的可用高度、在里面滚动（弹窗里的页签用）；缺省是会话里的紧凑卡（正文最高 `max-h-52`） */
  fill?: boolean
  /** `'panel'` = 弹窗里的整页形态：标题栏只留文件名、+N −N、常驻「复制补丁」；不收起、不放大、不画「已修改」；正文占满父容器、可滚动（长行横向滚）。隐含 `fill` */
  variant?: 'card' | 'panel'
}) {
  useTranslation('ai')
  const panel = variant === 'panel'
  const [open, setOpen] = useState(true)
  const [zoom, setZoom] = useState(false)
  const rows = useMemo(() => parseUnifiedDiff(diff), [diff])
  const added = rows.filter((r) => r.kind === 'add').length
  const removed = rows.filter((r) => r.kind === 'del').length
  const digits = String(rows.reduce((m, r) => (r.kind === 'gap' ? m : Math.max(m, r.no)), 0)).length
  const name = script ? scriptName(script) : ai('diff.title')
  const counts = (
    <>
      <span className="type-number text-ok">+{added}</span>
      <span className="type-number text-danger">−{removed}</span>
    </>
  )

  return (
    <>
      <div
        data-ai-diff
        data-ai-diff-variant={variant}
        className={cn('overflow-hidden rounded-lg border border-border bg-surface', (fill || panel) && 'flex max-h-full min-h-0 flex-col')}
      >
        {panel ? (
          <div className="flex h-10 shrink-0 items-center gap-2 pl-3 pr-2">
            <TruncateMiddle text={name} tail={8} className="min-w-0 flex-1 font-mono text-sm text-ink-2" />
            <span data-ai-diff-counts className="flex shrink-0 items-center gap-1.5">
              {counts}
            </span>
            <CopyAction data-ai-diff-copy text={diff} label={ai('diff.copyPatch')} copiedLabel={ai('code.copied')} />
          </div>
        ) : (
          // 整条标题栏（操作按钮除外）都点得动：收起 / 展开；常驻一个小箭头
          <div
            data-ai-diff-header
            className="group flex h-10 items-center gap-2 pl-3 pr-2"
            onClick={() => setOpen((v) => !v)}
          >
            <button
              type="button"
              data-ai-diff-toggle
              aria-expanded={open}
              className="flex h-full min-w-0 flex-1 items-center gap-2 rounded-sm text-left outline-none focus-visible:focus-ring"
            >
              <ChevronRight
                size={ICON_SIZE.sm}
                aria-hidden
                className={cn('shrink-0 text-ink-3 transition-transform duration-fast', open && 'rotate-90')}
              />
              <span className="shrink-0 text-base font-semibold text-ink">{ai('diff.edited')}</span>
              <TruncateMiddle text={name} tail={8} className="min-w-0 font-mono text-sm text-ink-2" />
            </button>
            {/* 计数与操作叠在同一格：常态是计数，hover / 键盘进入时换成操作（操作始终可 Tab 到）。计数只是展示，不接指针，免得盖住下面的按钮 */}
            <span className="grid shrink-0 items-center justify-items-end" onClick={(e) => e.stopPropagation()}>
              <span
                data-ai-diff-counts
                className="pointer-events-none col-start-1 row-start-1 flex items-center gap-1.5 transition-opacity duration-fast group-focus-within:opacity-0 group-hover:opacity-0"
              >
                {counts}
              </span>
              <span
                data-ai-diff-actions
                className="col-start-1 row-start-1 flex items-center gap-0.5 opacity-0 transition-opacity duration-fast group-focus-within:opacity-100 group-hover:opacity-100"
              >
                {onRevert && (
                  <IconButton
                    data-ai-revert
                    label={ai('panel.revert')}
                    iconSize="xs"
                    variant="danger"
                    side="top"
                    onClick={onRevert}
                  >
                    <RotateCcw size={ICON_SIZE.sm} />
                  </IconButton>
                )}
                <CopyAction
                  data-ai-diff-copy
                  text={diff}
                  label={ai('diff.copyPatch')}
                  copiedLabel={ai('code.copied')}
                />
                <IconButton
                  data-ai-diff-zoom
                  label={ai('diff.zoomTip')}
                  iconSize="xs"
                  side="top"
                  className="text-ink-3 hover:text-ink"
                  onClick={() => setZoom(true)}
                >
                  <Maximize2 size={ICON_SIZE.sm} />
                </IconButton>
              </span>
            </span>
          </div>
        )}
        {(open || panel) && (
          <div
            data-ai-diff-body
            className={cn('overflow-auto border-t border-border', fill || panel ? 'min-h-0 flex-1' : 'max-h-52')}
          >
            <DiffRows rows={rows} digits={digits} />
          </div>
        )}
      </div>

      <Dialog
        open={zoom}
        onOpenChange={setZoom}
        title={ai('diff.title')}
        description={`${script ? scriptName(script) : ''} · +${added} −${removed}`}
        size="xl"
        footer={
          <Button variant="secondary" size="lg" onClick={() => setZoom(false)}>
            {translate('actions.close')}
          </Button>
        }
      >
        <div className="max-h-[58vh] overflow-auto rounded-md border border-border bg-surface">
          <DiffRows rows={rows} digits={digits} />
        </div>
      </Dialog>
    </>
  )
}

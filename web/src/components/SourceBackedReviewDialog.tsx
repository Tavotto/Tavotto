import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { useSourceBackedReview, type ReviewRequest } from '@/store/sourceBackedReview'

const sr = (key: string, values?: Record<string, unknown>) =>
  translate(`sourceReview.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 差异比例 → 一句话里的百分数；非零但小到四舍五入成 0 时写「<0.01」，绝不写成「0」 */
export function percentText(ratio: number, differingPixels: number): string {
  const pct = ratio * 100
  if (differingPixels > 0 && pct < 0.01) return '<0.01'
  if (pct >= 10) return String(Math.round(pct))
  return String(Number(pct.toFixed(2)))
}

/**
 * 来源驱动采用的对照确认（ADR 0139）：左原 PNG（磁盘原像素）、右重画，差异包围框框在两张图上。
 * 一句话 + 一个主按钮「以重画为基础编辑」，次要「保留原图」；Esc / 点外面 = 保留原图（什么都不写）。
 * 对话框只回答是 / 否，写 guard 的事务在 `actions.ts`。
 */
export function SourceBackedReviewDialog() {
  useTranslation('dialogs')
  const request = useSourceBackedReview((s) => s.request)
  // 每一次对照各有一份加载状态（key 换了整份重来）
  return request ? <Review key={request.panelId + request.reconstructionSrc.length + request.originalSrc} request={request} /> : null
}

type Load = 'pending' | 'ok' | 'failed'

function Review({ request }: { request: ReviewRequest }) {
  const answer = useSourceBackedReview((s) => s.answer)
  const [load, setLoad] = useState<{ original: Load; redrawn: Load }>({ original: 'pending', redrawn: 'pending' })
  // 没看到两张图就不许确认：任一张还在加载（pending）或没加载出来（failed）都 fail closed
  const ready = load.original === 'ok' && load.redrawn === 'ok'
  const failed = load.original === 'failed' || load.redrawn === 'failed'
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && answer(false)}
      title={sr('title')}
      description={failed ? sr('imageFailed') : sr(request.mode === 'changed' ? 'descriptionChanged' : 'description', {
        percent: percentText(request.verdict.metrics.differingRatio, request.verdict.metrics.differingPixels),
      })}
      size="xl"
      anchor="source-review"
      footer={{
        secondary: (
          <Button data-source-review-keep variant="secondary" size="lg" onClick={() => answer(false)}>
            {sr('keepOriginal')}
          </Button>
        ),
        primary: (
          <Button data-source-review-confirm variant="primary" size="lg" disabled={!ready} onClick={() => { if (ready) answer(true) }}>
            {sr('confirm')}
          </Button>
        ),
      }}
    >
      <Compare request={request} onLoad={(kind, state) => setLoad((l) => (l[kind] === state ? l : { ...l, [kind]: state }))} />
    </Dialog>
  )
}

function Compare({ request, onLoad }: { request: ReviewRequest; onLoad: (kind: 'original' | 'redrawn', state: Load) => void }) {
  // 包围框是原图像素坐标：用原图加载后的自然尺寸换成百分比
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  const bbox = request.verdict.metrics.bbox
  const box = bbox && size
    ? {
        left: `${(bbox[0] / size.w) * 100}%`,
        top: `${(bbox[1] / size.h) * 100}%`,
        width: `${((bbox[2] - bbox[0]) / size.w) * 100}%`,
        height: `${((bbox[3] - bbox[1]) / size.h) * 100}%`,
      }
    : null
  const pane = (kind: 'original' | 'redrawn', src: string) => (
    <figure className="flex min-w-0 flex-1 flex-col gap-1.5" data-source-review-pane={kind}>
      <figcaption className="text-sm text-ink-2">{sr(kind)}</figcaption>
      <div className="relative overflow-hidden rounded-sm border border-border bg-paper">
        <img
          src={src}
          alt={sr(kind === 'original' ? 'originalAlt' : 'redrawnAlt')}
          className="block h-auto w-full select-none"
          draggable={false}
          onLoad={(e) => {
            if (kind === 'original') setSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })
            onLoad(kind, 'ok')
          }}
          onError={() => onLoad(kind, 'failed')}
        />
        {box && (
          <div
            data-source-review-diff={kind}
            aria-hidden="true"
            className="pointer-events-none absolute border-2 border-warn"
            style={box}
          />
        )}
      </div>
    </figure>
  )
  return (
    <div className="flex flex-col gap-3 sm:flex-row" data-source-review={request.mode}>
      {pane('original', request.originalSrc)}
      {pane('redrawn', request.reconstructionSrc)}
    </div>
  )
}

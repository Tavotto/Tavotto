import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { Maximize2 } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { cn } from '@/lib/utils'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { Tip } from '../ui/Tooltip'

type LineKind = 'add' | 'del' | 'hunk' | 'meta' | 'ctx'

function classify(line: string): LineKind {
  if (line.startsWith('+++') || line.startsWith('---')) return 'meta'
  if (line.startsWith('@@')) return 'hunk'
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'del'
  return 'ctx'
}

/** 增删只用语义色那一对（实色字 + 淡底，2026-09-15 学 Beautiful UI 的 Diff Table）：
    此前是四个只在这里出现的自造色，与 ok / danger 徽章两套绿红并存 */
const STYLES: Record<LineKind, string> = {
  add: 'bg-ok-subtle text-ok',
  del: 'bg-danger-subtle text-danger',
  hunk: 'text-ink-3',
  meta: 'text-ink-3',
  ctx: 'text-ink-2',
}

function DiffBody({ lines, maxH }: { lines: string[]; maxH: string }) {
  return (
    <div className={cn('overflow-auto', maxH)}>
      <pre className="w-max min-w-full font-mono text-xs leading-[1.5]">
        {lines.map((line, i) => (
          <div key={i} className={cn('whitespace-pre px-2', STYLES[classify(line)])}>
            {line || ' '}
          </div>
        ))}
      </pre>
    </div>
  )
}

/** unified diff：侧栏里给个紧凑预览，放大后在对话框里完整看 */
export function DiffView({ diff, script }: { diff: string; script?: string }) {
  useTranslation('ai')
  const [open, setOpen] = useState(false)
  const lines = useMemo(() => diff.replace(/\n$/, '').split('\n'), [diff])
  const added = lines.filter((l) => classify(l) === 'add').length
  const removed = lines.filter((l) => classify(l) === 'del').length

  return (
    <>
      {/* 住在会话卡里：卡里不再套第二张卡，hairline 框就够（Beautiful UI 的 Code Block 也是
          头部一行 + 正文，只是它自己就是卡）。行内计数只上字色不加底——底是给整块状态的 */}
      <div className="overflow-hidden rounded-sm border border-border bg-surface">
        <div className="flex items-center gap-2 border-b border-border px-2 py-1">
          <span className="text-xs font-medium text-ink">{translate('diff.title', { ns: 'ai' })}</span>
          {/* 计数是数值读数，不是代码：`type-number`（系统字体 + tabular-nums，第六节） */}
          <span className="type-number ml-auto text-ok">+{added}</span>
          <span className="type-number text-danger">−{removed}</span>
          <Tip label={translate('diff.zoomTip', { ns: 'ai' })}>
            <Button size="icon-sm" className="-mr-1" onClick={() => setOpen(true)} aria-label={translate('diff.zoomAria', { ns: 'ai' })}>
              <Maximize2 size={ICON_SIZE.sm} />
            </Button>
          </Tip>
        </div>
        <DiffBody lines={lines} maxH="max-h-52" />
      </div>

      <Dialog
        open={open}
        onOpenChange={setOpen}
        title={translate('diff.title', { ns: 'ai' })}
        description={`${script ?? ''} · +${added} −${removed}`}
        width={760}
        footer={
          <Button variant="secondary" size="md" onClick={() => setOpen(false)}>
            {translate('actions.close')}
          </Button>
        }
      >
        <div className="overflow-hidden rounded-sm border border-border bg-surface">
          <DiffBody lines={lines} maxH="max-h-[58vh]" />
        </div>
      </Dialog>
    </>
  )
}

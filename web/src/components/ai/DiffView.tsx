import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { Maximize2 } from 'lucide-react'
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

/** 低饱和的增删配色：能分辨即可，不跟界面抢注意力 */
const ADD = '#4C6B55'
const DEL = '#96594C'

const STYLES: Record<LineKind, string> = {
  add: 'bg-[#EEF3EF] text-[#4C6B55]',
  del: 'bg-[#F6EEEC] text-[#96594C]',
  hunk: 'bg-surface-2 text-ink-3',
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
      <div className="overflow-hidden rounded-sm border border-border">
        <div className="flex items-center gap-2 border-b border-border bg-surface-2 px-2 py-1">
          <span className="text-xs text-ink-2">{translate('diff.title', { ns: 'ai' })}</span>
          <span className="ml-auto font-mono text-xs" style={{ color: ADD }}>
            +{added}
          </span>
          <span className="font-mono text-xs" style={{ color: DEL }}>
            −{removed}
          </span>
          <Tip label={translate('diff.zoomTip', { ns: 'ai' })}>
            <Button size="icon-sm" className="-mr-1 h-5 w-5" onClick={() => setOpen(true)} aria-label={translate('diff.zoomAria', { ns: 'ai' })}>
              <Maximize2 size={11} />
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
          <Button variant="outline" size="md" onClick={() => setOpen(false)}>
            {translate('actions.close')}
          </Button>
        }
      >
        <div className="overflow-hidden rounded-sm border border-border">
          <DiffBody lines={lines} maxH="max-h-[58vh]" />
        </div>
      </Dialog>
    </>
  )
}

import { insertPreset, insertSymbol, PRESETS, SYMBOLS } from '@/lib/presets'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'

/**
 * 科研预设：组合插入既有对象（箭头/形状/文字成组）+ 常用符号。
 * 点击即插入到视口中心并关闭；全部可 ⌘Z 撤销。
 */
export function PresetsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()} title="科研预设" size="md">
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-1.5">
          {PRESETS.map((p) => (
            <Button
              key={p.id}
              variant="outline"
              size="md"
              className="justify-start"
              title={p.hint}
              onClick={() => {
                insertPreset(p.id)
                onClose()
              }}
            >
              {p.label}
            </Button>
          ))}
        </div>
        <div>
          <h3 className="mb-1 text-xs font-medium text-ink-2">常用符号</h3>
          <div className="grid grid-cols-8 gap-0.5">
            {SYMBOLS.map((s) => (
              <button
                key={s}
                onClick={() => {
                  insertSymbol(s)
                  onClose()
                }}
                aria-label={`插入符号 ${s}`}
                className="flex h-8 items-center justify-center rounded-sm text-sm text-ink outline-none hover:bg-ink/[.055] focus-visible:focus-ring"
                style={{ fontFamily: 'var(--font-doc)' }}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      </div>
    </Dialog>
  )
}

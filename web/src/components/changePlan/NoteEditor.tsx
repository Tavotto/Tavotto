import { useState } from 'react'
import { MAX_NOTE_CHARS } from '@/store/changePlanStore'
import { cn } from '@/lib/utils'
import { Button } from '../ui/Button'
import { TextArea } from '../ui/Input'
import { cp } from './changePlanModel'

/** 点「修改后」图上一处弹出的批注框（`Popover` 的内容）：这一处的名字 + 一行输入 +「删除」「取消」「记下」 */
export function NoteEditor({
  label,
  initial,
  onSave,
  onRemove,
  onDone,
}: {
  label: string
  /** 已有的批注文字（改一条已有的批注时给；新写的没有） */
  initial?: string
  onSave: (text: string) => void
  onRemove?: () => void
  onDone: () => void
}) {
  const [text, setText] = useState(initial ?? '')
  const save = () => {
    const v = text.trim()
    if (!v) return
    onSave(v)
    onDone()
  }
  return (
    <form
      className="flex flex-col gap-2 p-1"
      data-change-plan-note-editor
      onSubmit={(e) => {
        e.preventDefault()
        save()
      }}
    >
      <span className="type-meta truncate">{label}</span>
      <TextArea
        autoFocus
        maxLength={MAX_NOTE_CHARS}
        rows={1}
        maxRows={3}
        value={text}
        aria-label={cp('dialog.notePlaceholder')}
        placeholder={cp('dialog.notePlaceholder')}
        className="focus:border-ink-2"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            save()
          }
        }}
      />
      <div className="flex items-center gap-1.5">
        {onRemove && (
          <Button
            variant="ghost"
            size="sm"
            className="-ml-2.5 text-ink-3"
            onClick={() => {
              onRemove()
              onDone()
            }}
          >
            {cp('dialog.noteDelete')}
          </Button>
        )}
        <span className="flex-1" />
        <Button variant="ghost" size="sm" onClick={onDone}>
          {cp('dialog.noteCancel')}
        </Button>
        <Button type="submit" variant="secondary" size="sm" className={cn('rounded-full inset-ring inset-ring-border', text.trim() && 'bg-surface-active font-medium inset-ring-border-strong')} disabled={!text.trim()}>
          {cp('dialog.noteSave')}
        </Button>
      </div>
    </form>
  )
}

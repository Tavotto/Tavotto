/**
 * 「已有一个独立脚本？」——上传入口的**次级**形态（从首屏主角降级）。
 *
 * 边界必须在上传**之前**就说清楚：这是单文件脚本入口，不是完整项目入口。
 * 只保留一行常驻说明，不再展开「适合 / 不适合」清单。
 * 校验链一条没动：.py 扩展名 / 256 KiB / UTF-8 / 隐私承诺 / 哈希验证
 * ——那些都在 PlaygroundApp.openFile 与既有会话层里。
 *
 * 仍然接文件拖放（拖一个 .py 到这个小区域上），但视觉上不再邀请
 * 「把整个项目拖进来」。
 */
import { useRef, useState } from 'react'
import { Button } from '@/components/ui/Button'
import { Upload } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { dropZoneClass } from '@/components/ui/dropZone'
import { cn } from '@/lib/utils'
import { pg } from '../pgText'

export function IndependentScriptUpload({ onFile }: { onFile: (f: File) => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

  return (
    <section aria-label={pg('uploadHeading')} className="w-full">
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={(e) => {
          // 在子元素之间穿行不算离开（否则接收态一闪一闪）
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false)
        }}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          const f = e.dataTransfer.files?.[0]
          if (f) onFile(f)
        }}
        data-upload-zone
        data-dragging={over || undefined}
        className={cn(
          // 平时是一块安静的白面（不画虚线，2026-10-07 设计审计 §10.4 P2）；拖着文件进来才是接收态
          'flex flex-col gap-2 rounded-lg bg-surface px-4 py-3',
          dropZoneClass(over),
        )}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <div className="min-w-0 flex-1">
            <p className="text-base font-medium text-ink">{pg('uploadHeading')}</p>
            <p className="mt-0.5 text-xs leading-relaxed text-ink-3">{pg('uploadNote')}</p>
          </div>
          <Button variant="secondary" onClick={() => inputRef.current?.click()}>
            <Upload size={ICON_SIZE.sm} aria-hidden />
            {pg('uploadButton')}
          </Button>
        </div>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept=".py"
        className="sr-only"
        aria-label={pg('uploadButton')}
        onChange={(e) => {
          const f = e.target.files?.[0]
          e.target.value = ''
          if (f) onFile(f)
        }}
      />
    </section>
  )
}

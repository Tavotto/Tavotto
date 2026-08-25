/**
 * 「已有一个独立脚本？」——上传入口的**次级**形态（从首屏主角降级）。
 *
 * 边界必须在上传**之前**就说清楚：这是单文件脚本入口，不是完整项目入口。
 * 一行说明常驻，完整的「适合 / 不适合」清单收在可访问的 disclosure 里。
 * 校验链一条没动：.py 扩展名 / 256 KiB / UTF-8 / 隐私承诺 / 哈希验证
 * ——那些都在 PlaygroundApp.openFile 与既有会话层里。
 *
 * 仍然接文件拖放（拖一个 .py 到这个小区域上），但视觉上不再邀请
 * 「把整个项目拖进来」。
 */
import { useRef, useState } from 'react'
import { Upload } from 'lucide-react'
import { PRODUCT_NAME } from '@/lib/brand'
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
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          const f = e.dataTransfer.files?.[0]
          if (f) onFile(f)
        }}
        className={cn(
          'flex flex-col gap-2 rounded-[10px] border border-dashed px-4 py-3 transition-colors',
          over ? 'border-sel bg-sel/5' : 'border-border',
        )}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-medium text-ink">{pg('uploadHeading')}</p>
            <p className="mt-0.5 text-xs leading-relaxed text-ink-3">{pg('uploadNote')}</p>
          </div>
          <button
            onClick={() => inputRef.current?.click()}
            className="flex h-7 shrink-0 items-center gap-1.5 rounded-[6px] border border-border bg-surface px-2.5 text-xs text-ink-2 transition-colors hover:border-ink-faint hover:text-ink"
          >
            <Upload size={12} aria-hidden />
            {pg('uploadButton')}
          </button>
        </div>
        <details className="text-xs text-ink-3">
          <summary className="cursor-pointer underline-offset-2 hover:text-ink hover:underline">
            {pg('uploadScope')}
          </summary>
          <div className="mt-1.5 flex flex-col gap-1 leading-relaxed">
            <p>{pg('uploadScopeGood')}</p>
            <p>{pg('uploadScopeBad')}</p>
            {/* 产品名走 brand.ts 常量，不在译文里手写（品牌唯一出处纪律） */}
            <p className="text-ink-2">{pg('uploadDesktop', { product: PRODUCT_NAME })}</p>
          </div>
        </details>
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

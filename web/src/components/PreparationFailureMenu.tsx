import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { Check, Ellipsis } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import type { PreparationReport } from '@/lib/api'
import { IconButton } from './ui/Button'
import { Menu, MenuItem } from './ui/Menu'

const pt = (key: string, values?: Record<string, unknown>) =>
  translate(`prep.${key}`, { ns: 'workspace', ...(values ?? {}) })

/** 失败卡「⋯」菜单的三件事（2026-10-28 打磨第三批 B 方案 1）：原样参数、详情、稍后 */
type Item = 'args' | 'details' | 'allDetails' | 'later'

/**
 * 错误原文：traceback 的最后一行（脚本自己的异常那一句）优先，没有 traceback 用后端主文案；
 * `frame` 是最后一帧，`full` 是完整 traceback（没有就是那一句）。与 `SessionDetails` 同一读法。
 */
export function errorTextOf(report: PreparationReport | null | undefined): {
  line: string
  frame: string | undefined
  full: string
} | null {
  const failed = report?.outcome.kind === 'failed' && report.outcome.reason !== 'dependency_preparation'
  const error = report?.result?.error
  if (!failed || !error) return null
  const traceback = error.traceback ?? ''
  const lines = traceback.split('\n').map((l) => l.trim()).filter(Boolean)
  const line = String(lines[lines.length - 1] ?? error.message ?? '')
  if (!line) return null
  const frame = lines.filter((l) => l.startsWith('File ')).pop()
  return { line, frame, full: traceback.trim() ? traceback : line }
}

/**
 * 失败卡页眉里的「⋯」：一个入口、一种写法，收走卡上所有次要的东西。
 *
 * - **悬停 / 键盘聚焦一项**，菜单左侧弹出它的具体内容（真实的参数串、真实的错误原文末几行）；
 * - **点一项**：只读的（详情）= 复制那段内容，项上与预览里说一声「已复制」，随后收菜单；
 *   「原样参数」是可编辑的（粘贴命令要用）= 在卡上原位展开既有的参数块；「稍后」= 缩成角标（不复制）。
 * 不另开一种折叠写法：展开的内容进卡体那一列（`onOpenArgs` / `onOpenAllDetails`）。
 */
export function FailureMenu({
  argsPreview,
  error,
  onOpenArgs,
  onOpenDetails,
  onLater,
}: {
  /** null = 没有可编辑的参数（不出「原样参数」项）；字符串是此刻的参数串（敏感参数传占位说明） */
  argsPreview: string | null
  /** 错误原文；null = 这张卡没有错误文本（「详情」直接原位展开详情） */
  error: ReturnType<typeof errorTextOf>
  onOpenArgs: () => void
  /** 没有可复制的错误文本时，「详情」用它原位展开 */
  onOpenDetails: () => void
  onLater: () => void
}) {
  useTranslation('workspace')
  const [open, setOpen] = useState(false)
  const [hover, setHover] = useState<Item | null>(null)
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const copyError = async () => {
    if (!error) return
    try {
      await navigator.clipboard.writeText(error.full)
    } catch {
      return // 剪贴板不可用：什么都不说，菜单照常收
    }
    setCopied(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      setCopied(false)
      setOpen(false)
    }, 900)
  }

  const preview =
    hover === 'args' && argsPreview !== null
      ? { mono: true, text: argsPreview === '' ? pt('card.noArgs') : argsPreview }
      : hover === 'details' && error
        ? { mono: true, text: error.frame ? `${error.line}\n  ${error.frame}` : error.line }
        : null

  return (
    <Menu
      align="end"
      open={open}
      onOpenChange={(v) => {
        setOpen(v)
        if (!v) {
          setHover(null)
          setCopied(false)
        }
      }}
      trigger={
        <IconButton iconSize="sm" label={pt('card.more')} data-prep-more className="text-ink-3">
          <Ellipsis size={ICON_SIZE.sm} />
        </IconButton>
      }
    >
      {preview && (
        <div
          data-prep-menu-preview
          aria-hidden
          className="pointer-events-none absolute right-full top-0 mr-2 flex max-h-44 w-64 flex-col gap-1 overflow-hidden rounded-lg bg-surface p-2.5 shadow-pop"
        >
          {copied && hover === 'details' && (
            <span className="flex items-center gap-1 text-xs text-ok" data-prep-copied>
              <Check size={ICON_SIZE.xs} aria-hidden />
              {pt('card.copied')}
            </span>
          )}
          <pre className="whitespace-pre-wrap break-all font-mono text-xs leading-4 text-ink-2">{preview.text}</pre>
        </div>
      )}
      {argsPreview !== null && (
        <MenuItem data-prep-menu-args onFocus={() => setHover('args')} onSelect={onOpenArgs}>
          {pt('card.rawArgs')}
        </MenuItem>
      )}
      <MenuItem
        data-prep-menu-details
        onFocus={() => setHover('details')}
        onSelect={(e) => {
          if (!error) return onOpenDetails()
          e.preventDefault() // 菜单留着，让「已复制」被看见
          void copyError()
        }}
      >
        {copied ? pt('card.copied') : pt('card.details')}
      </MenuItem>
      {error && (
        <MenuItem data-prep-menu-all-details onFocus={() => setHover('allDetails')} onSelect={onOpenDetails}>
          {pt('card.allDetails')}
        </MenuItem>
      )}
      <MenuItem data-prep-menu-later onFocus={() => setHover('later')} onSelect={onLater}>
        {pt('btn.later')}
      </MenuItem>
    </Menu>
  )
}

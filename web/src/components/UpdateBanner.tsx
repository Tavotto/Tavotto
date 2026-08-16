import { RefreshCw } from 'lucide-react'
import { Button } from './ui/Button'

/**
 * 构建版本不一致时的常驻细提示条。
 * 只提示不自动刷新——用户可能正在图内编辑或等 AI 跑完。
 */
export function UpdateBanner() {
  return (
    <div className="flex h-6 shrink-0 items-center gap-2 border-b border-border bg-accent-subtle px-2.5">
      <RefreshCw size={12} className="shrink-0 text-accent" />
      <span className="min-w-0 flex-1 truncate text-xs text-accent">
        工具已更新，刷新后使用新版本（当前页面仍可继续操作）
      </span>
      <Button
        size="sm"
        className="shrink-0 text-accent hover:bg-accent/10"
        onClick={() => location.reload()}
      >
        刷新
      </Button>
    </div>
  )
}

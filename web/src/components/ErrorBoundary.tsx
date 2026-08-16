import { Component, type ErrorInfo, type ReactNode } from 'react'

interface State {
  error: Error | null
}

/**
 * 全局兜底：任何组件抛错不再白屏。文档由 documentStore 的防抖自动保存
 * 兜住（localStorage），刷新即可恢复到最后一次快照。
 * 刻意不依赖 ui/ 组件——它们崩了这里还得能渲染。
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('界面崩溃:', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="flex h-screen items-center justify-center bg-bg">
        <div className="w-[420px] rounded-lg border border-border bg-surface p-5">
          <div className="mb-1 text-[13px] font-medium text-ink">界面出错了</div>
          <div className="mb-3 text-xs leading-relaxed text-ink-2">
            文档已自动保存在本机，刷新后可从最后一次快照继续。
          </div>
          <pre className="mb-4 max-h-40 overflow-auto rounded-sm border border-border bg-surface-2 px-2 py-1.5 font-mono text-[11px] leading-relaxed text-ink-2">
            {this.state.error.message}
          </pre>
          <button
            onClick={() => location.reload()}
            className="h-7 rounded-md border border-border bg-surface px-3 text-xs text-ink transition-colors hover:border-border-strong"
          >
            重新加载
          </button>
        </div>
      </div>
    )
  }
}

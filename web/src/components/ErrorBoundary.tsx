import { Component, type ErrorInfo, type ReactNode } from 'react'
import { requestBlankStart } from '@/store/documentStore'
import { t } from '@/i18n'

interface State {
  error: Error | null
}

/**
 * 全局兜底：任何组件抛错不再白屏。文档由 documentStore 的防抖自动保存
 * 兜住（localStorage），刷新即可恢复到最后一次快照。
 * 刻意不依赖 ui/ 组件——它们崩了这里还得能渲染。
 *
 * 同样刻意**不用 useTranslation**：这是 class 组件，而且它渲染的时候
 * 界面已经崩了，能少一层订阅就少一层。直接取当前语言的文本。
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // console 是开发/诊断通道，不翻译（见 docs/i18n.md 的边界一节）
    console.error('界面崩溃:', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="flex h-screen items-center justify-center bg-bg">
        <div className="w-[420px] rounded-lg border border-border bg-surface p-5">
          <div className="mb-1 text-[13px] font-medium text-ink">
            {t('crash.title', { ns: 'workspace' })}
          </div>
          <div className="mb-3 text-xs leading-relaxed text-ink-2">
            {t('crash.body', { ns: 'workspace' })}
          </div>
          <pre className="mb-4 max-h-40 overflow-auto rounded-sm border border-border bg-surface-2 px-2 py-1.5 font-mono text-[11px] leading-relaxed text-ink-2">
            {this.state.error.message}
          </pre>
          <div className="flex gap-2">
            <button
              onClick={() => location.reload()}
              className="h-7 rounded-md border border-border bg-surface px-3 text-xs text-ink transition-colors hover:border-border-strong"
            >
              {t('actions.reload')}
            </button>
            <button
              onClick={() => {
                requestBlankStart()
                location.reload()
              }}
              className="h-7 rounded-md border border-border bg-surface px-3 text-xs text-ink-2 transition-colors hover:border-border-strong hover:text-ink"
            >
              {t('crash.blank', { ns: 'workspace' })}
            </button>
          </div>
        </div>
      </div>
    )
  }
}

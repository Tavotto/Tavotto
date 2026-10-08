import { Component, type ErrorInfo, type ReactNode } from 'react'
import { requestBlankStart } from '@/store/documentStore'
import { t } from '@/i18n'
import { Button } from './ui/Button'
import { Details, Summary } from './ui/Details'

interface State {
  error: Error | null
}

/**
 * 全局兜底：任何组件抛错不再白屏。文档由 documentStore 的防抖自动保存
 * 兜住（localStorage），刷新即可恢复到最后一次快照。
 * 只依赖最朴素的几个 ui 原语（Button / Details：没有 store、没有 portal）——它们崩了这里还得能渲染。
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
    const { error } = this.state
    if (!error) return this.props.children
    const detail = [error.message, error.stack].filter(Boolean).join('\n\n')
    return (
      <div className="flex h-screen items-center justify-center bg-bg p-4">
        {/* 与对话框同一副外壳：panel 16 圆角 + dialog 投影（2026-10-07 设计审计 §10.2） */}
        <div role="alert" data-crash-screen className="flex w-[480px] max-w-full flex-col gap-3 rounded-panel bg-surface p-5 shadow-dialog">
          <h1 className="type-heading">{t('crash.title', { ns: 'workspace' })}</h1>
          <p className="type-reading text-ink-2">{t('crash.body', { ns: 'workspace' })}</p>
          <Details data-crash-details className="text-sm">
            <Summary className="h-6 text-ink-2 hover:text-ink">{t('crash.details', { ns: 'workspace' })}</Summary>
            <div className="mt-1.5 flex flex-col items-start gap-1.5">
              <pre className="max-h-40 w-full overflow-auto rounded-md bg-surface-2 px-2.5 py-2 font-mono text-xs leading-relaxed text-ink-2">
                {detail}
              </pre>
              <Button
                variant="ghost"
                size="sm"
                data-crash-copy
                onClick={() => void navigator.clipboard?.writeText(detail).catch(() => {})}
              >
                {t('actions.copy')}
              </Button>
            </div>
          </Details>
          {/* 页脚：主动作是「重新加载」（文档有自动保存兜底）；「从空白开始」会丢掉恢复的那份，
              是破坏性的另一条路——左边、危险浅底胶囊，与对话框页脚 start 槽同一规矩 */}
          <div className="mt-1 flex items-center gap-2">
            <Button
              variant="danger-tinted"
              size="lg"
              data-crash-blank
              onClick={() => {
                requestBlankStart()
                location.reload()
              }}
            >
              {t('crash.blank', { ns: 'workspace' })}
            </Button>
            <span className="flex-1" />
            <Button variant="primary" size="lg" data-crash-reload onClick={() => location.reload()}>
              {t('actions.reload')}
            </Button>
          </div>
        </div>
      </div>
    )
  }
}

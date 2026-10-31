import { Component, type ErrorInfo, type ReactNode } from 'react'
import { requestBlankStart } from '@/store/documentStore'
import { t } from '@/i18n'
import { reportUnexpectedError } from '@/lib/unexpectedErrors'
import { DiagnosticsSendHost } from './DiagnosticsSendHost'
import { SendReportButton } from './SendReportButton'
import { Button } from './ui/Button'
import { Details, Summary } from './ui/Details'
import { dragRegionProps } from '@/lib/desktop'
import { TooltipProvider } from './ui/Tooltip'
import { useDiagSendStore } from '@/store/diagSendStore'

interface State {
  error: Error | null
}

/**
 * 崩溃页里的发送对话框的**内层兜底**：对话框自己抛错时退回空，崩溃页不能二次崩（外层 ErrorBoundary 再捕获就是死循环）。
 * 故意不上报 `reportUnexpectedError`——诊断 / 发送流程自身的错不许触发提示（防递归），console 环仍会记到。
 */
class SendHostBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  componentDidCatch(error: Error) {
    console.error('发送问题反馈对话框出错:', error)
  }
  render() {
    return this.state.failed ? null : this.props.children
  }
}

/**
 * 全局兜底：任何组件抛错不再白屏。文档由 documentStore 的防抖自动保存
 * 兜住（localStorage），刷新即可恢复到最后一次快照。
 * 只依赖最朴素的几个 ui 原语（Button / Details：没有 store、没有 portal）——它们崩了这里还得能渲染。
 *
 * **放宽的边界（PR A，ADR 0118 补充 2026-10-14）只有两样**：崩溃页的「详情」里多一颗 `SendReportButton`，
 * 以及崩溃页自己渲染一份 `DiagnosticsSendHost`（App 树已死，App 里那份跟着没了）——后者包在 `TooltipProvider` 与
 * `SendHostBoundary` 里。除 `diagSendStore` 与对话框外这里仍然什么 store 都不碰；「发送问题反馈」关着
 * （`capability === null`）时两样都渲染为空，崩溃页与改动前逐元素一致。
 *
 * **只有主应用入口传 `diagnostics`**（`main.tsx`）：playground / mcp 复用这个边界，但那里没有 Flask 诊断 API，
 * 默认（不传）既不取 `/api/diagnostics/send` 的能力说明、也不渲染入口与对话框——崩溃页与接入发送功能之前一致。
 *
 * 同样刻意**不用 useTranslation**：这是 class 组件，而且它渲染的时候
 * 界面已经崩了，能少一层订阅就少一层。直接取当前语言的文本。
 */
export class ErrorBoundary extends Component<{ children: ReactNode; diagnostics?: boolean }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // console 是开发/诊断通道，不翻译（见 docs/i18n.md 的边界一节）
    console.error('界面崩溃:', error, info.componentStack)
    // 记 `ui.unexpected_error`（source=boundary）并把栈 + componentStack 补进未捕获错误环；永不抛
    reportUnexpectedError({ source: 'boundary', error, componentStack: info.componentStack ?? undefined })
    // 首次渲染就崩时 App 的 effect 没跑过、能力说明从没取过：崩溃页自己取一次（本机请求、失败即关），否则永远没有发送入口
    if (this.props.diagnostics && useDiagSendStore.getState().capability === null) void useDiagSendStore.getState().load()
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    const detail = [error.message, error.stack].filter(Boolean).join('\n\n')
    return (
      // 崩溃页取代了整棵树：macOS 叠了标题栏的窗口靠这里的空白处拖动（Codex #994）
      <div className="flex h-screen items-center justify-center bg-bg p-4" {...dragRegionProps('self')}>
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
              {this.props.diagnostics && <SendReportButton />}
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
        {this.props.diagnostics && (
          <TooltipProvider>
            <SendHostBoundary>
              <DiagnosticsSendHost />
            </SendHostBoundary>
          </TooltipProvider>
        )}
      </div>
    )
  }
}

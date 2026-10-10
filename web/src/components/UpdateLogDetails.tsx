import { useTranslation } from 'react-i18next'
import { Details, Summary } from './ui/Details'

/**
 * 更新器 / pip 原样吐出来的那段话：危险 Notice 下面一个收起的「日志」（2026-10-07 设计审计 §10.2）。
 * 默认只露一句结论；要看原文的人点开，不必在弹窗里先读一屏英文堆栈。原文仍在 DOM 里（收起而已）。
 */
export function LogDetails({ text }: { text: string }) {
  const { t } = useTranslation('dialogs')
  return (
    <Details data-update-log className="text-sm">
      <Summary className="h-6 text-ink-2 hover:text-ink">{t('updateNotice.log')}</Summary>
      <pre className="mt-1 max-h-32 overflow-y-auto whitespace-pre-wrap break-words rounded-md bg-surface-2 p-2 font-mono text-xs text-ink-2">
        {text}
      </pre>
    </Details>
  )
}

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCircle2, MinusCircle, XCircle } from 'lucide-react'
import {
  CodexShellError,
  codexErrorText,
  codexResultErrorText,
  codexStepLabel,
  codexStepStateText,
  parseCodexResult,
  type CodexResult,
  type CodexStep,
} from '@/lib/codexInstall'
import { isDesktop, runCodexIntegration } from '@/lib/desktop'
import { Button } from '../ui/Button'
import { ag } from './agentState'

/**
 * 设置 → 编码 Agent →「在编码 Agent 中使用 Tavotto」里的安装入口（issue #170）。
 *
 * ## 按钮背后是那条命令，不是第二套安装器
 *
 * 「安装」spawn `tavotto-cli codex install --json`，「重新诊断」spawn
 * `codex doctor --json`（**只诊断不改动**——它是 `apply=False` 的同一条流水线）。
 * marketplace 名、插件引用、sparse 路径一个都不在前端（ADR 0012，
 * 看护 `tests/test_desktop_codex_button.py`）。
 *
 * ## 只在桌面模式出现
 *
 * 浏览器模式下没有壳、也就没有 `tavotto-cli` 可 spawn；那一档保持原样
 * （名字 + 使用指南外链），**不画一个按不动的按钮**。
 *
 * ## 失败显示的是原因，不是 code
 *
 * `error_code` 是分诊身份，不是句子（与 `unsupported_props` 同一条纪律）。
 * 界面上出现的永远是翻译过的那一句；引擎给的 `detail` 是诊断材料
 * （路径、CLI 原文），折叠在「详情」里、不翻译。
 */
export function CodexIntegrationPanel() {
  useTranslation('dialogs')
  const [busy, setBusy] = useState<'install' | 'doctor' | null>(null)
  const [result, setResult] = useState<CodexResult | null>(null)
  const [shellCode, setShellCode] = useState<string | null>(null)
  const [announce, setAnnounce] = useState('')

  if (!isDesktop()) return null

  const run = async (action: 'install' | 'doctor') => {
    setBusy(action)
    setResult(null)
    setShellCode(null)
    try {
      const parsed = parseCodexResult(await runCodexIntegration(action))
      setResult(parsed)
      setAnnounce(parsed.ok ? ag('codexInstall.announce.ok') : ag('codexInstall.announce.failed'))
    } catch (e) {
      // 连那行 JSON 都没拿到。**保留稳定 code 用于翻译，不显示它本身。**
      setShellCode(e instanceof CodexShellError ? e.code : 'spawn_failed')
      setAnnounce(ag('codexInstall.announce.failed'))
    } finally {
      setBusy(null)
    }
  }

  const failureText = shellCode
    ? codexErrorText(shellCode)
    : result && !result.ok
      ? codexResultErrorText(result)
      : null

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          loading={busy === 'install'}
          disabled={busy !== null}
          onClick={() => void run('install')}
        >
          {busy === 'install' ? ag('codexInstall.running') : ag('codexInstall.action')}
        </Button>
        <Button
          variant="outline"
          size="sm"
          loading={busy === 'doctor'}
          disabled={busy !== null}
          onClick={() => void run('doctor')}
        >
          {ag('codexInstall.doctor')}
        </Button>
      </div>

      <p aria-live="polite" className="sr-only">
        {announce}
      </p>

      {failureText && (
        <p role="alert" className="text-xs leading-relaxed text-danger">
          {failureText}
        </p>
      )}

      {/* 装完只说这一句。**不说「已启用」**——旧会话里验不出工具来
          （引擎收尾那句的同一条理由，ADR 0012） */}
      {result?.ok && result.action === 'install' && (
        <p className="text-xs leading-relaxed text-ink-2">{ag('codexInstall.doneNewSession')}</p>
      )}
      {result?.ok && result.action === 'doctor' && (
        <p className="text-xs leading-relaxed text-ink-2">{ag('codexInstall.healthy')}</p>
      )}

      {result && result.steps.length > 0 && <StepList steps={result.steps} />}
    </div>
  )
}

/** 逐步结论：名字 + 状态（完成 / 跳过 / 失败）+ 引擎给的 detail（诊断材料，不翻） */
function StepList({ steps }: { steps: CodexStep[] }) {
  return (
    <ul className="flex flex-col gap-0.5 rounded-md border border-border bg-surface px-2 py-1.5">
      {steps.map((s, i) => {
        const Icon = !s.ok ? XCircle : s.skipped ? MinusCircle : CheckCircle2
        const tone = !s.ok ? 'text-danger' : s.skipped ? 'text-ink-3' : 'text-ink-2'
        return (
          <li key={`${s.step}-${i}`} className="flex items-start gap-1.5 text-xs">
            <Icon size={12} className={`mt-0.5 shrink-0 ${tone}`} aria-hidden />
            <span className="min-w-0">
              <span className="text-ink">{codexStepLabel(s.step)}</span>
              <span className={`ml-1 ${tone}`}>{codexStepStateText(s)}</span>
              {s.detail && (
                <span className="ml-1 break-all font-mono text-ink-3">{s.detail}</span>
              )}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

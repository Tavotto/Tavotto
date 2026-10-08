import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CircleCheck, CircleMinus, CircleX, Ellipsis, ExternalLink, RefreshCw } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
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
import { CODEX_GUIDE_URL, PRODUCT_NAME } from '@/lib/brand'
import { isDesktop, runCodexIntegration } from '@/lib/desktop'
import { Button, IconButton } from '../ui/Button'
import { buttonClass } from '../ui/buttonClass'
import { Menu, MenuItem } from '../ui/Menu'
import { DiagnosticDisclosure, GroupNotice, SettingRow } from './SettingRow'
import { ag } from './agentState'

/**
 * 设置 → 改图助手 →「连接外部工具」组（`data-agent-section="external"`）里的那一行与它的结果（issue #170）。
 * 审计 T44 改名前它叫「在编码 Agent 中使用 Tavotto」。
 *
 * ## 一行：名字 · 安装 · ⋯（2026-10-07 设计审计 §9.1）
 *
 * 「Tavotto for Codex」一行，控件列是「安装」+ ⋯（重新诊断 · 使用指南）。此前名字一行、底下另起一排左对齐的
 * 「安装 / 重新诊断」，结果的逐步清单是设置里唯一带框的列表。现在结果落在这一行下面：失败是组内 danger Notice，
 * 逐步结论是不带框的清单——全部通过时折叠成一行（读一次就够），有一步失败就直接摊开。
 *
 * ## 按钮背后是那条命令，不是第二套安装器
 *
 * 「安装」spawn `tavotto-cli codex install --json`，「重新诊断」spawn
 * `codex doctor --json`（**只诊断不改动**——它是 `apply=False` 的同一条流水线）。
 * marketplace 名、插件引用、sparse 路径一个都不在前端（ADR 0012，
 * 看护 `tests/test_desktop_codex_button.py`）。
 *
 * ## 浏览器模式只有名字 + 使用指南
 *
 * 没有壳、也就没有 `tavotto-cli` 可 spawn；那一档是名字 + 指南外链，**不画一个按不动的按钮**。
 * 「本机装了 codex CLI」仍然绝不写成「Tavotto for Codex 已安装」。
 *
 * ## 失败显示的是原因，不是 code
 *
 * `error_code` 是分诊身份，不是句子（与 `unsupported_props` 同一条纪律）。
 * 界面上出现的永远是翻译过的那一句；引擎给的 `detail` 是诊断材料
 * （路径、CLI 原文），不翻译。
 */
export function CodexIntegrationPanel() {
  useTranslation('dialogs')
  const [busy, setBusy] = useState<'install' | 'doctor' | null>(null)
  const [result, setResult] = useState<CodexResult | null>(null)
  const [shellCode, setShellCode] = useState<string | null>(null)
  const [announce, setAnnounce] = useState('')
  const desktop = isDesktop()

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
  // 装完只说这一句。**不说「已启用」**——旧会话里验不出工具来（引擎收尾那句的同一条理由，ADR 0012）
  const okText =
    result?.ok && result.action === 'install'
      ? ag('codexInstall.doneNewSession')
      : result?.ok && result.action === 'doctor'
        ? ag('codexInstall.healthy')
        : undefined
  const steps = result?.steps ?? []
  const allPassed = steps.length > 0 && steps.every((s) => s.ok)

  const guide = (
    <a
      href={CODEX_GUIDE_URL}
      target="_blank"
      rel="noreferrer"
      className={buttonClass({ variant: 'ghost', size: 'sm' })}
    >
      {ag('viewGuide')}
      <ExternalLink size={ICON_SIZE.xs} aria-hidden />
    </a>
  )

  return (
    <>
      {/* 这一行走标准设置行（全面打磨 D12）；e2e 认 `data-agent-codex-integration` */}
      <SettingRow
        data-agent-codex-integration
        data-settings-anchor="ai.codex"
        label={ag('codexIntegrationName', { product: PRODUCT_NAME })}
        status={okText}
      >
        {desktop ? (
          <>
            <Button
              variant="secondary"
              size="sm"
              loading={busy === 'install'}
              disabled={busy !== null}
              data-codex-install
              onClick={() => void run('install')}
            >
              {busy === 'install' ? ag('codexInstall.running') : ag('codexInstall.action')}
            </Button>
            <Menu
              align="end"
              width={220}
              trigger={
                <IconButton label={ag('codexInstall.more')} iconSize="sm" data-codex-more>
                  <Ellipsis size={ICON_SIZE.sm} aria-hidden />
                </IconButton>
              }
            >
              {/* 重新诊断是排障用的次级出口（只诊断不改动） */}
              <MenuItem icon={RefreshCw} disabled={busy !== null} onSelect={() => void run('doctor')}>
                {busy === 'doctor' ? ag('codexInstall.running') : ag('codexInstall.doctor')}
              </MenuItem>
              <MenuItem icon={ExternalLink} onSelect={() => window.open(CODEX_GUIDE_URL, '_blank', 'noreferrer')}>
                {ag('viewGuide')}
              </MenuItem>
            </Menu>
          </>
        ) : (
          guide
        )}
      </SettingRow>

      {desktop && (
        <p aria-live="polite" className="sr-only p-0">
          {announce}
        </p>
      )}

      {failureText && <GroupNotice tone="danger">{failureText}</GroupNotice>}

      {steps.length > 0 &&
        (allPassed ? (
          <DiagnosticDisclosure variant="row" title={ag('codexInstall.stepsTitle')} value={ag('codexInstall.stepsAllPassed')} data-codex-steps>
            <StepList steps={steps} />
          </DiagnosticDisclosure>
        ) : (
          <div data-codex-steps>
            <StepList steps={steps} />
          </div>
        ))}
    </>
  )
}

/** 逐步结论：名字 + 状态（完成 / 跳过 / 失败）+ 引擎给的 detail（诊断材料，不翻）。不带框（设置里没有带框的清单） */
function StepList({ steps }: { steps: CodexStep[] }) {
  return (
    <ul className="flex flex-col gap-1">
      {steps.map((s, i) => {
        const Icon = !s.ok ? CircleX : s.skipped ? CircleMinus : CircleCheck
        const tone = !s.ok ? 'text-danger' : s.skipped ? 'text-ink-3' : 'text-ok'
        const textTone = !s.ok ? 'text-danger-content' : s.skipped ? 'text-ink-3' : 'text-ink-2'
        return (
          <li key={`${s.step}-${i}`} className="flex items-start gap-1.5 text-sm">
            <Icon size={ICON_SIZE.sm} className={`mt-0.5 shrink-0 ${tone}`} aria-hidden />
            <span className="min-w-0">
              <span className="text-ink">{codexStepLabel(s.step)}</span>
              <span className={`ml-1 ${textTone}`}>{codexStepStateText(s)}</span>
              {s.detail && (
                <span className="ml-1 break-all font-mono text-xs text-ink-3">{s.detail}</span>
              )}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

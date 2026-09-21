import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import type { DependencyPreparationOffer, WorkdirConfirmation, WorkdirMode } from '@/lib/api'
import { useDepRepairStore } from '@/store/depRepairStore'
import { useEnvStore } from '@/store/envStore'
import { SettingRow } from './settings/SettingRow'
import { Button } from './ui/Button'
import { Segmented } from './ui/Segmented'

const en = (key: string, values?: Record<string, unknown>) =>
  translate(`engine.${key}`, { ns: 'errors', ...(values ?? {}) })

//: 三档的文案键写成字面量（i18n 死键门禁按「源码里出现过这个串」判活）
const MODE_LABEL: Record<WorkdirMode, string> = {
  sandbox: 'engine.workdirMode_sandbox',
  project: 'engine.workdirMode_project',
  project_root: 'engine.workdirMode_project_root',
}
const MODE_STATUS: Record<WorkdirMode, string> = {
  sandbox: 'engine.workdirHintSandbox',
  project: 'engine.workdirHintProject',
  project_root: 'engine.workdirHintProjectRoot',
}
const MODES: WorkdirMode[] = ['sandbox', 'project', 'project_root']

/**
 * 「脚本的运行目录」——safe worker 工作目录模式的项目级三档（ADR 0047 / 0057）：
 * 沙盒（默认）/ 脚本目录 / 项目根。
 *
 * 文案与机制逐条一致：真实目录下脚本用相对路径读的数据找得到、用相对路径写的
 * 文件落进项目目录；Tavotto 仍然不替它保存图片、不删不改项目里的文件。切到两个
 * 真实目录都要确认一次（在 envStore.setWorkdirMode 里），切回沙盒不用。
 */
export function WorkdirRow() {
  const { t } = useTranslation('errors')
  const { env, setWorkdirMode } = useEnvStore()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const project = env?.project
  if (!project?.open || !project.workdir) return null
  const mode = project.workdir.mode
  // 老服务端只有两档：它报的 `modes` 里没有第三档时不摆出来
  const available = MODES.filter((m) => (project.workdir?.modes ?? MODES).includes(m))
  const pick = async (next: WorkdirMode) => {
    setBusy(true)
    setError(await setWorkdirMode(next))
    setBusy(false)
  }
  return (
    <div className="mt-1.5 border-t border-border pt-1.5">
      {/* 标准设置行（全面打磨 D14）。当前档那句话是**现状**不是说明（§13：低调提醒走
          `status`），常驻在标题列里，不收进问号。控件整行宽：三档分段放不进定宽控件列 */}
      <SettingRow label={en('workdirLabel')} status={t(MODE_STATUS[mode])} control="fill">
        <Segmented
          value={mode}
          onChange={(v) => void pick(v)}
          ariaLabel={en('workdirLabel')}
          data-testid="setting-workdir-mode"
          items={available.map((m) => ({
            value: m,
            label: t(MODE_LABEL[m]),
            disabled: busy,
          }))}
        />
      </SettingRow>
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  )
}

/**
 * 「脚本跑完没出图」错误块里的出口：多半是沙盒 cwd 下相对路径找不到数据。
 * 已经是真实目录模式时不显示——那时原因在别处。
 */
export function WorkdirSuggestion() {
  useTranslation('errors')
  const { env, setWorkdirMode } = useEnvStore()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const project = env?.project
  if (!project?.open || project.workdir?.mode !== 'sandbox') return null
  return (
    <div className="mt-1.5 flex flex-col gap-1">
      <p className="text-xs leading-relaxed text-ink-2">{en('workdirSuggest')}</p>
      <Button
        className="self-start"
        disabled={busy}
        onClick={async () => {
          setBusy(true)
          setError(await setWorkdirMode('project'))
          setBusy(false)
        }}
      >
        {en('workdirSuggestButton')}
      </Button>
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  )
}

/**
 * 「先准备依赖」错误块里的出口（U04）：授权框被「稍后」关掉之后，从这里再打开——
 * 载荷留在渲染条目上（`PanelRender.dependencyPreparation`），这里只把它交回依赖修复 store。
 */
export function DependencyPrepareButton({ offer }: { offer: DependencyPreparationOffer | null }) {
  useTranslation('errors')
  const request = useDepRepairStore((s) => s.requestPreparation)
  if (!offer) return null
  return (
    <div className="mt-1.5 flex flex-col gap-1">
      <Button className="self-start" onClick={() => request(offer)}>
        {en('dependencyPrepareOpen')}
      </Button>
    </div>
  )
}

/**
 * 「先选运行目录」错误块里的出口（U03）：确认框被「稍后」关掉之后，从这里再打开——
 * 载荷留在渲染条目上（`PanelRender.confirmation`），这里只把它交回 envStore。
 */
export function WorkdirChooseButton({ confirmation }: { confirmation: WorkdirConfirmation | null }) {
  useTranslation('errors')
  const request = useEnvStore((s) => s.requestWorkdirConfirmation)
  if (!confirmation) return null
  return (
    <div className="mt-1.5 flex flex-col gap-1">
      <p className="text-xs leading-relaxed text-ink-2">{en('workdirChooseSuggest')}</p>
      <Button className="self-start" onClick={() => request(confirmation)}>
        {en('workdirChooseButton')}
      </Button>
    </div>
  )
}

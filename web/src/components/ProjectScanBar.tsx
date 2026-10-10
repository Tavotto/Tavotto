import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronUp, Images } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import {
  dependenciesLine,
  environmentLine,
  issueLine,
  roleLabel,
  scanBarVisible,
  scanLine,
} from '@/lib/projectScanText'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useProjectScanStore } from '@/store/projectScanStore'
import { useProjectStore } from '@/store/projectStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { scriptTarget, useProjectPreparationStore } from '@/store/projectPreparationStore'
import { preparationPanelEnabled } from '@/lib/preparationFlag'
import { useUiStore } from '@/store/uiStore'
import { Button } from './ui/Button'
import { Banner } from './DocumentBanner'

/**
 * 导入即扫描的**轻量准备条**（T02）：项目被打开后后台自动做一次只读检查，这里如实显示它读到了什么。
 *
 * ```text
 * 发现绘图脚本 plot.py。已有的图可直接排版；要编辑它生成的图，需要先准备并运行。 [详情] [重新检查] [关闭]
 * ```
 *
 * 几条界线：
 *
 * * **不阻塞**：这是一条状态条，不是对话框——不抢焦点、不挡画布、不自动弹窗；静态素材照常可排版。
 *   没有需要注意的事（静态项目 / 脚本都已连接）它根本不出现。
 * * **只读后端的快照**：phase / 目标 / 每项检查的状态都来自 `projectScanStore`，没有第二份判据；
 *   环境与依赖永远显示成「未核验」——扫描阶段不会替它们说「可以了」。
 * * **三个动作不同**：「关闭」只隐藏这条；「取消检查」只取消扫描；切项目由 store 换代。都不碰执行。
 * * **教程**：教程进行中、或当前就是教程副本时不出现——教程的状态在 `onboardingStore`，本条不读写它，
 *   也不另起一个全局「已完成」开关，更不抢焦点（避免与教程的 coachmark 双重焦点）。
 * * 选定目标后的「准备并运行」打开准备面板（T09，后端准备会话：先只读检查，确认之后才运行）；本地开关
 *   （`lib/preparationFlag`）关掉时回到旧的「试运行」（`scriptRunStore.run`）。
 */
export function ProjectScanBar() {
  const { t } = useTranslation(['workspace', 'common'])
  const scan = useProjectScanStore((s) => s.scan)
  const dismissedScanId = useProjectScanStore((s) => s.dismissedScanId)
  const forced = useProjectScanStore((s) => s.forced)
  const slow = useProjectScanStore((s) => s.slow)
  const open = useUiStore((s) => s.scanPanelOpen)
  const tutorialProject = useProjectStore((s) => s.project?.tutorial === true)
  const onboardingActive = useOnboardingStore((s) => s.status === 'active')

  if (tutorialProject || onboardingActive) return null
  if (!scanBarVisible(scan, { dismissedScanId, forced, slow }) || !scan) return null

  const running = scan.state === 'running'
  const target = scan.default_target ?? null
  const targets = (scan.targets ?? []).filter((x) => x.role !== 'auxiliary')
  const deps = dependenciesLine(scan)
  const store = useProjectScanStore.getState()
  const ui = useUiStore.getState()

  return (
    <div data-project-scan data-scan-state={scan.state} data-scan-phase={scan.phase}>
      {/* 工作面板里的提示条是 `Banner`（Notice）：说明是 title、按钮在 action 槽（与同一摞里的其它条同一副） */}
      <Banner
        icon={Images}
        title={<span data-project-scan-line>{scanLine(scan)}</span>}
        action={
          <>
            <Button
              size="sm"
              className="shrink-0 text-ink-3"
              aria-expanded={open}
              onClick={() => ui.setScanPanelOpen(!open)}
            >
              {open ? <ChevronUp size={ICON_SIZE.sm} /> : <ChevronDown size={ICON_SIZE.sm} />}
              {t('workspace:scan.details')}
            </Button>
            {running ? (
              <Button size="sm" className="shrink-0" onClick={() => void store.cancel()}>
                {t('workspace:scan.cancel')}
              </Button>
            ) : (
              <Button size="sm" className="shrink-0" onClick={() => void store.start({ force: true, reason: 'manual' })}>
                {t('workspace:scan.rescan')}
              </Button>
            )}
            <Button size="sm" className="shrink-0 text-ink-3" onClick={() => store.hide()}>
              {t('common:actions.close')}
            </Button>
          </>
        }
      />
      {open && (
        <div
          data-project-scan-details
          className="flex flex-col gap-1 border-b border-border bg-surface-2 px-3 py-2 text-xs text-ink-2"
        >
          {targets.length > 0 && (
            <ul className="flex flex-col gap-0.5">
              {targets.slice(0, 6).map((x) => (
                <li key={x.script} className="flex items-center gap-2" data-scan-target={x.script}>
                  <span className="min-w-0 flex-1 truncate text-ink">{x.script}</span>
                  <span className="shrink-0 text-ink-3">{roleLabel(x.role)}</span>
                  {x.script === target &&
                    (preparationPanelEnabled() ? (
                      // T09：选定目标 → 准备面板（后端会话，先检查、确认后才运行）
                      <Button
                        size="sm"
                        className="shrink-0"
                        data-scan-prepare={x.script}
                        onClick={() => void useProjectPreparationStore.getState().open(scriptTarget(x.script))}
                      >
                        {t('workspace:scan.prepare')}
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        className="shrink-0"
                        onClick={() => void useScriptRunStore.getState().run(x.script)}
                      >
                        {t('workspace:scan.run')}
                      </Button>
                    ))}
                </li>
              ))}
            </ul>
          )}
          {scan.issues.map((i) => (
            <div key={`${i.code}:${i.path ?? ''}`} data-scan-issue={i.code}>
              {issueLine(i)}
            </div>
          ))}
          {scan.environment && <div data-scan-env>{environmentLine(scan)}</div>}
          {deps && <div data-scan-deps>{deps}</div>}
          <div className="flex gap-2">
            <Button size="sm" onClick={() => ui.setLeftTab('assets')}>
              {t('workspace:scan.openAssets')}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

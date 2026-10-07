import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { i18n, t as translate } from '@/i18n'
import {
  privatePythonOrigin,
  type DependencyProgress,
  type DependencyRepairOffer,
  type DependencyTarget,
  type InterpreterPin,
  type PrivatePythonOffer,
  type SystemInterpreterRejection,
} from '@/lib/api'
import { currentProjectId } from '@/lib/session'
import { listJoin } from '@/i18n/format'
import { isRepairRunning, managedPreviewKey, useDepRepairStore } from '@/store/depRepairStore'
import { useEnvStore } from '@/store/envStore'
import { useUiStore } from '@/store/uiStore'
import { Settings } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { RepairProgressLine, RepairStageList } from './RepairProgressLine'
import { PRODUCT_NAME } from '@/lib/brand'
import { Button } from './ui/Button'
import { TextInput } from './ui/Input'
import { Details, Summary } from '@/components/ui/Details'
import { Card } from './ui/Card'

/**
 * 「这个项目还缺 lmfit」→ 点一次 →「安装并继续」→ 图出来（ADR 0019）。
 *
 * 界面纪律，每条都有理由：
 *
 * * **不写成 Python 教程**。主文案只有「这个项目还缺少 X」和一个主动作，
 *   pip / site-packages / virtualenv 这些词一个都不出现在主界面上。
 * * **改用户环境要说清楚**。装进项目 `.venv` 的按钮写「安装到项目环境」而
 *   不是「确定」，旁边一行说明这会修改这个项目现有的 Python 环境。不做
 *   恐吓式弹窗，但也不把「我们要改你的科研环境」藏起来。
 * * **进度按状态说人话，不甩 pip 日志**。几百行 pip 输出放在「安装详情」
 *   折叠区里。
 * * **解析不出包名就不给一键安装**。那时给「指定安装包…」和「选择其他
 *   Python」——绝不拿 import 名当包名装。
 * * **受管环境是一次授权**（2026-09-28 用户裁决）：按钮下面把确认页里的全部要素说出口
 *   （装什么、需要联网、建隔离环境且不改源码与现有环境、要不要先下载私有 Python 与多大），
 *   点一次就形成计划并执行；后端算出来的计划超出了这里说过的，才停在确认页
 *   （`planMatchesDisclosure`）。项目 `.venv` 那条仍然先到确认页（ADR 0019 §八）。
 * * **失败 / 取消之后可以就地重试**（`RETRYABLE_REPAIR_CODES`），不用关掉卡片重来，更不用重启。
 */
const en = (key: string, values?: Record<string, unknown>) =>
  translate(`engine.${key}`, { ns: 'errors', ...(values ?? {}) })

/** 需求串 → 包名（`tabulate[widechars]==0.9.0` → `tabulate`）：句子里只说装哪些包，完整形态在「详情」里 */
export function requirementName(requirement: string): string {
  const m = /^[A-Za-z0-9._-]+/.exec(requirement.trim())
  return m ? m[0] : requirement
}

/**
 * 「要装哪些包」的一句话写法（卡片那一句、进度行、授权框三处共用）：一个 = 名字；两个 = 「a 和 b」；更多 =
 * 「a 等 N 个包」。**说的必须是计划里真正要装的全部**，不是用户点的那一个
 */
export function packagesPhrase(requirements: string[]): string {
  const names = requirements.map(requirementName)
  if (names.length <= 2) return listJoin(names)
  return en('packagesMany', { first: names[0], count: names.length })
}

/** 进度行里「正在安装 X…」的 X：进度记录带着全部要装的包（多于一个）就说全部，否则是用户点的那一个 */
export function progressPackages(progress: DependencyProgress, module: string): string {
  return progress.requirements && progress.requirements.length > 1
    ? packagesPhrase(progress.requirements)
    : progress.distribution || module
}

/**
 * 联合准备 / 跑前授权框 / 脚本行「要先准备」的进度那一句：安装阶段说出真正在装的包（与单包修复的进度行同一句
 * `repairInstalling`），其余阶段按 `stateText`。`en` 是调用方的 `engine.*` 翻译函数
 */
export function jointProgressText(
  progress: DependencyProgress,
  stateText: (state: string) => string,
): string {
  if (progress.state === 'installing' && progress.requirements?.length)
    return en('repairInstalling', { module: packagesPhrase(progress.requirements) })
  return stateText(progress.state)
}

/** 安装状态 → 一句话（前端**只按 state 换文案**，不解析日志） */
const STATE_KEY: Record<string, string> = {
  preparing: 'repairPreparing',
  downloading_python: 'dependencyPrepareState_downloading_python',
  creating_env: 'repairCreatingEnv',
  installing: 'repairInstalling',
  verifying: 'repairVerifying',
  done: 'repairDone',
  cancelled: 'repairCancelled',
}

export function DependencyRepairCard({
  offer,
  module,
  script,
  fromScriptRow = false,
}: {
  offer: DependencyRepairOffer
  module: string
  script: string
  /**
   * 挂在素材库脚本行上（#729）：发起时把这份 offer 交给 store 随作业收放——脚本行的 offer 住在切项目会被
   * 清空的 `scriptRunStore` 里，切回来那一行要靠它重新挂出卡片
   */
  fromScriptRow?: boolean
}) {
  useTranslation('errors')
  const {
    plan,
    progress,
    busy,
    errorCode,
    errorText,
    pinned: pinnedSince,
    request,
    makePlan,
    install,
    installNow,
    retry,
    adoptSystemPython,
    cancel,
    reset,
    clearPinnedInterpreter,
    managedPreviews,
    previewManaged,
  } = useDepRepairStore()
  const [manual, setManual] = useState('')
  const origin = fromScriptRow ? { offer, module } : null
  const running = isRepairRunning(progress)
  const pkg = offer.requirement?.distribution || module

  const exhausted = offer.code === 'dependency_repair_rounds_exhausted'
  // 采用这台机器上已有的解释器**不需要**解析出包名，也不消耗修复轮次
  //（它什么都不装）：解析不出 / 轮次用完时它照样列出——那正是用户仅剩的路。
  // 安装目标则两个前提都要。
  const canInstall = !!offer.requirement && !exhausted
  // 「指定安装包」要装到哪：第一个**安装**目标。系统解释器不是安装目标
  //（采用它一个字节都不装），排在最前时也不能被当成装包的地方。
  const installTarget = offer.targets.find((tg) => tg.kind !== 'system_interpreter')
  const managedTarget = offer.targets.find((tg) => tg.kind === 'tavotto_managed') ?? null
  // 受管目标能不能用 offer 形成时还不知道（后端在后台探基础解释器，于是也没挂私有 Python）：先形成一份计划
  // 读出真实要素（计划这一步什么都不装）。不预读的话，一键修复点下去计划多出一段下载，只能停在确认页再点一次
  const previewArgs = { module, script, target: 'tavotto_managed' as const }
  // 只在受管目标**会是主按钮**时预读（Codex #742）：这台电脑上已有装好这个包的解释器时，主按钮是改用它——
  // 那时去预读用不上的受管计划，「检查中」会把一个已经就绪的主按钮禁用住，慢的 / 卡住的请求让用户点不了。
  // 已有解释器能不能用与预读无关（系统候选的体检与基础解释器的探测各走各的），所以这里先判它
  const systemReady = offer.targets.some((tg) => tg.kind === 'system_interpreter' && tg.available !== false)
  const needPreview =
    canInstall &&
    !systemReady &&
    !pinnedSince &&
    !offer.pinned &&
    // 受管目标要新建第一代：要装的全部包（不止缺的这一个）只有形成计划时才算得出，先预读它再写那一句话；
    // 基础解释器还在后台探时同样要读（要不要下载、多大）。只披露私有 Python 而没有这份清单时也要读
    (managedTarget?.available === null || (!!managedTarget?.creates_environment && managedTarget.available !== false))
  // 这张卡自己那一格（按脚本 + 模块）：同时挂着的另一张卡预读别的包不会盖掉它
  const preview = managedPreviews[managedPreviewKey(previewArgs)] ?? null
  useEffect(() => {
    if (needPreview) void previewManaged(previewArgs)
    // previewArgs 由这三样决定；同一份只问一次（store 按 key 去重）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needPreview, module, script, !!preview])
  const checking = needPreview && (!preview || preview.pending)
  const previewUnavailable = preview?.code === 'managed_env_unavailable'
  // 预读说这台电脑没法建环境的，受管目标与 offer 上的 `available: false` 同样对待
  const targets = offer.targets.filter(
    (tg) =>
      tg.available !== false &&
      !(tg.kind === 'tavotto_managed' && previewUnavailable) &&
      (tg.kind === 'system_interpreter' || canInstall),
  )
  const managedReady = canInstall && !!managedTarget && managedTarget.available !== false && !previewUnavailable
  const managedUnavailable =
    previewUnavailable ||
    offer.targets.some(
      (tg) => tg.kind === 'tavotto_managed' && tg.available === false && tg.reason === 'managed_env_unavailable',
    )
  // 点之前说出口的私有 Python（offer 上挂着的，或预读的计划里的）：一次授权按它比对计划
  const disclosed = managedTarget?.private_python ?? preview?.plan?.private_python ?? null
  // 这次授权要装的全部包：预读的计划里说的（offer 在失败响应路径上不起解释器、算不出）> 只有缺的这一个
  const allRequirements = [
    ...(preview?.plan?.requirements ?? []),
  ]
  const requirementList =
    allRequirements.length > 0 ? allRequirements : offer.requirement ? [offer.requirement.requirement] : []
  const act = (tg: DependencyTarget) =>
    tg.kind === 'system_interpreter'
      ? // 采用已有的解释器不经 plan：没有要安装的东西可以「计划」
        void adoptSystemPython(tg.python, module, script, origin)
      : tg.kind === 'tavotto_managed' && offer.requirement
        ? // 一次授权：卡片已经把计划的要素说出口，点一次就开始
          void installNow(
            { module, script, target: 'tavotto_managed' },
            {
              requirement: offer.requirement.requirement,
              ...(requirementList.length > 1 ? { requirements: requirementList } : {}),
              target_kind: 'tavotto_managed',
              private_python: disclosed,
            },
            origin,
          )
        : void makePlan({ module, script, target: tg.kind }, origin)

  // ---- 全局显式解释器压住了项目级决策（#465）：只有一条出口 ---------------
  // offer 形成时就有的（`offer.pinned`）与之后才钉上的（plan 的 400 / 安装失败
  // 事件带回来的 `pinnedSince`）走同一支。装进任何目标都不会被用，所以这里
  // **不列安装目标、不给「选择其他 Python」**（那条写的也是项目级决策）。能解开
  // 它的只有清掉那条固定：设置里指定的在这里一键清，环境变量的说清楚要清什么、
  // 然后重启。排在进度之前：安装失败在「已被钉上」那一刻就结束了，进度页只会
  // 再说一遍失败。
  const pinned = offer.pinned ?? pinnedSince
  if (pinned) {
    return (
      <Pinned
        module={pkg}
        pinned={pinned}
        // 清固定、收卡、重排、重跑都在 store 里，按项目代际判（Codex #742 P1）
        onClear={() => clearPinnedInterpreter(module, script, pinned, origin)}
        storeError={errorText}
      />
    )
  }

  // ---- 安装进行中 / 刚结束：只显示进度，不再显示一堆选项 ------------------
  if (progress && (running || progress.state !== 'idle')) {
    return (
      <RepairProgress
        module={module}
        onCancel={() => void cancel()}
        onDone={reset}
        // 重试要知道上一次请求的是什么：本标签页里起的才有（切项目换回来的收着的作业没有）
        onRetry={request ? () => void retry() : undefined}
      />
    )
  }

  // ---- 已经形成计划，等用户确认 ------------------------------------------
  if (plan) {
    const toProject = plan.target_kind === 'project_venv'
    return (
      <Card className="flex flex-col gap-2.5">
        <div>
          {/* 小标题走 type-section（全面打磨 D14）：11/500/ink 是这一族自造的第七个角色 */}
          <h3 className="type-section">{en('repairConfirmTitle', { module: pkg })}</h3>
          <p className="mt-1 text-xs leading-relaxed text-ink-2">
            {toProject
              ? en('repairConfirmProject', { path: plan.python || '.venv' })
              : en('repairConfirmManaged')}
          </p>
          {toProject && (
            // 改用户自己的环境是不可逆的，这句不能藏起来
            <p className="mt-1 text-xs leading-relaxed text-warn-content">{en('repairModifiesEnv')}</p>
          )}
          <p className="mt-1 text-xs leading-relaxed text-ink-3">
            {en('repairWillInstall', {
              requirement: plan.requirements?.length ? listJoin(plan.requirements) : plan.requirement,
            })}
            {plan.network_required ? ` · ${en('repairNeedsNetwork')}` : ''}
          </p>
          {plan.private_python && <PrivatePythonDisclosure offer={plan.private_python} />}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Button variant="primary" disabled={busy} onClick={() => install()}>
            {toProject ? en('repairInstallToProject') : en('repairPrepareAndContinue')}
          </Button>
          <Button onClick={reset}>{en('repairBack')}</Button>
        </div>
        <Failure code={errorCode} text={errorText} />
      </Card>
    )
  }

  // ---- 起点 ---------------------------------------------------------------
  const rejected = offer.system_rejected ?? []
  const system = targets.find((tg) => tg.kind === 'system_interpreter') ?? null
  // 一键修复只有一个主动作（2026-09-29，面向不懂 Python 的用户）：这台电脑上已经装好这个包的环境最便宜
  // （不装、不下载，后端也把它排在最前），其次是为项目准备的受管环境。两样都没有时才把各条路摊开
  const primary: DependencyTarget | null = system ?? (managedReady ? managedTarget : null)
  const rest = targets.filter((tg) => tg !== primary)
  // 解析不出包名：用户可以自己指定，但那串东西同样要过后端的语法关
  const specify = !offer.requirement && !exhausted && (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs text-ink-2">{en('repairSpecifyPackage')}</span>
      <div className="flex items-center gap-1.5">
        <TextInput
          value={manual}
          onChange={(e) => setManual(e.target.value)}
          placeholder={en('repairPackagePlaceholder')}
          aria-label={en('repairPackageAria')}
        />
        <Button
          disabled={busy || !manual.trim()}
          onClick={() =>
            makePlan({
              module,
              script,
              target:
                installTarget?.kind === 'project_venv' ? 'project_venv' : 'tavotto_managed',
              distribution: manual.trim(),
            }, origin)
          }
        >
          {en('repairContinue')}
        </Button>
      </div>
    </div>
  )
  const managed = primary?.kind === 'tavotto_managed'
  const failing = !!(errorCode || errorText)
  // 「详情」首段只说**主按钮真正要做的事**（Codex #742）：改用已有 Python 时不许说「将安装 / 需下载」；受管环境等别的
  // 路另起一小节「其他方式（备选）」，明确是备选
  const lead = primary ?? (targets[0]?.kind === 'project_venv' ? targets[0] : null)
  const managedFacts = (
    <>
      <p>
        {en('repairWillInstall', {
          requirement: requirementList.length ? listJoin(requirementList) : (offer.requirement?.requirement ?? pkg),
        })}
      </p>
      <p data-one-click-cost {...(disclosed ? { 'data-dependency-private-python': '' } : {})}>
        {checking ? en('oneClickChecking') : downloadFact(disclosed)}
      </p>
      <p>{en('repairFactUntouched')}</p>
    </>
  )
  const primaryFacts =
    lead?.kind === 'tavotto_managed' && managedReady && offer.requirement ? (
      // 一次授权的披露，最多三条、各说一件事（2026-09-29 用户：「详情」不许重复啰嗦）：装什么 / 下载什么多大 / 不改动什么
      <div className="flex flex-col gap-1 text-xs leading-relaxed text-ink-3" data-repair-primary-facts data-dependency-disclosure>
        {managedFacts}
      </div>
    ) : lead?.kind === 'system_interpreter' ? (
      // 改用已有的 Python：改用哪一个（路径与版本给想核对的人）、不装也不下、不改动什么
      <div className="flex flex-col gap-1 text-xs leading-relaxed text-ink-3" data-repair-primary-facts>
        <p data-one-click-system>{hint(lead)}</p>
        <p>{en('repairFactUntouched')}</p>
      </div>
    ) : lead?.kind === 'project_venv' && offer.requirement ? (
      // 装进项目自己的环境：装什么、会改动哪个环境（这一条要用户看清，所以它先到确认页）
      <div className="flex flex-col gap-1 text-xs leading-relaxed text-ink-3" data-repair-primary-facts>
        <p>{en('repairWillInstall', { requirement: offer.requirement.requirement })}</p>
        <p>{en('dependencyTargetHint_project_venv', { venv: lead.venv || '.venv' })}</p>
      </div>
    ) : null
  const alternatives = primary ? rest : []
  const managedAlternative = alternatives.some((tg) => tg.kind === 'tavotto_managed') && managedReady && offer.requirement
  const advanced = (
    <Advanced>
      {failing && <FailureDetail code={errorCode} text={errorText} />}
      {primaryFacts}
      {!primary && managedUnavailable && (
        // 无路可走时那一句只说「先装 Python」；要装哪一段版本、装过了怎么指定，给展开的人
        <p className="text-xs leading-relaxed text-ink-3" data-managed-env-unavailable-hint>
          {en('repairManagedUnavailableHint', {
            min: offer.python_supported.min,
            max: offer.python_supported.max,
          })}
        </p>
      )}
      {rejected.length > 0 && <Rejections rejected={rejected} pkg={pkg} />}
      {/* 其他方式：不是主按钮做的事，明确标成备选 */}
      <div className="flex flex-col gap-2.5" data-repair-alternatives>
        <p className="type-meta">{en('repairAlternatives')}</p>
        {alternatives.length > 0 && <TargetList targets={alternatives} first={null} pkg={pkg} act={act} />}
        {managedAlternative && (
          // 备选里的受管环境点下去同样是一次授权：它的要素照样说出口，只是放在备选这一节里
          <div className="flex flex-col gap-1 text-xs leading-relaxed text-ink-3" data-alternative-managed-disclosure>
            {managedFacts}
          </div>
        )}
        {primary && specify}
        <OtherPython />
        <OpenEnvironment />
      </div>
    </Advanced>
  )

  if (primary) {
    // 默认可见的只有：一句话 + 一个主按钮 +「详情」（折叠）。标题、解释、下载大小都不摆出来
    return (
      <Card className="flex flex-col gap-2" data-one-click-repair={primary.kind}>
        {failing ? (
          // 失败了：那一句换成故障那一句（原因 + 下一步），不在它下面再叠一句；完整说明在「详情」里
          <p className="text-sm leading-relaxed text-danger" data-repair-failure>
            {repairShortMessage(errorCode)}
          </p>
        ) : (
          <p className="text-sm leading-relaxed text-ink" data-one-click-sentence>
            {managed
              ? oneClickSentence(requirementList.length > 1 ? packagesPhrase(requirementList) : pkg, checking ? null : disclosed)
              : en('oneClickSentenceSystem', { module: pkg })}
          </p>
        )}
        <Button
          className="self-start"
          variant="primary"
          disabled={busy || checking}
          aria-busy={checking || undefined}
          data-one-click-repair-button
          onClick={() => act(primary)}
        >
          {en('oneClickRepair')}
        </Button>
        {advanced}
      </Card>
    )
  }

  if (managedUnavailable && targets.length === 0 && canInstall) {
    // 真的无路可走（没有可建环境的 Python，也没有可下载的那份）：同样一句话说清下一步，其余收进「详情」
    return (
      <Card className="flex flex-col gap-2">
        {failing ? (
          <p className="text-sm leading-relaxed text-danger" data-repair-failure>
            {repairShortMessage(errorCode)}
          </p>
        ) : (
          <p className="text-sm leading-relaxed text-ink" data-managed-env-unavailable>
            {en('repairManagedUnavailable')}
          </p>
        )}
        {advanced}
      </Card>
    )
  }

  return (
    <Card className="flex flex-col gap-2.5">
      <div>
        <h3 className="type-section">{en('repairTitle', { module: pkg })}</h3>
        <p className="mt-1 text-xs leading-relaxed text-ink-2">
          {offer.requirement
            ? en('repairBody')
            : en('repairUnresolved', { module })}
        </p>
        {exhausted && (
          <p className="mt-1 text-xs leading-relaxed text-ink-3">{en('repairExhausted')}</p>
        )}
      </div>

      {targets.length > 0 && <TargetList targets={targets} first={targets[0].kind} pkg={pkg} act={act} />}

      {specify}

      <Failure code={errorCode} text={errorText} />
      {advanced}
    </Card>
  )
}

/**
 * 「高级」：默认折叠。**任何一条修复路径走不通时的兜底出口**（ADR 0019 §五 与兼容层 Layer 4）——换一个
 * 已经装好那个包的 Python——必须**始终在**这里：解析不出包名、没有可用目标、装完还是失败，用户都还有这条路
 * （e2e 抓到过一次：卡片只剩「指定安装包」）。它与「选择渲染环境」都是给懂 Python 的人的，所以收起来，
 * 不与一键修复的主按钮抢眼。
 */
function Advanced({ children }: { children: ReactNode }) {
  return (
    <Details className="border-t border-border pt-2.5" data-repair-advanced>
      <Summary className="type-meta">{en('repairAdvanced')}</Summary>
      <div className="mt-2 flex flex-col gap-2.5">{children}</div>
    </Details>
  )
}

/** 每个目标一块：按钮在上、说明在下 */
function TargetList({
  targets,
  first,
  pkg,
  act,
}: {
  targets: DependencyTarget[]
  pkg: string
  /** 摊开各条路时第一条是主按钮；收在「高级」里时一个主按钮都没有（卡片的主动作只有一键修复） */
  first: DependencyTarget['kind'] | null
  act: (tg: DependencyTarget) => void
}) {
  const busy = useDepRepairStore((s) => s.busy)
  return (
    <div className="flex flex-col gap-1.5">
      {targets.map((tg) => {
        // 受管环境那条已经没有副标题（hint 返回空串）：空的 <span> 会白留
        // 一行 gap，所以判空后整块不渲染，而不是渲染一个空元素。
        const detail = hint(tg)
        return (
          // 一个目标一块：按钮在上、说明在下。**不并排**——Button 是
          // whitespace-nowrap + shrink-0 的，右栏只有 296px，英文按钮
          // 一旦并排就会把旁边那句挤没或把整栏撑破。
          <div key={tg.kind} className="flex flex-col gap-0.5">
            <Button
              className="self-start"
              variant={tg.kind === first ? 'primary' : 'ghost'}
              disabled={busy}
              onClick={() => act(tg)}
            >
              {label(tg, pkg)}
            </Button>
            {detail && (
              <span className="truncate text-xs text-ink-3" title={tg.python || undefined}>
                {detail}
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}

/**
 * 探到了但没采用的系统解释器（ADR 0044）：用户手边明明有一套装了那个包的 Python，Tavotto 为什么没用它——
 * 不说出来，他看到的就是「缺包，要不要建一个新环境」，而自己的环境像是被无视了。带路径，收在「高级」里
 */
function Rejections({ rejected, pkg }: { rejected: SystemInterpreterRejection[]; pkg: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      {rejected.map((r) => (
        <p key={r.python} className="text-xs leading-relaxed text-ink-3">
          {rejectionText(r, pkg)}
        </p>
      ))}
    </div>
  )
}

/** 就地打开渲染环境对话框（`EngineEnvironmentDialog`，与脚本行恢复说明同一个入口，不深链设置页） */
function OpenEnvironment() {
  useTranslation('workspace')
  return (
    <Button
      variant="secondary"
      size="sm"
      className="self-start"
      data-repair-open-environment
      onClick={() => useUiStore.getState().setEngineEnvOpen(true)}
    >
      <Settings size={ICON_SIZE.sm} />
      {translate('scripts.openEnvSettings', { ns: 'workspace' })}
    </Button>
  )
}

/**
 * 一键修复的那一句。要下载私有 Python 时（`origin` 为 download，或缺字段时推断为下载）把大小用括号放进**同一句**
 * ——既守住「点之前说出下载多大」（2026-09-28），又守住「一句话就能读懂」（2026-09-29）；安装包自带 / 已缓存 /
 * 已就位时句子里不提下载。修复卡与跑前授权框共用
 */
export function oneClickSentence(packages: string, privatePython: PrivatePythonOffer | null): string {
  return privatePython && privatePythonOrigin(privatePython) === 'download'
    ? en('oneClickSentenceDownload', {
        packages,
        mb: Math.max(1, Math.round(privatePython.download_bytes / 1048576)),
      })
    : en('oneClickSentence', { packages })
}

/** 同上，干净机器上只需要准备环境本身（没有要装的包）时的那一句 */
export function oneClickEnvironmentSentence(privatePython: PrivatePythonOffer | null): string {
  return privatePython && privatePythonOrigin(privatePython) === 'download'
    ? en('oneClickSentenceEnvDownload', { mb: Math.max(1, Math.round(privatePython.download_bytes / 1048576)) })
    : en('oneClickSentenceEnv')
}

/**
 * 「详情」里「要下载什么」那一条（修复卡与跑前授权框共用）：要下载私有 Python 时说版本与大小，安装包自带 / 已就位时
 * 说用的是哪一份；都顺带一句装包要联网——联网只在这一条里说，不另起一行
 */
export function downloadFact(
  privatePython: PrivatePythonOffer | null,
  { packages = true }: { packages?: boolean } = {},
): string | null {
  // 只准备环境、一个包都不装（干净机器上的 `nothing_needed` 计划）：不说「装包需要联网」（Codex #742）；
  // 连私有 Python 都不用准备时这一条整条不出现
  if (!packages) {
    if (!privatePython) return null
    const values = { product: PRODUCT_NAME, version: privatePython.version }
    const origin = privatePythonOrigin(privatePython)
    if (origin === 'bundled') return en('repairFactEnvBundled', values)
    if (origin === 'cached') return en('repairFactEnvCached', values)
    return en('repairFactEnvDownload', { ...values, mb: Math.max(1, Math.round(privatePython.download_bytes / 1048576)) })
  }
  if (!privatePython) return en('repairFactNetwork')
  const values = { product: PRODUCT_NAME, version: privatePython.version }
  const origin = privatePythonOrigin(privatePython)
  if (origin === 'bundled') return en('repairFactBundled', values)
  if (origin === 'cached') return en('repairFactCached', values)
  return en('repairFactDownload', { ...values, mb: Math.max(1, Math.round(privatePython.download_bytes / 1048576)) })
}

/** Both the offer and the final plan must disclose the Python download before authorization. */
function PrivatePythonDisclosure({ offer }: { offer: NonNullable<DependencyTarget['private_python']> }) {
  return (
    <p className="mt-1 text-xs leading-relaxed text-ink-2" data-dependency-private-python>
      {privatePythonText(offer)}
    </p>
  )
}

/**
 * 私有 Python 那一句（修复卡与跑前授权框共用）：按来源三句——要下载的说版本与体积、数据目录里已有的说
 * 不用再下、安装包自带的说不用下载。来源的判据只有 `privatePythonOrigin` 一处
 */
export function privatePythonText(offer: NonNullable<DependencyTarget['private_python']>): string {
  const origin = privatePythonOrigin(offer)
  if (origin === 'bundled')
    return en('dependencyPreparePrivatePythonBundled', { version: offer.version, product: PRODUCT_NAME })
  if (origin === 'cached')
    return en('dependencyPreparePrivatePythonCached', { version: offer.version, product: PRODUCT_NAME })
  return en('dependencyPreparePrivatePython', {
    version: offer.version,
    mb: Math.max(1, Math.round(offer.download_bytes / 1048576)),
    product: PRODUCT_NAME,
  })
}

/**
 * 渲染解释器被全局固定时的卡片（#465）。
 *
 * 「恢复自动检测」清的是**全局**设置（`setPython(null)`）——这是这张卡里唯一
 * 一处碰全局设置的地方，理由正相反于 `OtherPython`：要解开的就是那条全局固定。
 * 清掉之后把因缺包失败的渲染重新排上：项目记住的环境（本例里已经装好包的
 * 受管环境）从此轮得到；没有记住的会再走一遍缺包 → 卡片 → 安装，那时安装
 * 才真的有用。
 */
function Pinned({
  module,
  pinned,
  onClear,
  storeError,
}: {
  module: string
  pinned: InterpreterPin
  onClear: () => Promise<string | null>
  /** 切项目期间才回来的结局（失败 / 回来核实没生效）记在 store 里，这张卡照样把它说出来 */
  storeError: string
}) {
  useTranslation('errors')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fromEnv = pinned.source === 'env_override'
  // 环境变量那档的来源标签按**供值的那个**变量拼（`sourceLabel.env_override` 写死
  // 的是新名）：正文说「环境变量 TAVOTTO_WORKER_PYTHON」、提示却让清
  // MM_WORKER_PYTHON，两句话打架。老服务端没有 variable 时退到新名。
  const variable = pinned.variable || 'TAVOTTO_WORKER_PYTHON'
  const source = fromEnv
    ? en('repairPinnedEnvSource', { variable })
    : en(`sourceLabel.${pinned.source || 'unknown'}`, { product: PRODUCT_NAME })
  const clear = async () => {
    setBusy(true)
    const failure = await onClear()
    setBusy(false)
    setError(failure)
  }
  return (
    <Card data-dependency-repair-pinned className="flex flex-col gap-2.5">
      <div>
        <h3 className="type-section">{en('repairTitle', { module })}</h3>
        <p className="mt-1 text-xs leading-relaxed text-ink-2">
          {en('repairPinnedBody', { python: pinned.python, source })}
        </p>
        {fromEnv && (
          // 点名**供值的那个**变量：旧名 MM_WORKER_PYTHON 供的值同样是 env_override，
          // 只让用户清新名的话固定还在、重启后照旧挡着。
          <p className="mt-1 text-xs leading-relaxed text-ink-3">
            {en('repairPinnedEnvHint', { variable })}
          </p>
        )}
      </div>
      {!fromEnv && (
        <div className="flex flex-wrap items-center gap-1.5">
          <Button variant="primary" disabled={busy} onClick={() => void clear()}>
            {en('repairPinnedClear')}
          </Button>
        </div>
      )}
      {(error || storeError) && <p className="text-xs text-danger">{error || storeError}</p>}
    </Card>
  )
}

/**
 * 「选择其他 Python」——写项目作用域那一条（ADR 0018 的 `scope="project"`），
 * 不写全局：用户是在修**这个项目**的这个脚本，没理由改变别的项目的渲染环境。
 */
function OtherPython() {
  useTranslation('errors')
  const { setProjectPython } = useEnvStore()
  const [path, setPath] = useState('')
  const [error, setError] = useState<string | null>(null)
  return (
    <div className="flex flex-col gap-1.5">
      {/* 只留主句：括号里的附加条件是「填错了再说」的事，这里先把出口指清楚 */}
      <span className="text-xs text-ink-2">{en('repairUseOtherPythonShort')}</span>
      {/* 占位符是「这里还没填」的提示，不是要读的正文，压到 faint 一档。写在行
          容器上（而不是传 className）是因为 placeholder 的样式归 TextInput 自己
          管，这条后代变体的特指度更高，能盖住它的默认色。 */}
      <div className="flex items-center gap-1.5 [&_input]:placeholder:text-ink-faint">
        {/* 占位符只留一条路径样例。它是**格式示范**不是要读的句子，各语言写法
            完全一致，所以不走文案表（原 `engine.pathPlaceholder` 里那句解释性
            补充随之去掉）。控件的无障碍名仍由 pathAria 提供，屏幕阅读器读到的
            依然是当前语言的完整说明。 */}
        <TextInput
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="/path/to/python"
          aria-label={en('pathAria')}
        />
        <Button
          disabled={!path.trim()}
          onClick={async () => setError(await setProjectPython(path.trim()))}
        >
          {en('apply')}
        </Button>
      </div>
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  )
}

/**
 * 目标环境的按钮文案。**必须短**：按钮不换行，长文案会撑破右栏。
 *
 * 受管环境这条点名要装的是哪个包（「将 lmfit 安装到 Tavotto 环境」）：主动作
 * 自己就是一句完整的话，用户不用回头找上面的标题确认主语。新建还是复用受管
 * 环境对他没有区别——两种情况都不碰他自己的 Python，所以合成同一句。
 */
function label(target: DependencyTarget, pkg: string): string {
  if (target.kind === 'project_venv') return en('repairUseProjectEnv')
  if (target.kind === 'system_interpreter') return en('repairUseSystemPython')
  return en('repairInstallToManaged', { module: pkg, product: PRODUCT_NAME })
}

/**
 * 按钮旁边那句「装到哪」，长了就截断；没有要补充的就返回空串（调用方判空后
 * 整块不渲染）。
 */
function hint(target: DependencyTarget): string {
  if (target.kind === 'project_venv') return target.venv || '.venv'
  if (target.kind === 'system_interpreter') {
    const base = en('repairSystemHint', {
      python: target.python,
      version: target.python_version || '?',
    })
    // 钉版之外但能 import 的 matplotlib 照用，但要如实标注（ADR 0018 §十）
    return target.support === 'unverified_but_compatible'
      ? `${base} · ${en('repairSystemUnverified')}`
      : base
  }
  // 受管环境不需要这一行副标题：装到哪按钮自己已经说清楚了（「将 X 安装到 Tavotto
  // 环境」）。一次授权要说出口的那几样（装什么 / 联网 / 隔离环境 / 私有 Python）
  // 在按钮下面的披露块里（`data-dependency-disclosure`），不在这一行。
  return ''
}

/** 「找到了 X 装了这个包，但没采用」——三种原因三句话，用户的下一步各不相同 */
function rejectionText(r: SystemInterpreterRejection, pkg: string): string {
  switch (r.code) {
    case 'project_env_unsupported_python':
      return en('repairSystemRejectedUnsupported', {
        python: r.python,
        module: pkg,
        version: r.python_version || '?',
        product: PRODUCT_NAME,
      })
    case 'project_env_no_matplotlib':
      return en('repairSystemRejectedNoMatplotlib', { python: r.python, module: pkg })
    default:
      return en('repairSystemRejectedUnusable', { python: r.python, module: pkg })
  }
}

/**
 * 失败后可以就地「重试」的 code：都是**这一次**没做成、换个时刻再来一次就可能成的——
 * 断网 / 超时 / 环境正忙 / 被取消 / 磁盘满（腾出空间后）/ 下载的私有 Python 损坏（重新下载会重新校验）/
 * 确认期间环境变了 / 私有 Python 的来源变了（重试会重新形成计划，超出卡片说过的就停在确认页）。
 *
 * **不在表里的不给重试**，各有理由：`dependency_hash_mismatch`（声明里的哈希与包不符——重下一遍还是那个包，
 * 要用户核对锁文件）、`dependency_not_found` / `dependency_requires_build` / `dependency_conflict`
 * （软件源与声明的事实，重试不变）、`dependency_import_still_failed` / `dependency_worker_selftest_failed` /
 * `dependency_consistency_failed`（pip 已经跑成，后端记为「这一轮已经装过」，再来一次同样的结果）、
 * `private_python_source_unavailable` / `private_python_not_offered`（要升级 Tavotto）、
 * `private_python_invalid_archive` / `private_python_launch_failed`（下载的 Python 本身不能用）、
 * 通用的 `dependency_install_failed`（原因不明，出口是「安装详情」与换环境）。
 */
export const RETRYABLE_REPAIR_CODES: ReadonlySet<string> = new Set([
  'dependency_network_unavailable',
  'dependency_install_timeout',
  'dependency_install_busy',
  'dependency_install_cancelled',
  'environment_mutating',
  'package_disk_low',
  'managed_env_write_failed',
  'repair_plan_stale',
  // 计划里说的 Python 来源（安装包自带 / 缓存 / 下载）在确认之后变了（#743）：与 repair_plan_stale 同类——重试会按
  // 此刻的来源重新形成计划，超出卡片说过的就停在确认页把新的来源 / 大小说出口
  'private_python_source_changed',
  'private_python_offline',
  'private_python_cancelled',
  'private_python_hash_mismatch',
  'private_python_disk_low',
  'private_python_write_failed',
])

/**
 * 安装进度。进行中只有**一行**（`RepairProgressLine`：「正在下载 Python… 12 / 25 MB」），pip 日志与换用镜像
 * 的说明折叠在「安装详情」里。
 *
 * 取消之后**不假装完整回滚**：改的是用户自己的环境时如实说「可能已发生
 * 部分修改」——那正是「改用户环境必须明确确认」的另一面。
 */
function RepairProgress({
  module,
  onCancel,
  onDone,
  onRetry,
}: {
  module: string
  onCancel: () => void
  onDone: () => void
  onRetry?: () => void
}) {
  useTranslation('errors')
  const { progress } = useDepRepairStore()
  if (!progress) return null
  const running = isRepairRunning(progress)
  const key = STATE_KEY[progress.state] ?? 'repairPreparing'
  const failed = progress.state === 'failed'
  const cancelled = progress.state === 'cancelled'
  const canRetry =
    !!onRetry &&
    progress.retryable !== false &&
    (cancelled || (failed && RETRYABLE_REPAIR_CODES.has(progress.code)))
  return (
    <Card className="flex flex-col gap-2.5">
      {!failed && !cancelled ? (
        <RepairProgressLine progress={progress} text={en(key, { module: progressPackages(progress, module) })} />
      ) : (
      <div>
        {failed ? (
          // 失败：只露一句（原因 + 下一步，`repairShortMessage`），标题、完整说明、错误码、日志都在「详情」里
          <p className="text-sm leading-relaxed text-danger" data-repair-failure>
            {repairShortMessage(progress.code)}
          </p>
        ) : (
          <h3 className="type-section">{en(key, { module: progressPackages(progress, module) })}</h3>
        )}
        {cancelled && (
          <p className="mt-1 text-xs leading-relaxed text-ink-2">
            {progress.target_kind === 'project_venv'
              ? en('repairCancelledProjectEnv')
              : en('repairCancelledManaged', { product: PRODUCT_NAME })}
          </p>
        )}
      </div>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        {running ? (
          <Button onClick={onCancel}>{en('repairCancel')}</Button>
        ) : (
          <>
            {canRetry && (
              <Button variant="primary" onClick={onRetry} data-dependency-repair-retry>
                {en('dependencyPrepareRetry')}
              </Button>
            )}
            <Button onClick={onDone}>{en('repairClose')}</Button>
          </>
        )}
      </div>
      {(progress.log || progress.pypi_mirror || !cancelled) && (
        // 默认折叠：完整的阶段列表、换用镜像的说明、pip 日志（主区域只有那一行进度）
        <Details className="text-xs text-ink-3" data-repair-progress-details>
          <Summary className="text-ink-2">{en('repairDetails')}</Summary>
          {!failed && !cancelled && (
            <div className="mt-1">
              <RepairStageList progress={progress} />
            </div>
          )}
          {failed && (
            <div className="mt-1">
              <FailureDetail code={progress.code} text={progress.error} />
            </div>
          )}
          {progress.pypi_mirror && (
            // 连不上默认包源、后端改用了镜像（与日志里那一行同一件事），只在详情里说
            <p className="mt-1 leading-relaxed" data-repair-pypi-mirror>
              {en('repairPypiMirror', { mirror: progress.pypi_mirror })}
            </p>
          )}
          {progress.log && (
          <pre className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-sm bg-surface-2 p-1.5 font-mono text-xs">
            {progress.log}
          </pre>
          )}
        </Details>
      )}
    </Card>
  )
}

/**
 * 稳定错误码 → 当前语言的一句话；没登记的回 null（让调用方用后端原文兜底）。
 * 包管理页（`settings/PackagesSettings.tsx`）与这张卡共用这一份：两处的码
 * 来自同一个后端漏斗（`app._repair_error`）、同一张文案表。
 */
export function repairCodeMessage(code: string): string | null {
  if (!code) return null
  // 先查修复专用表，再查后端通用表：`environment_in_use_by_native_session` 这种
  // 两条控制面共用的 code 文案只在 `backend.*` 里有一份，不为这张卡再抄一份。
  for (const key of [`engine.repairError.${code}`, `backend.${code}`]) {
    if (i18n.exists(key, { ns: 'errors' })) return translate(key, { ns: 'errors' })
  }
  return null
}

/**
 * 故障的**那一句**（2026-09-29 用户：故障卡同样「一句话就能读懂」）：原因 + 下一步合成一句，按错误码查
 * `repairErrorShort.*`；没登记的码（含只在 `backend.*` 里有文案的）与只有后端原文的一律「没装好，原因在详情里」。
 * 完整的说明（`repairError.*` / 后端原文，可以是多句）与错误码只进「详情」（`FailureDetail`）——`repairError.*`
 * 那张表设置 › 包管理页照旧整句在用，不动它
 */
export function repairShortMessage(code: string): string {
  if (code && i18n.exists(`engine.repairErrorShort.${code}`, { ns: 'errors' }))
    return en(`repairErrorShort.${code}`, { product: PRODUCT_NAME })
  return en('repairErrorShortGeneric')
}

/** 「详情」里的故障明细：完整说明 + 错误码（两样都没有就不渲染） */
function FailureDetail({ code, text }: { code: string; text: string | null | undefined }) {
  const full = repairCodeMessage(code) ?? text ?? ''
  if (!code && !full) return null
  return (
    <div className="flex flex-col gap-0.5 text-xs leading-relaxed text-ink-3" data-repair-failure-detail>
      {full && <p>{full}</p>}
      {code && <p className="font-mono">{en('repairErrorCode', { code })}</p>}
    </div>
  )
}

/**
 * 摊开各条路的那几种卡（解析不出包名 / 只有项目环境 / 确认页）上的故障：同样只露那一句，完整说明折叠在它自己的「详情」里
 */
function Failure({ code, text }: { code: string; text: string }) {
  if (!code && !text) return null
  return (
    <div className="flex flex-col gap-1">
      <p className="text-xs leading-relaxed text-danger" data-repair-failure>
        {repairShortMessage(code)}
      </p>
      <Details className="text-xs text-ink-3">
        <Summary className="text-ink-2">{en('repairDetails')}</Summary>
        <div className="mt-1">
          <FailureDetail code={code} text={text} />
        </div>
      </Details>
    </div>
  )
}

/**
 * Tavotto 受管环境的「重建」入口（设置页里）。
 *
 * 只对**我们自己建的**环境出现：用户的 `.venv` 不归我们重建，那是他的东西。
 */
export function ManagedEnvironmentRow() {
  useTranslation('errors')
  const { env } = useEnvStore()
  const { busy: repairBusy, rebuildRunningFor, rebuildManaged } = useDepRepairStore()
  // 本项目的重建还没结束时起不了第二次（别的项目的重建不挡这里，#606）
  const busy = repairBusy || rebuildRunningFor(currentProjectId())
  const managed = env?.project?.managed
  if (!env?.project?.open || !managed?.exists) return null
  return (
    <div className="mt-1.5 flex flex-col gap-0.5 border-t border-border pt-1.5">
      <span className="text-xs text-ink-2">
        {en('managedEnvUsing', { version: managed.python_version || '?', product: PRODUCT_NAME })}
      </span>
      {managed.installed.length > 0 && (
        <span className="text-xs text-ink-3">
          {en('managedEnvInstalled', {
            packages: managed.installed
              .map((p) => `${p.distribution} ${p.resolved_version}`)
              .join('、'),
          })}
        </span>
      )}
      {/* 重建会真动环境，不该长得像一句可点的说明文字，所以它是一颗真按钮：
          「这是按钮」要在扫一眼时就成立，而不是靠 hover 才显形。变体就用
          `secondary`（全面打磨 D14）——此前是 ghost 外面手画一圈 border-strong，
          那正好是 secondary 的样子，只是自己又实现了一遍，而且边比 secondary 重一档。
          mt-1.5 是让它和上面两行环境说明拉开，不跟着 gap-0.5 贴成一坨。 */}
      <Button
        variant="secondary"
        className="mt-1.5 self-start"
        disabled={busy}
        onClick={() => void rebuildManaged()}
      >
        {en('managedEnvRebuild')}
      </Button>
    </div>
  )
}

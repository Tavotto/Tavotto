import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import type {
  DependencyRepairOffer,
  DependencyTarget,
  SystemInterpreterRejection,
} from '@/lib/api'
import { isRepairRunning, useDepRepairStore } from '@/store/depRepairStore'
import { useEnvStore } from '@/store/envStore'
import { PRODUCT_NAME } from '@/lib/brand'
import { Button } from './ui/Button'
import { TextInput } from './ui/Input'
import { Details, Summary } from '@/components/ui/Details'

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
 */
const en = (key: string, values?: Record<string, unknown>) =>
  translate(`engine.${key}`, { ns: 'errors', ...(values ?? {}) })

/** 安装状态 → 一句话（前端**只按 state 换文案**，不解析日志） */
const STATE_KEY: Record<string, string> = {
  preparing: 'repairPreparing',
  creating_env: 'repairCreatingEnv',
  installing: 'repairInstalling',
  verifying: 'repairVerifying',
  done: 'repairDone',
  failed: 'repairFailed',
  cancelled: 'repairCancelled',
}

export function DependencyRepairCard({
  offer,
  module,
  script,
}: {
  offer: DependencyRepairOffer
  module: string
  script: string
}) {
  useTranslation('errors')
  const {
    plan,
    progress,
    busy,
    errorCode,
    errorText,
    makePlan,
    install,
    adoptSystemPython,
    cancel,
    reset,
  } = useDepRepairStore()
  const [manual, setManual] = useState('')
  const running = isRepairRunning(progress)
  const pkg = offer.requirement?.distribution || module

  // ---- 安装进行中 / 刚结束：只显示进度，不再显示一堆选项 ------------------
  if (progress && (running || progress.state !== 'idle')) {
    return <RepairProgress module={module} onCancel={() => void cancel()} onDone={reset} />
  }

  // ---- 已经形成计划，等用户确认 ------------------------------------------
  if (plan) {
    const toProject = plan.target_kind === 'project_venv'
    return (
      <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
        <div>
          <h3 className="text-xs font-medium text-ink">
            {en('repairConfirmTitle', { module: pkg })}
          </h3>
          <p className="mt-1 text-xs leading-relaxed text-ink-2">
            {toProject
              ? en('repairConfirmProject', { path: plan.python || '.venv' })
              : en('repairConfirmManaged')}
          </p>
          {toProject && (
            // 改用户自己的环境是不可逆的，这句不能藏起来
            <p className="mt-1 text-xs leading-relaxed text-warn">{en('repairModifiesEnv')}</p>
          )}
          <p className="mt-1 text-xs leading-relaxed text-ink-3">
            {en('repairWillInstall', { requirement: plan.requirement })}
            {plan.network_required ? ` · ${en('repairNeedsNetwork')}` : ''}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Button variant="primary" disabled={busy} onClick={() => install()}>
            {toProject ? en('repairInstallToProject') : en('repairPrepareAndContinue')}
          </Button>
          <Button onClick={reset}>{en('repairBack')}</Button>
        </div>
        <Failure code={errorCode} text={errorText} />
      </div>
    )
  }

  // ---- 起点：给出口 -------------------------------------------------------
  const exhausted = offer.code === 'dependency_repair_rounds_exhausted'
  // 采用这台机器上已有的解释器**不需要**解析出包名，也不消耗修复轮次
  //（它什么都不装）：解析不出 / 轮次用完时它照样列出——那正是用户仅剩的路。
  // 安装目标则两个前提都要。
  const targets = offer.targets.filter(
    (tg) =>
      tg.available !== false &&
      (tg.kind === 'system_interpreter' || (offer.requirement && !exhausted)),
  )
  // 「指定安装包」要装到哪：第一个**安装**目标。系统解释器不是安装目标
  //（采用它一个字节都不装），排在最前时也不能被当成装包的地方。
  const installTarget = offer.targets.find((tg) => tg.kind !== 'system_interpreter')
  const rejected = offer.system_rejected ?? []
  return (
    <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
      <div>
        <h3 className="text-xs font-medium text-ink">{en('repairTitle', { module: pkg })}</h3>
        <p className="mt-1 text-xs leading-relaxed text-ink-2">
          {offer.requirement
            ? en('repairBody')
            : en('repairUnresolved', { module })}
        </p>
        {exhausted && (
          <p className="mt-1 text-xs leading-relaxed text-ink-3">{en('repairExhausted')}</p>
        )}
      </div>

      {targets.length > 0 && (
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
                  variant={tg.kind === targets[0].kind ? 'primary' : 'ghost'}
                  disabled={busy}
                  onClick={() =>
                    tg.kind === 'system_interpreter'
                      ? // 采用已有的解释器不经 plan：没有要安装的东西可以「计划」
                        void adoptSystemPython(tg.python, module)
                      : makePlan({ module, script, target: tg.kind })
                  }
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
      )}

      {/* 探到了但没采用的系统解释器（ADR 0044）：用户手边明明有一套装了那个包
          的 Python，Tavotto 为什么没用它——不说出来，他看到的就是「缺包，
          要不要建一个新环境」，而自己的环境像是被无视了。 */}
      {rejected.length > 0 && (
        <div className="flex flex-col gap-0.5">
          {rejected.map((r) => (
            <p key={r.python} className="text-xs leading-relaxed text-ink-3">
              {rejectionText(r, pkg)}
            </p>
          ))}
        </div>
      )}

      {/* 解析不出包名：用户可以自己指定，但那串东西同样要过后端的语法关 */}
      {!offer.requirement && !exhausted && (
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
                })
              }
            >
              {en('repairContinue')}
            </Button>
          </div>
        </div>
      )}

      {/* **任何一条修复路径走不通时的兜底出口**（ADR 0019 §五 与兼容层
          Layer 4）：换一个已经装好那个包的 Python。它必须**始终在**——
          解析不出包名、没有可用目标、装完还是失败，用户都还有这条路。
          少了它，文案里那句「或者换一个已经装好它的 Python 环境」就指不出
          任何控件（e2e 抓到过一次：卡片只剩「指定安装包」）。 */}
      <OtherPython />

      <Failure code={errorCode} text={errorText} />
    </div>
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
    <div className="flex flex-col gap-1.5 border-t border-border pt-2.5">
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
  // 受管环境不需要副标题：装到哪按钮自己已经说清楚了（「将 X 安装到 Tavotto
  // 环境」——那本来就不是用户的环境），再补一句「不动你已有的环境」只是把同一
  // 件事说第二遍。真会动用户环境的是项目 .venv 那条，那句警告在确认页里。
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
      })
    case 'project_env_no_matplotlib':
      return en('repairSystemRejectedNoMatplotlib', { python: r.python, module: pkg })
    default:
      return en('repairSystemRejectedUnusable', { python: r.python, module: pkg })
  }
}

/**
 * 安装进度。四个阶段各一句话，pip 日志折叠在「安装详情」里。
 *
 * 取消之后**不假装完整回滚**：改的是用户自己的环境时如实说「可能已发生
 * 部分修改」——那正是「改用户环境必须明确确认」的另一面。
 */
function RepairProgress({
  module,
  onCancel,
  onDone,
}: {
  module: string
  onCancel: () => void
  onDone: () => void
}) {
  useTranslation('errors')
  const { progress } = useDepRepairStore()
  if (!progress) return null
  const running = isRepairRunning(progress)
  const key = STATE_KEY[progress.state] ?? 'repairPreparing'
  const failed = progress.state === 'failed'
  const cancelled = progress.state === 'cancelled'
  return (
    <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
      <div>
        <h3 className="text-xs font-medium text-ink">
          {en(key, { module: progress.distribution || module })}
        </h3>
        {failed && (
          <p className="mt-1 text-xs leading-relaxed text-danger">
            {repairCodeMessage(progress.code) ?? progress.error ?? ''}
          </p>
        )}
        {cancelled && (
          <p className="mt-1 text-xs leading-relaxed text-ink-2">
            {progress.target_kind === 'project_venv'
              ? en('repairCancelledProjectEnv')
              : en('repairCancelledManaged')}
          </p>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {running ? (
          <Button onClick={onCancel}>{en('repairCancel')}</Button>
        ) : (
          <Button onClick={onDone}>{en('repairClose')}</Button>
        )}
      </div>
      {progress.log && (
        <Details className="text-xs text-ink-3">
          <Summary className="text-ink-2">{en('repairDetails')}</Summary>
          <pre className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-sm bg-surface-2 p-1.5 font-mono text-xs">
            {progress.log}
          </pre>
        </Details>
      )}
    </div>
  )
}

/**
 * 稳定错误码 → 当前语言的一句话；没登记的回 null（让调用方用后端原文兜底）。
 * 包管理页（`settings/PackagesSettings.tsx`）与这张卡共用这一份：两处的码
 * 来自同一个后端漏斗（`app._repair_error`）、同一张文案表。
 */
export function repairCodeMessage(code: string): string | null {
  if (!code) return null
  const key = `repairError.${code}`
  const text = en(key)
  return text === `engine.${key}` ? null : text
}

function Failure({ code, text }: { code: string; text: string }) {
  if (!code && !text) return null
  return <p className="text-xs leading-relaxed text-danger">{repairCodeMessage(code) ?? text}</p>
}

/**
 * Tavotto 受管环境的「重建」入口（设置页里）。
 *
 * 只对**我们自己建的**环境出现：用户的 `.venv` 不归我们重建，那是他的东西。
 */
export function ManagedEnvironmentRow() {
  useTranslation('errors')
  const { env } = useEnvStore()
  const { busy, rebuildManaged } = useDepRepairStore()
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
      {/* 重建会真动环境，不该长得像一句可点的说明文字：换成组件库的 Button
          （默认变体、self-start 不撑满），busy 时禁用的行为与文案来源不变。
          mt-1.5 是让它和上面两行环境说明拉开，不跟着 gap-0.5 贴成一坨。
          再补一圈实边框（border-strong 而不是 border——这块坐在白色 surface
          上，浅一档的边线几乎看不出来）：这一行只有它一个可点的东西，要让
          「这是按钮」在扫一眼时就成立，而不是靠 hover 才显形。 */}
      <Button
        className="mt-1.5 self-start border border-border-strong"
        disabled={busy}
        onClick={() => void rebuildManaged()}
      >
        {en('managedEnvRebuild')}
      </Button>
    </div>
  )
}

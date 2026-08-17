import { useEffect, useState } from 'react'
import { useEnvStore } from '@/store/envStore'
import type { EngineSource } from '@/lib/api'
import { Button } from './ui/Button'
import { TextInput } from './ui/Input'

/**
 * 渲染环境的状态与出口。
 *
 * 三种局面，给的东西完全不同：
 *
 *  1. **一切正常**（多数用户，尤其 Windows 桌面版——安装包自带内置环境）。
 *     `compact` 时什么都不显示：正常工作流里不该有一个常驻卡片提醒你「环境没问题」。
 *     设置页里显示一行状态 + 折叠起来的高级入口。
 *  2. **缺环境**（源码 / pip 安装，机器上没有科学栈）：给「自动安装」按钮。
 *  3. **内置环境缺失或损坏**（桌面版）：这不是用户的环境问题，是我们的安装包
 *     不完整——只能让他重装，绝不假装能现场修（embeddable 里连 pip 都没有）。
 *
 * 「用户脚本要的包内置环境里没有」是第四种，由渲染错误单独引导（见
 * MissingDependencyCard），不在这里处理——那时环境本身是好的。
 */

const SOURCE_LABEL: Record<Exclude<EngineSource, ''>, string> = {
  bundled: 'Magplot 内置环境',
  configured: '你指定的环境',
  managed_venv: 'Magplot 自建的环境',
  env_override: '环境变量 MM_WORKER_PYTHON',
  current_process: 'Magplot 自身的解释器',
  system: '系统 Python / Conda',
}

export function EngineEnvironmentCard({ compact }: { compact?: boolean }) {
  const { env, log, installing, refresh, install, setPython } = useEnvStore()
  const [manual, setManual] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [advanced, setAdvanced] = useState(false)

  useEffect(() => {
    if (!env) void refresh()
  }, [env, refresh])

  if (!env) return null
  // 正常工作流里不制造多余提示：环境没问题时，紧凑位置（图内元素面板）什么都不显示
  if (env.ok && compact) return null

  const apply = async () => {
    const msg = await setPython(manual.trim() || null)
    setError(msg)
    if (!msg) setManual('')
  }

  const advancedBlock = (
    <div className="flex flex-col gap-1.5 border-t border-border pt-2.5">
      {advanced ? (
        <>
          <span className="text-xs text-ink-2">使用其他 Python 环境</span>
          <div className="flex items-center gap-1.5">
            <TextInput
              value={manual}
              onChange={(e) => setManual(e.target.value)}
              placeholder="/path/to/python 或 conda 环境里的 python"
              aria-label="渲染解释器路径"
            />
            <Button onClick={() => void apply()}>应用</Button>
          </div>
          <p className="text-xs leading-relaxed text-ink-3">
            脚本用到内置环境里没有的包（rdkit、astropy…）时换成你自己那套。
            留空并应用即可恢复默认。Magplot
            <strong className="font-medium text-ink-2">不会改动你选中的环境</strong>，
            只是启动它来渲染。
          </p>
          {error && <p className="text-xs text-danger">{error}</p>}
        </>
      ) : (
        <button
          type="button"
          onClick={() => setAdvanced(true)}
          className="self-start text-xs text-accent hover:underline"
        >
          使用其他 Python 环境…
        </button>
      )}
    </div>
  )

  // ---- 1. 一切正常 -------------------------------------------------------
  if (env.ok) {
    const label = env.source ? SOURCE_LABEL[env.source] : '已配置的环境'
    return (
      <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
        <div>
          <h3 className="text-xs font-medium text-ink">渲染环境</h3>
          <p className="mt-1 text-xs leading-relaxed text-ink-2">
            {label}
            {env.matplotlib && (
              <span className="ml-1.5 font-mono text-ink-3">matplotlib {env.matplotlib}</span>
            )}
          </p>
          {env.bundled ? (
            <p className="mt-1 text-xs leading-relaxed text-ink-3">
              常用科学栈（numpy / matplotlib / pandas / scipy / seaborn / Pillow）已随
              Magplot 一起安装，不需要你另外装 Python，首次渲染也不联网。
            </p>
          ) : (
            <p className="mt-1 break-all font-mono text-xs text-ink-3">{env.python}</p>
          )}
        </div>
        {env.bundled && Object.keys(env.runtime?.packages ?? {}).length > 0 && (
          <details className="text-xs text-ink-3">
            <summary className="cursor-pointer select-none text-ink-2">内置包版本</summary>
            <ul className="mt-1 flex flex-col gap-0.5 font-mono">
              {Object.entries(env.runtime.packages).map(([name, ver]) => (
                <li key={name}>
                  {name} {ver}
                </li>
              ))}
            </ul>
          </details>
        )}
        {!compact && advancedBlock}
      </div>
    )
  }

  // ---- 3. 内置环境缺失 / 损坏（桌面版）-----------------------------------
  if (env.runtime?.expected) {
    return (
      <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
        <div>
          <h3 className="text-xs font-medium text-ink">安装文件不完整</h3>
          <p className="mt-1 text-xs leading-relaxed text-ink-2">
            Magplot 自带的渲染环境
            {env.code === 'bundled_runtime_invalid' ? '已损坏' : '不见了'}
            ——请重新安装 Magplot。如果是杀毒软件误删，安装后把 Magplot
            的安装目录加入白名单。
          </p>
          <p className="mt-1 text-xs leading-relaxed text-ink-3">
            排版、标注和导出不受影响，只有图内元素编辑需要渲染环境。
            设置 →「环境诊断」可以导出诊断包。
          </p>
        </div>
        {!compact && advancedBlock}
      </div>
    )
  }

  // ---- 2. 缺环境（源码 / pip 安装）---------------------------------------
  return (
    <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
      <div>
        <h3 className="text-xs font-medium text-ink">尚未配置渲染环境</h3>
        <p className="mt-1 text-xs leading-relaxed text-ink-2">
          图内元素编辑需要一个装了 matplotlib 的 Python——Magplot 运行的是你自己的脚本，
          解释器得能 import 它们用到的库。排版、标注和导出不受影响。
        </p>
      </div>

      {env.can_install ? (
        <>
          <Button variant="primary" onClick={() => void install()} disabled={installing}>
            {installing ? '正在安装…' : '自动安装'}
          </Button>
          <p className="text-xs leading-relaxed text-ink-3">
            会在 Magplot 自己的目录里建一个独立环境并装上 matplotlib，
            <strong className="font-medium text-ink-2">不会改动你现有的任何 Python 环境</strong>。
            首次需要下载几十 MB。
          </p>
        </>
      ) : (
        <p className="text-xs leading-relaxed text-danger">
          这台机器上没找到可用的 Python。请先安装{' '}
          <a
            href="https://www.python.org/downloads/"
            target="_blank"
            rel="noreferrer"
            className="text-accent hover:underline"
          >
            Python 3.10 以上
          </a>
          （或 Anaconda），再回到这里。
        </p>
      )}

      {log && (
        <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-sm bg-surface-2 p-1.5 font-mono text-xs text-ink-3">
          {log}
        </pre>
      )}

      {!compact && advancedBlock}
    </div>
  )
}

/**
 * 用户脚本 import 了当前渲染环境里没有的包。
 *
 * 这一档**刻意不提供「帮你装上」**：往内置环境里随便 pip install 会让它不再
 * 可复现，也让「重装就能修」这条退路失效。给的是另一个出口——换成用户自己
 * 那套已经装好这些包的科研环境。
 */
export function MissingDependencyCard({ module }: { module: string }) {
  const { setPython } = useEnvStore()
  const [manual, setManual] = useState('')
  const [error, setError] = useState<string | null>(null)

  return (
    <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
      <div>
        <h3 className="text-xs font-medium text-ink">
          渲染环境里没有 <span className="font-mono">{module || '这个包'}</span>
        </h3>
        <p className="mt-1 text-xs leading-relaxed text-ink-2">
          Magplot 内置的是常用科学栈（numpy / matplotlib / pandas / scipy / seaborn /
          Pillow）。这个脚本还需要别的包——把渲染环境换成你平时跑它的那套
          Python / Conda 环境即可。
        </p>
      </div>
      <div className="flex items-center gap-1.5">
        <TextInput
          value={manual}
          onChange={(e) => setManual(e.target.value)}
          placeholder="/path/to/python 或 conda 环境里的 python"
          aria-label="渲染解释器路径"
        />
        <Button onClick={() => void setPython(manual.trim() || null).then(setError)}>
          应用
        </Button>
      </div>
      {error && <p className="text-xs text-danger">{error}</p>}
      <p className="text-xs leading-relaxed text-ink-3">
        Magplot 只是启动它来渲染，
        <strong className="font-medium text-ink-2">不会往里面安装任何东西</strong>。
      </p>
    </div>
  )
}

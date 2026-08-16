import { useEffect, useState } from 'react'
import { useEnvStore } from '@/store/envStore'
import { Button } from './ui/Button'
import { TextInput } from './ui/Input'

/**
 * 缺渲染环境时的引导。⚡ 编辑需要一个能 import 用户脚本依赖的 Python——
 * 这不是错误，是一次可以当场解决的缺件，所以给按钮而不是给报错。
 *
 * 「自动安装」建的是 Magplot 自己数据目录下的隔离环境，**不动用户已有的环境**：
 * 那是他做研究用的，装坏了后果由他承担。
 */
export function EngineEnvironmentCard({ compact }: { compact?: boolean }) {
  const { env, log, installing, refresh, install, setPython } = useEnvStore()
  const [manual, setManual] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!env) void refresh()
  }, [env, refresh])

  if (!env || env.ok) return null

  const apply = async () => {
    setError(await setPython(manual.trim() || null))
  }

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

      {!compact && (
        <div className="flex flex-col gap-1.5 border-t border-border pt-2.5">
          <span className="text-xs text-ink-2">或指定你已有的解释器</span>
          <div className="flex items-center gap-1.5">
            <TextInput
              value={manual}
              onChange={(e) => setManual(e.target.value)}
              placeholder="/path/to/python 或 conda 环境里的 python"
              aria-label="渲染解释器路径"
            />
            <Button onClick={() => void apply()}>应用</Button>
          </div>
          <p className="text-xs text-ink-3">
            用你画图时那个环境效果最好——脚本要的 scipy、pandas 之类只有它才有。
          </p>
          {error && <p className="text-xs text-danger">{error}</p>}
        </div>
      )}
    </div>
  )
}

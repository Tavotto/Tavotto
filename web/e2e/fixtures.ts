import { test as base, expect } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import { cpSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'

const REPO = path.resolve(import.meta.dirname, '..', '..')

/**
 * 启动被测应用。
 *
 * 默认打的是**打包产物**（`TAVOTTO_EXE`），CI 上就是 PyInstaller 出来的
 * .exe——「本地能跑、装完就崩」的问题只有这样才拦得住。本地没有产物时
 * 退回 `python -m tavotto`，方便边写边跑。
 *
 * 本地跑之前记得 `python scripts/build_frontend.py`：包内 `src/tavotto/web/`
 * 优先于 `web/dist`，只跑 `pnpm build` 的话测的还是上一次的界面。
 */
function launchCommand(): { cmd: string; args: string[] } {
  const exe = process.env.TAVOTTO_EXE
  if (exe) return { cmd: exe, args: [] }
  const py = process.env.TAVOTTO_PYTHON ?? path.join(REPO, '.venv', 'bin', 'python')
  return { cmd: py, args: ['-m', 'tavotto'] }
}

async function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.once('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const port = (srv.address() as net.AddressInfo).port
      srv.close(() => resolve(port))
    })
  })
}

export interface AppOptions {
  /** 图库目录；不给就用 examples/figures 的一份拷贝 */
  figures?: string
  /** 不带 --figures 启动：模拟「首次启动，用户目录为空」 */
  noProject?: boolean
  /** 额外环境变量（用来伪造「没装 Python」「只有 .cmd 的 CLI」等场景） */
  env?: Record<string, string>
  /** 指定端口（测端口冲突时用） */
  port?: number
}

export interface RunningApp {
  baseURL: string
  port: number
  home: string
  figures: string
  dataDir: string
  proc: ChildProcess
  /** 应用进程的 stdout/stderr（含 app.log 同款启动日志），失败诊断用 */
  logs: string[]
  stop(): Promise<void>
}

/** 起一个**全新用户目录**的实例：每个场景都真的从零开始。 */
export async function startApp(opts: AppOptions = {}): Promise<RunningApp> {
  const workdir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-'))
  const home = path.join(workdir, 'home')
  const dataDir = path.join(workdir, 'data')
  for (const d of [home, dataDir, path.join(workdir, 'AppData', 'Roaming'),
                   path.join(workdir, 'AppData', 'Local')]) {
    mkdirSync(d, { recursive: true })
  }

  let figures = opts.figures
  if (!figures && !opts.noProject) {
    figures = path.join(workdir, 'figures')
    cpSync(path.join(REPO, 'examples', 'figures'), figures, { recursive: true })
  }

  const port = opts.port ?? (await freePort())
  const { cmd, args } = launchCommand()
  const proc = spawn(
    cmd,
    [...args, '--port', String(port), '--no-browser',
     ...(figures ? ['--figures', figures] : [])],
    {
      env: {
        ...process.env,
        TAVOTTO_DATA_DIR: dataDir,
        TAVOTTO_CONFIG_DIR: path.join(workdir, 'config'),
        TAVOTTO_ALLOW_SHUTDOWN: '1',
        // 匿名用量统计硬关。两件事：① e2e 每次都是全新的配置目录，
        // 同意态是 unset，首启询问框会盖在画布上，之后每一次点击都点在
        // 遮罩上；② 就算它不挡路，CI 也绝不该产生真实的产品事件。
        TAVOTTO_NO_TELEMETRY: '1',
        HOME: home,
        USERPROFILE: home,
        APPDATA: path.join(workdir, 'AppData', 'Roaming'),
        LOCALAPPDATA: path.join(workdir, 'AppData', 'Local'),
        ...opts.env,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  )
  const logs: string[] = []
  proc.stdout?.on('data', (b) => logs.push(String(b)))
  proc.stderr?.on('data', (b) => logs.push(String(b)))

  const baseURL = `http://127.0.0.1:${port}`
  const deadline = Date.now() + 120_000
  for (;;) {
    if (proc.exitCode !== null) {
      throw new Error(`应用在就绪前退出（code=${proc.exitCode}）\n${logs.join('')}`)
    }
    try {
      const r = await fetch(`${baseURL}/api/version`)
      if (r.ok) break
    } catch {
      /* 还没起来 */
    }
    if (Date.now() > deadline) {
      throw new Error(`120s 内没起来\n${logs.join('')}`)
    }
    await new Promise((r) => setTimeout(r, 500))
  }

  return {
    baseURL,
    port,
    home,
    figures: figures ?? '',
    dataDir,
    proc,
    logs,
    async stop() {
      try {
        await fetch(`${baseURL}/api/shutdown`, { method: 'POST' })
      } catch {
        proc.kill()
      }
      await new Promise((r) => setTimeout(r, 800))
      if (proc.exitCode === null) proc.kill('SIGKILL')
      rmSync(workdir, { recursive: true, force: true })
    },
  }
}

/** 造一个「文件名只有运行时才知道」的图库，用来测脚本注册表那条路径。 */
export function writeRuntimeNamedProject(dir: string): void {
  mkdirSync(dir, { recursive: true })
  writeFileSync(
    path.join(dir, 'render_map.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      'from pathlib import Path',
      '',
      'NAMES = None  # 运行期才决定，静态扫描解不出',
      '',
      'def main():',
      '    for name in (NAMES or ["Runtime_map"]):',
      '        fig, ax = plt.subplots(figsize=(2, 2))',
      '        ax.plot([0, 1], [1, 0])',
      '        fig.savefig(Path(f"{name}.pdf"))',
      '        plt.close(fig)',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(path.join(dir, 'tavotto_registry.json'),
                JSON.stringify({ version: 1, scripts: {} }), 'utf-8')
}

/** 每个用例自带一个干净实例；用例里按需 `await app()` 拿到它。 */
export const test = base.extend<{ app: (o?: AppOptions) => Promise<RunningApp> }>({
  // 第二个参数是 Playwright 的「交出去再收回来」回调。名字不叫 use 是因为
  // lint 会把 `use(...)` 当成 React Hook 调用（它是位置参数，随便起名）。
  // 第一参必须写成对象解构（哪怕不取任何内置 fixture）——Playwright 靠这个
  // 语法形态解析依赖，写普通标识符它在加载期直接拒收。
  // oxlint-disable-next-line no-empty-pattern
  app: async ({}, provide, testInfo) => {
    const started: RunningApp[] = []
    await provide(async (o?: AppOptions) => {
      const a = await startApp(o)
      started.push(a)
      return a
    })
    // 失败时先把后端现场保下来再清理：app.log 在 dataDir 里，rmSync 之后
    // 就什么都不剩了——CI 只收集 test-results/**，拷进去才带得走。
    const failed = testInfo.status !== testInfo.expectedStatus
    for (const [i, a] of started.entries()) {
      if (failed) {
        try {
          cpSync(a.dataDir, testInfo.outputPath(`app-${i}-data`), { recursive: true })
        } catch { /* 数据目录可能没建出来 */ }
        console.log(`[e2e] app-${i} 进程输出（尾部）:\n${a.logs.slice(-40).join('')}`)
      }
      await a.stop()
    }
  },
})

export { expect }

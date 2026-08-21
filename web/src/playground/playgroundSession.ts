/**
 * playground 会话编排：Worker 生命周期 ↔ 既有 stores。
 *
 * 生命周期（ADR 0007）：一个源文件 = 一个 Worker = 一个 Pyodide 会话。
 * 换文件不复用解释器——`teardown` 直接 terminate，比在活着的解释器里
 * 追着清 matplotlib 全局状态可靠得多，也是唯一能从坏 Python 状态里
 * 恢复的办法。**暖机的那个 Worker 还没跑过任何用户代码**，所以它可以
 * 当第一个会话用（`prewarm.ts`）；跑过脚本的解释器一律不复用。
 */
import { msg } from '@/i18n'
import { seedEmbeddedSession } from '@/embedded/session'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { installBrowserTransport } from './browserEngineTransport'
import { engineZipUrl, takeWarmClient } from './prewarm'
import { PlaygroundClient } from './pyodideClient'
import type { LoadResult, PlaygroundPhase } from './protocol'
import { compareHashes, sha256Hex, type SourceIntegrity } from './sourceIntegrity'
import { PYODIDE_BASE_URL, SUPPORTED_ROOTS } from './runtime'

export interface ActiveSession {
  client: PlaygroundClient
  /** 用户给的文件名（界面显示用） */
  filename: string
  /** 虚拟 FS 里实际被执行的脚本名——完整性说的是**这个**文件 */
  scriptName: string
  /** 用户给的原文，**只读**——只读源码面板显示它 */
  originalSource: string
  /** 「源文件未被修改」的当前结论；只有真正核对完才会变成 unchanged */
  integrity: SourceIntegrity
  uninstallTransport: () => void
}

/**
 * 起会话：（暖着的或新的）Worker → Pyodide → engine → 分类 → 包 → 跑脚本。
 * 失败时（含超时/崩溃）client 已经自毁，调用方只需展示错误。
 *
 * 原文哈希在主线程用 Web Crypto 算，与 Worker 里 Python 读回来算的那个
 * 是两套独立实现——`sourceIntegrity.ts` 说明了为什么必须这样。
 */
export async function startSession(
  filename: string,
  source: string,
  onProgress: (phase: PlaygroundPhase) => void,
): Promise<{ session: ActiveSession; load: LoadResult; prewarmed: boolean }> {
  const { client, wasWarm } = takeWarmClient()
  client.onProgress = onProgress
  // 传输先装后种：stores 一挂载就可能发渲染请求，那一刻拿到默认的 HTTP
  // 传输会打到一个不存在的 /api（静态页面后面没有 Tavotto 服务）
  const uninstallTransport = installBrowserTransport(client)
  try {
    // 哈希与运行时初始化并行——两件事互不相干，没必要串起来等
    const originalPromise = sha256Hex(source)
    await client.init(PYODIDE_BASE_URL, engineZipUrl())
    const load = await client.load(filename, source, SUPPORTED_ROOTS)
    const integrity = compareHashes(await originalPromise, load.source_sha256, Date.now())
    return {
      session: {
        client,
        filename,
        scriptName: load.script || filename,
        originalSource: source,
        integrity,
        uninstallTransport,
      },
      load,
      prewarmed: wasWarm,
    }
  } catch (err) {
    uninstallTransport()
    client.dispose()
    throw err
  }
}

/**
 * 重新核对源文件完整性：让 Worker 再读一次虚拟 FS 里的脚本并算 sha256。
 *
 * 编辑走的是 override 层，按设计碰不到这个文件——所以不必每次指针事件都
 * 验一遍；在**有意义的时刻**验（第一次改完、打开源码面板）就够，而界面
 * 显示的状态必须对应一次**真正完成过**的核对。
 */
export async function verifySourceIntegrity(session: ActiveSession): Promise<SourceIntegrity> {
  try {
    const status = await session.client.sourceStatus()
    return compareHashes(session.integrity.originalSha256, status.sha256, Date.now())
  } catch {
    // 会话已经死了/请求失败：这是「查不了」，不是「被改了」——两者别混
    return {
      ...session.integrity,
      verdict: 'unavailable',
      reason: 'worker_error',
    }
  }
}

/** 打开一张图进编辑态：真 manifest + 真 SVG 灌进既有 stores。 */
export async function openFigure(
  session: ActiveSession,
  stem: string,
): Promise<{ panelId: string; fileId: string }> {
  const opened = await session.client.open(stem)
  return seedEmbeddedSession(
    {
      stem: opened.stem,
      project: '/workspace',
      script: session.scriptName,
      cost: 'light',
      manifest: opened.manifest,
      svg: opened.svg,
      renderRevision: opened.render_revision,
      warnings: opened.warnings,
    },
    msg('history.playgroundOpenFigure', undefined, 'workspace'),
  )
}

/** 收尾：卸传输、杀 Worker、清渲染态。幂等。 */
export function teardownSession(session: ActiveSession | null): void {
  if (!session) return
  session.uninstallTransport()
  session.client.dispose()
  useUiStore.getState().setElementPanel(null)
  useRenderStore.getState().clear()
  useDocumentStore.setState({ past: [], future: [], dirty: false })
}

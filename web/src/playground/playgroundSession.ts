/**
 * playground 会话编排：Worker 生命周期 ↔ 既有 stores。
 *
 * 生命周期（ADR 0007）：一个源文件 = 一个 Worker = 一个 Pyodide 会话。
 * 换文件不复用解释器——`teardown` 直接 terminate，比在活着的解释器里
 * 追着清 matplotlib 全局状态可靠得多，也是唯一能从坏 Python 状态里
 * 恢复的办法。
 */
import { msg } from '@/i18n'
import { seedEmbeddedSession } from '@/embedded/session'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { installBrowserTransport } from './browserEngineTransport'
import { PlaygroundClient } from './pyodideClient'
import type { LoadResult, PlaygroundPhase } from './protocol'
import { ENGINE_ZIP_NAME, PYODIDE_BASE_URL, SUPPORTED_ROOTS } from './runtime'

export interface ActiveSession {
  client: PlaygroundClient
  filename: string
  /** 用户给的原文，**只读**——「源文件未被修改」的证明就是与它比对 */
  originalSource: string
  /** 实际送进 Worker 的那份（与 originalSource 必须始终相同） */
  loadedSource: string
  uninstallTransport: () => void
}

/** engine.zip 与页面同目录（构建脚本放在 dist 根），按页面地址解析。 */
function engineZipUrl(): string {
  return new URL(ENGINE_ZIP_NAME, document.baseURI).href
}

/**
 * 起会话：新 Worker → Pyodide → engine → 分类 → 包 → 跑脚本。
 * 失败时（含超时/崩溃）client 已经自毁，调用方只需展示错误。
 */
export async function startSession(
  filename: string,
  source: string,
  onProgress: (phase: PlaygroundPhase) => void,
): Promise<{ session: ActiveSession; load: LoadResult }> {
  const client = new PlaygroundClient()
  client.onProgress = onProgress
  client.start()
  // 传输先装后种：stores 一挂载就可能发渲染请求，那一刻拿到默认的 HTTP
  // 传输会打到一个不存在的 /api（静态页面后面没有 Tavotto 服务）
  const uninstallTransport = installBrowserTransport(client)
  try {
    await client.init(PYODIDE_BASE_URL, engineZipUrl())
    const load = await client.load(filename, source, SUPPORTED_ROOTS)
    return {
      session: {
        client,
        filename,
        originalSource: source,
        loadedSource: source,
        uninstallTransport,
      },
      load,
    }
  } catch (err) {
    uninstallTransport()
    client.dispose()
    throw err
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
      script: session.filename,
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

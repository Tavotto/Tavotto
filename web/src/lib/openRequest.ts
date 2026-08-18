/**
 * 交接请求：外部程序（Codex 插件、`magplot open`、编辑器、别的 Agent）把一张
 * 刚画好的图送进来时，前端要做的三件事——切到那个项目、把面板放进画布、选中它。
 *
 * 两条入口共用这一份实现，**语义必须完全一样**：
 *   * 浏览器模式 / 桌面首启：地址栏 `?open=<stem>`（项目已由后端 `--figures`
 *     或 `?pj=` 认领），落地形状的唯一出处是
 *     `src/magplot/engine/handoff.py` 的 `browser_url()`；
 *   * 桌面二次交接：Tauri 事件 `magplot:open`（带 project + stem）——单实例插件
 *     把第二次启动的 argv 转发给已经在跑的窗口，后端一套进程不动。
 *
 * 三条纪律：
 *  1. **同一个项目绝不调 projectStore.open**：那条路会 switchDocument 成空白
 *     文档，用户正在排的版当场没了。同项目只重扫素材。
 *  2. **必须重扫素材**：交接的图是刚刚才写到磁盘上的，运行中的实例手里那份
 *     panels 是旧的，不重扫就是「打开了，但说找不到这张图」。
 *  3. **找不到就说找不到**：绝不退而求其次选中别的面板——用户要的是那一张。
 */
import { msg } from '@/i18n'
import { addPanel } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelInfo } from '@/lib/api'

export interface OpenRequest {
  /** 图库目录绝对路径；桌面事件才有，URL 形态下项目由后端认领 */
  project?: string | null
  /** 产物文件名主干（Fig1_kinetics）——注册表与引擎认的就是它。
   *  可以没有：`magplot open <目录>` 是「把这个图库打开」，不指定面板。 */
  stem?: string | null
}

export type OpenOutcome =
  | 'placed'        // 面板已加入画布并选中
  | 'selected'      // 画布上本来就有，只是选中
  | 'project-only'  // 只交接了项目，没指定面板
  | 'missing'       // 项目里找不到这个 stem
  | 'no-project'    // 没有项目可落
  | 'failed'

/** 素材 id 是图库相对路径（可能带子目录）；stem = 去目录去扩展名。 */
export function stemOf(fileId: string): string {
  const base = fileId.split(/[/\\]/).pop() ?? fileId
  const dot = base.lastIndexOf('.')
  return dot > 0 ? base.slice(0, dot) : base
}

function findByStem(panels: PanelInfo[], stem: string): PanelInfo | undefined {
  // 同 stem 的 PDF 与 PNG 可能同时在（写回会两个载体一起更新）：矢量优先，
  // 它才是能进图内编辑、能导出真矢量的那一份。
  const hits = panels.filter((p) => stemOf(p.id) === stem)
  return hits.find((p) => p.kind === 'pdf') ?? hits[0]
}

const norm = (p: string) => p.replace(/[/\\]+$/, '')

/**
 * 地址栏里的 `?open=<stem>`。认下之后立刻抹掉——项目与面板归属都在应用状态里，
 * 地址栏留着它只会在用户之后自己换项目时变成一个撒谎的 URL（与 lib/session.ts
 * 处理 `?pj=` 的理由完全一样）。
 */
export function readOpenRequestFromUrl(): OpenRequest | null {
  try {
    const stem = new URLSearchParams(window.location.search).get('open')
    if (!stem) return null
    const url = new URL(window.location.href)
    url.searchParams.delete('open')
    window.history.replaceState(null, '', url.pathname + url.search + url.hash)
    return { stem }
  } catch {
    return null
  }
}

/** 执行一次交接。返回值给测试与调用方看，用户看到的是选中的面板或一条 toast。 */
export async function applyOpenRequest(req: OpenRequest): Promise<OpenOutcome> {
  const stem = (req.stem ?? '').trim()
  if (!stem && !req.project) return 'failed'
  const ui = useUiStore.getState()

  try {
    const proj = useProjectStore.getState()
    const current = proj.project?.figures_dir
    if (req.project && (!current || norm(current) !== norm(req.project))) {
      // 换项目：projectStore.open 里已经先冲刷了当前文档的自动保存
      await proj.open(req.project)
    } else if (proj.phase !== 'open') {
      ui.setStatus(msg('handoff.noProject', undefined, 'project'), 'error')
      return 'no-project'
    } else {
      await useAssetStore.getState().load()
    }
  } catch (err) {
    ui.setStatus(
      msg(
        'handoff.openFailed',
        { error: err instanceof Error ? err.message : String(err) },
        'project',
      ),
      'error',
    )
    return 'failed'
  }

  // `magplot open <目录>`：只把图库换过来，不指定面板
  if (!stem) {
    ui.setStatus(
      msg('handoff.projectOpened', { name: useProjectStore.getState().project?.name ?? '' }, 'project'),
    )
    return 'project-only'
  }

  const info = findByStem(useAssetStore.getState().panels, stem)
  if (!info) {
    ui.setStatus(msg('handoff.panelMissing', { stem }, 'project'), 'error')
    return 'missing'
  }

  // 画布上已经有这张图了就只选中它，不再叠一份（重复交接同一张是常态：
  // 用户在 Codex 里改一版就交一次，每次都新增会堆出一摞同名面板）
  const existing = useDocumentStore
    .getState()
    .doc.objects.find((o) => o.type === 'panel' && o.fileId === info.id)
  if (existing) {
    useSelectionStore.getState().set([existing.id])
    ui.setStatus(msg('handoff.located', { name: info.name }, 'project'))
    return 'selected'
  }

  addPanel(info)
  ui.setStatus(msg('handoff.added', { name: info.name }, 'project'))
  return 'placed'
}

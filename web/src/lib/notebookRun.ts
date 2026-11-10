/**
 * `.ipynb` 抽成同名 `.py` 再「打开并运行」——所有入口（脚本库、拖进画布、拖进主页）共用的这一条（ADR 0179）。
 *
 * 抽取与落盘全在后端（`POST /api/notebook/extract|import`：确定性、不经 AI、不联网，笔记本一个字节不动）；
 * 这里只翻译结果、接到现有的「打开并运行」准备流程（`projectPreparationStore.open`），不另造第二套运行路径。
 *
 * 出口只落三种结局（产品负责人 2026-11-10）：成了就静默做完并说一句；做不成就一句话 + 「发送问题反馈」
 * （抽取失败没有别的路可走：不是笔记本 / 版本太旧 / 没有代码 / 写不进去）。没有纯文字报错。
 */
import { msg, type UiMessage } from '@/i18n'
import { extractNotebook, importNotebook, type NotebookScript } from '@/lib/api'
import { feedbackAction, reasonOf, sayDrop } from '@/lib/canvasFileIntake'
import type { CanvasDropPlace } from '@/lib/canvasScriptDrop'
import { baseName } from '@/lib/scriptImport'
import { currentProjectId } from '@/lib/session'
import { scriptTarget, useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { useUiStore, type StatusAction } from '@/store/uiStore'

/** 要转的笔记本：项目里的（项目相对路径），或页面手里的 `File`（拿不到路径时，内容交给后端、抽成当前项目根里的 `.py`） */
export type NotebookInput = { path: string } | { file: File }

export interface RunNotebookOpts {
  /** 拖到画布上：跑出来的图放进这张画布、落在放下的位置（同 `.py` 的拖放） */
  place?: CanvasDropPlace | null
  /** 同一次放下里这次没处理的文件数（要说一声） */
  skipped?: number
}

const sayNotebook = (key: string, values: Record<string, unknown>, tone: 'done' | 'error', action?: StatusAction) =>
  useUiStore.getState().setStatus(msg(`notebook.${key}`, values, 'workspace'), tone, action ? { action } : undefined)

/** 抽取失败 / 没有项目：一句话 + 「发送问题反馈」（入口被关时不画按钮） */
export function sayNotebookFailed(name: string, reason: UiMessage): void {
  sayNotebook('failed', { name, reason }, 'error', feedbackAction())
}

/**
 * 转成脚本并运行。成功 `true`；失败（已说了话）`false`；中途换了项目（A 项目的响应绝不落进 B）`false` 且什么都不说。
 */
export async function runNotebook(input: NotebookInput, opts: RunNotebookOpts = {}): Promise<boolean> {
  const name = 'file' in input ? input.file.name : baseName(input.path)
  const pj = currentProjectId()
  // 没有项目（浏览器 playground / MCP 画布）：没有项目文件夹可放，如实说（那里没有反馈入口，不画按钮）
  if (pj === null) {
    sayNotebookFailed(name, msg('canvasDrop.noProjectFolder', undefined, 'workspace'))
    return false
  }
  let res: NotebookScript
  try {
    res = 'file' in input ? await importNotebook(input.file, input.file.name, pj) : await extractNotebook(input.path, pj)
  } catch (e) {
    if (currentProjectId() !== pj) return false
    sayNotebookFailed(name, reasonOf(e))
    return false
  }
  if (currentProjectId() !== pj) return false
  // 与主页同一道闸：路径里首尾带空白的段后端各脚本端点会 strip，可能找不到、或跑错同目录的另一个脚本
  if (res.script.split('/').some((seg) => seg !== seg.trim())) {
    sayDrop('cannotRun', { name: baseName(res.script) }, 'error', feedbackAction())
    return false
  }
  // 新写出来的 .py 要出现在脚本库里（`registry.changed` 通常也会触发，这里不等它）
  void useScriptLibraryStore.getState().load()
  void useProjectPreparationStore
    .getState()
    .open(scriptTarget(res.script, { reuse: true }), { run: true, placeIn: opts.place ?? undefined })
  const values = { notebook: name, script: baseName(res.script), count: opts.skipped ?? 0 }
  if ((opts.skipped ?? 0) > 0) sayNotebook('convertedMore', values, 'done')
  else sayNotebook('converted', values, 'done')
  return true
}

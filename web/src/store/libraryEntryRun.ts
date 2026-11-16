/**
 * 库模块卡（TVD-Y5JF）：改为运行 import 了那个库模块的入口脚本（后端 `engine/libmodule` 找到的）。
 *
 * * 入口在项目里：当前项目里「打开并运行」它（与脚本行 ▶ 同一个入口）。
 * * 在项目外：打开它所在的仓库文件夹（带着这个脚本进来，素材区只看它的图，ADR 0184 §三），再「打开并运行」——
 *   与拖进项目外脚本时的「打开它所在的项目」同一条路。点按钮本身就是确认，不另弹框。
 *
 * 回 false = 文件夹没能打开（调用方说一句话）；运行本身的结局由准备卡接着说。
 */
import type { LibraryEntryScript } from '@/lib/api'
import { scriptTarget, useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useProjectStore } from '@/store/projectStore'

export async function runEntryScript(entry: LibraryEntryScript): Promise<boolean> {
  if (entry.in_project && entry.script) {
    void useProjectPreparationStore.getState().open(scriptTarget(entry.script, { reuse: true }), { run: true })
    return true
  }
  try {
    await useProjectStore.getState().open(entry.open_root, false, { entry: 'script', script: entry.open_script })
  } catch {
    return false
  }
  void useProjectPreparationStore.getState().open(scriptTarget(entry.open_script, { reuse: true }), { run: true })
  return true
}

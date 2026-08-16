import { useEffect } from 'react'
import { isJustBakedBaseline } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { panelRotation, rotationSwaps, type PanelObject } from '@/types/document'

/** 文字/数值输入合并成一次渲染的窗口；颜色、开关、拖动结束走 immediate */
const DEBOUNCE_MS = 300

const timers = new Map<string, number>()

/**
 * 请求渲染。同一文件的连续请求会被合并：debounce 期内只保留最后一次，
 * 真正发出后由 renderStore 的 busy/queued 再兜一层。
 */
export function requestRender(fileId: string, patches: unknown[], immediate = false) {
  const store = useRenderStore.getState()
  store.patch(fileId, { wantPatches: JSON.stringify(patches) })

  const fire = () => {
    timers.delete(fileId)
    void store.render(fileId, patches)
  }
  window.clearTimeout(timers.get(fileId))
  if (immediate) fire()
  else timers.set(fileId, window.setTimeout(fire, DEBOUNCE_MS))
}

/** 立刻冲刷某文件挂起的渲染（松开拖动、切换枚举等） */
export function flushRender(fileId: string) {
  const t = timers.get(fileId)
  if (t == null) return
  window.clearTimeout(t)
  timers.delete(fileId)
  const want = useRenderStore.getState().get(fileId).wantPatches
  if (want) void useRenderStore.getState().render(fileId, JSON.parse(want))
}

/**
 * 引擎渲染的唯一驱动点：只要「文档里的 overrides」与「已渲染的 patches」不一致
 * 就重渲染。撤销/重做、AI 改脚本、文件变更全部经由同一条路径，无需各自触发。
 */
export function useEngineSync() {
  const objects = useDocumentStore((s) => s.doc.objects)
  const editingId = useUiStore((s) => s.elementPanelId)
  const byFile = useRenderStore((s) => s.byFile)

  useEffect(() => {
    const targets = objects.filter(
      (o): o is PanelObject =>
        o.type === 'panel' &&
        !!o.script &&
        // 编辑中 / 有图内修改 / 脚本已领先磁盘文件（AI 改过）。
        // 「只带基线、还没动过」的面板不渲染：磁盘文件本身就是那个样子，
        // 白跑一次引擎（heavy 脚本要几分钟）没有意义。
        (o.id === editingId ||
          !!byFile[o.fileId]?.tracked ||
          (o.overrides.length > 0 && !isJustBakedBaseline(o))),
    )

    for (const panel of targets) {
      const want = JSON.stringify(panel.overrides)
      const state = byFile[panel.fileId]
      if (state && (state.lastPatches === want || state.wantPatches === want)) continue
      // 进入编辑态的首次渲染立即发出，其余（打字等）走防抖
      requestRender(panel.fileId, panel.overrides, !state)
    }
  }, [objects, editingId, byFile])

  // 渲染回来的图幅尺寸变了（改了 size_mm）→ 同步面板原生尺寸并按新纵横比调高度
  useEffect(() => {
    for (const [fileId, state] of Object.entries(byFile)) {
      const size = state.manifest?.size_mm
      if (!size) continue
      const [wMm, hMm] = size
      const stale = objects.some(
        (o) =>
          o.type === 'panel' &&
          o.fileId === fileId &&
          (Math.abs(o.nativeW - wMm) > 0.05 || Math.abs(o.nativeH - hMm) > 0.05),
      )
      if (!stale) continue
      useDocumentStore.getState().silent((d) => {
        for (const o of d.objects) {
          if (o.type !== 'panel' || o.fileId !== fileId) continue
          o.nativeW = wMm
          o.nativeH = hMm
          // x/y/w/h 是旋转后的页面包围盒：90/270 时内容的长宽是互换的，
          // 直接按 hMm/wMm 调 o.h 会把旋转过的面板越调越偏
          if (rotationSwaps(panelRotation(o))) o.w = o.h * (hMm / wMm)
          else o.h = o.w * (hMm / wMm)
        }
      })
    }
  }, [byFile, objects])
}

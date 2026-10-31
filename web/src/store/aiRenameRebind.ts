/**
 * AI 改名之后，画布上已有的编辑跟着换身份（ADR 0083 §五）。
 *
 * 背景：override 的目标身份是显式 label 的摘要。AI 改图时若只改了某条线的 label（数据指纹逐部件相同），
 * 后端判它为“改名”并报出 `{stem, gid, from_identity, to_identity}`；本模块把画布上写在该 gid、旧身份上的
 * override 改写成新身份，否则下一次渲染引擎会把它们全部当“编辑的对象已找不到”拒绝。
 * 撤销这次 AI 修改（脚本回到旧 label）时反向改回。
 *
 * - **一条历史**：激活画布走 `documentStore.commit`（整次提交标 `restored`——这些身份是写下那一刻的事实，
 *   不许被抄写器按此刻的 manifest 重抄），非激活画布直接写快照（与 `renameCanvas` 同一个先例）。
 * - **不另外触发渲染**：改写的是 `overrides` 内容，`useEngineSync` 看到“文档 overrides ≠ 已渲染 patches”会自己渲染一次，
 *   与随 `panel.file_changed` 来的重建合并（撤销时本函数先于 `markStale`）。
 * - **幂等**：只改身份恰好等于起点的条目（见 `rebindOverrideIdentities`）。
 */
import { produce } from 'immer'
import { msg } from '@/i18n'
import { panelStemOfFileId } from '@/lib/panelStem'
import {
  rebindOverrideIdentities,
  type IdentityRename,
  type RebindDirection,
} from '@/lib/overrideIdentity'
import type { PanelObject } from '@/types/document'
import { useDocumentStore } from './documentStore'

/** 这个面板属于哪张图：运行时面板按持久化的捕获来源，磁盘面板按素材清单的 stem（缺省退回文件名 stem）。 */
function panelIsStem(panel: PanelObject, stem: string, rename: IdentityRename): boolean {
  // 运行时面板自带运行配置；后端只按一种配置做了提交后验证（磁盘面板恒跟默认配置 = 被验证的那份），
  // 别的配置下这条线可能没改名，它的 override 不能被改到新身份。配置对不上就不改（保守）
  if (panel.fileKind === 'runtime') return panel.source?.stem === stem && (panel.source?.runConfig ?? undefined) === rename.run_config
  // 权威 stem（素材清单）已知就只认它；`panelStemOfFileId` 查不到时自己退回文件名 stem。
  // 不能再 OR 一个文件名 stem：panel.png / panel.pdf 注册成 panel / panel-2 时会串到无关的图（#1251）
  return panelStemOfFileId(panel.fileId) === stem
}

/** 有没有会被改写的条目（只读预检：没有就不开提交，也不动画布快照）。 */
const wouldRebind = (
  objects: Parameters<typeof rebindOverrideIdentities>[0],
  renames: readonly IdentityRename[],
  direction: RebindDirection,
): boolean => rebindOverrideIdentities(objects, renames, direction, panelIsStem, false) > 0

const deferred: { renames: readonly IdentityRename[]; direction: RebindDirection; documentId: string | null }[] = []
let unsubscribe: (() => void) | null = null

/**
 * 改写画布上的 override 身份，回改了几条（所有画布合计）。`renames` 为空 → 什么都不做。
 * 调用方负责只在“这次修改确实落地”时调用：forward 在 AI 完成（`ai.done`，状态 done 且 changed），back 在撤销成功之后。
 */
export function rebindRenamedIdentities(renames: readonly IdentityRename[], direction: RebindDirection): number {
  if (!renames.length) return 0
  // 手势（拖动 / 属性页连续调整）开着时 commit 会并入它的事务：手势取消就把改写一起回滚，
  // 而 AI 的脚本改动留着。外部事件不替用户收手势，等事务结束后再作为独立一条历史落地
  // （入队按到达顺序，forward / back 不会错序）。此时回 0：改了几条要等事务结束才知道。
  if (useDocumentStore.getState().txn) {
    // 记下排队那一刻的文档：手势结束前换了文档（切项目 / 打开别的排版）就丢掉，不落到另一份文档上
    deferred.push({ renames, direction, documentId: useDocumentStore.getState().documentId })
    if (!unsubscribe) {
      unsubscribe = useDocumentStore.subscribe((s) => {
        if (s.txn) return
        unsubscribe?.()
        unsubscribe = null
        const queue = deferred.splice(0)
        for (const q of queue) {
          if (q.documentId === s.documentId) rebindRenamedIdentities(q.renames, q.direction)
        }
      })
    }
    return 0
  }
  const store = useDocumentStore.getState()
  let total = 0
  // 激活画布：一条历史
  if (wouldRebind(store.doc.objects, renames, direction)) {
    store.commit(
      msg('history.setProp', { prop: msg('prop.label', undefined, 'inspector') }, 'workspace'),
      (d) => {
        total += rebindOverrideIdentities(d.objects, renames, direction, panelIsStem)
      },
      { overrides: 'restored' },
    )
  }
  // 非激活画布：直接写快照
  const inactive = store.canvases.filter((c) => c.id !== store.activeCanvasId && wouldRebind(c.objects, renames, direction))
  if (inactive.length) {
    const ids = new Set(inactive.map((c) => c.id))
    useDocumentStore.setState((s) => ({
      canvases: s.canvases.map((c) =>
        ids.has(c.id)
          ? produce(c, (d) => {
              total += rebindOverrideIdentities(d.objects, renames, direction, panelIsStem)
            })
          : c,
      ),
    }))
  }
  return total
}

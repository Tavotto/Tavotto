import { createVersion } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { documentDigest, recordDiagnosticEvent, versionHash } from '@/diagnostics'

/**
 * 布局版本的自动检查点：编辑停顿后落一个服务器快照。
 *
 * 与本机自动保存（localStorage，1s 防抖）互不替代：本机保存兜「刷新不丢」，
 * 检查点兜「改乱了想回到半小时前」。频率刻意低（停顿 15s 且距上个检查点
 * ≥5 分钟），服务器端还会去重（与最近一版相同则跳过）并滚动清理。
 */
const DEBOUNCE_MS = 15_000
const MIN_GAP_MS = 5 * 60_000

export function startVersionCheckpoints(): () => void {
  let timer: number | undefined
  let lastSaved = 0

  const fire = () => {
    const wait = lastSaved + MIN_GAP_MS - Date.now()
    if (wait > 0) {
      timer = window.setTimeout(fire, wait)
      return
    }
    const { doc, documentId } = useDocumentStore.getState()
    if (!doc.objects.length) return
    lastSaved = Date.now()
    void createVersion(documentId, { auto: true, doc })
      .then((res) => {
        // 服务器与最近一版相同会跳过（skipped）——那不是一次新版本，不记
        if (res.skipped || !res.version) return
        recordDiagnosticEvent({
          type: 'layout_version.save',
          version: versionHash(res.version.id),
          document_hash: documentDigest(doc),
          auto: true,
        })
      })
      .catch(() => {
        /* 检查点失败不打扰编辑；下一轮改动会再试 */
      })
  }

  const unsub = useDocumentStore.subscribe((state, prev) => {
    if (state.doc === prev.doc) return
    window.clearTimeout(timer)
    timer = window.setTimeout(fire, DEBOUNCE_MS)
  })

  return () => {
    window.clearTimeout(timer)
    unsub()
  }
}

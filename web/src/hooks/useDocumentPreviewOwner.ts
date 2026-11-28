import { useMemo } from 'react'
import { captureDocumentPreviewOwner, useDocumentStore } from '@/store/documentStore'
import type { PreviewOwner } from '@/lib/previewOwner'

/** 正文引用与归属一起捕获；只有门变化时重算资格，不能把旧正文重新认领给新项目。 */
export function useDocumentPreviewOwner(subject: unknown, captured?: PreviewOwner): PreviewOwner {
  const gate = useDocumentStore(s => s.restoreGate)
  const bound = useMemo(() => ({ subject, owner: captured ?? captureDocumentPreviewOwner() }), [subject, captured])
  return useMemo(() => ({ gate, owner: bound.owner.refresh() }), [bound, gate]).owner
}

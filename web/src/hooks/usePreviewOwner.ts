import { useSyncExternalStore } from 'react'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { onCurrentProjectChange } from '@/lib/session'
import { useDocumentStore } from '@/store/documentStore'

const projectGeneration = () => captureProjectEpoch().generation

/** 即使项目间复用了同名素材，或同一文档被重新载入，在途预览也只能交给发起它的那一代。 */
export function usePreviewOwner() {
  const generation = useSyncExternalStore(onCurrentProjectChange, projectGeneration)
  const loadSeq = useDocumentStore(s => s.loadSeq)
  const project = captureProjectEpoch()
  return {
    key: JSON.stringify([project.pj, generation, loadSeq]),
    pj: project.pj,
    still: () => project.still() && useDocumentStore.getState().loadSeq === loadSeq,
  }
}

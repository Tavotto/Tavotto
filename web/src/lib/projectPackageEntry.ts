import { PACKAGE_EXT } from '@/lib/brand'
import { useProjectPackageInstallStore } from '@/store/projectPackageInstallStore'

/** Resume a known publication, or select bytes for the backend's strict reader. */
export function chooseProjectPackage(forceNew = false): void {
  const flow = useProjectPackageInstallStore.getState()
  if (!forceNew && (flow.receipts.length || flow.attempts.some(row => row.state === 'installing' || row.state === 'completion_unknown'))) {
    flow.reopen()
    return
  }
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = PACKAGE_EXT + ',.zip,application/zip'
  input.onchange = () => {
    const file = input.files?.[0]
    if (file) void useProjectPackageInstallStore.getState().inspect(file)
  }
  input.click()
}

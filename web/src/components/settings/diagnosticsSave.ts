/**
 * 把一份诊断包的字节存成文件（导出诊断包与「发送问题反馈」里的「保存这份诊断包」共用）。
 *
 * 文件名与后端 `Content-Disposition` 同一形状：`tavotto-diagnostics-YYYYMMDD-HHMMSS.zip`（本地时间）。
 */

/** 本地时间的 YYYYMMDD-HHMMSS，与后端给的 Content-Disposition 同一形状 */
export function stampForFilename(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}` +
    `-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`
  )
}

export function saveDiagnosticsZip(blob: Blob): void {
  const url = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = url
    a.download = `tavotto-diagnostics-${stampForFilename()}.zip`
    a.click()
  } finally {
    // 不撤销就是一条挂到刷新为止的引用，而 zip 全在内存里
    URL.revokeObjectURL(url)
  }
}

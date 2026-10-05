import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'

/** READY belongs to the holder, after it acquired the real FileShare.Read handle. */
export function withWindowsFileLock<T>(target: string, run: () => Promise<T>): Promise<T> {
  const holder = spawn('powershell', [
    '-NoProfile', '-NonInteractive', '-Command',
    `$ErrorActionPreference='Stop';try {` +
      `$f=[System.IO.File]::Open('${target.replace(/'/g, "''")}',` +
      `[System.IO.FileMode]::Open,[System.IO.FileAccess]::Read,[System.IO.FileShare]::Read);` +
      `[Console]::Out.WriteLine('READY');[Console]::Out.Flush();` +
      `[Console]::In.ReadLine() | Out-Null` +
      `} finally {if ($f) {$f.Dispose()}}`,
  ], { stdio: 'pipe' })
  return withReadyFileLock(holder, run)
}

/** Exported for browser-free process regressions; Windows locking is exercised by native E2E. */
export async function withReadyFileLock<T>(
  holder: ChildProcessWithoutNullStreams,
  run: () => Promise<T>,
  readyTimeout = 15_000,
): Promise<T> {
  let stdout = ''
  let stderr = ''
  let stopping = false
  let forced = false
  let didClose = false
  let holderFailure: Error | undefined
  let fail!: (error: Error) => void
  const failed = new Promise<never>((_, reject) => {
    fail = (error) => { holderFailure ??= error; reject(error) }
  })
  const diagnostic = (reason: string) => new Error(
    `File lock holder ${reason}\nstdout: ${stdout}\nstderr: ${stderr}`,
  )
  const ready = new Promise<void>((resolve) => {
    holder.stdout.on('data', (chunk) => {
      stdout = (stdout + String(chunk)).slice(-8_000)
      if (stdout.split(/\r?\n/).slice(0, -1).includes('READY')) resolve()
    })
  })
  holder.stderr.on('data', (chunk) => { stderr = (stderr + String(chunk)).slice(-8_000) })
  holder.on('error', (error) => fail(diagnostic(`process error: ${error.message}`)))
  holder.stdin.on('error', (error) => {
    if (!stopping) fail(diagnostic(`stdin error: ${error.message}`))
  })
  const closed = new Promise<void>((resolve) => {
    holder.once('close', (code, signal) => {
      didClose = true
      resolve()
      if (!stopping || (!forced && code !== 0)) {
        fail(diagnostic(`exited (code=${code}, signal=${signal})`))
      }
    })
  })
  const waitForClose = async (ms: number) => {
    let timer: ReturnType<typeof setTimeout> | undefined
    try {
      await Promise.race([closed, new Promise<void>((resolve) => { timer = setTimeout(resolve, ms) })])
    } finally {
      clearTimeout(timer)
    }
  }
  let timer: ReturnType<typeof setTimeout> | undefined
  let result!: T
  let runFailure: { error: unknown } | undefined
  try {
    try {
      await Promise.race([ready, failed, new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(diagnostic(`did not report READY within ${readyTimeout}ms`)), readyTimeout)
      })])
    } finally {
      clearTimeout(timer)
    }
    result = await Promise.race([run(), failed])
  } catch (error) {
    runFailure = { error }
  } finally {
    stopping = true
    // EOF releases the handle in PowerShell's finally. Await close, including on startup failure.
    holder.stdin.end()
    await waitForClose(5_000)
    if (!didClose) {
      forced = true
      holder.kill('SIGKILL')
      await waitForClose(5_000)
    }
  }
  if (!didClose) throw diagnostic('did not exit after forced termination')
  if (runFailure) throw runFailure.error
  if (holderFailure) throw holderFailure
  return result
}

import { spawn } from 'node:child_process'
import { mkdtempSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from '@playwright/test'
import { withReadyFileLock, withWindowsFileLock } from './file-lock'

// No browser/app fixtures: these exercise real child events on every CI platform.
const node = (script: string) => spawn(process.execPath, ['-e', script], { stdio: 'pipe' })
const untilEof = `process.stdin.resume();process.stdin.on('end', () => process.exit(0));`

test('file lock fixture rejects a holder that never reports READY and reaps it', async () => {
  const holder = node(`console.log('NOT_READY');${untilEof}`)
  let ran = false
  await expect(withReadyFileLock(holder, async () => { ran = true }, 1_000))
    .rejects.toThrow('did not report READY within 1000ms')
  expect(ran).toBe(false)
  expect(holder.exitCode !== null || holder.signalCode !== null).toBe(true)
})

test('file lock fixture diagnoses spawn failure before running assertions', async () => {
  const holder = spawn(path.join(os.tmpdir(), 'tavotto-no-such-lock-holder'), [], { stdio: 'pipe' })
  let ran = false
  await expect(withReadyFileLock(holder, async () => { ran = true }))
    .rejects.toThrow(/process error:.*ENOENT/)
  expect(ran).toBe(false)
})

test('file lock fixture diagnoses exit before READY with stderr and exit code', async () => {
  const holder = node(`console.error('cannot acquire handle');process.exitCode=17;`)
  let ran = false
  await expect(withReadyFileLock(holder, async () => { ran = true }))
    .rejects.toThrow(/exited \(code=17, signal=null\)[\s\S]*cannot acquire handle/)
  expect(ran).toBe(false)
  expect(holder.exitCode).toBe(17)
})

test('file lock fixture fails if the holder exits while assertions are running', async () => {
  const holder = node(`console.log('READY');process.stdin.once('data', () => {
    console.error('lost handle');process.exit(19);
  });`)
  await expect(withReadyFileLock(holder, () => {
    holder.stdin.write('exit\n')
    return new Promise<void>(() => {})
  })).rejects.toThrow(/exited \(code=19, signal=null\)[\s\S]*lost handle/)
  expect(holder.exitCode).toBe(19)
})

test('file lock fixture waits for complete READY and releases before returning', async () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-lock-fixture-'))
  const target = path.join(dir, "figure's.pdf")
  const marker = path.join(dir, 'state')
  writeFileSync(target, 'original')
  try {
    // POSIX cannot prove Windows sharing semantics. This child only proves the process protocol.
    const holder = node(String.raw`
      const fs = require('node:fs');
      const fd = fs.openSync(${JSON.stringify(target)}, 'r');
      process.stdout.write('NOT_READY\nRE');
      setTimeout(() => {
        fs.writeFileSync(${JSON.stringify(marker)}, 'acquired');
        process.stdout.write('ADY\r\n');
      }, 100);
      process.stdin.resume();
      process.stdin.on('end', () => {
        fs.closeSync(fd);fs.writeFileSync(${JSON.stringify(marker)}, 'released');
      });
    `)
    await withReadyFileLock(holder, async () => {
      expect(readFileSync(marker, 'utf8')).toBe('acquired')
      expect(holder.exitCode).toBe(null)
    })
    expect(readFileSync(marker, 'utf8')).toBe('released')
    expect(holder.exitCode).toBe(0)

    // The same CI test additionally verifies genuine FileShare.Read acquire/release on Windows.
    if (process.platform === 'win32') {
      const replacement = path.join(dir, 'replacement.pdf')
      writeFileSync(replacement, 'replacement')
      await withWindowsFileLock(target, async () => {
        expect(readFileSync(target, 'utf8')).toBe('original')
        expect(() => renameSync(replacement, target)).toThrow()
        expect(readFileSync(target, 'utf8')).toBe('original')
      })
      renameSync(replacement, target)
      expect(readFileSync(target, 'utf8')).toBe('replacement')
    } else {
      renameSync(target, `${target}.moved`)
      expect(readFileSync(`${target}.moved`, 'utf8')).toBe('original')
    }
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('file lock fixture reaps an uncooperative holder when assertions fail', async () => {
  const holder = node(`console.log('READY');setInterval(() => {}, 1_000);`)
  await expect(withReadyFileLock(holder, async () => { throw new Error('assertion failed') }))
    .rejects.toThrow('assertion failed')
  expect(holder.exitCode !== null || holder.signalCode !== null).toBe(true)
})

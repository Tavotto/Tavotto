/**
 * Photographs the real desktop window — title bar, shadow and all — for the
 * README's first image.
 *
 *     node assets/readme/hero-window.mjs [en-US|zh-CN]   # → assets/readme/hero-window(.zh).png
 *
 * 1. Boots the app in a browser against a directory this script owns and lays
 *    out the figure the same way capture.mjs does (so the window opens onto a
 *    real saved layout session, not a staged picture), then stops it.
 * 2. Starts the desktop shell (`TAVOTTO_DESKTOP_BIN`, a debug build of src-tauri/
 *    made with a throwaway bundle identifier so it shares no WebKit storage with an
 *    installed Tavotto) with the sidecar pointed at this worktree's engine, on
 *    the same directories.
 *    The page is driven by `hero-driver.js`, which is added to the built frontend
 *    (src/tavotto/web/, git-ignored) for the run and removed again: it dismisses
 *    the first-run hint toast and selects panel (a) with DOM events, because a
 *    WKWebView has no remote-debugging port and nothing may type into the window.
 * 3. Finds the window's id in the window server's list and captures only that
 *    window with `screencapture -l <id> -o`. Nothing is clicked or typed into any
 *    other app; no global key or mouse events are sent.
 */
import { execFileSync } from 'node:child_process'
import { realpathSync, chmodSync, copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { OUT, REPO, STRINGS, boot, driver } from './lib.mjs'

const LOCALE = process.argv[2] ?? 'en-US'
const SUFFIX = LOCALE === 'en-US' ? '' : '.' + LOCALE.split('-')[0]
const S = STRINGS[LOCALE]
const BIN = process.env.TAVOTTO_DESKTOP_BIN
if (!BIN) throw new Error('set TAVOTTO_DESKTOP_BIN to a built src-tauri binary')
const PY = process.env.TAVOTTO_PYTHON ?? path.join(REPO, '.venv', 'bin', 'python')

const workdir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-hero-'))
mkdirSync(path.join(workdir, 'home'), { recursive: true })

// 1 · the layout, built in a browser
const app = await boot(LOCALE, undefined, { workdir })
const d = driver(app.page, S)
await d.buildLayout()
await d.pause(1500)
const keep = /^tavotto\.(currentDoc|locale|ui|docIndex|onboarding|assetUsed|projectDoc\..*|projectFile\..*)$/
const storage = await app.page.evaluate(() => ({ ...localStorage }))
const snapshot = Object.fromEntries(Object.entries(storage).filter(([k]) => keep.test(k)))
await app.close()

// 2 · the window
const webDir = path.join(REPO, 'src', 'tavotto', 'web')
const indexHtml = path.join(webDir, 'index.html')
const indexOriginal = readFileSync(indexHtml, 'utf8')
copyFileSync(path.join(import.meta.dirname, 'hero-driver.js'), path.join(webDir, 'assets', 'hero-driver.js'))
writeFileSync(path.join(webDir, 'assets', 'hero-ls.js'), `window.__HERO_LS = ${JSON.stringify(snapshot)}`)
writeFileSync(indexHtml, indexOriginal.replace('</body>', `<script src="/assets/hero-ls.js?v=${Date.now()}"></script><script src="/assets/hero-driver.js?v=${Date.now()}"></script></body>`))
const swift = path.join(workdir, 'windows.swift')
writeFileSync(swift, `import CoreGraphics
import Foundation
let pid = Int32(CommandLine.arguments[1])!
let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID) as! [[String: Any]]
for w in list where (w[kCGWindowOwnerPID as String] as? Int32) == pid {
  let b = w[kCGWindowBounds as String] as! [String: Any]
  print(w[kCGWindowNumber as String]!, b["Width"]!, b["Height"]!, w[kCGWindowLayer as String]!)
}
`)
const lang = LOCALE === 'en-US' ? '(en)' : '(zh-Hans)'
// Launched through LaunchServices with `open -g`, so the window opens behind whatever
// the user is working in and never takes the foreground. The debug binary is
// wrapped in a throwaway .app bundle for that (Info.plist: the throwaway identifier).
const appDir = path.join(workdir, 'Tavotto-shots.app')
mkdirSync(path.join(appDir, 'Contents', 'MacOS'), { recursive: true })
const exe = path.join(appDir, 'Contents', 'MacOS', 'Tavotto-shots')
copyFileSync(BIN, exe)
chmodSync(exe, 0o755)
writeFileSync(path.join(appDir, 'Contents', 'Info.plist'), `<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>Tavotto-shots</string>
<key>CFBundleIdentifier</key><string>com.tavotto.readmeshots</string>
<key>CFBundleName</key><string>Tavotto</string>
<key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
`)
const env = {
  PYTHONPATH: path.join(REPO, 'src'),
  TAVOTTO_SIDECAR_EXE: PY,
  TAVOTTO_SIDECAR_ARGS: '-m tavotto',
  TAVOTTO_DATA_DIR: path.join(workdir, 'data'),
  TAVOTTO_CONFIG_DIR: path.join(workdir, 'config'),
  TAVOTTO_NO_TELEMETRY: '1',
}
execFileSync('open', [
  '-g', '-n', '-a', appDir,
  ...Object.entries(env).flatMap(([k, v]) => ['--env', `${k}=${v}`]),
  '--args', '--open', app.figures, '-AppleLanguages', lang,
])
const shell = { pid: null, kill() { if (this.pid) process.kill(this.pid) } } // our own bundle's process only
const wait = (ms) => new Promise((r) => setTimeout(r, ms))
let id = null
try {
  for (let i = 0; i < 90 && !id; i++) {
    await wait(1000)
    if (!shell.pid) {
      try {
        shell.pid = Number(execFileSync('pgrep', ['-n', '-f', realpathSync(exe)], { encoding: 'utf8' }).trim()) || null
      } catch {
        /* not started yet */
      }
    }
    if (!shell.pid) continue
    const rows = execFileSync('swift', [swift, String(shell.pid)], { encoding: 'utf8' })
      .trim().split('\n').filter(Boolean).map((l) => l.split(' '))
    id = rows.find((r) => r[3] === '0' && Number(r[1]) > 600)?.[0] ?? null
  }
  if (!id) throw new Error('the desktop window never appeared')
  // sidecar start, engine warm-up, first render; TAVOTTO_HERO_SNAPS=<dir> keeps a frame every 4 s to watch it settle
  const STEP = Number(process.env.TAVOTTO_HERO_STEP ?? 4000)
  const total = Number(process.env.TAVOTTO_HERO_WAIT ?? 25_000)
  for (let t = 0; t < total; t += STEP) {
    if (process.env.TAVOTTO_HERO_SNAPS) execFileSync('screencapture', ['-l', id, '-o', '-x', path.join(process.env.TAVOTTO_HERO_SNAPS, `t${String(t).padStart(5, "0")}.png`)])
    await wait(STEP)
  }
  const file = path.join(OUT, `hero-window${SUFFIX}.png`)
  execFileSync('screencapture', ['-l', id, '-o', '-x', file])
  console.log('→', file)
} finally {
  shell.kill() // our own child only
  writeFileSync(indexHtml, indexOriginal)
  rmSync(path.join(webDir, 'assets', 'hero-driver.js'), { force: true })
  rmSync(path.join(webDir, 'assets', 'hero-ls.js'), { force: true })
  await wait(1500)
  rmSync(workdir, { recursive: true, force: true })
}

import { afterEach, describe, expect, it, vi } from 'vitest'
import { readJournalFacts } from './journalFactsView'

function responseWithPage(token: string, extra = ''): string {
  return `{"journal_id":"fx-journal","journal":{"label":"Fixture","publisher_id":"fx-pub"},` +
    `"catalog_rev":1,"next_offset":null,"sync_state":"updated","sync_code":null,` +
    `"catalog_completeness":"unknown","semantic_review":"not_authenticated",` +
    `"facts":[],"coverage":[],"sources":{"s":{"url":"https://example.invalid/guide.pdf",` +
    `"kind":"pdf","retrieved":"2026-11-11"${token ? `,"page":${token}` : ''}${extra}}}}`
}

async function read(raw: string) {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(raw, { status: 200 }))
  return readJournalFacts('fx-journal', 0, null, null, new AbortController().signal)
}

afterEach(() => vi.restoreAllMocks())

describe('PDF page integer precision', () => {
  it.each([
    ['7', 7],
    ['9007199254740991', 9007199254740991],
    ['', undefined],
  ])('preserves a safe or absent page (%s)', async (token, expected) => {
    const result = await read(responseWithPage(token, ',"pageExactUnavailable":true'))
    expect(result.ok).toBe(true)
    if (!result.ok) throw new Error('unexpected rejection')
    expect(result.page.sources.s.page).toBe(expected)
    expect(result.page.sources.s.pageExactUnavailable).toBeUndefined()
  })

  it.each(['9007199254740993', `1${'0'.repeat(310)}`])('removes an inexact parsed page (%s)', async (token) => {
    const result = await read(responseWithPage(token, ',"pageExactUnavailable":false'))
    expect(result.ok).toBe(true)
    if (!result.ok) throw new Error('unexpected rejection')
    expect(result.page.sources.s.page).toBeUndefined()
    expect(result.page.sources.s.pageExactUnavailable).toBe(true)
    expect(JSON.stringify(result.page)).not.toContain('9007199254740992')
    expect(JSON.stringify(result.page)).not.toContain('Infinity')
  })

  it.each(['-1', '0', '1.5', '7.0', '7e0', '1e309', '9007199254740992.5', '"7"', 'null'])('rejects invalid page %s', async (token) => {
    expect(await read(responseWithPage(token))).toEqual({ ok: false, code: 'invalid_response' })
  })

  it.each([
    ['7,"page":9007199254740993', undefined, true],
    ['9007199254740993,"page":7', 7, undefined],
  ])('uses the actual parsed source property when keys repeat', async (token, expected, unavailable) => {
    const result = await read(responseWithPage(token))
    expect(result.ok).toBe(true)
    if (!result.ok) throw new Error('unexpected rejection')
    expect(result.page.sources.s.page).toBe(expected)
    expect(result.page.sources.s.pageExactUnavailable).toBe(unavailable)
  })

  it('does not mistake an unrelated nested page token for a source page', async () => {
    const raw = responseWithPage('9007199254740993').replace('"facts":[]', '"facts":[],"other":{"page":1.5}')
    const result = await read(raw)
    expect(result.ok).toBe(true)
    if (!result.ok) throw new Error('unexpected rejection')
    expect(result.page.sources.s.pageExactUnavailable).toBe(true)
  })

  it.each([['7', true], ['9007199254740993', false]])('falls back safely without reviver source for %s', async (token, expected) => {
    const nativeParse = JSON.parse
    vi.spyOn(JSON, 'parse').mockImplementation((raw, reviver) => nativeParse(raw, function (this: object, key: string, value: unknown) {
      return reviver ? reviver.call(this, key, value) : value
    }))
    const result = await read(responseWithPage(token))
    expect(result.ok).toBe(expected)
    if (!expected) expect(result).toEqual({ ok: false, code: 'invalid_response' })
  })
})

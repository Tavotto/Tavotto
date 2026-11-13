import { afterEach, describe, expect, it } from 'vitest'
import {
  clearFigureExecutionProofs, figureExecutionProofFor, recordFigureExecutionProof,
} from './figureExecutionProof'

const proof = {
  version: 1 as const,
  sourceId: 'History.pdf',
  receiptId: 'receipt-A',
  executionOwner: { script: 'plot.py', run_config: null },
  patchHash: 'sha256:abc',
}
const patches = [{ gid: 'line', prop: 'linewidth', value: 2 }]

afterEach(() => clearFigureExecutionProofs())

describe('verified render proofs', () => {
  it('answers only for the exact project, source and ordered patch list', () => {
    recordFigureExecutionProof(proof, { pj: 'A', fileId: 'History.pdf', overrides: patches })
    expect(figureExecutionProofFor('A', 'History.pdf', patches)).toEqual(proof)
    expect(figureExecutionProofFor('B', 'History.pdf', patches)).toBeUndefined()
    expect(figureExecutionProofFor('A', 'Other.pdf', patches)).toBeUndefined()
    expect(figureExecutionProofFor('A', 'History.pdf', [])).toBeUndefined()
    expect(figureExecutionProofFor('A', 'History.pdf', [{ ...patches[0], value: 3 }])).toBeUndefined()
    // Array order is part of the identity, not just the set of patches.
    const two = [patches[0], { gid: 'line', prop: 'color', value: 'red' }]
    recordFigureExecutionProof(proof, { pj: 'A', fileId: 'History.pdf', overrides: two })
    expect(figureExecutionProofFor('A', 'History.pdf', [...two].reverse())).toBeUndefined()
    expect(figureExecutionProofFor('A', 'History.pdf', two)).toEqual(proof)
  })

  it('ignores malformed or misaddressed evidence instead of trusting it', () => {
    for (const bad of [
      undefined, null, 'proof', [], {},
      { ...proof, version: 2 },
      { ...proof, receiptId: '' },
      { ...proof, patchHash: '' },
      { ...proof, executionOwner: null },
      { ...proof, executionOwner: { script: '', run_config: null } },
      { ...proof, executionOwner: { script: 'plot.py', run_config: 7 } },
    ]) {
      recordFigureExecutionProof(bad, { pj: 'A', fileId: 'History.pdf', overrides: patches })
    }
    expect(figureExecutionProofFor('A', 'History.pdf', patches)).toBeUndefined()

    // A proof for another source id cannot be filed under this one.
    recordFigureExecutionProof({ ...proof, sourceId: 'Other.pdf' }, { pj: 'A', fileId: 'History.pdf', overrides: patches })
    expect(figureExecutionProofFor('A', 'History.pdf', patches)).toBeUndefined()
  })

  it('stays bounded and is cleared explicitly', () => {
    for (let index = 0; index < 70; index += 1) {
      recordFigureExecutionProof({ ...proof, receiptId: `r-${index}` }, {
        pj: 'A', fileId: 'History.pdf', overrides: [{ gid: 'line', prop: 'linewidth', value: index }],
      })
    }
    expect(figureExecutionProofFor('A', 'History.pdf', [{ gid: 'line', prop: 'linewidth', value: 0 }])).toBeUndefined()
    expect(figureExecutionProofFor('A', 'History.pdf', [{ gid: 'line', prop: 'linewidth', value: 69 }]))
      .toMatchObject({ receiptId: 'r-69' })
    clearFigureExecutionProofs()
    expect(figureExecutionProofFor('A', 'History.pdf', [{ gid: 'line', prop: 'linewidth', value: 69 }])).toBeUndefined()
  })
})

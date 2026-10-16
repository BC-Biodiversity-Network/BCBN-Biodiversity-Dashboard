import { describe, expect, it } from 'vitest'

import {
  COLOUR_SCHEMES, RECORD_BIN_EDGES, SPECIES_BIN_EDGES, binAlphas, binColours, binIndexFor, hexagonColour,
  legendEdgeLabels, outlineOpacity,
} from './colours'

describe('the bins', () => {
  it('are the decided log-scale breaks', () => {
    expect(SPECIES_BIN_EDGES).toEqual([1, 3, 10, 30, 100, 300, 1000, 3000])
    expect(RECORD_BIN_EDGES).toEqual([1, 10, 100, 1000, 10000, 100000, 1000000])
    expect(COLOUR_SCHEMES.speciesMap.binEdges).toBe(SPECIES_BIN_EDGES)
  })

  it('give each scale as many colours as bins', () => {
    expect(COLOUR_SCHEMES.species.steps).toHaveLength(8)
    expect(COLOUR_SCHEMES.records.steps).toHaveLength(7)
    expect(COLOUR_SCHEMES.speciesMap.steps).toHaveLength(8)
  })

  it('put each value in the last bin it has reached, with an open top bin', () => {
    expect(binIndexFor(1, SPECIES_BIN_EDGES)).toBe(0)
    expect(binIndexFor(2, SPECIES_BIN_EDGES)).toBe(0)
    expect(binIndexFor(3, SPECIES_BIN_EDGES)).toBe(1)
    expect(binIndexFor(2999, SPECIES_BIN_EDGES)).toBe(6)
    expect(binIndexFor(3000, SPECIES_BIN_EDGES)).toBe(7)
    expect(binIndexFor(10847, SPECIES_BIN_EDGES)).toBe(7)
    expect(binIndexFor(999999, RECORD_BIN_EDGES)).toBe(5)
    expect(binIndexFor(5411732, RECORD_BIN_EDGES)).toBe(6)
  })

  it('label the legend with the start of each bin and a "+" on the open top bin', () => {
    expect(legendEdgeLabels(SPECIES_BIN_EDGES)).toEqual(['1', '3', '10', '30', '100', '300', '1K', '3K+'])
    expect(legendEdgeLabels(RECORD_BIN_EDGES)).toEqual(['1', '10', '100', '1K', '10K', '100K', '1M+'])
  })
})

describe('the colours', () => {
  it('get more opaque bin by bin, from 0.85 to 0.95', () => {
    const alphas = binAlphas(8)
    expect(alphas[0]).toBe(0.85)
    expect(alphas[7]).toBeCloseTo(0.95)
    expect([...alphas].sort()).toEqual(alphas)
  })

  it('use the same opacity on all three maps, from 0.85 to 0.95', () => {
    const opacities = (scheme) => binColours(scheme).map((colour) => colour[3])
    expect(opacities(COLOUR_SCHEMES.speciesMap)).toEqual([217, 220, 224, 228, 231, 235, 239, 242])
    expect(opacities(COLOUR_SCHEMES.species)).toEqual(opacities(COLOUR_SCHEMES.speciesMap))
    expect(opacities(COLOUR_SCHEMES.records)[0]).toBe(217)
    expect(opacities(COLOUR_SCHEMES.records)[6]).toBe(242)
  })

  it('colour a hexagon by its scale', () => {
    expect(hexagonColour({ occurrences: 1 }, COLOUR_SCHEMES.speciesMap)).toEqual([228, 108, 159, 217])
    expect(hexagonColour({ occurrences: 5 }, COLOUR_SCHEMES.speciesMap)).toEqual([210, 82, 149, 220])
    expect(hexagonColour({ occurrences: 3000 }, COLOUR_SCHEMES.speciesMap)).toEqual([42, 12, 63, 242])
    expect(hexagonColour({ distinctSpecies: 3000 }, COLOUR_SCHEMES.species)).toEqual([...COLOUR_SCHEMES.species.steps[7], 242])
    expect(hexagonColour({ occurrences: 1 }, COLOUR_SCHEMES.records)).toEqual([...COLOUR_SCHEMES.records.steps[0], 217])
  })

  it('keep the overview colours as they were', () => {
    expect(COLOUR_SCHEMES.species.steps[0]).toEqual([205, 226, 251])
    expect(COLOUR_SCHEMES.species.steps[7]).toEqual([13, 54, 107])
    expect(COLOUR_SCHEMES.records.steps[0]).toEqual([254, 233, 214])
    expect(COLOUR_SCHEMES.records.steps[6]).toEqual([107, 42, 4])
  })

  it('give the species map its own colours, shared with neither overview scale', () => {
    const asText = (steps) => steps.map((step) => step.join(','))
    const species = new Set(asText(COLOUR_SCHEMES.speciesMap.steps))
    for (const other of [COLOUR_SCHEMES.species.steps, COLOUR_SCHEMES.records.steps]) {
      expect(asText(other).some((colour) => species.has(colour))).toBe(false)
    }
  })
})

describe('the hexagon outline', () => {
  it('is full at 20 pixels and over, gone at 8 and under, in quarter steps between', () => {
    expect(outlineOpacity(80)).toBe(1)
    expect(outlineOpacity(20)).toBe(1)
    expect(outlineOpacity(17)).toBe(0.75)
    expect(outlineOpacity(14)).toBe(0.5)
    expect(outlineOpacity(11)).toBe(0.25)
    expect(outlineOpacity(8)).toBe(0)
    expect(outlineOpacity(3)).toBe(0)
  })
})

import { describe, expect, it } from 'vitest'

import { incompleteYearsNote, readYearBoxes, sameYears, yearsKey, yearsLabel } from './yearRange'

describe('year ranges', () => {
  it('are written as people write them', () => {
    expect(yearsLabel([2000, 2020])).toBe('2000 to 2020')
    expect(yearsLabel([2010, 2010])).toBe('2010')
  })

  it('have a short key, with open ends left blank', () => {
    expect(yearsKey(null)).toBe('all')
    expect(yearsKey({ from: 2000, to: 2020 })).toBe('2000-2020')
    expect(yearsKey({ from: 2000, to: null })).toBe('2000-')
    expect(sameYears({ from: 2000, to: null }, { from: 2000, to: null })).toBe(true)
    expect(sameYears(null, { from: 2000, to: null })).toBe(false)
  })

  it('are read from the two boxes, empty boxes being open ends', () => {
    expect(readYearBoxes('', '', 1784, 2026)).toEqual({ years: null })
    expect(readYearBoxes('2000', '2020', 1784, 2026)).toEqual({ years: { from: 2000, to: 2020 } })
    expect(readYearBoxes(' 2000 ', '', 1784, 2026)).toEqual({ years: { from: 2000, to: null } })
    expect(readYearBoxes('', '1900', 1784, 2026)).toEqual({ years: { from: null, to: 1900 } })
  })

  it('say what is wrong with a year outside the data, a non-year or a backwards range', () => {
    expect(readYearBoxes('1700', '', 1784, 2026)).toEqual({ problem: 'Years run from 1784 to 2026.' })
    expect(readYearBoxes('', '2030', 1784, 2026)).toEqual({ problem: 'Years run from 1784 to 2026.' })
    expect(readYearBoxes('200', '', 1784, 2026)).toEqual({ problem: 'Years run from 1784 to 2026.' })
    expect(readYearBoxes('20.5', '', 1784, 2026)).toEqual({ problem: 'Years run from 1784 to 2026.' })
    expect(readYearBoxes('2020', '2000', 1784, 2026)).toEqual({ problem: 'The first year must not be after the last.' })
  })

  it('note that the most recent years are incomplete', () => {
    expect(incompleteYearsNote(2026)).toBe(
      'Records from 2025 and 2026 are still incomplete: many reach GBIF a year or more after they are made.')
    expect(incompleteYearsNote(2025)).toBe(
      'Records from 2025 are still incomplete: many reach GBIF a year or more after they are made.')
  })
})

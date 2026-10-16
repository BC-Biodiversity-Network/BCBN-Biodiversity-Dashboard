import { describe, expect, it } from 'vitest'

import { BCSEE_LIST_DEFINITIONS, bcseeDefinition } from './bcseeDefinitions'
import { OGL_BC_STATEMENT, bcseeCitation, bcseeCitationEnd, citationDate, colRelease } from './sources'

describe('the BCSEE definitions', () => {
  it('cover every B.C. List value in the data', () => {
    for (const value of ['Red', 'Blue', 'Yellow', 'Exotic', 'Not Reviewed', 'Unknown', 'Accidental', 'No Status']) {
      expect(BCSEE_LIST_DEFINITIONS[value]).toBeTruthy()
    }
  })

  it('use BCSEE’s own words (checked against its help page, list.htm)', () => {
    expect(bcseeDefinition('Yellow')).toBe(
      'Includes species or ecological communities that are apparently secure and not at risk of extinction.')
    expect(bcseeDefinition('Accidental')).toBe(
      'Species occurring infrequently and unpredictably, outside their usual range.')
  })

  it('say plainly when a value is not defined', () => {
    expect(bcseeDefinition('Extirpated')).toBe(
      '"Extirpated" is a value on BCSEE\'s B.C. List that its help page does not define.')
  })
})

describe('the OGL-BC statement', () => {
  it('is the licence’s own wording, with its en dash', () => {
    expect(OGL_BC_STATEMENT).toBe('Contains information licensed under the Open Government Licence \u2013 British Columbia.')
  })
})

describe('the citations', () => {
  it('find the Catalogue of Life release in the card text', () => {
    expect(colRelease('COL26.6 XR (ChecklistBank 315557)').doiUrl).toBe('https://doi.org/10.48580/dgy8b')
    expect(colRelease('COL27.1 XR (ChecklistBank 999999)')).toBeNull()
    expect(colRelease(undefined)).toBeNull()
  })

  it('cite BCSEE the way the Conservation Data Centre asks', () => {
    expect(`${bcseeCitation('2026-05-05')} URL ${bcseeCitationEnd('2026-05-05', '2026-09-26')}`).toBe(
      'B.C. Conservation Data Centre. 2026. BC Species and Ecosystems Explorer. B.C. Government, ' +
      'Victoria B.C. Available: URL (provincial export of 2026-05-05, accessed 2026-09-26).')
  })

  it('write the GBIF download date the way GBIF does, in UTC', () => {
    expect(citationDate('2026-10-04T06:24:04Z')).toBe('4 October 2026')
    expect(citationDate('2026-01-01T00:30:00Z')).toBe('1 January 2026')
  })
})

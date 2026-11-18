import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'

import { cellToBoundary } from 'h3-js'
import { describe, expect, it } from 'vitest'

import {
  BC_BOUNDARY_WKT, GBIF_CHECKLIST_KEY, GBIF_COUNT_NOTE, boxWkt, cellWkt, gbifAreaSearchUrl, gbifBcSearchUrl,
  gbifBoxSearchUrl, gbifCellSearchUrl, gbifLeftOutNote, ringIsClockwise, ringWkt, ringsWkt,
} from './gbifLinks'

const CELL = '8728d1aacffffff'

// The corners of a WKT polygon as [lng, lat] numbers.
function corners(wkt) {
  return wkt.replace(/^POLYGON\(\(|\)\)$/g, '').split(',').map((pair) => pair.split(' ').map(Number))
}

describe('the hexagon outline for GBIF', () => {
  it('is a closed WKT polygon of the hexagon\'s corners, longitude first, to 6 decimals', () => {
    const wkt = cellWkt(CELL)
    expect(wkt).toMatch(/^POLYGON\(\((-?\d+\.\d{6} -?\d+\.\d{6},){6}-?\d+\.\d{6} -?\d+\.\d{6}\)\)$/)
    const points = corners(wkt)
    expect(points).toHaveLength(7)
    expect(points[0]).toEqual(points[6])
    const [lat, lng] = cellToBoundary(CELL)[0]
    expect(points[0][0]).toBeCloseTo(lng, 6)
    expect(points[0][1]).toBeCloseTo(lat, 6)
  })

  it('runs counter-clockwise, as GBIF asks', () => {
    const points = corners(cellWkt(CELL))
    let twiceArea = 0
    for (let i = 0; i < points.length - 1; i++) {
      twiceArea += points[i][0] * points[i + 1][1] - points[i + 1][0] * points[i][1]
    }
    expect(twiceArea).toBeGreaterThan(0)
  })
})

describe('the GBIF search link', () => {
  it('names the Catalogue of Life checklist, every key, the outline and present records only', () => {
    const url = new URL(gbifCellSearchUrl({ keys: ['CVW5J', 'VP2T6'], cell: CELL }))
    expect(url.origin + url.pathname).toBe('https://www.gbif.org/occurrence/search')
    expect(url.searchParams.get('checklistKey')).toBe(GBIF_CHECKLIST_KEY)
    expect(GBIF_CHECKLIST_KEY).toBe('7ddf754f-d193-4cc9-b351-99906754a03b')
    expect(url.searchParams.getAll('taxonKey')).toEqual(['CVW5J', 'VP2T6'])
    expect(url.searchParams.get('geometry')).toBe(cellWkt(CELL))
    expect(url.searchParams.get('occurrenceStatus')).toBe('PRESENT')
    expect(url.searchParams.has('year')).toBe(false)
  })

  it('adds the year range, both years included', () => {
    const url = new URL(gbifCellSearchUrl({ keys: ['59PMB'], cell: CELL, years: [2000, 2020] }))
    expect(url.searchParams.get('year')).toBe('2000,2020')
    expect(url.searchParams.getAll('taxonKey')).toEqual(['59PMB'])
  })
})

describe("the all-species hexagon panel's GBIF link", () => {
  it('with no keys and no years, finds every species in the hexagon, every year', () => {
    const url = new URL(gbifCellSearchUrl({ cell: CELL }))
    expect(url.origin + url.pathname).toBe('https://www.gbif.org/occurrence/search')
    expect([...url.searchParams.keys()]).toEqual(['checklistKey', 'geometry', 'occurrenceStatus'])
    expect(url.searchParams.get('checklistKey')).toBe(GBIF_CHECKLIST_KEY)
    expect(url.searchParams.get('geometry')).toBe(cellWkt(CELL))
    expect(url.searchParams.get('occurrenceStatus')).toBe('PRESENT')
  })

  it('carries a year range, both years included', () => {
    const url = new URL(gbifCellSearchUrl({ cell: CELL, years: [2000, 2020] }))
    expect([...url.searchParams.keys()]).toEqual(['checklistKey', 'geometry', 'occurrenceStatus', 'year'])
    expect(url.searchParams.get('year')).toBe('2000,2020')
    expect(url.searchParams.has('taxonKey')).toBe(false)
  })

  it('is exactly this address for this hexagon and 2000 to 2020', () => {
    // On a rank map the panel gives the link the same cell and years: the
    // rank goes in the note instead (see gbifLeftOutNote).
    expect(gbifCellSearchUrl({ cell: CELL, years: [2000, 2020] })).toBe(
      'https://www.gbif.org/occurrence/search?checklistKey=7ddf754f-d193-4cc9-b351-99906754a03b'
      + '&geometry=POLYGON%28%28-123.346457+48.419051%2C-123.338215+48.429769%2C-123.348150+48.438713'
      + '%2C-123.366327+48.436938%2C-123.374565+48.426220%2C-123.364631+48.417276%2C-123.346457+48.419051%29%29'
      + '&occurrenceStatus=PRESENT&year=2000%2C2020')
  })
})

describe('the note on what a GBIF link leaves out', () => {
  const TURDUS = { rank: 'genus', name: 'Turdus' }
  const NO_FILTER = { q: '', status: [], sort: 'english', page: 3 }

  it('is null when the list has no filter the link cannot carry (order and page are not filters)', () => {
    expect(gbifLeftOutNote({ place: 'hexagon' })).toBeNull()
    expect(gbifLeftOutNote({ place: 'hexagon', list: NO_FILTER })).toBeNull()
  })

  it('names each filter left out: a rank, a name filter, a BC status filter', () => {
    expect(gbifLeftOutNote({ place: 'hexagon', rank: TURDUS, list: NO_FILTER }))
      .toBe("GBIF shows every species in the hexagon: the link leaves out this list's genus Turdus.")
    expect(gbifLeftOutNote({ place: 'hexagon', list: { ...NO_FILTER, status: ['red', 'blue'] } }))
      .toBe("GBIF shows every species in the hexagon: the link leaves out this list's BC status filter.")
    expect(gbifLeftOutNote({ place: 'hexagon', list: { ...NO_FILTER, q: 'thrush' } }))
      .toBe("GBIF shows every species in the hexagon: the link leaves out this list's name filter.")
    expect(gbifLeftOutNote({ place: 'hexagon', rank: TURDUS, list: { q: 'thrush', status: ['red'] } }))
      .toBe('GBIF shows every species in the hexagon: the link leaves out this list\'s genus Turdus, name filter '
        + 'and BC status filter.')
  })

  it('never names the hexagon size number', () => {
    const note = gbifLeftOutNote({ place: 'hexagon', rank: TURDUS, list: { q: 'a', status: ['red'] } })
    expect(note).not.toMatch(/resolution/i)
  })
})

// The BC polygon file as it is on disk, and the SHA-1 of the file it was
// copied from (~/Desktop/BCBN/Results/bc_boundary_gbif.wkt, the polygon of
// GBIF download 0009771-260928105237408; see geo/README.md). Read from
// frontend/, where the tests run.
const BC_FILE = readFileSync('src/geo/bc_boundary_gbif.wkt', 'utf8')
const BC_FILE_SHA1 = '07b56f163b7ada86023a415c9d007bbb4cfff125'

describe('the BC records link', () => {
  it('uses the polygon file unchanged, the one our GBIF download used', () => {
    expect(createHash('sha1').update(BC_FILE).digest('hex')).toBe(BC_FILE_SHA1)
    expect(BC_BOUNDARY_WKT).toBe(BC_FILE)
    expect(BC_FILE.length).toBe(5533)
    expect(BC_FILE.startsWith('POLYGON ((-135.06414 59.55753, ')).toBe(true)
    expect(BC_FILE.endsWith(', -135.06414 59.55753))')).toBe(true)
  })

  it('carries the full polygon unchanged, every key, the checklist and present records, and no years', () => {
    const url = new URL(gbifBcSearchUrl({ keys: ['49JFH'] }))
    expect(url.origin + url.pathname).toBe('https://www.gbif.org/occurrence/search')
    expect(url.searchParams.get('geometry')).toBe(BC_FILE)
    expect(url.searchParams.get('checklistKey')).toBe(GBIF_CHECKLIST_KEY)
    expect(url.searchParams.getAll('taxonKey')).toEqual(['49JFH'])
    expect(url.searchParams.get('occurrenceStatus')).toBe('PRESENT')
    expect(url.searchParams.has('year')).toBe(false)
  })

  it('gives one taxonKey per key, a card\'s merged keys included, in the order given', () => {
    const url = new URL(gbifBcSearchUrl({ keys: ['CVW5J', 'VP2T6', 'OLD1'] }))
    expect(url.searchParams.getAll('taxonKey')).toEqual(['CVW5J', 'VP2T6', 'OLD1'])
  })

  it('has the same parameters, in the same order, as the hexagon link', () => {
    const names = (address) => [...new URL(address).searchParams.keys()]
    expect(names(gbifBcSearchUrl({ keys: ['A', 'B'] })))
      .toEqual(names(gbifCellSearchUrl({ keys: ['A', 'B'], cell: CELL })))
  })

  it('is 6,209 characters long for Sockeye Salmon', () => {
    expect(gbifBcSearchUrl({ keys: ['49JFH'] }).length).toBe(6209)
  })

  it('shares its note with the hexagon panel', () => {
    expect(GBIF_COUNT_NOTE).toBe(
      'GBIF shows its current data, without the cleaning of our snapshot, so its count may differ a little from ours.')
  })
})

describe('the GBIF link of an area of several shapes', () => {
  const KAMLOOPS = { west: -120.6, south: 50.55, east: -120.1, north: 50.8 }
  const BOX_RING = [[-120.6, 50.55], [-120.1, 50.55], [-120.1, 50.8], [-120.6, 50.8]]

  it('gives exactly the box link for a single box', () => {
    expect(ringsWkt([BOX_RING])).toEqual([boxWkt(KAMLOOPS)])
    expect(gbifAreaSearchUrl({ rings: [BOX_RING], years: [2000, 2020] }))
      .toBe(gbifBoxSearchUrl({ box: KAMLOOPS, years: [2000, 2020] }))
    expect(gbifAreaSearchUrl({ keys: ['59PMB'], rings: [BOX_RING] })).toBe(gbifBoxSearchUrl({ keys: ['59PMB'], box: KAMLOOPS }))
  })

  it('writes each ring closed and counter-clockwise, keeping its first corner first', () => {
    const clockwise = [[0, 0], [0, 1], [1, 1], [1, 0]]
    expect(ringIsClockwise(clockwise)).toBe(true)
    expect(ringWkt(clockwise)).toBe('POLYGON((0.000000 0.000000,1.000000 0.000000,1.000000 1.000000,'
      + '0.000000 1.000000,0.000000 0.000000))')
    // Already counter-clockwise: kept as it is.
    expect(ringWkt([[0, 0], [1, 0], [0.5, 1]])).toBe('POLYGON((0.000000 0.000000,1.000000 0.000000,'
      + '0.500000 1.000000,0.000000 0.000000))')
  })

  it('sends one geometry parameter per shape, in order', () => {
    const url = new URL(gbifAreaSearchUrl({ rings: [BOX_RING, [[-121, 50], [-120.8, 50], [-120.9, 50.2]]] }))
    expect(url.searchParams.getAll('geometry')).toEqual([
      boxWkt(KAMLOOPS),
      'POLYGON((-121.000000 50.000000,-120.800000 50.000000,-120.900000 50.200000,-121.000000 50.000000))',
    ])
    expect([...url.searchParams.keys()]).toEqual(['checklistKey', 'geometry', 'geometry', 'occurrenceStatus'])
    expect(url.searchParams.get('checklistKey')).toBe(GBIF_CHECKLIST_KEY)
  })
})

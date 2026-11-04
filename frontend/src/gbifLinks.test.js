import { cellToBoundary } from 'h3-js'
import { describe, expect, it } from 'vitest'

import { GBIF_CHECKLIST_KEY, cellWkt, gbifCellSearchUrl } from './gbifLinks'

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

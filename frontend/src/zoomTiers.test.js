import { describe, expect, it } from 'vitest'

import {
  FIT_MAX_ZOOM, MAX_ZOOM, ZOOM_TIERS, hexagonWidthPixels, pickResolution, zoomAtWidth,
} from './zoomTiers'

describe('the zoom tiers', () => {
  it('pick the resolution for a zoom from nothing showing', () => {
    expect(pickResolution(4.5, null)).toBe(4)
    expect(pickResolution(7, null)).toBe(5)
    expect(pickResolution(8, null)).toBe(6)
    expect(pickResolution(10, null)).toBe(7)
  })

  it('only switch once clearly past a threshold (the dead zone)', () => {
    const r5Starts = ZOOM_TIERS[1].minZoom
    expect(pickResolution(r5Starts + 0.1, 4)).toBe(4)
    expect(pickResolution(r5Starts + 0.3, 4)).toBe(5)
    expect(pickResolution(r5Starts - 0.1, 5)).toBe(5)
    expect(pickResolution(r5Starts - 0.3, 5)).toBe(4)
  })

  it('fit a species no closer than zoom 8, inside the res 6 tier', () => {
    expect(FIT_MAX_ZOOM).toBe(8)
    expect(pickResolution(FIT_MAX_ZOOM, null)).toBe(6)
    expect(FIT_MAX_ZOOM).toBeLessThan(MAX_ZOOM)
  })
})

describe('hexagon size on screen', () => {
  it('is the opposite of zoomAtWidth', () => {
    expect(hexagonWidthPixels(4, zoomAtWidth(4, 20))).toBeCloseTo(20)
    expect(hexagonWidthPixels(7, zoomAtWidth(7, 80))).toBeCloseTo(80)
  })

  it('starts every finer tier at about 30 pixels, and the opening view at about 26', () => {
    for (const tier of ZOOM_TIERS.slice(1)) {
      expect(hexagonWidthPixels(tier.resolution, tier.minZoom)).toBeCloseTo(30.2, 0)
    }
    expect(hexagonWidthPixels(4, 4.5)).toBeCloseTo(26, 0)
  })
})

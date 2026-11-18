import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  CLOSE_PIXELS, CROSSING_CLOSE, CROSSING_POINT, NO_POINTS_LEFT, POINTS_LEFT_WARNING, TOO_FEW_POINTS, addPoint,
  areaPointCount, closeProblem, drawingFeatures, nearFirstPoint, pointsLeftText, useAreaPolygon,
} from './areaPolygon'
import { MAX_AREA_POINTS, boxShapeOf, pointWouldCross, readAreaText } from './areaShapes'

// A pretend map: 100 pixels to a degree, north up.
const project = ([lng, lat]) => ({ x: lng * 100, y: -lat * 100 })
const pixel = (lng, lat) => project([lng, lat])

// Three points of a triangle near Kamloops, and a point that would make the
// fourth edge cross the first.
const A = [-120.6, 50.5]
const B = [-120.0, 50.5]
const C = [-120.3, 50.9]
const ACROSS = [-120.3, 50.3]

afterEach(() => {
  cleanup()
})

describe('drawing a polygon, point by point', () => {
  it('rounds each point as a box is rounded, and ignores one on the last point', () => {
    expect(addPoint([], [-120.123456, 50.987654], 250)).toEqual({ points: [[-120.1235, 50.9877]] })
    const points = [[-120.1235, 50.9877]]
    expect(addPoint(points, [-120.12351, 50.98769], 249).points).toBe(points)
  })

  it('refuses a point that would make two edges cross, and one past the limit', () => {
    expect(addPoint([A, B, C], ACROSS, 247)).toEqual({ message: CROSSING_POINT })
    expect(addPoint([A, B], C, 0)).toEqual({ message: NO_POINTS_LEFT })
    expect(NO_POINTS_LEFT).toBe('The area can have at most 250 points (a box counts as 4), so no more can be added. '
      + 'Finish the polygon or remove a point.')
  })

  it('closes with 3 points or more, unless the closing edge would cross', () => {
    expect(closeProblem([A, B])).toBe(TOO_FEW_POINTS)
    expect(closeProblem([A, B, C])).toBeNull()
    // A bow tie: the edge back to the first point crosses the second edge.
    expect(closeProblem([A, B, [-120.0, 50.9], [-120.6, 50.9], [-120.3, 50.4]])).toBe(CROSSING_CLOSE)
  })

  it('finds a click on or near the first point, in pixels', () => {
    expect(CLOSE_PIXELS).toBe(10)
    expect(nearFirstPoint([A, B, C], pixel(-120.6, 50.5), project)).toBe(true)
    expect(nearFirstPoint([A, B, C], pixel(-120.53, 50.53), project)).toBe(true)
    expect(nearFirstPoint([A, B, C], pixel(-120.48, 50.5), project)).toBe(false)
    // pointWouldCross counts the first point as touching the first edge,
    // which is why closing is checked before it.
    expect(pointWouldCross([A, B, C], A)).toBe(true)
  })

  it('counts the area\'s points as the API does, and says how many are left when few are', () => {
    const shapes = [boxShapeOf({ west: -121, south: 50, east: -120, north: 51 }), ...readAreaText('p:-122,50,-121.5,50,-121.7,50.4')]
    expect(areaPointCount(shapes)).toBe(7)
    expect(POINTS_LEFT_WARNING).toBe(20)
    expect(pointsLeftText(21)).toBeNull()
    expect(pointsLeftText(20)).toBe("20 points left before the area's limit of 250.")
    expect(pointsLeftText(1)).toBe("1 point left before the area's limit of 250.")
  })

  it('draws its edges, the line to the mouse and its points, the first marked', () => {
    const data = drawingFeatures([A, B], C)
    expect(data.features.map((feature) => [feature.properties.kind, feature.geometry.coordinates]))
      .toEqual([['edges', [A, B]], ['next', [B, C]], ['point', A], ['point', B]])
    expect(data.features.filter((f) => f.properties.kind === 'point').map((f) => f.properties.first)).toEqual([true, false])
    expect(drawingFeatures([], C).features).toEqual([])
  })
})

describe('the polygon being drawn', () => {
  // Draws with the hook: onFinish answers with the given problem (or null).
  function draw({ shapes = [], problem = null } = {}) {
    const onFinish = vi.fn(() => problem)
    const hook = renderHook(() => useAreaPolygon({ shapes, onFinish, project }))
    act(() => hook.result.current.start())
    return { hook, onFinish, polygon: () => hook.result.current }
  }

  // Clicks the map at [lng, lat].
  function click(polygon, lngLat) {
    act(() => polygon().click(lngLat, pixel(...lngLat)))
  }

  // Presses a key on the page (or on a given element).
  function press(key, target = window) {
    act(() => {
      target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
    })
  }

  it('closes on a click near the first point, handing on the points once', () => {
    const { onFinish, polygon } = draw()
    click(polygon, A)
    click(polygon, B)
    click(polygon, C)
    click(polygon, [-120.58, 50.52])
    expect(onFinish).toHaveBeenCalledTimes(1)
    expect(onFinish).toHaveBeenCalledWith([A, B, C])
    expect(polygon().active).toBe(false)
    expect(polygon().points).toEqual([])
  })

  it('finishes with Enter, and with finish() (a double-click or the Finish button)', () => {
    const first = draw()
    for (const point of [A, B, C]) click(first.polygon, point)
    press('Enter')
    expect(first.onFinish).toHaveBeenCalledWith([A, B, C])
    expect(first.polygon().active).toBe(false)
    const second = draw()
    for (const point of [A, B, C]) click(second.polygon, point)
    act(() => second.polygon().finish())
    expect(second.onFinish).toHaveBeenCalledWith([A, B, C])
  })

  it('refuses a crossing point, and closing with too few points', () => {
    const { onFinish, polygon } = draw()
    click(polygon, A)
    click(polygon, B)
    // Near the first point with only 2: not closed.
    click(polygon, A)
    expect(polygon().message).toBe(TOO_FEW_POINTS)
    press('Enter')
    expect(polygon().message).toBe(TOO_FEW_POINTS)
    click(polygon, C)
    expect(polygon().message).toBeNull()
    click(polygon, ACROSS)
    expect(polygon().message).toBe(CROSSING_POINT)
    expect(polygon().points).toEqual([A, B, C])
    expect(onFinish).not.toHaveBeenCalled()
  })

  it('removes the last point with Backspace and cancels with Esc, handing nothing on', () => {
    const { onFinish, polygon } = draw()
    for (const point of [A, B, C]) click(polygon, point)
    press('Backspace')
    expect(polygon().points).toEqual([A, B])
    act(() => polygon().undo())
    expect(polygon().points).toEqual([A])
    press('Escape')
    expect(polygon().active).toBe(false)
    expect(polygon().points).toEqual([])
    expect(onFinish).not.toHaveBeenCalled()
  })

  it('leaves the keys alone while typing in a field', () => {
    const { polygon } = draw()
    for (const point of [A, B, C]) click(polygon, point)
    const input = document.createElement('input')
    document.body.append(input)
    press('Backspace', input)
    press('Enter', input)
    expect(polygon().points).toEqual([A, B, C])
    expect(polygon().active).toBe(true)
    input.remove()
  })

  it('keeps drawing with the reason when the shape is refused', () => {
    const { onFinish, polygon } = draw({ problem: 'The polygon is about 9.9 km², smaller than the minimum.' })
    for (const point of [A, B, C]) click(polygon, point)
    press('Enter')
    expect(onFinish).toHaveBeenCalledTimes(1)
    expect(polygon().active).toBe(true)
    expect(polygon().points).toEqual([A, B, C])
    expect(polygon().message).toBe('The polygon is about 9.9 km², smaller than the minimum.')
  })

  it('counts the points left before the area\'s limit, and stops at it', () => {
    // 61 boxes: 244 points, so 6 are left.
    const boxes = Array.from({ length: 61 }, (_, i) => boxShapeOf({ west: -125 + i * 0.01, south: 50, east: -124.995 + i * 0.01, north: 50.005 }))
    const { polygon } = draw({ shapes: boxes })
    expect(polygon().left).toBe(MAX_AREA_POINTS - 244)
    const ring = [[-120, 50], [-119, 50], [-118.5, 50.5], [-118.6, 51], [-119.2, 51.4], [-120, 51]]
    for (const point of ring) click(polygon, point)
    expect(polygon().left).toBe(0)
    click(polygon, [-120.3, 50.6])
    expect(polygon().message).toBe(NO_POINTS_LEFT)
    expect(polygon().points).toHaveLength(6)
  })

  it('does nothing until started, and ignores keys after', () => {
    const onFinish = vi.fn(() => null)
    const hook = renderHook(() => useAreaPolygon({ shapes: [], onFinish, project }))
    act(() => hook.result.current.click(A, pixel(...A)))
    expect(hook.result.current.points).toEqual([])
    press('Enter')
    act(() => hook.result.current.finish())
    expect(onFinish).not.toHaveBeenCalled()
  })
})

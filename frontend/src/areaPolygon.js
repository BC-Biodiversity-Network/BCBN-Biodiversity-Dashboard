// Drawing a polygon on the map for an area of interest (the panel's
// "Polygon" button, Draw tab), kept apart from HexMap.jsx so the map itself
// changes little.
//
// Each click on the map adds a point (rounded as a box's corners are, see
// roundCorner); dragging still pans the map, as MapLibre gives no click for
// a drag. Clicking on or near the first point (CLOSE_PIXELS), Enter, a
// double-click or the panel's "Finish" closes the polygon, which then joins
// the area with the same checks as every other shape (App.jsx). A point
// that would make two edges cross is not added, and closing is refused when
// the closing edge would cross. Backspace (or "Undo point") removes the
// last point, Esc (or "Cancel") drops the polygon; neither changes the area
// or the history.

import { useEffect, useRef, useState } from 'react'

import { MAX_AREA_POINTS, pointWouldCross, ringCrosses, roundCorner } from './areaShapes'

// The one-line hint while a polygon is being drawn.
export const POLYGON_HINT = 'Click to add points. Click the first point, press Enter or double-click to finish. '
  + 'Backspace removes the last point, Esc cancels.'

// How near the first point (in screen pixels) a click closes the polygon.
export const CLOSE_PIXELS = 10

// How many points may be left before the area's limit (MAX_AREA_POINTS)
// when the hint starts saying how many are left.
export const POINTS_LEFT_WARNING = 20

// The short messages while drawing.
export const CROSSING_POINT = 'That point would make two edges cross, so it was not added.'
export const CROSSING_CLOSE = 'Closing the polygon here would make two edges cross. Remove the last point '
  + '(Backspace) or add another.'
export const TOO_FEW_POINTS = 'A polygon needs at least 3 different points.'
export const NO_POINTS_LEFT = `The area can have at most ${MAX_AREA_POINTS} points (a box counts as 4), so no more `
  + 'can be added. Finish the polygon or remove a point.'

/**
 * How many points the area's shapes have, as the API counts them: 4 for a
 * box, a polygon's own points.
 */
export function areaPointCount(shapes) {
  return shapes.reduce((sum, shape) => sum + (shape.isBox ? 4 : shape.ring.length), 0)
}

/**
 * The hint's note on how many points are left before the area's limit
 * (left, after the area's shapes and the points drawn so far), or null
 * while more than POINTS_LEFT_WARNING are left.
 */
export function pointsLeftText(left) {
  if (left > POINTS_LEFT_WARNING) {
    return null
  }
  return `${left} ${left === 1 ? 'point' : 'points'} left before the area's limit of ${MAX_AREA_POINTS}.`
}

/**
 * Whether a click at pixel { x, y } is on or near the first point drawn
 * (points, [lng, lat] each), as the map shows it (project([lng, lat]) gives
 * its pixel { x, y }).
 */
export function nearFirstPoint(points, pixel, project) {
  if (points.length === 0) {
    return false
  }
  const first = project(points[0])
  return Math.hypot(first.x - pixel.x, first.y - pixel.y) <= CLOSE_PIXELS
}

/**
 * What a click at lngLat ([lng, lat]) does to the points drawn so far:
 * { points } with the new point added (rounded), or the same points when it
 * rounds to the last one, or { message } when it is refused: no points left
 * (left, before this one) or a crossing.
 */
export function addPoint(points, lngLat, left) {
  const point = roundCorner(lngLat)
  const last = points[points.length - 1]
  if (last && last[0] === point[0] && last[1] === point[1]) {
    return { points }
  }
  if (left <= 0) {
    return { message: NO_POINTS_LEFT }
  }
  if (pointWouldCross(points, point)) {
    return { message: CROSSING_POINT }
  }
  return { points: [...points, point] }
}

/**
 * Why the points drawn cannot be closed into a polygon yet, or null: fewer
 * than 3, or a closing edge (last point back to the first) that would cross
 * or touch another.
 */
export function closeProblem(points) {
  if (points.length < 3) {
    return TOO_FEW_POINTS
  }
  return ringCrosses(points) ? CROSSING_CLOSE : null
}

/**
 * The polygon being drawn as GeoJSON for the map: its edges so far (kind
 * "edges"), the line from the last point to the mouse (kind "next"), and
 * its points (kind "point", first true for the first one, which closes the
 * polygon when clicked).
 */
export function drawingFeatures(points, mouse) {
  const features = []
  if (points.length >= 2) {
    features.push({ type: 'Feature', properties: { kind: 'edges' }, geometry: { type: 'LineString', coordinates: points } })
  }
  if (points.length >= 1 && mouse) {
    features.push({
      type: 'Feature', properties: { kind: 'next' },
      geometry: { type: 'LineString', coordinates: [points[points.length - 1], mouse] },
    })
  }
  points.forEach((point, index) => {
    features.push({ type: 'Feature', properties: { kind: 'point', first: index === 0 }, geometry: { type: 'Point', coordinates: point } })
  })
  return { type: 'FeatureCollection', features }
}

/**
 * Whether a key press belongs to a form field or a button (typing in the
 * search box, Enter on a focused button), not to the polygon being drawn.
 */
function keyForAField(event) {
  return Boolean(event.target?.closest?.('input, textarea, select, button, [contenteditable="true"]'))
}

/**
 * The polygon being drawn. shapes is the area's shapes (for the point
 * limit); onFinish(ring) adds a closed ring ([lng, lat] points) to the area
 * and returns null, or why it was refused (the API's message, as for every
 * shape), in which case drawing goes on so it can be fixed; project([lng,
 * lat]) gives a point's pixel on the map. Returns:
 *   active, points, mouse   whether drawing, the points so far, the mouse
 *   message                 why the last point or finish was refused, or null
 *   left                    points left before the area's limit
 *   start(), cancel(), finish(), undo()
 *   click(lngLat, pixel)    a click on the map
 *   move(lngLat)            the mouse moving over the map
 * While drawing, Enter finishes, Backspace removes the last point and Esc
 * cancels (not while typing in a field).
 */
export function useAreaPolygon({ shapes, onFinish, project }) {
  const [active, setActive] = useState(false)
  const [points, setPoints] = useState([])
  const [mouse, setMouse] = useState(null)
  const [message, setMessage] = useState(null)
  const left = MAX_AREA_POINTS - areaPointCount(shapes) - points.length

  /** Drops the polygon being drawn and stops drawing. */
  function cancel() {
    setActive(false)
    setPoints([])
    setMouse(null)
    setMessage(null)
  }

  /** Starts a new polygon. */
  function start() {
    cancel()
    setActive(true)
  }

  /** Closes the polygon and adds it to the area, or says why not. */
  function finish() {
    if (!active) {
      return
    }
    const problem = closeProblem(points) ?? onFinish(points)
    if (problem) {
      setMessage(problem)
    } else {
      cancel()
    }
  }

  /** Removes the last point. */
  function undo() {
    setPoints((current) => current.slice(0, -1))
    setMessage(null)
  }

  /** A click on the map: closes on the first point, else adds a point. */
  function click(lngLat, pixel) {
    if (!active) {
      return
    }
    if (points.length >= 1 && nearFirstPoint(points, pixel, project)) {
      // Checked before the crossing test, which counts the first point as
      // touching the first edge.
      if (points.length >= 3) {
        finish()
      } else {
        setMessage(TOO_FEW_POINTS)
      }
      return
    }
    const result = addPoint(points, lngLat, left)
    setMessage(result.message ?? null)
    if (result.points) {
      setPoints(result.points)
    }
  }

  /** The mouse over the map, for the line from the last point. */
  function move(lngLat) {
    if (active) {
      setMouse(lngLat)
    }
  }

  // The keys, through a ref so the listener always calls this render's
  // functions.
  const keys = useRef({})
  keys.current = { finish, undo, cancel }
  useEffect(() => {
    if (!active) {
      return undefined
    }
    /** Enter finishes, Backspace removes the last point, Esc cancels. */
    function onKey(event) {
      if (keyForAField(event)) {
        return
      }
      if (event.key === 'Enter') {
        event.preventDefault()
        keys.current.finish()
      } else if (event.key === 'Backspace') {
        event.preventDefault()
        keys.current.undo()
      } else if (event.key === 'Escape') {
        keys.current.cancel()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active])

  return { active, points, mouse, message, left, start, cancel, finish, undo, click, move }
}

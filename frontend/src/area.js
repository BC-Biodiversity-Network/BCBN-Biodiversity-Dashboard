// The area search's rules on the front end: a box drawn on the map, which
// hexagons it selects, and whether it is too small or too big to ask the API
// about (backend/api/area.py, whose rules these copy; a back end test runs
// this file with node to check they still agree).

import {
  POLYGON_TO_CELLS_FLAGS, cellToParent, cellsToMultiPolygon, getHexagonAreaAvg, getResolution, polygonToCellsExperimental,
} from 'h3-js'

// The smallest box, in average hexagons of the resolution it is drawn at:
// the same as MIN_AREA_HEXAGONS in backend/api/area.py. Kept above 1 so a
// box cannot be drawn around one small place (such as a First Nations
// reserve) to get only that place's records.
export const MIN_AREA_HEXAGONS = 4

// The most hexagons a box may touch: the same as MAX_AREA_HEXAGONS in
// backend/api/area.py.
export const MAX_AREA_HEXAGONS = 200000

// The radius of the sphere H3 measures areas on, in km, so a box's area
// compares with H3's average hexagon area (the same as EARTH_RADIUS_KM in
// backend/api/area.py).
export const EARTH_RADIUS_KM = 6371.007180918475

// How many decimals of a degree a drawn box's edges keep: four is about
// 11 m, plenty for a box, and keeps the address short.
const BOX_DECIMALS = 4

// Rounds a number of degrees to BOX_DECIMALS.
function roundDegrees(value) {
  return Number(value.toFixed(BOX_DECIMALS))
}

// The box between two corners of a drag on the map, each [lng, lat], in
// whichever order they were dragged: { west, south, east, north }, rounded
// to BOX_DECIMALS. A box on the map is a rectangle on screen too, because
// the map's lines of longitude and latitude are straight.
export function boxFromCorners([lng1, lat1], [lng2, lat2]) {
  return {
    west: roundDegrees(Math.min(lng1, lng2)),
    south: roundDegrees(Math.min(lat1, lat2)),
    east: roundDegrees(Math.max(lng1, lng2)),
    north: roundDegrees(Math.max(lat1, lat2)),
  }
}

// Turns degrees into radians.
function radians(degrees) {
  return (degrees * Math.PI) / 180
}

// A box's area in km² on H3's sphere: the radius squared, times the width in
// radians, times the difference of the sines of its north and south edges
// (the same formula as box_area_km2 in backend/api/area.py).
export function boxAreaKm2({ west, south, east, north }) {
  return EARTH_RADIUS_KM ** 2 * radians(east - west) * (Math.sin(radians(north)) - Math.sin(radians(south)))
}

// The average hexagon's area at a resolution, in km² (H3's own figure).
export function averageHexagonKm2(res) {
  return getHexagonAreaAvg(res, 'km2')
}

// The smallest box allowed at a resolution, in km².
export function minimumAreaKm2(res) {
  return MIN_AREA_HEXAGONS * averageHexagonKm2(res)
}

// Writes a number the way the messages do: with commas, and decimals
// figures after the point.
function written(value, decimals) {
  return value.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

// The message for a box that touches more hexagons (count, about) of a
// resolution than an area search can count. Like every message here it
// gives the hexagons' size, never H3's resolution number.
function tooBigText(count, res) {
  return `This box touches about ${written(count, 0)} hexagons of about ${written(averageHexagonKm2(res), 1)} km², `
    + `more than the ${written(MAX_AREA_HEXAGONS, 0)} an area search can count. Draw a smaller box, or zoom out `
    + 'first for larger hexagons.'
}

// What is wrong with a box at a resolution before any hexagon is found, or
// null when nothing is: too small (under MIN_AREA_HEXAGONS average hexagons),
// or so big that it must touch more than MAX_AREA_HEXAGONS hexagons. The
// same checks, in the same order, as check_box_size in backend/api/area.py,
// written for the map's visitors.
export function boxSizeProblem(box, res) {
  const area = boxAreaKm2(box)
  const smallest = minimumAreaKm2(res)
  if (area < smallest) {
    return `This box is about ${written(area, 1)} km², smaller than the minimum of ${written(smallest, 1)} km² `
      + `for hexagons of this size (${MIN_AREA_HEXAGONS} hexagons of about ${written(averageHexagonKm2(res), 1)} km²). `
      + 'Draw a larger box, or zoom in first for smaller hexagons.'
  }
  const estimate = area / averageHexagonKm2(res)
  return estimate > MAX_AREA_HEXAGONS ? tooBigText(estimate, res) : null
}

// Every hexagon at resolution res that overlaps the box, even by a corner,
// sorted: polygonToCellsExperimental with containmentOverlapping, the
// corners as [lat, lng], exactly as the API finds them (box_cells in
// backend/api/area.py, the same H3 library).
export function boxCells({ west, south, east, north }, res) {
  const corners = [[south, west], [south, east], [north, east], [north, west]]
  return polygonToCellsExperimental(corners, res, POLYGON_TO_CELLS_FLAGS.containmentOverlapping).sort()
}

// The outline of a group of hexagons (cells, all at one resolution) as
// one GeoJSON shape around the outside of the whole group, not around each
// hexagon, for the map to draw: h3-js joins them (about 0.8 s for the
// largest box allowed, 117,442 hexagons, so it is worked out once per box).
export function groupOutline(cells) {
  return {
    type: 'Feature',
    properties: {},
    geometry: { type: 'MultiPolygon', coordinates: cellsToMultiPolygon(cells, true) },
  }
}

// A test of whether a hexagon the map shows is in the selected group (cells,
// at resolution res), whatever resolution the map shows now: at the same
// resolution, the hexagon itself is in the group; at a finer one, the
// larger hexagon it lies in is; at a coarser one, it holds a hexagon of the
// group. Returns a function of a hexagon's id.
export function selectionTest(cells, res) {
  const chosen = new Set(cells)
  // The group's hexagons at each coarser resolution, worked out when needed.
  const coarser = new Map()
  return (cell) => {
    const cellRes = getResolution(cell)
    if (cellRes === res) {
      return chosen.has(cell)
    }
    if (cellRes > res) {
      return chosen.has(cellToParent(cell, res))
    }
    if (!coarser.has(cellRes)) {
      coarser.set(cellRes, new Set(cells.map((one) => cellToParent(one, cellRes))))
    }
    return coarser.get(cellRes).has(cell)
  }
}

// What is wrong with a box at a resolution, or null: boxSizeProblem first
// (which needs no hexagons, so a huge box costs nothing), then the real
// number of hexagons it touches against the most allowed, as the API checks
// both.
export function areaProblem(box, res) {
  const problem = boxSizeProblem(box, res)
  if (problem) {
    return problem
  }
  const count = boxCells(box, res).length
  return count > MAX_AREA_HEXAGONS ? tooBigText(count, res) : null
}

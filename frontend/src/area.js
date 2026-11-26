// The area search's rules on the front end for a box: the box between two
// corners of a drag, its area, the hexagons it selects, and which map
// hexagons stay bright (backend/api/area.py, whose rules these copy; a
// back end test runs this file with node to check they still agree). An
// area of several shapes, its checks and its messages are in areaShapes.js.

import {
  POLYGON_TO_CELLS_FLAGS, cellToChildrenSize, cellToParent, getHexagonAreaAvg, getResolution, polygonToCellsExperimental,
} from 'h3-js'

// The smallest shape, in average hexagons of the smallest size the maps use
// (SMALLEST_HEXAGONS_RES, about 3 km): the same as MIN_AREA_HEXAGONS in
// backend/api/area.py. Kept above 1 so a shape cannot be drawn around one
// small place (such as a First Nations reserve) to get only that place's
// records.
export const MIN_AREA_HEXAGONS = 4

// The resolution of the smallest hexagons the maps use (about 3 km), whose
// average area sets the minimum (the largest of the API's RESOLUTIONS).
export const SMALLEST_HEXAGONS_RES = 7

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

// Turns degrees into radians the way Python's math.radians does (times
// pi / 180), so a box's area matches the API's box_area_km2 as closely as
// the two sine functions allow.
function radians(degrees) {
  return degrees * (Math.PI / 180)
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

// The smallest shape allowed, in km², the same at every hexagon size:
// MIN_AREA_HEXAGONS average hexagons of about 3 km (about 20.6 km²; the
// same as MIN_AREA_KM2 in backend/api/area.py). Whole hexagons are always
// counted, so with larger hexagons the area counted is never smaller than
// at about 3 km, and one minimum is enough.
export const MIN_AREA_KM2 = MIN_AREA_HEXAGONS * averageHexagonKm2(SMALLEST_HEXAGONS_RES)

// Every hexagon at resolution res that overlaps the box, even by a corner,
// sorted: polygonToCellsExperimental with containmentOverlapping, the
// corners as [lat, lng], exactly as the API finds them (box_cells in
// backend/api/area.py, the same H3 library).
export function boxCells({ west, south, east, north }, res) {
  const corners = [[south, west], [south, east], [north, east], [north, west]]
  return polygonToCellsExperimental(corners, res, POLYGON_TO_CELLS_FLAGS.containmentOverlapping).sort()
}

// A test of whether a hexagon the map shows is in the selected group (cells,
// at resolution res), whatever resolution the map shows now, so the map
// keeps bright only what the area counts: at the same resolution, the
// hexagon itself is in the group; at a finer one, the larger hexagon it
// lies in is; at a coarser one (the area's hexagons are smaller than the
// map's, which happens since the area's size no longer follows the zoom),
// every one of its smaller hexagons at res is in the group, so a large
// hexagon only partly in the area is faded (the map then draws the area's
// own hexagons on top once it has them, see areaLayers.js). Returns a
// function of a hexagon's id.
export function selectionTest(cells, res) {
  const chosen = new Set(cells)
  // How many of the group's hexagons lie in each larger hexagon, for each
  // coarser resolution, worked out when needed.
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
      const counts = new Map()
      for (const one of cells) {
        const parent = cellToParent(one, cellRes)
        counts.set(parent, (counts.get(parent) ?? 0) + 1)
      }
      coarser.set(cellRes, counts)
    }
    return coarser.get(cellRes).get(cell) === cellToChildrenSize(cell, res)
  }
}

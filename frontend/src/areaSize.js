// The hexagon size of an area of interest, chosen without regard to the
// map's zoom: "Auto" (the default) picks the smallest hexagons at which every
// shape passes the area's checks, and a visitor can pick a size of their own
// in the panel instead. One area always uses one size for all its shapes.
//
// The checks are the API's own (shapesProblem in areaShapes.js): each shape
// at least MIN_AREA_HEXAGONS average hexagons, the whole area at most
// MAX_AREA_HEXAGONS hexagons. Smaller hexagons follow the shapes more
// closely, so Auto tries the smallest first and less land outside the shapes
// is counted.

import { minimumAreaKm2 } from './area.js'
import { shapeAreaKm2, shapeSizeProblem, shapesCells, shapesProblem } from './areaShapes.js'
import { hexagonWidthMetres } from './zoomTiers.js'

// The hexagon sizes an area can use (those of the maps), as H3 resolutions,
// smallest hexagons first: the order Auto tries them in.
export const AREA_SIZES = [7, 6, 5, 4]

// A size's width in km, rounded as the map's label rounds it ("about 52 km").
export function sizeKm(res) {
  return Math.round(hexagonWidthMetres(res) / 1000)
}

// A size as the panel's choice names it: "About 52 km".
export function sizeLabel(res) {
  return `About ${sizeKm(res)} km`
}

// Chooses the size for Auto: the smallest hexagons (of AREA_SIZES) at which
// the shapes pass every check. Returns { res, cells } with the area's
// hexagons at that size (sorted, each once), or { problem } with the API's
// message when no size works: a shape too small even at the smallest
// hexagons gets that size's message, anything else (too big even at the
// largest) the largest size's.
export function autoSize(shapes) {
  for (const res of AREA_SIZES) {
    if (shapeSizeProblem(shapes, res)) {
      continue
    }
    const { cells, problem } = shapesCells(shapes, res)
    if (!problem) {
      return { res, cells }
    }
  }
  const smallest = AREA_SIZES[0]
  const tooSmallEverywhere = shapes.some((shape) => shapeAreaKm2(shape) < minimumAreaKm2(smallest))
  return { problem: shapesProblem(shapes, tooSmallEverywhere ? smallest : AREA_SIZES[AREA_SIZES.length - 1]) }
}

// The hint the page adds to a size refusal (shapesProblem) at a size the
// visitor picked: " Or choose smaller hexagons." for a shape too small,
// unless the size is already the smallest. An area too big needs none: the
// API's message already says to use larger hexagons.
export function sizeHint(shapes, res) {
  const tooSmall = shapes.some((shape) => shapeAreaKm2(shape) < minimumAreaKm2(res))
  return tooSmall && res !== AREA_SIZES[0] ? ' Or choose smaller hexagons.' : ''
}

// Checks shapes at a size the visitor picked: null when they pass, else the
// API's message with the page's hint about another size (sizeHint).
export function pickedSizeProblem(shapes, res) {
  const problem = shapesProblem(shapes, res)
  return problem ? problem + sizeHint(shapes, res) : null
}

// The area's size and hexagons: at the size picked (res, a number), or chosen
// by Auto (res null). Returns { res, cells } or { problem }; a picked size
// is not checked here (the API says what is wrong with it, as before), only
// worked out.
export function sizedArea(shapes, res) {
  if (res === null || res === undefined) {
    return autoSize(shapes)
  }
  return { res, cells: shapesCells(shapes, res).cells }
}

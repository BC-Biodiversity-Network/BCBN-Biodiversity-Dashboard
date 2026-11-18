// The hexagon size of an area of interest, chosen without regard to the
// map's zoom: "Auto" (the default) prefers hexagons of about 7 km
// (AUTO_PREFERRED), and a visitor can pick a size of their own in the panel
// instead. One area always uses one size for all its shapes, and while an
// area is shown the whole map uses that size at every zoom (HexMap.jsx).
//
// The checks are the API's own (shapesProblem in areaShapes.js): each shape
// at least MIN_AREA_HEXAGONS average hexagons, the whole area at most
// MAX_AREA_HEXAGONS hexagons. About 7 km reads well on the whole map at
// most zooms, where about 3 km becomes specks when seen from far out, so
// Auto uses about 3 km only for a shape too small for about 7 km, and larger
// sizes only for an area too big for it.

import { minimumAreaKm2 } from './area.js'
import { shapeAreaKm2, shapeSizeProblem, shapesCells, shapesProblem } from './areaShapes.js'
import { hexagonWidthMetres } from './zoomTiers.js'

// The hexagon sizes an area can use (those of the maps), as H3 resolutions,
// smallest hexagons first (Auto's order is autoSizes).
export const AREA_SIZES = [7, 6, 5, 4]

// A size's width in km, rounded as the map's label rounds it ("about 52 km").
export function sizeKm(res) {
  return Math.round(hexagonWidthMetres(res) / 1000)
}

// A size as the panel's choice names it: "About 52 km".
export function sizeLabel(res) {
  return `About ${sizeKm(res)} km`
}

// The size Auto prefers: about 7 km (resolution 6).
export const AUTO_PREFERRED = 6

/**
 * The sizes Auto tries for these shapes, in order: only about 3 km when a
 * shape is smaller than the minimum for about 7 km (MIN_AREA_HEXAGONS
 * average hexagons of it, about 145 km²), else about 7 km and then the
 * larger sizes, for an area too big for it.
 */
export function autoSizes(shapes) {
  const tooSmall = shapes.some((shape) => shapeAreaKm2(shape) < minimumAreaKm2(AUTO_PREFERRED))
  return tooSmall ? [AREA_SIZES[0]] : AREA_SIZES.filter((res) => res <= AUTO_PREFERRED)
}

/**
 * Chooses the size for Auto: the first size of autoSizes(shapes) at which
 * the shapes pass every check. Returns { res, cells } with the area's
 * hexagons at that size (sorted, each once), or { problem } with the API's
 * message when no size works, at the last size tried (about 3 km for a
 * small shape, else about 52 km).
 */
export function autoSize(shapes) {
  const sizes = autoSizes(shapes)
  for (const res of sizes) {
    if (shapeSizeProblem(shapes, res)) {
      continue
    }
    const { cells, problem } = shapesCells(shapes, res)
    if (!problem) {
      return { res, cells }
    }
  }
  return { problem: shapesProblem(shapes, sizes[sizes.length - 1]) }
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

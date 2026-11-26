// The hexagon size of an area of interest, chosen without regard to the
// map's zoom: "Auto" (the default) prefers hexagons of about 7 km
// (AUTO_PREFERRED), and a visitor can pick a size of their own in the panel
// instead. One area always uses one size for all its shapes, and while an
// area is shown the whole map uses that size at every zoom (HexMap.jsx).
//
// The checks are the API's own (shapesProblem in areaShapes.js): each shape
// at least MIN_AREA_KM2 (the same at every size), the whole area at most
// MAX_AREA_HEXAGONS hexagons. About 7 km reads well on the whole map at
// most zooms, where about 3 km becomes specks when seen from far out, so
// Auto uses about 3 km first only for a small shape (AUTO_SMALL_SHAPE_KM2),
// which would show as only a hexagon or two of about 7 km, and larger sizes
// only for an area too big for the first size tried.

import { MIN_AREA_HEXAGONS, averageHexagonKm2 } from './area.js'
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

// A shape smaller than this (km²) makes Auto start at about 3 km: under
// MIN_AREA_HEXAGONS average hexagons of about 7 km (about 145 km²), the
// same line as before 2026-11-26, when it was the minimum at about 7 km.
export const AUTO_SMALL_SHAPE_KM2 = MIN_AREA_HEXAGONS * averageHexagonKm2(AUTO_PREFERRED)

/**
 * The sizes Auto tries for these shapes, in order: about 3 km first when a
 * shape is small (under AUTO_SMALL_SHAPE_KM2), then the larger sizes, for
 * an area too big for it; else about 7 km first and then the larger sizes.
 */
export function autoSizes(shapes) {
  const small = shapes.some((shape) => shapeAreaKm2(shape) < AUTO_SMALL_SHAPE_KM2)
  return small ? AREA_SIZES : AREA_SIZES.filter((res) => res <= AUTO_PREFERRED)
}

/**
 * Chooses the size for Auto: the first size of autoSizes(shapes) at which
 * the shapes pass every check. Returns { res, cells } with the area's
 * hexagons at that size (sorted, each once), or { problem } with the API's
 * message when no size works, at the last size tried (about 52 km; a shape
 * too small is too small at every size, with the same message).
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

/**
 * Checks shapes at a size the visitor picked: null when they pass, else the
 * API's message. Since the minimum is the same at every size (2026-11-26),
 * only an area too big can fail at one size and pass at another, and the
 * API's message for it already says to use larger hexagons, so no hint is
 * added (it used to add " Or choose smaller hexagons." for a shape too
 * small for the size picked).
 */
export function pickedSizeProblem(shapes, res) {
  return shapesProblem(shapes, res)
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

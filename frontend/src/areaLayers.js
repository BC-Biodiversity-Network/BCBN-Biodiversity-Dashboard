// How the map shows an area of interest, kept apart from HexMap.jsx so the
// map itself changes little.
//
// An area has its own hexagon size (Auto or picked, see areaSize.js). While
// an area has at least one shape, and the map is not a single species' map,
// the whole map is locked to that size at every zoom (lockedResolution), so
// every map hexagon is either one of the area's or not: the area's keep
// their colours, the rest are faded (selectionTest in area.js), and
// hexagons with no records are not drawn, so the basemap shows through.
// "Clear area" unlocks it, and the size follows the zoom again.
//
// Order on the map, bottom to top: the map's hexagons (deck.gl layers just
// under the basemap's labels), then the shapes' outline (a MapLibre line
// layer added last, in HexMap.jsx).

import { areaText } from './areaShapes'
import { sizeKm } from './areaSize'
import { hexagonSizeLabel } from './zoomTiers'

// The outline of each of the area's shapes (the boxes and polygons the
// visitor made): a dark solid line on a wider white one, so it reads on
// light and dark hexagons of either scale (blue or orange), and on the
// basemap. It is the only outline the area has, at every zoom.
export const SHAPE_OUTLINE = { dark: '#1a1a1a', light: '#ffffff', darkWidth: 2, lightWidth: 4.5 }

/**
 * A short text naming an area (its hexagon size and its area text), to tell
 * areas apart: in deck.gl's update triggers and in React keys.
 */
export function areaId(area) {
  return `${area.res}:${areaText(area.shapes)}`
}

/**
 * The box around an area's shapes, as [[west, south], [east, north]], from
 * every corner of every shape, for the map to fit the area in view.
 */
export function shapesBounds(shapes) {
  let west = Infinity
  let south = Infinity
  let east = -Infinity
  let north = -Infinity
  for (const shape of shapes) {
    for (const [lng, lat] of shape.ring) {
      west = Math.min(west, lng)
      east = Math.max(east, lng)
      south = Math.min(south, lat)
      north = Math.max(north, lat)
    }
  }
  return [[west, south], [east, north]]
}

/**
 * The hexagon size (an H3 resolution) the whole map is locked to, or null
 * when it follows the zoom: the size of the area shown (area, { shapes, res,
 * ... }, or null), unless a single species' map is open (speciesOpen), which
 * never shows the area.
 */
export function lockedResolution(area, speciesOpen) {
  return area && !speciesOpen ? area.res : null
}

/**
 * The hexagon size label at the top left: "Hexagons ~52 km" for the size
 * the zoom chose (res), or "Hexagons ~7 km (the area's size)" while the map
 * is locked to an area's size (locked).
 */
export function hexagonSizeText(res, locked) {
  return locked ? `Hexagons ~${sizeKm(res)} km (the area's size)` : hexagonSizeLabel(res)
}

/**
 * The legend's line while the map is locked to an area's size (res, or null
 * when it is not locked), or null: why zooming no longer changes the size,
 * and how to undo it.
 */
export function areaLegendNote(res) {
  if (res === null || res === undefined) {
    return null
  }
  return `While an area is shown, the whole map uses its hexagon size, about ${sizeKm(res)} km, at every zoom. `
    + 'Clear the area to go back.'
}

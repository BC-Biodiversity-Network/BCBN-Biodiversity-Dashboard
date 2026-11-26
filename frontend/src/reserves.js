// First Nations reserves on the species map: the outline layer, its on/off
// switch, and the reminder in the hexagon and area panels.
//
// The rules (from the proposal, November 2026): reserves can be seen but not
// clicked, and nothing lets a visitor select a reserve's data. The layer is
// drawn with MapLibre's own layers, which deck.gl never picks, and the map
// gives them no click or hover handlers, so a click on a reserve reaches the
// hexagon under it exactly as if the layer were not there. The reminder
// only uses the names the API gives for hexagons the visitor already chose
// (reserve_names in /api/hexes/{cell} and /api/area).

// Whether the reserve layer is shown when the address does not say. Change
// this one line to change the default; the address only records a choice
// that differs from it (see readReservesShown in route.js).
export const RESERVES_SHOWN_BY_DEFAULT = true

// How the outlines look: a dark olive-brown line with a light sand fill,
// drawn above the hexagons. The brown was picked to stay clear of the three
// ramps (colours.js): its closest step is the darkest orange, 20.6 apart
// by CIEDE2000, where the more reddish browns tried came within 9 to 15.
// The fill is faint, so the hexagon colours still read through it.
export const RESERVE_STYLE = {
  line: '#6b5a2e',
  lineWidth: 1.5,
  fill: '#e9dcb5',
  fillOpacity: 0.3,
  // The names: darker brown on a white halo, in the basemap's own font.
  // Its glyphs (OpenFreeMap's Noto Sans) include every letter the names use,
  // such as Ɂ, Ǫ, Ḏ, Ẕ and the accented capitals (checked November 2026).
  text: '#4a3e1f',
  textHalo: '#ffffff',
  textSize: 11,
  font: ['Noto Sans Regular'],
}

// The zoom from which the names show. Below it most reserves are only a few
// pixels across and their names would cover the map; at 9 one pixel is
// about 90 m of ground, so a reserve 1 km across is about 11 pixels wide.
// Names that would overlap each other or the basemap's labels are left out
// by the map itself.
export const RESERVE_LABEL_MIN_ZOOM = 9

// The most reserve names the panels' reminder lists before "and N more".
export const REMINDER_MAX_NAMES = 5

// The reserve names for the reminder: the first REMINDER_MAX_NAMES, exactly
// as given and in the order given (the API sorts them), then ", and N more"
// (", and 1 more" for one) when there are more.
export function reminderNamesText(names) {
  const shown = names.slice(0, REMINDER_MAX_NAMES).join(', ')
  const extra = names.length - REMINDER_MAX_NAMES
  return extra > 0 ? `${shown}, and ${extra.toLocaleString('en-US')} more` : shown
}

// The layer's data when there is nothing to draw: no reserves yet, or none
// available. The map always has the source, so the layer keeps its place
// under the area outlines however late the reserves arrive.
export const NO_RESERVE_OUTLINES = { type: 'FeatureCollection', features: [] }

// The outlines to draw from the /api/reserves answer (null while it loads or
// when it failed): its outlines when available, else none.
export function reserveOutlines(reserves) {
  return reserves?.available && reserves.outlines ? reserves.outlines : NO_RESERVE_OUTLINES
}

// The area of a ring of [lng, lat] points (positive or negative by its
// direction) and its centroid, by the shoelace formula, in plain degrees:
// close enough to place a label inside one small reserve.
function ringAreaAndCentroid(ring) {
  let twiceArea = 0
  let x = 0
  let y = 0
  for (let i = 0; i < ring.length - 1; i += 1) {
    const [x0, y0] = ring[i]
    const [x1, y1] = ring[i + 1]
    const cross = x0 * y1 - x1 * y0
    twiceArea += cross
    x += (x0 + x1) * cross
    y += (y0 + y1) * cross
  }
  if (twiceArea === 0) {
    return { area: 0, centroid: ring[0] }
  }
  return { area: Math.abs(twiceArea / 2), centroid: [x / (3 * twiceArea), y / (3 * twiceArea)] }
}

// Where a horizontal line at latitude lat crosses a ring: the longitudes of
// every crossing, sorted.
function crossings(ring, lat) {
  const found = []
  for (let i = 0; i < ring.length - 1; i += 1) {
    const [x0, y0] = ring[i]
    const [x1, y1] = ring[i + 1]
    if ((y0 <= lat && y1 > lat) || (y1 <= lat && y0 > lat)) {
      found.push(x0 + ((lat - y0) / (y1 - y0)) * (x1 - x0))
    }
  }
  return found.sort((a, b) => a - b)
}

// A point inside a ring for its label: the centroid when it is inside the
// ring, else the middle of the widest stretch of the ring along the
// centroid's latitude (inside by construction), so a bent or hollow shape
// still gets its name on itself.
export function labelPointOfRing(ring) {
  const { centroid } = ringAreaAndCentroid(ring)
  const [lng, lat] = centroid
  const xs = crossings(ring, lat)
  let widest = null
  for (let i = 0; i + 1 < xs.length; i += 2) {
    if (lng >= xs[i] && lng <= xs[i + 1]) {
      return centroid
    }
    if (!widest || xs[i + 1] - xs[i] > widest[1] - widest[0]) {
      widest = [xs[i], xs[i + 1]]
    }
  }
  return widest ? [(widest[0] + widest[1]) / 2, lat] : ring[0]
}

// One label point per reserve, for the names: the map would otherwise put a
// name on every piece of a reserve, and on every map tile it crosses. The
// point is inside the reserve's largest part (see labelPointOfRing), and
// carries only the name.
export function reserveLabelPoints(outlines) {
  const features = []
  for (const feature of outlines.features) {
    const { type, coordinates } = feature.geometry ?? {}
    const polygons = type === 'Polygon' ? [coordinates] : type === 'MultiPolygon' ? coordinates : []
    const rings = polygons.map((polygon) => polygon[0]).filter((ring) => ring && ring.length >= 4)
    if (rings.length === 0) {
      continue
    }
    const largest = rings.reduce((best, ring) => (ringAreaAndCentroid(ring).area > ringAreaAndCentroid(best).area ? ring : best))
    features.push({
      type: 'Feature',
      properties: { name: feature.properties.name },
      geometry: { type: 'Point', coordinates: labelPointOfRing(largest) },
    })
  }
  return { type: 'FeatureCollection', features }
}

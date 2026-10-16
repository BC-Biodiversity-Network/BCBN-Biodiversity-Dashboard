// The colour scales of the three maps: the overview coloured by species, the
// overview coloured by records, and the map of one species' records.
//
// Every scale uses fixed bins on a log scale with an open top bin (decided on
// 14 October 2026), the same bins at every zoom: a colour has to mean the
// same number at every resolution, otherwise two tiers cannot be compared.
// That makes the map go paler as you zoom in, because one big hexagon splits
// into smaller ones that each hold fewer records. That is deliberate.
//
// How the ramps were made: the earlier nine-step blue and orange ramps were
// kept as the path from palest to darkest, and resampled into 8 and 7 colours
// spaced evenly along it by CIEDE2000, a measure of how different two colours
// look. That keeps their hues and both ends, and makes neighbouring steps
// equally easy to tell apart (blue: 8.1 to 8.6 apart; orange: 11.0 to 11.8),
// where simply dropping steps from the old ramps left two dark blues only 4.8
// apart. The species map uses its own pink-to-purple ramp (see
// PINK_PURPLE_STEPS below).

// The overview species ramp: eight blues, palest to darkest. Written as red,
// green, blue numbers because that is what deck.gl expects.
export const BLUE_STEPS = [
  [205, 226, 251], // #cde2fb
  [164, 200, 245], // #a4c8f5
  [119, 173, 238], // #77adee
  [78, 145, 231], // #4e91e7
  [48, 122, 212], // #307ad4
  [33, 100, 182], // #2164b6
  [23, 77, 146], // #174d92
  [13, 54, 107], // #0d366b
]

// The overview records ramp: seven oranges, palest to darkest.
//
// The nine-step ramp these come from started from the well known ColorBrewer
// "Oranges" ramp, darkened throughout. The published ramp begins at a
// near-white #fff5eb, which at the fill opacity used here is almost the same
// colour as the basemap, so the lightest bin would look like missing data
// rather than a small count. Darkening also brought its lightness range into
// line with the blue one, so neither map looks flatter than the other.
export const ORANGE_STEPS = [
  [254, 233, 214], // #fee9d6
  [254, 203, 154], // #fecb9a
  [253, 164, 86], // #fda456
  [239, 120, 30], // #ef781e
  [202, 87, 11], // #ca570b
  [157, 63, 5], // #9d3f05
  [107, 42, 4], // #6b2a04
]

// The species map ramp: eight colours from saturated pink through magenta and
// purple to a very dark purple, palest to darkest.
//
// Chosen on 16 October 2026 after comparing single-hue ramps (teal, magenta,
// violet, green, red) and established multi-hue palettes (viridis, inferno,
// ColorBrewer YlOrRd and YlGnBu). It follows the style of the Biodiversité
// Québec Atlas "Number of species" map. The six colours read off that map's
// legend (#e46c9f, #c8418f, #a01c79, #6c1874, #460b6b, #170420, estimated from
// a screenshot) were used as the path, and eight steps spaced evenly along it
// by CIEDE2000. The path stops 87% of the way to its near-black end, so the
// darkest step (#2a0c3f) still reads as a purple, not as black.
//
// The palest step is a strong pink, not a pale tint, because most species are
// rare: 52% of species never have more than 2 records in any resolution 7
// hexagon, so the 1-to-2-record bin is the colour most species maps are
// mostly made of, and a lone hexagon has to be easy to find. At the lowest
// opacity (LOWEST_ALPHA) it sits 32.5 from bare land, 35.3 from water, 45.9
// from parks and 38.5 from forest. Neighbouring steps are
// 6.4 to 7.9 apart. It is at least 18.1 from any overview blue and 35.0 from
// any overview orange, so a species map is never mistaken for either overview.
export const PINK_PURPLE_STEPS = [
  [228, 108, 159], // #e46c9f
  [210, 82, 149], // #d25295
  [189, 56, 137], // #bd3889
  [163, 31, 123], // #a31f7b
  [130, 26, 118], // #821a76
  [95, 19, 113], // #5f1371
  [63, 12, 96], // #3f0c60
  [42, 12, 63], // #2a0c3f
]

// The lowest value that falls in each bin. A value belongs to the last bin
// whose lower edge it has reached, so the final bin has no upper limit (the
// legend writes it with a "+").
export const SPECIES_BIN_EDGES = [1, 3, 10, 30, 100, 300, 1000, 3000]
export const RECORD_BIN_EDGES = [1, 10, 100, 1000, 10000, 100000, 1000000]

// How see-through each bin is, from the lowest bin to the highest, spread
// evenly between these two. The same on all three maps.
//
// Nearly solid, like the Biodiversité Québec Atlas maps (decided on 16 October
// 2026). Earlier versions ran from 0.55 to 0.82 so the basemap showed through
// the sparse bins, but once every hexagon had a white outline (see
// HEXAGON_OUTLINE) the outlines did the work of keeping hexagons apart, and
// near-solid fills make each colour look the way the legend shows it.
// Measured against the earlier opacity, every comparison improved: the
// closest pair of neighbouring bins on the map went from 4.9 to 7.4 apart
// (blue), 8.4 to 10.4 (orange) and 5.8 to 6.4 (pink to purple); the palest
// overview blue went from 4.3 to 6.2 away from water, and the palest overview
// orange from 4.6 to 7.0 away from bare land (CIEDE2000, where under about 4
// is hard to tell apart). What is lost is the terrain and lakes under the
// hexagons; place names are drawn above the hexagons, so they stay readable.
//
// Opacity still rises a little with the count, so it and darkness both say
// "more".
const LOWEST_ALPHA = 0.85
const HIGHEST_ALPHA = 0.95

// The opacity of each of count bins, evenly spaced from LOWEST_ALPHA to
// HIGHEST_ALPHA.
export function binAlphas(count) {
  return Array.from({ length: count }, (_, index) =>
    LOWEST_ALPHA + ((HIGHEST_ALPHA - LOWEST_ALPHA) * index) / (count - 1))
}

// The thin white line drawn around every hexagon on all three maps, so each
// hexagon stands apart from its neighbours. Its width is in screen pixels, so
// it stays the same 1-pixel line at every zoom instead of growing with the
// hexagons.
//
// Every zoom tier starts with hexagons about 30 pixels across, so the line is
// never more than a thin edge. Only when zooming out past the opening view do
// the resolution 4 hexagons keep shrinking, and below about 12 pixels a white
// line on every edge turns the map into a white mesh. So the line is fully
// white while hexagons are at least fullAtPixels wide, fades out as they
// shrink, and is gone at goneAtPixels.
export const HEXAGON_OUTLINE = {
  rgb: [255, 255, 255],
  widthPixels: 1,
  fullAtPixels: 20,
  goneAtPixels: 8,
}

// How strong the outline is (0 to 1) when hexagons are this many pixels
// wide. Rounded to quarter steps, so the map only redraws its outlines a few
// times while zooming out rather than at every frame.
export function outlineOpacity(hexagonPixels) {
  const { fullAtPixels, goneAtPixels } = HEXAGON_OUTLINE
  const fraction = (hexagonPixels - goneAtPixels) / (fullAtPixels - goneAtPixels)

  return Math.round(Math.min(1, Math.max(0, fraction)) * 4) / 4
}

// The three scales. valueOf reads the number a hexagon is coloured by.
export const COLOUR_SCHEMES = {
  species: {
    buttonLabel: 'Species',
    legendCaption: 'Distinct species per hexagon',
    valueOf: (hexagon) => hexagon.distinctSpecies,
    binEdges: SPECIES_BIN_EDGES,
    steps: BLUE_STEPS,
  },
  records: {
    buttonLabel: 'Records',
    legendCaption: 'Occurrence records per hexagon',
    valueOf: (hexagon) => hexagon.occurrences,
    binEdges: RECORD_BIN_EDGES,
    steps: ORANGE_STEPS,
  },
  speciesMap: {
    valueOf: (hexagon) => hexagon.occurrences,
    binEdges: SPECIES_BIN_EDGES,
    steps: PINK_PURPLE_STEPS,
  },
}

// The two overview scales the toggle chooses between, in button order.
export const OVERVIEW_SCHEMES = ['species', 'records']

// Works out which bin a value belongs to, as a position in the list of edges.
// Walks up the edges and keeps the last one the value has reached.
export function binIndexFor(value, binEdges) {
  let index = 0

  for (let i = 0; i < binEdges.length; i++) {
    if (value >= binEdges[i]) {
      index = i
    }
  }

  return index
}

// The colour of every bin of a scale, as drawn on the map: red, green, blue
// and an opacity, all 0 to 255. The opacity comes from the same bin as the
// colour, so a low bin is both paler and more see-through than a high one.
// The map and the legend both use this, so the legend always matches.
export function binColours(scheme) {
  const alphas = binAlphas(scheme.steps.length)
  return scheme.steps.map(([red, green, blue], bin) => [red, green, blue, Math.round(alphas[bin] * 255)])
}

// Each scale's bin colours, worked out once: hexagonColour runs once per
// hexagon, up to about 98,000 times when the scale is switched.
const binColoursOf = new WeakMap()

// Picks the fill colour for one hexagon under a scale (see binColours).
export function hexagonColour(hexagon, scheme) {
  if (!binColoursOf.has(scheme)) {
    binColoursOf.set(scheme, binColours(scheme))
  }
  return binColoursOf.get(scheme)[binIndexFor(scheme.valueOf(hexagon), scheme.binEdges)]
}

// Shortens a bin edge for the legend, so the strip stays narrow.
// 1000000 becomes "1M", 3000 becomes "3K" and 1200 "1.2K"; anything under a
// thousand is written out in full.
export function shortEdgeLabel(edge) {
  if (edge >= 1000000) {
    const millions = edge / 1000000
    return Number.isInteger(millions) ? `${millions}M` : `${millions.toFixed(1)}M`
  }
  if (edge < 1000) {
    return String(edge)
  }

  const thousands = edge / 1000

  // Whole thousands lose the decimal point: 5000 is "5K", not "5.0K".
  return Number.isInteger(thousands)
    ? `${thousands}K`
    : `${thousands.toFixed(1)}K`
}

// The labels under the legend's colours: where each bin starts, with a "+"
// on the last one because it has no upper limit.
export function legendEdgeLabels(binEdges) {
  return binEdges.map((edge, index) =>
    shortEdgeLabel(edge) + (index === binEdges.length - 1 ? '+' : ''))
}

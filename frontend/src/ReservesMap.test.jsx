import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// The reserve layer on the map (HexMap.jsx): the "First Nations reserves" switch, the
// outlines handed to the map, and that the layer cannot be clicked. The real
// map and deck.gl need a graphics card, so, as in HexMap.test.jsx, stand-ins
// record what the map is given: sources (each GeoJSON source's data, by id),
// mapLayers (each MapLibre layer's props, by id), deckLayers (the layers
// handed to deck.gl), the overlay's click handler and the map's props.
const mapState = vi.hoisted(() => ({ sources: {}, mapLayers: {}, deckLayers: [], props: {}, onClick: null }))

vi.mock('react-map-gl/maplibre', async () => {
  const React = await import('react')
  // A stand-in for the map: renders its children and keeps its props.
  const Map = React.forwardRef(function Map(props, ref) {
    React.useImperativeHandle(ref, () => ({
      fitBounds: () => {}, getContainer: () => ({ clientWidth: 1280, clientHeight: 800 }), loaded: () => true,
      getBounds: () => ({ contains: () => true }), easeTo: () => {}, jumpTo: () => {},
    }))
    mapState.props = props
    mapState.sources = {}
    mapState.mapLayers = {}
    return React.createElement('div', { 'data-testid': 'map' }, props.children)
  })
  // A stand-in for a GeoJSON source: records its data.
  function Source({ id, data, children }) {
    mapState.sources[id] = data
    return React.createElement(React.Fragment, null, children)
  }
  // A stand-in for a MapLibre layer: records its props.
  function Layer(props) {
    mapState.mapLayers[props.id] = props
    return null
  }
  // A stand-in for useControl: keeps the overlay's latest layers.
  function useControl() {
    return {
      setProps: (props) => {
        mapState.deckLayers = props.layers
        mapState.onClick = props.onClick
      },
    }
  }
  return { default: Map, Layer, Source, useControl }
})
vi.mock('@deck.gl/mapbox', () => ({ MapboxOverlay: class {} }))
vi.mock('@deck.gl/geo-layers', () => ({
  // A stand-in layer that only keeps what it was given.
  H3HexagonLayer: class {
    constructor(props) {
      this.props = props
      this.id = props.id
    }
  },
}))

// Imported after the stand-ins above, which vitest puts in place first.
import HexMap from './HexMap'
import { NO_RESERVE_OUTLINES, RESERVE_LABEL_MIN_ZOOM } from './reserves'

// One overview hexagon per size, real H3 cells in BC.
const OVERVIEW = {
  rows: {
    4: [{ h3Cell: '8428c9bffffffff', occurrences: 120, distinctSpecies: 40 }],
    5: [{ h3Cell: '8528c9b7fffffff', occurrences: 50, distinctSpecies: 20 }],
    6: [{ h3Cell: '8628c9b47ffffff', occurrences: 10, distinctSpecies: 5 }],
    7: [{ h3Cell: '8728c9b40ffffff', occurrences: 3, distinctSpecies: 2 }],
  },
  loading: false,
  error: null,
}

// A made-up /api/reserves answer with one reserve, its name using letters
// the labels must show.
const OUTLINES = {
  type: 'FeatureCollection',
  features: [{
    type: 'Feature', properties: { alcode: '90001', name: 'K’IT TSAḎẔE Ɂ Ǫ Á 1' },
    geometry: { type: 'Polygon', coordinates: [[[-123, 49], [-122.9, 49], [-122.9, 49.1], [-123, 49]]] },
  }],
}
const AVAILABLE = { available: true, source: { source_title: 'Test' }, outlines: OUTLINES }
const UNAVAILABLE = { available: false, source: null, outlines: null }

// The map with these props.
function show(props = {}) {
  return render(<HexMap overview={OVERVIEW} species={null} {...props} />)
}

// The "First Nations reserves" switch, or null when there is none.
function reservesSwitch() {
  return screen.queryByRole('switch', { name: 'First Nations reserves' })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the Reserves switch', () => {
  it('is hidden when the API has no reserves, or before (or without) its answer', () => {
    show({ reserves: UNAVAILABLE })
    expect(reservesSwitch()).toBeNull()
    cleanup()
    show({ reserves: null })
    expect(reservesSwitch()).toBeNull()
  })

  it('shows with a swatch when the reserves are available, and switches the layer', () => {
    const onChange = vi.fn()
    const { container, rerender } = render(
      <HexMap overview={OVERVIEW} species={null} reserves={AVAILABLE} reservesShown onReservesShownChange={onChange} />)
    const toggle = reservesSwitch()
    expect(toggle.checked).toBe(true)
    expect(container.querySelector('.top-left-controls .reserves-switch .reserves-swatch')).toBeTruthy()
    fireEvent.click(toggle)
    expect(onChange).toHaveBeenLastCalledWith(false)

    rerender(<HexMap overview={OVERVIEW} species={null} reserves={AVAILABLE} reservesShown={false}
      onReservesShownChange={onChange} />)
    expect(reservesSwitch().checked).toBe(false)
    fireEvent.click(reservesSwitch())
    expect(onChange).toHaveBeenLastCalledWith(true)
  })

  it('is there on a species map too', () => {
    show({ species: { key: '59PMB', name: 'Turdus migratorius', id: 1 }, reserves: AVAILABLE })
    expect(reservesSwitch()).toBeTruthy()
  })
})

describe('the reserve layer', () => {
  it('hands the outlines to the map, with a fill, an outline and the names from a zoom', () => {
    show({ reserves: AVAILABLE, reservesShown: true })
    expect(mapState.sources.reserves).toBe(OUTLINES)
    // One label point per reserve, carrying only its name, exactly as given.
    expect(mapState.sources['reserve-labels'].features.map((feature) => feature.properties))
      .toEqual([{ name: 'K’IT TSAḎẔE Ɂ Ǫ Á 1' }])
    const { 'reserves-fill': fill, 'reserves-line': line, 'reserves-label': label } = mapState.mapLayers
    expect(fill.type).toBe('fill')
    expect(line.type).toBe('line')
    expect(label.type).toBe('symbol')
    expect(label.minzoom).toBe(RESERVE_LABEL_MIN_ZOOM)
    // The name as given: no change of case or letters.
    expect(label.layout['text-field']).toEqual(['get', 'name'])
    expect(label.layout['text-transform']).toBeUndefined()
    for (const layer of [fill, line, label]) {
      expect(layer.layout.visibility).toBe('visible')
    }
  })

  it('hides every part of the layer when switched off', () => {
    show({ reserves: AVAILABLE, reservesShown: false })
    for (const id of ['reserves-fill', 'reserves-line', 'reserves-label']) {
      expect(mapState.mapLayers[id].layout.visibility).toBe('none')
    }
  })

  it('draws nothing when the API has no reserves', () => {
    show({ reserves: UNAVAILABLE, reservesShown: true })
    expect(mapState.sources.reserves).toEqual(NO_RESERVE_OUTLINES)
    expect(mapState.mapLayers['reserves-line'].layout.visibility).toBe('none')
  })

  it('cannot be clicked or hovered: it is not a deck.gl layer and the map listens to no layer', () => {
    show({ reserves: AVAILABLE, reservesShown: true })
    // deck.gl picks the hexagon under a click; the reserves are not among
    // its layers, so they can never be the thing picked.
    expect(mapState.deckLayers.length).toBeGreaterThan(0)
    expect(mapState.deckLayers.some((layer) => layer.id.includes('reserve'))).toBe(false)
    // The map is told of no layers to report clicks or hovers on.
    expect(mapState.props.interactiveLayerIds).toBeUndefined()
    // No layer takes a handler of its own.
    for (const layer of Object.values(mapState.mapLayers)) {
      expect(Object.keys(layer).filter((name) => name.startsWith('on'))).toEqual([])
    }
  })

  it('comes before the area outlines, so a selected area is drawn above it', () => {
    const outline = { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] } }
    show({
      reserves: AVAILABLE, reservesShown: true,
      selectedArea: { box: { west: -123, south: 49, east: -122, north: 50 }, res: 6, cells: [], outline },
    })
    const ids = Object.keys(mapState.mapLayers)
    expect(ids.indexOf('reserves-label')).toBeLessThan(ids.indexOf('area-group-edge'))
    expect(ids.indexOf('reserves-label')).toBeLessThan(ids.indexOf('area-box-edge'))
  })
})

describe('a click on a reserve', () => {
  it('reaches the hexagon under it, the same with the layer on or off', () => {
    // deck.gl picks only its own layers, so a click inside a reserve comes
    // to the overlay's click handler as the hexagon under it.
    const click = { object: { h3Cell: '8728c9b40ffffff' }, layer: { id: 'bc-hexagons-r7' } }
    for (const reservesShown of [true, false]) {
      const onCellClick = vi.fn()
      show({ reserves: AVAILABLE, reservesShown, onCellClick })
      act(() => mapState.onClick(click))
      expect(onCellClick).toHaveBeenCalledWith('8728c9b40ffffff')
      cleanup()
    }
  })
})

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// The real map (MapLibre) needs a graphics card, which the test page does
// not have. These stand-ins render the map's sources as plain elements, each
// with the number of shapes it was given.
vi.mock('react-map-gl/maplibre', async () => {
  const React = await import('react')
  const Map = React.forwardRef(function Map(props, ref) {
    React.useImperativeHandle(ref, () => ({ fitBounds: () => {}, flyTo: () => {} }))
    return React.createElement('div', { 'data-testid': 'map' }, props.children)
  })
  // A stand-in source: says how many shapes it draws.
  function Source({ id, data }) {
    return React.createElement('div', { 'data-testid': id, 'data-count': data.features.length })
  }
  return { default: Map, Layer: () => null, Source }
})
vi.mock('maplibre-gl/dist/maplibre-gl.css', () => ({}))
vi.mock('./HexMap', () => ({ BASEMAP_STYLE: {}, INITIAL_VIEW_STATE: { longitude: -125, latitude: 54, zoom: 4 } }))

// Imported after the stand-ins above, which vitest puts in place first.
import DatasetMap, { locationGeoJson } from './DatasetMap'
import { describeLocation } from './datasetText'

afterEach(cleanup)

// A dataset as the detail endpoint gives it, with no location.
function dataset(fields) {
  return { source_name: 'CanWIN', boxes: [], points: [], looked_up_places: [], ...fields }
}

const COLUMBIA_BASIN = {
  name: 'Columbia Basin', kind: 'outside_bc', role: 'study_location', latitude: 41.8, longitude: -87.6,
  large_area: false,
}

describe('the Datasets map', () => {
  it('draws only the record\'s own boxes and points, never a looked-up place', () => {
    const location = describeLocation(dataset({ points: [{ lat: 49, lon: -123 }], looked_up_places: [COLUMBIA_BASIN] }))
    expect(Object.keys(locationGeoJson(location))).toEqual(['boxes', 'matchingBoxes', 'points'])
    render(<DatasetMap location={location} datasetId="a" sourceName="CanWIN" />)
    expect(screen.queryByTestId('dataset-places')).toBeNull()
    expect(screen.getByTestId('dataset-points').dataset.count).toBe('1')
  })

  it('keys only what is drawn, with no line for looked-up places', () => {
    const location = describeLocation(dataset({
      boxes: [{ w: -124, e: -123, s: 49, n: 50 }], points: [{ lat: 49, lon: -123 }] }))
    const { container } = render(<DatasetMap location={location} datasetId="a" sourceName="CanWIN" />)
    expect([...container.querySelectorAll('.dataset-map-key-line')].map((line) => line.textContent)).toEqual([
      'Solid line: extent from the CanWIN record', 'Point from the CanWIN record'])
  })

  it('has no key and nothing drawn when the record gives only a place list (or only looked-up places)', () => {
    const location = describeLocation(dataset({ places: ['Kamloops; British Columbia; Canada'],
      looked_up_places: [COLUMBIA_BASIN] }))
    const { container } = render(<DatasetMap location={location} datasetId="a" sourceName="CanWIN" />)
    expect(container.querySelector('.dataset-map-key')).toBeNull()
    for (const id of ['dataset-boxes', 'dataset-matching-boxes', 'dataset-points']) {
      expect(screen.getByTestId(id).dataset.count).toBe('0')
    }
  })
})

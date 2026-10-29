import { useEffect, useMemo, useRef, useState } from 'react'
import Map, { Layer, Popup, Source } from 'react-map-gl/maplibre'

import 'maplibre-gl/dist/maplibre-gl.css'

import { BASEMAP_STYLE, INITIAL_VIEW_STATE } from './HexMap'
import { placeLabel } from './datasetText'

// The colours of what the map draws: the record's own boxes and points in
// the site's blue, the places our lookup found in orange rings, so the two
// never look alike.
const RECORD_COLOUR = '#184f95'
const LOOKED_UP_COLOUR = '#c2410c'

// The closest the map zooms in when fitting a dataset, so a single point or
// a tiny box does not fill the screen.
const FIT_MAX_ZOOM = 9

// The box layers: a light fill and a solid outline.
const BOX_FILL = { id: 'dataset-box-fill', type: 'fill', paint: { 'fill-color': RECORD_COLOUR, 'fill-opacity': 0.08 } }
const BOX_LINE = { id: 'dataset-box-line', type: 'line', paint: { 'line-color': RECORD_COLOUR, 'line-width': 2 } }
// The record's points: filled dots.
const POINT_LAYER = {
  id: 'dataset-points',
  type: 'circle',
  paint: {
    'circle-radius': 5, 'circle-color': RECORD_COLOUR, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5,
  },
}
// The looked-up places: hollow orange rings.
const PLACE_LAYER = {
  id: 'dataset-places',
  type: 'circle',
  paint: {
    'circle-radius': 6, 'circle-color': '#fff', 'circle-opacity': 0.6,
    'circle-stroke-color': LOOKED_UP_COLOUR, 'circle-stroke-width': 3,
  },
}

// A box as a GeoJSON polygon. A box crossing the 180 degree line (west in
// the eastern half, east in the western half) is drawn the short way across
// it, by writing its east edge past 180.
export function boxFeature(box) {
  const east = box.w > 0 && box.e < 0 ? box.e + 360 : box.e
  return {
    type: 'Feature',
    properties: {},
    geometry: {
      type: 'Polygon',
      coordinates: [[[box.w, box.s], [east, box.s], [east, box.n], [box.w, box.n], [box.w, box.s]]],
    },
  }
}

// A point as a GeoJSON feature, with its label for the hover box.
function pointFeature(longitude, latitude, label = '') {
  return { type: 'Feature', properties: { label }, geometry: { type: 'Point', coordinates: [longitude, latitude] } }
}

// The GeoJSON of everything drawn for a location (see describeLocation):
// { boxes, points, places }, each a FeatureCollection.
export function locationGeoJson(location) {
  const collection = (features) => ({ type: 'FeatureCollection', features })
  return {
    boxes: collection((location?.boxes ?? []).map(boxFeature)),
    points: collection((location?.points ?? []).map((point) => pointFeature(point.lon, point.lat))),
    places: collection((location?.places ?? []).map(
      (place) => pointFeature(place.longitude, place.latitude, placeLabel(place)))),
  }
}

// The box around everything drawn, as [[west, south], [east, north]], or
// null when nothing is drawn.
export function boundsOfLocation(geojson) {
  let west = Infinity
  let south = Infinity
  let east = -Infinity
  let north = -Infinity
  // Widens the box to take in one [longitude, latitude] position.
  function take([lng, lat]) {
    west = Math.min(west, lng)
    east = Math.max(east, lng)
    south = Math.min(south, lat)
    north = Math.max(north, lat)
  }
  for (const feature of geojson.boxes.features) {
    feature.geometry.coordinates[0].forEach(take)
  }
  for (const feature of [...geojson.points.features, ...geojson.places.features]) {
    take(feature.geometry.coordinates)
  }
  return west === Infinity ? null : [[west, south], [east, north]]
}

// The key in the map's corner: one line for each kind of thing drawn, so
// the map itself always says where each shape comes from.
function MapKey({ geojson, sourceName }) {
  const lines = []
  if (geojson.boxes.features.length > 0) {
    lines.push(['box', `Extent from the ${sourceName} record`])
  }
  if (geojson.points.features.length > 0) {
    lines.push(['point', `Point from the ${sourceName} record`])
  }
  if (geojson.places.features.length > 0) {
    lines.push(['place', 'Place named in the record, located by BCBN (GeoNames)'])
  }
  if (lines.length === 0) {
    return null
  }
  return (
    <div className="dataset-map-key">
      {lines.map(([kind, text]) => (
        <div key={kind} className="dataset-map-key-line">
          <span className={`dataset-map-key-swatch ${kind}`} aria-hidden="true" />
          {text}
        </div>
      ))}
    </div>
  )
}

// The map of the Datasets page. It only ever shows the selected dataset
// (never all datasets at once): location is what describeLocation worked out
// for it, or null when no dataset is selected (or it is still loading).
// datasetId changes when another dataset is picked, which moves the map.
export default function DatasetMap({ location, datasetId, sourceName, message }) {
  const mapRef = useRef(null)
  const [loaded, setLoaded] = useState(false)
  const [hovered, setHovered] = useState(null)
  const geojson = useMemo(() => locationGeoJson(location), [location])

  // Fits the map to what is drawn for a newly selected dataset, or goes
  // back to the whole of BC when nothing is drawn.
  useEffect(() => {
    const map = mapRef.current
    if (!loaded || !map) {
      return
    }
    const bounds = boundsOfLocation(geojson)
    if (bounds) {
      map.fitBounds(bounds, { padding: 48, maxZoom: FIT_MAX_ZOOM, duration: 700 })
    } else {
      map.flyTo({ center: [INITIAL_VIEW_STATE.longitude, INITIAL_VIEW_STATE.latitude],
        zoom: INITIAL_VIEW_STATE.zoom, duration: 700 })
    }
  }, [loaded, datasetId, geojson])

  // Shows the name of the point or place under the pointer, if it has one.
  function handleHover(event) {
    const feature = event.features?.find((f) => f.properties?.label)
    setHovered(feature ? { label: feature.properties.label, lngLat: event.lngLat } : null)
  }

  return (
    <div className="dataset-map">
      {message && <div className="status-message">{message}</div>}
      <MapKey geojson={geojson} sourceName={sourceName} />
      <Map
        ref={mapRef}
        initialViewState={INITIAL_VIEW_STATE}
        mapStyle={BASEMAP_STYLE}
        style={{ width: '100%', height: '100%' }}
        onLoad={() => setLoaded(true)}
        interactiveLayerIds={[PLACE_LAYER.id, POINT_LAYER.id]}
        onMouseMove={handleHover}
        onMouseLeave={() => setHovered(null)}
      >
        <Source id="dataset-boxes" type="geojson" data={geojson.boxes}>
          <Layer {...BOX_FILL} />
          <Layer {...BOX_LINE} />
        </Source>
        <Source id="dataset-points" type="geojson" data={geojson.points}>
          <Layer {...POINT_LAYER} />
        </Source>
        <Source id="dataset-places" type="geojson" data={geojson.places}>
          <Layer {...PLACE_LAYER} />
        </Source>
        {hovered && (
          <Popup longitude={hovered.lngLat.lng} latitude={hovered.lngLat.lat} closeButton={false} offset={10}>
            {hovered.label}
          </Popup>
        )}
      </Map>
    </div>
  )
}

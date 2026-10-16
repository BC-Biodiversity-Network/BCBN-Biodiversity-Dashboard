import { useEffect, useRef, useState } from 'react'

import { fetchMeta } from './api'
import HexMap from './HexMap'
import { loadHexData } from './loadHexData'
import SearchBox from './SearchBox'
import SiteFooter from './SiteFooter'
import SpeciesCard from './SpeciesCard'
import { RESOLUTIONS } from './zoomTiers'

// Where the four overview hexagon files live, one per resolution.
//
// All four are fetched at startup. Together they are about 469 KB, which is
// less than a single photograph, so fetching only the one the current zoom
// needs would add code and a loading pause for no real saving. Please do not
// add lazy loading here without a measurement showing it is worth it.
const HEX_DATA_URLS = {
  4: 'data/bc_hex_r4.csv.gz',
  5: 'data/bc_hex_r5.csv.gz',
  6: 'data/bc_hex_r6.csv.gz',
  7: 'data/bc_hex_r7.csv.gz',
}

// The whole page: the map (the overview, or the species whose card is open),
// the search box and species card on the right, and the footer.
export default function App() {
  const [overviewRows, setOverviewRows] = useState({})
  const [overviewError, setOverviewError] = useState(null)
  // The species whose card is open, or null for the plain overview:
  // { key, name, id }. key is the key the card and map are opened with; when
  // an old merged key was typed it is that old key, so the card can say it
  // was merged. name is the scientific name for the map legend. id changes
  // every time a species is picked, so the map moves to it again even when
  // the same species is picked twice.
  const [species, setSpecies] = useState(null)
  const picks = useRef(0)
  // What the data is and where it comes from (/api/meta), for the GBIF
  // download citation on the card; null until it arrives or if it fails.
  const [meta, setMeta] = useState(null)

  // Fetches /api/meta once, when the page first appears. If the API is not
  // there, the overview still works and the card says the citation is loading.
  useEffect(() => {
    fetchMeta()
      .then(setMeta)
      .catch((metaError) => console.error(metaError))
  }, [])

  // Downloads the four overview files once, when the page first appears, and
  // keeps them for the whole visit, so zooming never waits on the network.
  useEffect(() => {
    Promise.all(RESOLUTIONS.map((r) => loadHexData(HEX_DATA_URLS[r])))
      .then((loaded) => {
        const byResolution = {}
        RESOLUTIONS.forEach((r, index) => {
          byResolution[r] = loaded[index]
        })
        setOverviewRows(byResolution)
      })
      .catch((loadError) => {
        console.error(loadError)
        setOverviewError(loadError.message)
      })
  }, [])

  // Opens the card and the map of a species picked in the search box.
  function pickSpecies(result) {
    picks.current += 1
    setSpecies({
      key: result.merged_from ?? result.specieskey,
      name: result.col_name,
      id: picks.current,
    })
  }

  const overview = {
    rows: overviewRows,
    loading: !overviewError && Object.keys(overviewRows).length === 0,
    error: overviewError,
  }

  // The map with its controls fills the window above the footer, so the
  // footer never covers the legend, the controls or the map credits.
  return (
    <div className="app-layout">
      <div className="map-area">
        <HexMap overview={overview} species={species} />

        <div className="top-right-controls">
          <SearchBox onSelect={pickSpecies} />
          {species && (
            <SpeciesCard
              requestedKey={species.key}
              meta={meta}
              onClose={() => setSpecies(null)}
            />
          )}
        </div>
      </div>
      <SiteFooter />
    </div>
  )
}

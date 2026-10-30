import { useEffect, useRef, useState } from 'react'

import { fetchMeta } from './api'
import HexMap from './HexMap'
import { loadHexData } from './loadHexData'
import { readSpeciesKey, speciesDatasetsSearch, speciesMapSearch } from './route'
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
// the search box and species card on the right, and the footer. search is
// the address's query string, which names the species whose card is open
// (?species=<key>, see route.js), and navigate changes it, so a card can be
// linked to and the back button returns to it.
export default function App({ search = '', navigate = () => {} }) {
  const [overviewRows, setOverviewRows] = useState({})
  const [overviewError, setOverviewError] = useState(null)
  // The species whose card is open, or null for the plain overview:
  // { key, name, id }. key is the key the card and map are opened with; when
  // an old merged key was typed it is that old key, so the card can say it
  // was merged. name is the scientific name for the map legend (null until
  // the card arrives, for a species opened from the address). id changes
  // every time a species is picked, so the map moves to it again even when
  // the same species is picked twice.
  const addressKey = readSpeciesKey(search)
  const [species, setSpecies] = useState(() => (addressKey ? { key: addressKey, name: null, id: 0 } : null))
  const picks = useRef(0)
  // What the data is and where it comes from (/api/meta), for the GBIF
  // download citation on the card; null until it arrives or if it fails.
  const [meta, setMeta] = useState(null)
  // The year range for whichever map is shown: null for all years (the
  // default, so the site looks as it always has until it is changed), or
  // { from, to } (see yearRange.js). It lives here rather than in the map,
  // so it stays the same when a species card is opened or closed.
  const [years, setYears] = useState(null)

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

  // When the address names another species than the open one (the back or
  // forward button), opens that one, or closes the card when it names none.
  useEffect(() => {
    const id = picks.current + 1
    setSpecies((open) => {
      if ((open?.key ?? null) === addressKey) {
        return open
      }
      picks.current = id
      return addressKey ? { key: addressKey, name: null, id } : null
    })
  }, [addressKey])

  // Opens the card and the map of a species picked in the search box, and
  // puts it in the address in place of the current entry, so picking species
  // one after another does not fill the back button's history.
  function pickSpecies(result) {
    picks.current += 1
    const key = result.merged_from ?? result.specieskey
    setSpecies({ key, name: result.col_name, id: picks.current })
    navigate(speciesMapSearch(key), { replace: true })
  }

  // Closes the card, back to the plain overview.
  function closeCard() {
    setSpecies(null)
    navigate(speciesMapSearch(null), { replace: true })
  }

  // Keeps the scientific name of a species opened from the address, for the
  // map legend, once its card arrives.
  function cardLoaded(card) {
    setSpecies((open) => (open && !open.name && open.key === card.requested_key
      ? { ...open, name: card.name.col_name }
      : open))
  }

  const overview = {
    rows: overviewRows,
    loading: !overviewError && Object.keys(overviewRows).length === 0,
    error: overviewError,
  }
  // The years the filter allows: those of the whole dataset, once
  // /api/meta has arrived.
  const yearBounds = meta ? { firstYear: meta.first_year, lastYear: meta.last_year } : null

  // The map with its controls fills the window above the footer, so the
  // footer never covers the legend, the controls or the map credits.
  return (
    <div className="app-layout">
      <div className="map-area">
        <HexMap
          overview={overview}
          species={species}
          years={years}
          yearBounds={yearBounds}
          onYearsChange={setYears}
        />

        <div className="top-right-controls">
          <SearchBox onSelect={pickSpecies} />
          {species && (
            <SpeciesCard
              requestedKey={species.key}
              meta={meta}
              onClose={closeCard}
              onLoaded={cardLoaded}
              onOpenDatasets={(key) => navigate(speciesDatasetsSearch(key))}
            />
          )}
        </div>
      </div>
      <SiteFooter />
    </div>
  )
}

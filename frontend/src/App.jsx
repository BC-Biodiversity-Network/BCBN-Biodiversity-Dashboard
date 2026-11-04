import { useEffect, useRef, useState } from 'react'

import { fetchMeta } from './api'
import HexagonPanel from './HexagonPanel'
import HexMap from './HexMap'
import { loadHexData } from './loadHexData'
import RankCard from './RankCard'
import RankName from './RankName'
import {
  CELL_LIST, PANEL_FROM_CLICK, isPlainMap, keptListChoices, mapSearch, rankKey, readCell, readList, readRank,
  readSpeciesKey, speciesDatasetsSearch, speciesMapSearch,
} from './route'
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
// (?species=<key>) and the hexagon whose panel is open (?cell=<H3 id>, see
// route.js), and navigate changes it, so a card or a hexagon can be linked
// to and the back button returns to it. goBack is the browser's back button
// (the card's "Back to this hexagon's species" link uses it).
//
// The side panel under the search box shows one thing at a time:
//   - the hexagon panel when the address has a cell: on the overview, every
//     species in the hexagon; on a species map, that species alone in the
//     hexagon; on a rank map, that rank's species in the hexagon; on both of
//     these, with a link to show every species (which switches to the
//     overview for the same hexagon); a card is then only covered, not
//     closed;
//   - else the species card when the address has a species;
//   - else the rank card when the address has a rank (?rank=...&<path>, see
//     route.js); its map is the overview restricted to the rank's species;
//   - else nothing (the plain overview).
// History: the first hexagon click (no cell in the address yet) adds an
// address entry, so Back closes the panel and returns to what was there
// before; further clicks while the panel is open replace that entry, so
// clicking hexagon after hexagon does not fill the history. Opening a
// species from the panel's list adds an entry, so Back returns to the panel;
// the card then shows a link that does the same. The panel's x acts like
// Back when the panel's entry was added by a click in this visit (it is
// marked with PANEL_FROM_CLICK in the history state), so no dead entry is
// left; when the panel came from a shared link, with nothing to go back to,
// the x removes the cell in place. Either way the overview shows again, or
// the species card on a species map. "Show all species" on a species map's
// panel adds an entry (not marked), so Back returns to the species panel and
// the x then closes the overview's panel in place. Picking a species in the
// search box replaces the entry, as before, which closes the panel.
//
// Ranks: opening a rank (picked in the search box, or from a card's
// classification line or a rank card's next rank down) adds an entry, which
// remembers the address it came from, so Back returns there; so does
// opening a species from a rank card (whose card then offers the way back).
// Opening the rank already shown only moves the map to it again. Closing a
// rank (the x on its card or on the map's "Showing ..." label) returns to
// all species: like Back when the rank was opened in this visit from the
// plain map, so no dead entry is left; else in place, keeping an open
// hexagon.
//
// Species lists: the rank card's and the hexagon panel's lists keep their
// options (name filter, order, BC statuses, page) in the address (see
// readList in route.js), so a filtered list can be shared. Changing them
// replaces the entry, keeping its history state, so Back and the x's still
// work as above. Clicking another hexagon, or opening another rank, keeps
// the list's order and statuses and starts with no name filter on page 1.
//
// historyState reads the current history entry's state (the browser's
// window.history.state; the tests give their own).
export default function App({
  search = '', navigate = () => {}, goBack = () => window.history.back(),
  historyState = () => window.history.state,
}) {
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
  const addressCell = readCell(search)
  const addressRank = readRank(search)
  const addressRankKey = rankKey(addressRank)
  const addressRankList = readList(search)
  const addressCellList = readList(search, CELL_LIST)
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
  // The species last opened from a hexagon's list or a rank card ({ key,
  // rank }: rank null for a hexagon's list), so its card can offer the way
  // back; null when none was.
  const [openedFrom, setOpenedFrom] = useState(null)
  // The rank whose page is open, or null: { rank, name, path, id }, from the
  // address; id changes every time a rank is opened, so the map moves to it.
  const [rankOpen, setRankOpen] = useState(() => (addressRank ? { ...addressRank, id: 0 } : null))

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

  // When the address names another rank than the open one (the back or
  // forward button, or a link), opens that one, or closes it when it names
  // none.
  useEffect(() => {
    const id = picks.current + 1
    setRankOpen((open) => {
      if (rankKey(open) === addressRankKey) {
        return open
      }
      picks.current = id
      return addressRank ? { ...addressRank, id } : null
    })
    // addressRankKey stands for addressRank, which is a new object each time.
  }, [addressRankKey])

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

  // Opens the panel of a clicked hexagon, keeping the species map if one is
  // open: a new address entry for the first click, marked as added by a
  // click; the same entry replaced, with its mark kept, while the panel is
  // already open (see the top of this component).
  function openCell(cell) {
    if (cell === addressCell) {
      return
    }
    const target = mapSearch({
      species: addressKey, rank: addressRank, rankList: addressRankList, cell,
      cellList: keptListChoices(addressCellList),
    })
    if (addressCell) {
      navigate(target, { replace: true, state: historyState() })
    } else {
      navigate(target, { state: PANEL_FROM_CLICK })
    }
  }

  // Closes the hexagon panel: like Back when a click in this visit added its
  // entry, else in place. Either way the overview shows again, or the
  // species card on a species map.
  function closeCell() {
    if (historyState()?.panelFromClick) {
      goBack()
    } else {
      navigate(mapSearch({ species: addressKey, rank: addressRank, rankList: addressRankList }), { replace: true })
    }
  }

  // Switches from a species map's hexagon panel to every species in the same
  // hexagon on the overview, as a new address entry, so Back returns to the
  // species panel. It is not marked as a click's entry: the x on the
  // overview's panel then closes it in place rather than going back to the
  // species panel.
  function showAllSpecies() {
    navigate(mapSearch({ cell: addressCell, cellList: keptListChoices(addressCellList) }))
  }

  // Changes the options of the rank card's species list, or of the hexagon
  // panel's, in place (see the top of this component).
  function changeRankList(list) {
    navigate(mapSearch({ rank: addressRank, rankList: list, cell: addressCell, cellList: addressCellList }),
      { replace: true, state: historyState() })
  }
  function changeCellList(list) {
    navigate(mapSearch({
      species: addressKey, rank: addressRank, rankList: addressRankList, cell: addressCell, cellList: list,
    }), { replace: true, state: historyState() })
  }

  // Opens a species picked from a hexagon's list or a rank card (from, the
  // rank, or null for a hexagon's list): its card and map, as a new address
  // entry, so Back returns to the list or the card.
  function openSpeciesFrom(key, from = null) {
    picks.current += 1
    setSpecies({ key, name: null, id: picks.current })
    setOpenedFrom({ key, rank: from })
    navigate(speciesMapSearch(key))
  }

  // Opens a rank's page (see the top of this component), as a new address
  // entry that remembers the address it came from; for the rank already
  // shown, only moves the map to it again.
  function openRank(rankRef) {
    if (rankKey(rankRef) === addressRankKey && !addressCell) {
      picks.current += 1
      const id = picks.current
      setRankOpen((open) => ({ ...open, id }))
      return
    }
    navigate(mapSearch({ rank: rankRef, rankList: keptListChoices(addressRankList) }),
      { state: { rankOpenedFrom: search } })
  }

  // Closes the rank, back to all species (see the top of this component).
  function closeRank() {
    const from = historyState()?.rankOpenedFrom
    if (from !== undefined && isPlainMap(from) && !addressCell) {
      goBack()
    } else {
      navigate(mapSearch({ cell: addressCell, cellList: addressCellList }), { replace: true })
    }
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
          selectedCell={addressCell}
          onCellClick={openCell}
          rank={species ? null : rankOpen}
          rankId={species ? null : rankKey(rankOpen)}
          onCloseRank={closeRank}
        />

        <div className="top-right-controls">
          <SearchBox onSelect={pickSpecies} onSelectRank={openRank} />
          {addressCell && (
            <HexagonPanel
              key={addressCell}
              cell={addressCell}
              years={years}
              species={species}
              rank={species ? null : rankOpen}
              list={addressCellList}
              onListChange={changeCellList}
              onClose={closeCell}
              onOpenSpecies={(key) => openSpeciesFrom(key)}
              onShowAllSpecies={showAllSpecies}
            />
          )}
          {species && !addressCell && openedFrom?.key === species.key && (
            <button type="button" className="text-button panel-back" onClick={goBack}>
              {openedFrom.rank
                ? <>Back to {openedFrom.rank.rank} <RankName rank={openedFrom.rank.rank} name={openedFrom.rank.name} /></>
                : <>Back to this hexagon&apos;s species</>}
            </button>
          )}
          {!species && rankOpen && (
            <div className="card-holder" hidden={Boolean(addressCell)}>
              <RankCard
                rankRef={rankOpen}
                list={addressRankList}
                onListChange={changeRankList}
                onClose={closeRank}
                onOpenSpecies={(key) => openSpeciesFrom(key, rankOpen)}
                onOpenRank={openRank}
              />
            </div>
          )}
          {species && (
            <div className="card-holder" hidden={Boolean(addressCell)}>
              <SpeciesCard
                requestedKey={species.key}
                meta={meta}
                onClose={closeCard}
                onLoaded={cardLoaded}
                onOpenDatasets={(key) => navigate(speciesDatasetsSearch(key))}
                onOpenRank={openRank}
              />
            </div>
          )}
        </div>
      </div>
      <SiteFooter />
    </div>
  )
}

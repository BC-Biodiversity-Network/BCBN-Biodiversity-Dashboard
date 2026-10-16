import { useEffect, useRef, useState } from 'react'

import { fetchOverviewHexes, fetchSpeciesHexes } from './api'
import { yearsKey } from './yearRange'

// Turns the API's species map answer (two lists, cells and occurrences) into
// one row per hexagon, the shape the map layers and the hover box read, and
// keeps the summary numbers.
export function toMapData(answer) {
  return {
    specieskey: answer.specieskey,
    resolution: answer.res,
    rows: answer.cells.map((cell, index) => ({ h3Cell: cell, occurrences: answer.occurrences[index] })),
    recordsShown: answer.records_shown,
    hexagonCount: answer.hexagon_count,
    yearsCounted: answer.years_counted ?? null,
    recordsNoYearLeftOut: answer.records_no_year_left_out ?? 0,
  }
}

// Turns the API's overview answer (cells, occurrences, distinct_species)
// into one row per hexagon, the same shape as the shipped overview files,
// and keeps the summary numbers.
export function toOverviewData(answer) {
  return {
    resolution: answer.res,
    rows: answer.cells.map((cell, index) => ({
      h3Cell: cell,
      occurrences: answer.occurrences[index],
      distinctSpecies: answer.distinct_species[index],
    })),
    recordsShown: answer.records_shown,
    hexagonCount: answer.hexagon_count,
    yearsCounted: answer.years_counted ?? null,
    recordsNoYearLeftOut: answer.records_no_year_left_out ?? 0,
  }
}

// A map fetched from the API, one resolution and year range at a time.
//
// group names what is being mapped (a species key, or "overview"), or is
// null for nothing. Each resolution and year range is requested only when it
// is needed, and every answer is kept for the rest of the visit, so going
// back to a tier or a year range needs no new request. Answers are filed
// under the group, years and resolution they were asked for, and the map
// only reads the entry it wants, so a slow answer for an earlier species,
// tier or year range can never replace the current one. Changing any of them
// also cancels the request still waiting.
//
// While the wanted map loads, the last map shown for the same group stays
// up (an earlier tier or year range), so the map does not go blank; its own
// resolution and years travel with it, so the legend describes what is
// really on screen. fetchAt(resolution, years, signal) asks the API and
// toData turns its answer into map data. Returns:
//   data        the map to draw, or null
//   loading     true while the wanted map has not arrived
//   error       the ApiError if the wanted map failed, or null
export function useHexesFromApi(group, years, resolution, fetchAt, toData) {
  const cache = useRef(new Map())
  const lastShown = useRef(null)
  const [errors, setErrors] = useState(() => new Map())
  // Bumped when an answer arrives, so the map is drawn again.
  const [, setArrivals] = useState(0)
  const id = group === null ? null : `${group}|${yearsKey(years)}|${resolution}`

  // Requests the wanted map if it is not here yet. Cleaning up (a new
  // group, tier or year range) cancels the request.
  useEffect(() => {
    if (id === null || cache.current.has(id)) {
      return undefined
    }

    const controller = new AbortController()
    fetchAt(resolution, years, controller.signal)
      .then((answer) => {
        cache.current.set(id, toData(answer))
        setErrors((previous) => {
          const next = new Map(previous)
          next.delete(id)
          return next
        })
        setArrivals((count) => count + 1)
      })
      .catch((error) => {
        if (error.name === 'AbortError') {
          return
        }
        console.error(error)
        setErrors((previous) => new Map(previous).set(id, error))
      })
    return () => controller.abort()
  }, [id, resolution, years, fetchAt, toData])

  if (id === null) {
    return { data: null, loading: false, error: null }
  }

  const wanted = cache.current.get(id)
  if (wanted) {
    lastShown.current = { group, data: wanted }
    return { data: wanted, loading: false, error: null }
  }
  const error = errors.get(id) ?? null
  const data = lastShown.current?.group === group ? lastShown.current.data : null
  return { data, loading: !error, error }
}

// Asks the API for one species map (see useHexesFromApi).
function fetchSpeciesAt(speciesKey) {
  return (resolution, years, signal) => fetchSpeciesHexes(speciesKey, resolution, { signal, years })
}

// The species fetchers made so far, one per key, so each stays the same
// function between renders and does not restart the request.
const speciesFetchers = new Map()

// The fetcher for one species key, made once.
function speciesFetcher(speciesKey) {
  if (!speciesFetchers.has(speciesKey)) {
    speciesFetchers.set(speciesKey, fetchSpeciesAt(speciesKey))
  }
  return speciesFetchers.get(speciesKey)
}

// Stands in for a fetcher when there is nothing to map; never called.
function fetchNothing() {
  return Promise.resolve(null)
}

// Asks the API for the overview map (see useHexesFromApi).
function fetchOverviewAt(resolution, years, signal) {
  return fetchOverviewHexes(resolution, { signal, years })
}

// The species map for the species, year range and resolution being shown
// (see useHexesFromApi). speciesKey is the key the card was opened with (an
// old merged key works: the API answers with the kept key's map), or null.
// years is null for all years.
export function useSpeciesHexes(speciesKey, years, resolution) {
  return useHexesFromApi(
    speciesKey, years, resolution, speciesKey ? speciesFetcher(speciesKey) : fetchNothing, toMapData)
}

// The overview map for a year range, from the API (see useHexesFromApi).
// years is null when no range is set: the shipped overview files are used
// then and nothing is fetched.
export function useOverviewHexes(years, resolution) {
  return useHexesFromApi(years ? 'overview' : null, years, resolution, fetchOverviewAt, toOverviewData)
}

import { useEffect, useRef, useState } from 'react'

import { fetchSpeciesHexes } from './api'

// The cache key of one species map at one resolution.
function cacheKey(speciesKey, resolution) {
  return `${speciesKey}|${resolution}`
}

// Turns the API's answer (two lists, cells and occurrences) into one row per
// hexagon, the shape the map layers and the hover box read, and keeps the
// summary numbers.
export function toMapData(answer) {
  return {
    specieskey: answer.specieskey,
    resolution: answer.res,
    rows: answer.cells.map((cell, index) => ({ h3Cell: cell, occurrences: answer.occurrences[index] })),
    recordsShown: answer.records_shown,
    hexagonCount: answer.hexagon_count,
  }
}

// The species map for the species and resolution being shown.
//
// Each resolution is requested only when it is needed, and every answer is
// kept for the rest of the visit, so zooming back to a tier, or opening the
// same species again, needs no new request. Answers are filed under the
// species key and resolution they were asked for, and the map only ever
// reads the entry for the species and resolution it is showing, so a slow
// answer for an earlier species or tier can never replace the current one.
// Switching species or tier also cancels the request still waiting.
//
// speciesKey is the key the card was opened with (an old merged key works:
// the API answers with the kept key's map). Returns:
//   data        the map to draw: the wanted resolution if it has arrived,
//               otherwise the nearest resolution already here for this
//               species (so the map does not go blank while a new tier
//               loads), or null
//   loading     true while the wanted resolution has not arrived
//   error       the ApiError if the wanted resolution failed, or null
export function useSpeciesHexes(speciesKey, resolution) {
  const cache = useRef(new Map())
  const [errors, setErrors] = useState(() => new Map())
  // Bumped when an answer arrives, so the map is drawn again.
  const [, setArrivals] = useState(0)

  // Requests the wanted resolution if it is not here yet. Cleaning up (a new
  // species or tier) cancels the request.
  useEffect(() => {
    if (!speciesKey) {
      return undefined
    }
    const id = cacheKey(speciesKey, resolution)
    if (cache.current.has(id)) {
      return undefined
    }

    const controller = new AbortController()
    fetchSpeciesHexes(speciesKey, resolution, { signal: controller.signal })
      .then((answer) => {
        cache.current.set(id, toMapData(answer))
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
  }, [speciesKey, resolution])

  if (!speciesKey) {
    return { data: null, loading: false, error: null }
  }

  const wanted = cache.current.get(cacheKey(speciesKey, resolution))
  const error = wanted ? null : errors.get(cacheKey(speciesKey, resolution)) ?? null
  let data = wanted ?? null
  if (!data) {
    // The nearest resolution already here for this species, if any.
    const nearest = [4, 5, 6, 7]
      .filter((other) => cache.current.has(cacheKey(speciesKey, other)))
      .sort((a, b) => Math.abs(a - resolution) - Math.abs(b - resolution))[0]
    data = nearest ? cache.current.get(cacheKey(speciesKey, nearest)) : null
  }

  return { data, loading: !wanted && !error, error }
}

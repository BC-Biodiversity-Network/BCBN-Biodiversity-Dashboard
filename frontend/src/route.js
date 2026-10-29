import { useCallback, useEffect, useState } from 'react'

// Which page is shown, and the Datasets page's state, kept in the address
// bar's query string so a link can be shared and the back button works.
//
//   (no query)                        the species map, as the site always was
//   ?view=datasets&q=eagle&catalogue=catalogue.data.gov.bc.ca&wider=1&page=2
//       &dataset=<id>                 the Datasets page
//
// catalogue can be given more than once. The query string is used rather
// than paths such as /datasets so the built site works on any static host,
// with no rewrite rules.

// Which page a query string asks for: 'datasets' or 'species'.
export function readView(search) {
  return new URLSearchParams(search).get('view') === 'datasets' ? 'datasets' : 'species'
}

// The Datasets page's state from a query string: { q, catalogues, wider,
// page, dataset }. Missing or odd values fall back to the defaults.
export function readDatasetsState(search) {
  const params = new URLSearchParams(search)
  const page = Number.parseInt(params.get('page') ?? '', 10)
  return {
    q: params.get('q') ?? '',
    catalogues: params.getAll('catalogue'),
    wider: params.get('wider') === '1',
    page: Number.isFinite(page) && page > 1 ? page : 1,
    dataset: params.get('dataset') || null,
  }
}

// The query string for a Datasets page state, leaving out every value that
// is the default, so the plain page is just "?view=datasets".
export function datasetsSearch(state) {
  const params = new URLSearchParams({ view: 'datasets' })
  if (state.q?.trim()) {
    params.set('q', state.q.trim())
  }
  for (const catalogue of state.catalogues ?? []) {
    params.append('catalogue', catalogue)
  }
  if (state.wider) {
    params.set('wider', '1')
  }
  if (state.page > 1) {
    params.set('page', String(state.page))
  }
  if (state.dataset) {
    params.set('dataset', state.dataset)
  }
  return `?${params}`
}

// Keeps track of the address bar's query string. Returns [search, navigate]:
// navigate(newSearch) puts a new entry in the browser history (so back
// returns to the old one), or replaces the current one with { replace: true }.
// The back and forward buttons update search too.
export function useLocationSearch() {
  const [search, setSearch] = useState(() => window.location.search)

  useEffect(() => {
    // Called when the back or forward button changes the address.
    function onPopState() {
      setSearch(window.location.search)
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  const navigate = useCallback((newSearch, { replace = false } = {}) => {
    const url = `${window.location.pathname}${newSearch === '?' ? '' : newSearch}`
    if (replace) {
      window.history.replaceState(null, '', url)
    } else {
      window.history.pushState(null, '', url)
    }
    setSearch(window.location.search)
  }, [])

  return [search, navigate]
}

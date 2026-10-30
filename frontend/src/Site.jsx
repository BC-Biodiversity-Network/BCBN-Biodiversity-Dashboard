import App from './App'
import DatasetsPage from './DatasetsPage'
import { readView, useLocationSearch } from './route'

// The two pages the top bar switches between, with the query string each
// one opens on.
const PAGES = [
  { view: 'species', label: 'Species map', search: '?' },
  { view: 'datasets', label: 'Datasets', search: '?view=datasets' },
]

// The bar along the top of every page: the site name and a link to each
// page, the current one marked. The links are real links (they work when
// opened in a new tab) that switch page without reloading it.
function TopBar({ view, onNavigate }) {
  return (
    <nav className="top-bar" aria-label="Pages">
      <span className="top-bar-name">BC Biodiversity</span>
      {PAGES.map((page) => (
        <a
          key={page.view}
          href={page.search === '?' ? window.location.pathname : page.search}
          className={page.view === view ? 'top-bar-link current' : 'top-bar-link'}
          aria-current={page.view === view ? 'page' : undefined}
          onClick={(event) => {
            if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) {
              return
            }
            event.preventDefault()
            if (page.view !== view) {
              onNavigate(page.search)
            }
          }}
        >
          {page.label}
        </a>
      ))}
    </nav>
  )
}

// The whole site: the top bar, then the page the address asks for. The
// species map page is the App component; it opens fresh each time it is
// chosen, with the species card the address names (if any) open.
export default function Site() {
  const [search, navigate] = useLocationSearch()
  const view = readView(search)

  return (
    <div className="site">
      <TopBar view={view} onNavigate={navigate} />
      {view === 'datasets'
        ? <DatasetsPage search={search} navigate={navigate} />
        : <App search={search} navigate={navigate} />}
    </div>
  )
}

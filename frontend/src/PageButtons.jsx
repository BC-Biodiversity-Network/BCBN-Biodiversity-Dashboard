import { useEffect, useRef, useState } from 'react'

import { pageItems } from './paging'

// The page buttons under a list: on the Datasets page, and under the species
// lists of a rank card and a hexagon's panel, so every list on the site
// pages the same way. (The class names still say "datasets", where they
// began.)

// Below this width (in pixels) the page buttons show no pages on either side
// of the current one, so the row fits on one line: a phone, or a narrow
// window, where the list is narrow too.
const WIDE_PAGING_WIDTH = 420

// The width of an element, in pixels, kept up to date as it changes; null
// until it is known (and always in a browser without ResizeObserver).
function useWidth(ref) {
  const [width, setWidth] = useState(null)
  useEffect(() => {
    const element = ref.current
    if (!element || typeof ResizeObserver === 'undefined') {
      return undefined
    }
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(element)
    return () => observer.disconnect()
  }, [ref])
  return width
}

// The page buttons: « Previous, the page numbers with "..." for the pages
// left out (see pageItems), Next ». The current page is highlighted and
// marked as the current one for screen readers. onPage(page) goes to a page.
function PageButtons({ page, pages, onPage }) {
  const ref = useRef(null)
  const width = useWidth(ref)
  const around = width !== null && width < WIDE_PAGING_WIDTH ? 0 : 1
  return (
    <nav className="datasets-paging" aria-label="Pages of results" ref={ref}>
      <button type="button" className="datasets-page-step" disabled={page <= 1} onClick={() => onPage(page - 1)}
        aria-label="Previous page">
        « Previous
      </button>
      {pageItems(page, pages, around).map((item, i) => (item === 'gap'
        ? <span key={`gap-${i}`} className="datasets-page-gap" aria-hidden="true">...</span>
        : (
          <button
            key={item}
            type="button"
            className={item === page ? 'datasets-page-number current' : 'datasets-page-number'}
            aria-current={item === page ? 'page' : undefined}
            aria-label={`Page ${item}`}
            onClick={() => item !== page && onPage(item)}
          >
            {item}
          </button>
        )))}
      <button type="button" className="datasets-page-step" disabled={page >= pages} onClick={() => onPage(page + 1)}
        aria-label="Next page">
        Next »
      </button>
    </nav>
  )
}

// The page buttons, or nothing when there is only one page.
export default function Paging({ page, pages, onPage }) {
  return pages > 1 ? <PageButtons page={page} pages={pages} onPage={onPage} /> : null
}

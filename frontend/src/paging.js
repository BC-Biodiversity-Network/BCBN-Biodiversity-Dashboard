// Which page buttons the Datasets list shows: always the first and last
// page, the current page and `around` pages on each side of it, with a gap
// ('gap', shown as "...") for the pages left out. The number of items stays
// the same on every page (2 * around + 5, or all the pages when there are no
// more than that), so the row keeps its width and Next does not move under
// the pointer: near either end, the pages next to it fill the places the gap
// and the pages beyond the end would take.
//
//   pageItems(10, 64)    [1, 'gap', 9, 10, 11, 'gap', 64]
//   pageItems(1, 64)     [1, 2, 3, 4, 5, 'gap', 64]
//   pageItems(64, 64)    [1, 'gap', 60, 61, 62, 63, 64]
//   pageItems(10, 64, 0) [1, 'gap', 10, 'gap', 64]
export function pageItems(page, pages, around = 1) {
  const slots = 2 * around + 5
  if (pages <= slots) {
    return pagesFrom(1, pages)
  }
  // Near the start: the first slots - 2 pages, a gap, the last page.
  if (page <= around + 3) {
    return [...pagesFrom(1, slots - 2), 'gap', pages]
  }
  // Near the end: the first page, a gap, the last slots - 2 pages.
  if (page >= pages - around - 2) {
    return [1, 'gap', ...pagesFrom(pages - slots + 3, pages)]
  }
  return [1, 'gap', ...pagesFrom(page - around, page + around), 'gap', pages]
}

// The page numbers from first to last, both included.
function pagesFrom(first, last) {
  return Array.from({ length: last - first + 1 }, (_, i) => first + i)
}

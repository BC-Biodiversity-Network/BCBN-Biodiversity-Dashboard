import { describe, expect, it } from 'vitest'

import { pageItems } from './paging'

describe('pageItems', () => {
  it.each([
    [10, 64, [1, 'gap', 9, 10, 11, 'gap', 64]],
    [1, 64, [1, 2, 3, 4, 5, 'gap', 64]],
    [4, 64, [1, 2, 3, 4, 5, 'gap', 64]],
    [5, 64, [1, 'gap', 4, 5, 6, 'gap', 64]],
    [60, 64, [1, 'gap', 59, 60, 61, 'gap', 64]],
    [61, 64, [1, 'gap', 60, 61, 62, 63, 64]],
    [64, 64, [1, 'gap', 60, 61, 62, 63, 64]],
    [3, 7, [1, 2, 3, 4, 5, 6, 7]],
    [1, 2, [1, 2]],
    [1, 1, [1]],
  ])('on page %i of %i, with one page each side: %j', (page, pages, items) => {
    expect(pageItems(page, pages)).toEqual(items)
  })

  it.each([
    [10, 64, [1, 'gap', 10, 'gap', 64]],
    [1, 64, [1, 2, 3, 'gap', 64]],
    [4, 64, [1, 'gap', 4, 'gap', 64]],
    [64, 64, [1, 'gap', 62, 63, 64]],
    [2, 5, [1, 2, 3, 4, 5]],
  ])('on page %i of %i, with no page each side (a phone): %j', (page, pages, items) => {
    expect(pageItems(page, pages, 0)).toEqual(items)
  })

  it('always shows the first, last and current page and keeps the same number of items', () => {
    for (const around of [0, 1]) {
      for (let page = 1; page <= 40; page += 1) {
        const items = pageItems(page, 40, around)
        expect(items).toHaveLength(2 * around + 5)
        expect(items).toContain(1)
        expect(items).toContain(40)
        expect(items).toContain(page)
        for (let step = 1; step <= around; step += 1) {
          expect(items).toEqual(expect.arrayContaining([page - step, page + step].filter((p) => p >= 1 && p <= 40)))
        }
      }
    }
  })
})

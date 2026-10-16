import { useEffect, useId, useState } from 'react'

import { incompleteYearsNote, readYearBoxes, sameYears } from './yearRange'

// Writes a range's two ends into the two boxes' text.
function boxTexts(years) {
  return [years?.from == null ? '' : String(years.from), years?.to == null ? '' : String(years.to)]
}

// The year control: two boxes to type the first and last year, and an "All
// years" button that clears them. It applies to whichever map is shown.
//
// Typing is the main way in, because the data runs from the 1780s but almost
// all of it is from after 2000: a slider across 240 years would make the
// years people want hard to hit. The boxes are applied when Enter is pressed
// or the control is left, not on every key, so typing "2005" does not ask
// for 2, 20 and 200 on the way, and moving from one box to the other does
// not apply half a range. The up and down arrow keys step one year. An empty
// box is an open end: only a first year means "from then on".
//
// years is the range in use (null for all years); bounds is { firstYear,
// lastYear } from /api/meta, or null while it has not arrived or if the API
// cannot be reached, which turns the control off (the filter needs the API).
// onChange is called with the new range, or null for all years.
export default function YearFilter({ years, bounds, onChange }) {
  const id = useId()
  const [[fromText, toText], setTexts] = useState(() => boxTexts(years))
  const [problem, setProblem] = useState(null)

  // Shows the range in use whenever it changes from outside the boxes.
  useEffect(() => {
    setTexts(boxTexts(years))
    setProblem(null)
  }, [years])

  // Reads both boxes and applies the range, or says what is wrong with it.
  function apply(nextFrom = fromText, nextTo = toText) {
    if (!bounds) {
      return
    }
    const result = readYearBoxes(nextFrom, nextTo, bounds.firstYear, bounds.lastYear)
    if (result.problem) {
      setProblem(result.problem)
      return
    }
    setProblem(null)
    if (!sameYears(result.years, years)) {
      onChange(result.years)
    }
  }

  // Clears both boxes: back to all years.
  function showAllYears() {
    setTexts(['', ''])
    setProblem(null)
    if (years !== null) {
      onChange(null)
    }
  }

  const disabled = !bounds
  return (
    <form
      className="year-filter"
      aria-label="Years shown on the map"
      onSubmit={(event) => event.preventDefault()}
      // Enter is handled here: a form with two boxes and no submit button is
      // not submitted by Enter in a browser.
      onKeyDown={(event) => {
        if (event.key === 'Enter' && event.target.tagName === 'INPUT') {
          event.preventDefault()
          apply()
        }
      }}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) {
          apply()
        }
      }}
    >
      <div className="year-filter-row">
        <label htmlFor={`${id}-from`}>Years</label>
        <input
          id={`${id}-from`}
          className="year-box"
          type="number"
          inputMode="numeric"
          aria-label="First year"
          placeholder={bounds ? String(bounds.firstYear) : ''}
          min={bounds?.firstYear}
          max={bounds?.lastYear}
          value={fromText}
          disabled={disabled}
          onChange={(event) => setTexts([event.target.value, toText])}
        />
        <span aria-hidden="true">to</span>
        <input
          className="year-box"
          type="number"
          inputMode="numeric"
          aria-label="Last year"
          placeholder={bounds ? String(bounds.lastYear) : ''}
          min={bounds?.firstYear}
          max={bounds?.lastYear}
          value={toText}
          disabled={disabled}
          onChange={(event) => setTexts([fromText, event.target.value])}
        />
        <button
          type="button"
          className="year-reset"
          onClick={showAllYears}
          disabled={disabled || (years === null && fromText === '' && toText === '')}
        >
          All years
        </button>
      </div>
      {problem && <p className="year-filter-problem" role="alert">{problem}</p>}
      {disabled && <p className="year-filter-note">The year filter needs the species API.</p>}
      {bounds && <p className="year-filter-note">{incompleteYearsNote(bounds.lastYear)}</p>}
    </form>
  )
}

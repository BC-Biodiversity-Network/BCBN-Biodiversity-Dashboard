import { useState } from 'react'

// The bars' colour: a middle step of the species map's pink-to-purple ramp
// (#821a76, PINK_PURPLE_STEPS in colours.js), so the chart reads as the
// same species as the map; the hovered bar takes the next darker step.
const BAR_COLOUR = '#821a76'
const BAR_HOVER_COLOUR = '#3f0c60'

// The chart's size in its own units (it scales to the panel's width), and
// the room around the plot for the labels.
const WIDTH = 340
const HEIGHT = 96
const PLOT = { left: 36, right: 6, top: 8, bottom: 18 }

// The widest a single bar gets (with only a few years), and the gap left
// between bars once they are wide enough to have one.
const MAX_BAR = 24
const GAP_FROM_WIDTH = 6

// Every year from first to last, each with its records (0 for a year with
// none), from [{ year, records }] in order. span is [first, last], or null
// for the years of the data.
export function fillYears(perYear, span = null) {
  if (perYear.length === 0 && !span) {
    return []
  }
  const [first, last] = span ?? [perYear[0].year, perYear[perYear.length - 1].year]
  const byYear = new Map(perYear.map((item) => [item.year, item.records]))
  const filled = []
  for (let year = first; year <= last; year++) {
    filled.push({ year, records: byYear.get(year) ?? 0 })
  }
  return filled
}

// A sentence for screen readers: the years shown and the busiest year.
export function chartSummary(filled) {
  const busiest = filled.reduce((best, item) => (item.records > best.records ? item : best), filled[0])
  const span = filled.length === 1 ? String(filled[0].year) : `${filled[0].year} to ${filled[filled.length - 1].year}`
  return `Records per year in this hexagon, ${span}. Most in ${busiest.year}: ` +
    `${busiest.records.toLocaleString()} ${busiest.records === 1 ? 'record' : 'records'}.`
}

// A year's records in words: "1995: 12 records".
function yearText(item) {
  return `${item.year}: ${item.records.toLocaleString()} ${item.records === 1 ? 'record' : 'records'}`
}

// A small bar chart of one species' records per year in a hexagon: one thin
// bar per year from the first to the last (or across the year range asked
// for, span), on one axis from 0 to the busiest year. Hovering a year (its
// whole column is the target) shows its records above the chart; the same
// numbers are in a table under it, for screen readers and for reading
// exact values.
export default function YearBars({ perYear, span = null }) {
  const [hovered, setHovered] = useState(null)
  const filled = fillYears(perYear, span)
  if (filled.length === 0) {
    return null
  }
  const most = Math.max(...filled.map((item) => item.records), 1)
  const plotWidth = WIDTH - PLOT.left - PLOT.right
  const plotHeight = HEIGHT - PLOT.top - PLOT.bottom
  const step = plotWidth / filled.length
  const gap = step >= GAP_FROM_WIDTH ? 2 : 0
  const barWidth = Math.min(MAX_BAR, Math.max(step - gap, 0.6))
  const baseline = PLOT.top + plotHeight
  const shown = hovered === null ? null : filled[hovered]

  return (
    <figure className="year-bars">
      <p className="year-bars-readout" aria-live="polite">
        {shown ? yearText(shown) : 'Records per year (hover a year)'}
      </p>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={chartSummary(filled)}
        onMouseLeave={() => setHovered(null)}>
        <line x1={PLOT.left} x2={WIDTH - PLOT.right} y1={PLOT.top} y2={PLOT.top} className="year-bars-grid" />
        <text x={PLOT.left - 4} y={PLOT.top + 4} textAnchor="end" className="year-bars-label">
          {most.toLocaleString()}
        </text>
        <text x={PLOT.left - 4} y={baseline} textAnchor="end" className="year-bars-label">0</text>
        {filled.map((item, index) => {
          const height = (item.records / most) * plotHeight
          const x = PLOT.left + index * step + (step - barWidth) / 2
          return (
            <g key={item.year}>
              {item.records > 0 && (
                <rect x={x} y={baseline - height} width={barWidth} height={height}
                  fill={hovered === index ? BAR_HOVER_COLOUR : BAR_COLOUR} />
              )}
              <rect x={PLOT.left + index * step} y={PLOT.top} width={step} height={plotHeight}
                fill="transparent" onMouseEnter={() => setHovered(index)}>
                <title>{yearText(item)}</title>
              </rect>
            </g>
          )
        })}
        <line x1={PLOT.left} x2={WIDTH - PLOT.right} y1={baseline} y2={baseline} className="year-bars-axis" />
        <text x={PLOT.left} y={HEIGHT - 4} className="year-bars-label">{filled[0].year}</text>
        {filled.length > 1 && (
          <text x={WIDTH - PLOT.right} y={HEIGHT - 4} textAnchor="end" className="year-bars-label">
            {filled[filled.length - 1].year}
          </text>
        )}
      </svg>
      <details className="year-bars-table">
        <summary>Records per year as a table</summary>
        <table>
          <thead><tr><th scope="col">Year</th><th scope="col">Records</th></tr></thead>
          <tbody>
            {perYear.map((item) => (
              <tr key={item.year}><td>{item.year}</td><td>{item.records.toLocaleString()}</td></tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  )
}

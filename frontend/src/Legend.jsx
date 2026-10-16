import { binColours, legendEdgeLabels } from './colours'

// The colour key, bottom left: a caption saying what is shown, a strip of the
// colours with the number each one starts at underneath (the last with a "+",
// as it has no upper limit), an optional line of status or summary under it,
// and the note that the maps show recording effort.
//
// Each swatch is drawn exactly as its bin is drawn on the map, with the same
// opacity, over the basemap's land colour (set on .legend-swatches in
// index.css), so a swatch looks like a hexagon of that bin on bare land.
//
// scheme is one of COLOUR_SCHEMES; caption can hold markup (the species name
// in italics); summary and status are short lines of text, or null.
export default function Legend({ scheme, caption, summary = null, status = null, statusIsError = false }) {
  return (
    <div className="legend">
      <div className="legend-caption">{caption}</div>

      <div className="legend-strip legend-swatches">
        {binColours(scheme).map(([red, green, blue, alpha], index) => (
          <div
            key={index}
            className="legend-swatch"
            style={{ background: `rgba(${red}, ${green}, ${blue}, ${(alpha / 255).toFixed(3)})` }}
          />
        ))}
      </div>

      <div className="legend-strip">
        {legendEdgeLabels(scheme.binEdges).map((label) => (
          <div key={label} className="legend-edge">
            {label}
          </div>
        ))}
      </div>

      {summary && <div className="legend-summary">{summary}</div>}
      {status && (
        <div className={statusIsError ? 'legend-status error' : 'legend-status'} role="status">
          {status}
        </div>
      )}

      <div className="legend-note">
        These maps show where people have recorded wildlife, not where wildlife
        is. Records cluster where people go.
      </div>
    </div>
  )
}

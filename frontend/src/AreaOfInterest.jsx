import { useState } from 'react'

import { PASTE_EXAMPLE, PASTE_HELP } from './areaPaste'
import { AREA_SIZES, sizeKm, sizeLabel } from './areaSize'

// The "Create an area of interest" button and its small panel, with the
// map's other controls at the top left (as in Lucia's approved mockup,
// Results/Claude outputs/area_of_interest_mockup.html). The panel lists the
// area's shapes, each with an x that removes it, and has tabs for the ways
// to add a shape: "Draw" with "Box" ("Polygon" comes later) and "Paste"
// (WKT or GeoJSON, see areaPaste.js), then "Back" (removes the last shape)
// and "Clear area" (removes them all). Every shape added joins the same area.

// What the button says on a single species' map, where an area is not
// available yet.
export const DRAW_UNAVAILABLE = "Creating an area of interest is not available on a single species' map yet."

// The hint shown while a box is being drawn.
export const DRAW_HINT = 'Press and drag on the map to draw a box. Esc to cancel.'

// The line under the tabs when nothing is being drawn.
export const BOX_HELP = 'Box: press and drag on the map. Each shape you add joins the same area.'

// What the "Hexagon size" choice says for Auto: the size it chose, "Auto
// (about 7 km)", or "Auto" alone before there is a size.
export function autoLabel(res) {
  return res ? `Auto (about ${sizeKm(res)} km)` : 'Auto'
}

// The line at the top of the panel: "No shapes yet", "1 shape" or "3 shapes".
export function shapeCountText(count) {
  if (count === 0) {
    return 'No shapes yet'
  }
  return count === 1 ? '1 shape' : `${count.toLocaleString('en-US')} shapes`
}

// How the panel's list names a shape: "Shape 1: box", "Shape 2: polygon
// (6 points)".
export function shapeLabel(shape, index) {
  return `Shape ${index + 1}: ${shape.isBox ? 'box' : `polygon (${shape.ring.length.toLocaleString('en-US')} points)`}`
}

// The button and its panel. open says whether the panel is open, and
// onOpenChange(open) opens or closes it (closing it also stops drawing,
// which the map does). shapes is the area's shapes (see areaShapes.js; empty
// for no area). drawing is true while a box is being drawn, and onDrawBox
// starts or cancels it. size is the area's hexagon size, { res, picked }
// (res the size used, picked whether the visitor picked it rather than
// Auto), or null for no area; onSizeChange(res) picks a size, or Auto with
// null. onPaste(text) adds the shapes of a pasted text and
// returns null, or returns why nothing was added (shown under the text box,
// which keeps the text so it can be fixed). onRemoveShape(index), onBack
// and onClear change the area. unavailable greys the button out (a single species' map). message
// (or null) is what was wrong with the last shape drawn, with an x that
// calls onDismissMessage. onShow (or null) is the "Show area" button beside
// the main one, which opens the area panel again when it was closed.
export default function AreaOfInterest({
  open, onOpenChange, shapes, drawing, onDrawBox, onPaste = () => null, onRemoveShape, onBack, onClear, unavailable,
  message, onDismissMessage, onShow = null, size = null, onSizeChange = () => {},
}) {
  const hasShapes = shapes.length > 0
  // The tab shown ("draw" or "paste"), the text in the Paste tab's box, and
  // why the last paste was not added (or null).
  const [tab, setTab] = useState('draw')
  const [pasted, setPasted] = useState('')
  const [pasteProblem, setPasteProblem] = useState(null)

  // Shows a tab; leaving Draw while a box is being drawn stops drawing.
  function showTab(name) {
    if (name !== 'draw' && drawing) {
      onDrawBox()
    }
    setTab(name)
  }

  // Adds the pasted shapes ("Add"): the text box empties when they are
  // added, and keeps the text with the reason when they are not.
  function addPasted() {
    const problem = onPaste(pasted)
    setPasteProblem(problem)
    if (!problem) {
      setPasted('')
    }
  }
  return (
    <div className="draw-box">
      <div className="draw-box-buttons">
        <span className="draw-box-wrap" title={unavailable ? DRAW_UNAVAILABLE : undefined}>
          <button type="button" className={open && !unavailable ? 'draw-box-button drawing' : 'draw-box-button'}
            disabled={unavailable} aria-expanded={open && !unavailable} aria-controls="area-of-interest"
            onClick={() => onOpenChange(!open)}
            aria-describedby={unavailable ? 'draw-box-unavailable' : undefined}>
            Create an area of interest
          </button>
        </span>
        {onShow && !drawing && (
          <button type="button" className="draw-box-button" onClick={onShow}>Show area</button>
        )}
      </div>
      {unavailable && <span id="draw-box-unavailable" className="visually-hidden">{DRAW_UNAVAILABLE}</span>}
      {open && !unavailable && (
        <section id="area-of-interest" className="aoi-panel" aria-label="Area of interest">
          <div className="aoi-head">
            <strong>{shapeCountText(shapes.length)}</strong>
            <button type="button" className="aoi-close" onClick={() => onOpenChange(false)}>Close</button>
          </div>
          {hasShapes && (
            <ul className="aoi-shapes" aria-label="Shapes in this area">
              {shapes.map((shape, index) => (
                // The shapes have no id of their own; their place in the list is
                // what the x removes.
                <li key={index}>
                  <span>{shapeLabel(shape, index)}</span>
                  <button type="button" className="aoi-remove" onClick={() => onRemoveShape(index)}
                    aria-label={`Remove shape ${index + 1}`} title="Remove this shape">
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
          {hasShapes && size && (
            <label className="aoi-size">
              <span>Hexagon size</span>
              <select value={size.picked ? String(size.res) : 'auto'}
                onChange={(event) => onSizeChange(event.target.value === 'auto' ? null : Number(event.target.value))}>
                <option value="auto">{autoLabel(size.picked ? null : size.res)}</option>
                {AREA_SIZES.slice().reverse().map((res) => (
                  <option key={res} value={String(res)}>{sizeLabel(res)}</option>
                ))}
              </select>
            </label>
          )}
          <div className="aoi-tabs" role="tablist" aria-label="Ways to add a shape">
            {[['draw', 'Draw'], ['paste', 'Paste']].map(([name, label]) => (
              <button key={name} type="button" role="tab" aria-selected={tab === name}
                className={tab === name ? 'aoi-tab on' : 'aoi-tab'} id={`aoi-tab-${name}`}
                aria-controls={`aoi-pane-${name}`} onClick={() => showTab(name)}>
                {label}
              </button>
            ))}
          </div>
          {tab === 'draw' && (
            <div className="aoi-pane" role="tabpanel" id="aoi-pane-draw" aria-labelledby="aoi-tab-draw">
              <div className="aoi-row">
                <button type="button" className={drawing ? 'aoi-button on' : 'aoi-button'} aria-pressed={drawing}
                  onClick={onDrawBox}>
                  Box
                </button>
              </div>
              <p className="aoi-small" role={drawing ? 'status' : undefined}>{drawing ? DRAW_HINT : BOX_HELP}</p>
            </div>
          )}
          {tab === 'paste' && (
            <div className="aoi-pane" role="tabpanel" id="aoi-pane-paste" aria-labelledby="aoi-tab-paste">
              <textarea className="aoi-paste" value={pasted} spellCheck={false}
                aria-label="Shapes to paste, as WKT or GeoJSON" placeholder="WKT or GeoJSON, in longitude and latitude"
                onChange={(event) => setPasted(event.target.value)} />
              <div className="aoi-row aoi-paste-row">
                <button type="button" className="aoi-button aoi-add" onClick={addPasted}>Add</button>
                <button type="button" className="aoi-link" onClick={() => {
                  setPasted(PASTE_EXAMPLE)
                  setPasteProblem(null)
                }}>
                  Fill in an example
                </button>
              </div>
              {pasteProblem && <p className="aoi-error" role="alert">{pasteProblem}</p>}
              <p className="aoi-small">{PASTE_HELP}</p>
            </div>
          )}
          {message && !drawing && (
            <p className="draw-box-message aoi-message" role="alert">
              <span>{message}</span>
              <button type="button" className="draw-box-message-close" onClick={onDismissMessage}
                aria-label="Dismiss this message">
                ×
              </button>
            </p>
          )}
          <div className="aoi-row">
            <button type="button" className="aoi-button" disabled={!hasShapes} onClick={onBack}>Back</button>
            <button type="button" className="aoi-button" disabled={!hasShapes} onClick={onClear}>Clear area</button>
          </div>
        </section>
      )}
    </div>
  )
}

import { useState } from 'react'

import { PASTE_EXAMPLE, PASTE_HELP } from './areaPaste'
import { POLYGON_HINT, pointsLeftText } from './areaPolygon'
import { AREA_SIZES, sizeKm, sizeLabel } from './areaSize'

// The "Create an area of interest" button and its small panel, with the
// map's other controls at the top left (as in Lucia's approved mockup,
// Results/Claude outputs/area_of_interest_mockup.html). The panel lists the
// area's shapes (one shape on its own, several each with an x that
// removes it), and has tabs for the ways
// to make the area: "Draw" with "Box" and "Polygon" (see areaPolygon.js) and
// "Paste" (WKT or GeoJSON, see areaPaste.js), then "Previous area" (back to the
// area before the last change) and "Clear area" (removes it all). The area is one
// shape at a time (Evan, 2026-11-18): a new box, polygon or paste replaces
// the current area; one paste may still give several polygons at once.

// What the button says on a single species' map, where an area is not
// available yet.
export const DRAW_UNAVAILABLE = "Creating an area of interest is not available on a single species' map yet."

// The tooltip of "Previous area" while it is greyed out: going back would
// bring back no earlier area that is not empty.
export const NO_PREVIOUS_AREA = 'No previous area'

// The hint shown while a box is being drawn.
export const DRAW_HINT = 'Press and drag on the map to draw a box. Esc to cancel.'

// The line under the tabs when nothing is being drawn (as in the mockup).
export const DRAW_HELP = 'Box: press and drag on the map. Polygon: click to add points, then click the first point '
  + 'or press Finish. A new shape replaces the current area.'

/**
 * The hint while a polygon is being drawn: POLYGON_HINT, and how many
 * points are left when few are (left, see pointsLeftText).
 */
export function polygonHint(left) {
  const note = pointsLeftText(left)
  return note ? `${POLYGON_HINT} ${note}` : POLYGON_HINT
}

// What the "Hexagon size" choice says for Auto: the size it chose, "Auto
// (about 7 km)", or "Auto" alone before there is a size.
export function autoLabel(res) {
  return res ? `Auto (about ${sizeKm(res)} km)` : 'Auto'
}

/**
 * The heading at the top of the panel, from how many shapes the area has:
 * "No area yet", "Your area" for one shape (the usual case, as a new shape
 * replaces the area), or "Your area: 3 shapes" for several (a pasted
 * MULTIPOLYGON or FeatureCollection, or an old link).
 */
export function shapeCountText(count) {
  if (count === 0) {
    return 'No area yet'
  }
  return count === 1 ? 'Your area' : `Your area: ${count.toLocaleString('en-US')} shapes`
}

/**
 * What a shape is, on its own: "Box", or "Polygon (6 points)". The panel
 * shows it alone when the area has only this shape.
 */
export function shapeKind(shape) {
  return shape.isBox ? 'Box' : `Polygon (${shape.ring.length.toLocaleString('en-US')} points)`
}

/**
 * How the panel's list names a shape of an area of several (its place in
 * the list, index, from 0): "Shape 1: box", "Shape 2: polygon (6 points)".
 */
export function shapeLabel(shape, index) {
  return `Shape ${index + 1}: ${shape.isBox ? 'box' : `polygon (${shape.ring.length.toLocaleString('en-US')} points)`}`
}

// The button and its panel. open says whether the panel is open, and
// onOpenChange(open) opens or closes it (closing it also stops drawing,
// which the map does). shapes is the area's shapes (see areaShapes.js; empty
// for no area). drawing is true while a box is being drawn, and onDrawBox
// starts or cancels it. polygon is the polygon being drawn (useAreaPolygon
// in areaPolygon.js: active, message, left, start, finish, undo, cancel);
// starting a box, a polygon, another tab or Clear area cancels it. size is the area's hexagon size, { res, picked }
// (res the size used, picked whether the visitor picked it rather than
// Auto), or null for no area; onSizeChange(res) picks a size, or Auto with
// null. onPaste(text) adds the shapes of a pasted text and
// returns null, or returns why nothing was added (shown under the text box,
// which keeps the text so it can be fixed). onRemoveShape(index), onBack
// (or null, which greys "Previous area" out with the tooltip
// NO_PREVIOUS_AREA: going back would bring no earlier area that is not
// empty) and
// onClear change the area. unavailable greys the button out (a single species' map). message
// (or null) is what was wrong with the last shape drawn, with an x that
// calls onDismissMessage. onShow (or null) is the "Show area" button beside
// the main one, which opens the area panel again when it was closed.
export default function AreaOfInterest({
  open, onOpenChange, shapes, drawing, onDrawBox, onPaste = () => null, onRemoveShape, onBack, onClear, unavailable,
  message, onDismissMessage, onShow = null, size = null, onSizeChange = () => {}, polygon = null,
}) {
  const polygonActive = Boolean(polygon?.active)
  const hasShapes = shapes.length > 0
  // The tab shown ("draw" or "paste"), the text in the Paste tab's box, and
  // why the last paste was not added (or null).
  const [tab, setTab] = useState('draw')
  const [pasted, setPasted] = useState('')
  const [pasteProblem, setPasteProblem] = useState(null)

  // Shows a tab; leaving Draw stops drawing a box or a polygon.
  function showTab(name) {
    if (name !== 'draw') {
      if (drawing) {
        onDrawBox()
      }
      polygon?.cancel()
    }
    setTab(name)
  }

  /** "Box": cancels a polygon being drawn, then starts or cancels a box. */
  function pressBox() {
    polygon?.cancel()
    onDrawBox()
  }

  /** "Polygon": stops a box being drawn, then starts or cancels a polygon. */
  function pressPolygon() {
    if (drawing) {
      onDrawBox()
    }
    if (polygonActive) {
      polygon.cancel()
    } else {
      onDismissMessage()
      polygon.start()
    }
  }

  /** "Clear area": cancels a polygon being drawn too. */
  function clearArea() {
    polygon?.cancel()
    onClear()
  }

  // Uses the pasted shapes ("Use this shape") in place of the area: the text box
  // empties when they are taken, and keeps the text with the reason when
  // they are not.
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
          {shapes.length === 1 && (
            // One shape: only what it is, with no x (Clear area removes it).
            <ul className="aoi-shapes" aria-label="Shapes in this area">
              <li><span>{shapeKind(shapes[0])}</span></li>
            </ul>
          )}
          {shapes.length > 1 && (
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
                  onClick={pressBox}>
                  Box
                </button>
                {polygon && (
                  <button type="button" className={polygonActive ? 'aoi-button on' : 'aoi-button'}
                    aria-pressed={polygonActive} onClick={pressPolygon}>
                    Polygon
                  </button>
                )}
              </div>
              {polygonActive ? (
                <>
                  <p className="aoi-small" role="status">{polygonHint(polygon.left)}</p>
                  {polygon.message && <p className="aoi-error" role="alert">{polygon.message}</p>}
                  {/* For touch screens, which have no keyboard or double-click. */}
                  <div className="aoi-row aoi-polygon-row">
                    <button type="button" className="aoi-button aoi-add" onClick={polygon.finish}>Finish</button>
                    <button type="button" className="aoi-button" onClick={polygon.undo}
                      disabled={polygon.points.length === 0}>
                      Undo point
                    </button>
                    <button type="button" className="aoi-button" onClick={polygon.cancel}>Cancel</button>
                  </div>
                </>
              ) : (
                <p className="aoi-small" role={drawing ? 'status' : undefined}>{drawing ? DRAW_HINT : DRAW_HELP}</p>
              )}
            </div>
          )}
          {tab === 'paste' && (
            <div className="aoi-pane" role="tabpanel" id="aoi-pane-paste" aria-labelledby="aoi-tab-paste">
              <textarea className="aoi-paste" value={pasted} spellCheck={false}
                aria-label="Shapes to paste, as WKT or GeoJSON" placeholder="WKT or GeoJSON, in longitude and latitude"
                onChange={(event) => setPasted(event.target.value)} />
              <div className="aoi-row aoi-paste-row">
                <button type="button" className="aoi-button aoi-add" onClick={addPasted}>
                  Use this shape
                </button>
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
          {message && !drawing && !polygonActive && (
            <p className="draw-box-message aoi-message" role="alert">
              <span>{message}</span>
              <button type="button" className="draw-box-message-close" onClick={onDismissMessage}
                aria-label="Dismiss this message">
                ×
              </button>
            </p>
          )}
          <div className="aoi-row">
            {/* The tooltip sits on a wrapper, as a greyed-out button itself
                gets no mouse events in some browsers. */}
            <span className="draw-box-wrap" title={onBack ? undefined : NO_PREVIOUS_AREA}>
              <button type="button" className="aoi-button" disabled={!onBack} onClick={onBack ?? undefined}
                aria-describedby={onBack ? undefined : 'aoi-no-previous'}>
                Previous area
              </button>
            </span>
            {!onBack && <span id="aoi-no-previous" className="visually-hidden">{NO_PREVIOUS_AREA}</span>}
            <button type="button" className="aoi-button" disabled={!hasShapes} onClick={clearArea}>Clear area</button>
          </div>
        </section>
      )}
    </div>
  )
}

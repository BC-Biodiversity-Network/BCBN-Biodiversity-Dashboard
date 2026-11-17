// The "Create an area of interest" button and its small panel, with the
// map's other controls at the top left (as in Lucia's approved mockup,
// Results/Claude outputs/area_of_interest_mockup.html). The panel lists the
// area's shapes, each with an x that removes it, and has tabs for the ways
// to add a shape: only "Draw" with "Box" for now ("Paste" and "Polygon"
// come later), then "Back" (removes the last shape) and "Clear area"
// (removes them all). Every shape added joins the same area.

// What the button says on a single species' map, where an area is not
// available yet.
export const DRAW_UNAVAILABLE = "Creating an area of interest is not available on a single species' map yet."

// The hint shown while a box is being drawn.
export const DRAW_HINT = 'Press and drag on the map to draw a box. Esc to cancel.'

// The line under the tabs when nothing is being drawn.
export const BOX_HELP = 'Box: press and drag on the map. Each shape you add joins the same area.'

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
// starts or cancels it. onRemoveShape(index), onBack and onClear change the
// area. unavailable greys the button out (a single species' map). message
// (or null) is what was wrong with the last shape drawn, with an x that
// calls onDismissMessage. onShow (or null) is the "Show area" button beside
// the main one, which opens the area panel again when it was closed.
export default function AreaOfInterest({
  open, onOpenChange, shapes, drawing, onDrawBox, onRemoveShape, onBack, onClear, unavailable,
  message, onDismissMessage, onShow = null,
}) {
  const hasShapes = shapes.length > 0
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
          <div className="aoi-tabs" role="tablist" aria-label="Ways to add a shape">
            <button type="button" role="tab" aria-selected="true" className="aoi-tab on" id="aoi-tab-draw"
              aria-controls="aoi-pane-draw">
              Draw
            </button>
          </div>
          <div className="aoi-pane" role="tabpanel" id="aoi-pane-draw" aria-labelledby="aoi-tab-draw">
            <div className="aoi-row">
              <button type="button" className={drawing ? 'aoi-button on' : 'aoi-button'} aria-pressed={drawing}
                onClick={onDrawBox}>
                Box
              </button>
            </div>
            <p className="aoi-small" role={drawing ? 'status' : undefined}>{drawing ? DRAW_HINT : BOX_HELP}</p>
          </div>
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

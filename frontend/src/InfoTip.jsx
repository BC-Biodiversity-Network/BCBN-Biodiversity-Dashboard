import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'

// How far below its button a tip appears, and how wide it may be, in pixels.
const GAP = 6
const MAX_WIDTH = 280

// Whether the button was reached with the keyboard (Tab) rather than clicked
// or tapped, so a tip opens for keyboard users without opening on every click.
function reachedByKeyboard(element) {
  try {
    return element.matches(':focus-visible')
  } catch {
    // A browser that does not know :focus-visible: open on any focus.
    return true
  }
}

// A small button with a short explanation that opens next to it: while the
// mouse is over it, when it is reached with the keyboard, and on a tap or
// click (which keeps it open until the next tap, a tap elsewhere, or
// Escape). The explanation is only on the page while it is open.
//
// label is what the button shows (a status chip, or "?"), ariaLabel names the
// button for screen readers, and children is the explanation.
export default function InfoTip({ label, ariaLabel, className, children }) {
  const tipId = useId()
  const wrapperRef = useRef(null)
  const buttonRef = useRef(null)
  const [hovered, setHovered] = useState(false)
  const [keyboardFocus, setKeyboardFocus] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [position, setPosition] = useState(null)
  const open = hovered || keyboardFocus || pinned

  // Places the tip just under its button, kept inside the window. It is
  // placed on the page itself (position: fixed), so a scrolling card never
  // cuts it off.
  useLayoutEffect(() => {
    if (!open || !buttonRef.current) {
      return
    }
    const box = buttonRef.current.getBoundingClientRect()
    // A short tip ("Class") stays as narrow as its text; a long one wraps at
    // maxWidth. The left edge is kept far enough in for the widest tip.
    const maxWidth = Math.min(MAX_WIDTH, window.innerWidth - 16)
    setPosition({
      top: box.bottom + GAP,
      left: Math.max(8, Math.min(box.left, window.innerWidth - maxWidth - 8)),
      maxWidth,
    })
  }, [open])

  // Closes the tip when the page or the card scrolls (it would be left in
  // the wrong place), and a tapped-open tip when something else is tapped.
  useEffect(() => {
    if (!open) {
      return undefined
    }
    function closeAll() {
      setHovered(false)
      setKeyboardFocus(false)
      setPinned(false)
    }
    function closeIfOutside(event) {
      if (!wrapperRef.current?.contains(event.target)) {
        closeAll()
      }
    }
    window.addEventListener('scroll', closeAll, true)
    document.addEventListener('pointerdown', closeIfOutside)
    return () => {
      window.removeEventListener('scroll', closeAll, true)
      document.removeEventListener('pointerdown', closeIfOutside)
    }
  }, [open])

  return (
    <span
      className="info-tip"
      ref={wrapperRef}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        type="button"
        ref={buttonRef}
        className={className}
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-describedby={open ? tipId : undefined}
        onFocus={(event) => setKeyboardFocus(reachedByKeyboard(event.currentTarget))}
        onBlur={() => {
          setKeyboardFocus(false)
          setPinned(false)
        }}
        onClick={() => setPinned((wasPinned) => !wasPinned)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            setHovered(false)
            setKeyboardFocus(false)
            setPinned(false)
          }
        }}
      >
        {label}
      </button>
      {open && (
        <span
          role="tooltip"
          id={tipId}
          className="info-tip-text"
          style={position ?? undefined}
        >
          {children}
        </span>
      )}
    </span>
  )
}

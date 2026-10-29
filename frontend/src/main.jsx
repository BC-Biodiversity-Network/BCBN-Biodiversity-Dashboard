import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import Site from './Site.jsx'
import './index.css'

// Starting point of the app: put the Site component (the top bar and the
// page the address asks for) inside the empty <div id="root"> that
// index.html provides.
createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Site />
  </StrictMode>,
)

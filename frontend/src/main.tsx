import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import AdminConsole from './components/AdminConsole.tsx'
import './components/AdminConsole.css'

// One deployed artifact serves both surfaces, so the split is a path check
// rather than a router. A router would be the right call the moment the
// console needs deep links; today it would be a dependency bought for one
// branch.
const isOperatorConsole = window.location.pathname.startsWith('/admin')

createRoot(document.getElementById('root')!).render(
  <StrictMode>{isOperatorConsole ? <AdminConsole /> : <App />}</StrictMode>,
)

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { TooltipProvider } from '@/components/ui/tooltip'
import App from './App'
import './index.css'

// Follow the system theme: the shadcn tokens switch on the .dark class.
const darkQuery = window.matchMedia('(prefers-color-scheme: dark)')
const applyTheme = () => document.documentElement.classList.toggle('dark', darkQuery.matches)
applyTheme()
darkQuery.addEventListener('change', applyTheme)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <TooltipProvider delayDuration={200}>
      <App />
    </TooltipProvider>
  </StrictMode>,
)

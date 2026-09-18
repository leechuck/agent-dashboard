import { mount } from 'svelte'
import './app.css'
import App from './App.svelte'
import { applyTheme, readTheme } from './lib/theme'

applyTheme(readTheme(), false)
window.addEventListener('storage', (event) => {
  if (event.key === 'theme' || event.key === null) applyTheme(readTheme(), false)
})

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {})
}

// `?token=…` signs in once (used by the phone-width QA harness), then is stripped from the URL.
const qs = new URLSearchParams(location.search)
if (qs.get('token')) {
  try {
    await fetch('/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ token: qs.get('token') }) })
  } finally {
    history.replaceState(null, '', location.pathname + location.hash)
  }
}

const app = mount(App, { target: document.getElementById('app')! })
export default app

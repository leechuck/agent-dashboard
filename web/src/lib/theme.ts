export const themes = [
  { id: 'terminator', label: 'Terminator', description: 'Green on black' },
  { id: 'light', label: 'Light', description: 'Light background' },
  { id: 'dark', label: 'Dark', description: 'Dark background' },
  { id: 'auto', label: 'System', description: 'Follow this device' },
] as const

export function readTheme(): string {
  try {
    const saved = localStorage.getItem('theme')
    if (themes.some(t => t.id === saved)) return saved!
  } catch { /* Storage may be unavailable in a private browser. */ }
  return 'terminator'
}

export function applyTheme(value: string, persist = true) {
  if (!themes.some(t => t.id === value)) return
  if (value === 'auto') delete document.documentElement.dataset.theme
  else document.documentElement.dataset.theme = value
  if (persist) {
    try { localStorage.setItem('theme', value) } catch { /* Keep this tab usable. */ }
  }
}

/** xterm uses canvas colours, so pass it the same tokens as the surrounding UI. */
export function terminalAppearance() {
  const style = getComputedStyle(document.documentElement)
  const color = (name: string) => style.getPropertyValue(name).trim()
  return {
    fontFamily: color('--mono'),
    theme: {
      background: color('--terminal-bg'), foreground: color('--terminal-fg'),
      cursor: color('--terminal-fg'), cursorAccent: color('--terminal-bg'),
      selectionBackground: color('--terminal-selection'),
    },
  }
}

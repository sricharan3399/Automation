import { createTheme } from '@mui/material/styles'

/**
 * Internal-engineering-tool styling: dense, fast, high-contrast status colours,
 * no decorative animation. Dark by default because these workstations sit next
 * to video review screens.
 */
export const theme = createTheme({
  palette: {
    mode: 'dark',
    background: { default: '#0f1419', paper: '#161b22' },
    primary: { main: '#4299e1' },
    secondary: { main: '#38b2ac' },
    success: { main: '#48bb78' },
    warning: { main: '#f6ad55' },
    error: { main: '#fc8181' },
    info: { main: '#63b3ed' },
    divider: '#2d3748',
    text: { primary: '#e2e8f0', secondary: '#a0aec0' },
  },
  typography: {
    fontFamily:
      "'Segoe UI', system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif",
    fontSize: 13,
    h4: { fontSize: '1.35rem', fontWeight: 600 },
    h5: { fontSize: '1.1rem', fontWeight: 600 },
    h6: { fontSize: '0.95rem', fontWeight: 600 },
    body2: { fontSize: '0.8125rem' },
    caption: { fontSize: '0.72rem' },
  },
  shape: { borderRadius: 6 },
  transitions: { create: () => 'none' },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        'html, body, #root': { height: '100%' },
        '::-webkit-scrollbar': { width: 10, height: 10 },
        '::-webkit-scrollbar-thumb': { background: '#2d3748', borderRadius: 5 },
        code: { fontFamily: "'Cascadia Mono', 'Consolas', monospace", fontSize: '0.78rem' },
      },
    },
    MuiPaper: { defaultProps: { elevation: 0 }, styleOverrides: { root: { border: '1px solid #2d3748' } } },
    MuiButton: { defaultProps: { size: 'small', disableElevation: true } },
    MuiTextField: { defaultProps: { size: 'small' } },
    MuiSelect: { defaultProps: { size: 'small' } },
    MuiChip: { defaultProps: { size: 'small' } },
    MuiTable: { defaultProps: { size: 'small' } },
    MuiTableCell: { styleOverrides: { root: { borderColor: '#2d3748', paddingTop: 5, paddingBottom: 5 } } },
    MuiTooltip: { defaultProps: { arrow: true } },
  },
})

export const STATUS_COLOURS: Record<string, string> = {
  CANDIDATE: '#a0aec0',
  AUTO_PREPARED: '#63b3ed',
  REVIEW_REQUIRED: '#f6ad55',
  CONFIRMED_BY_TESTER: '#48bb78',
  REJECTED_BY_TESTER: '#a0aec0',
  BLOCKED_DATA_ERROR: '#fc8181',
  SENIOR_REVIEW_REQUIRED: '#d69e2e',
  CONNECTED: '#48bb78',
  CONFIGURED: '#48bb78',
  DISCONNECTED: '#a0aec0',
  NOT_CONFIGURED: '#a0aec0',
  AUTH_FAILED: '#fc8181',
  DEMO_ONLY: '#d69e2e',
  ERROR: '#fc8181',
  COMPLETED: '#48bb78',
  RUNNING: '#4299e1',
  VALIDATING: '#4299e1',
  PAUSED: '#f6ad55',
  CANCELLED: '#a0aec0',
  FAILED: '#fc8181',
  PENDING: '#a0aec0',
  BLOCKING: '#fc8181',
  WARNING: '#f6ad55',
  INFO: '#63b3ed',
}

export function statusColour(value?: string | null): string {
  return (value && STATUS_COLOURS[value]) || '#a0aec0'
}

export function confidenceColour(value?: number | null): string {
  if (value === null || value === undefined) return '#a0aec0'
  if (value >= 0.95) return '#48bb78'
  if (value >= 0.8) return '#63b3ed'
  if (value >= 0.5) return '#f6ad55'
  return '#fc8181'
}

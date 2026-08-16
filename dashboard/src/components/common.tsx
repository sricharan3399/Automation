import {
  Alert,
  AlertTitle,
  Box,
  Chip,
  CircularProgress,
  LinearProgress,
  Paper,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import type { ReactNode } from 'react'

import { confidenceColour, statusColour } from '@/theme'

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
}) {
  return (
    <Stack
      direction="row"
      justifyContent="space-between"
      alignItems="flex-start"
      sx={{ mb: 2, gap: 2, flexWrap: 'wrap' }}
    >
      <Box>
        <Typography variant="h4">{title}</Typography>
        {subtitle && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, maxWidth: 900 }}>
            {subtitle}
          </Typography>
        )}
      </Box>
      {actions && (
        <Stack direction="row" spacing={1} flexWrap="wrap">
          {actions}
        </Stack>
      )}
    </Stack>
  )
}

export function SectionCard({
  title,
  subtitle,
  actions,
  children,
  dense,
}: {
  title?: string
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
  dense?: boolean
}) {
  return (
    <Paper sx={{ p: dense ? 1.5 : 2, height: '100%' }}>
      {(title || actions) && (
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1.5 }}>
          <Box>
            {title && <Typography variant="h6">{title}</Typography>}
            {subtitle && (
              <Typography variant="caption" color="text.secondary" component="div">
                {subtitle}
              </Typography>
            )}
          </Box>
          {actions && (
            <Stack direction="row" spacing={1}>
              {actions}
            </Stack>
          )}
        </Stack>
      )}
      {children}
    </Paper>
  )
}

export function StatusChip({ value, label }: { value?: string | null; label?: string }) {
  const colour = statusColour(value)
  return (
    <Chip
      label={label ?? value ?? 'UNKNOWN'}
      sx={{
        bgcolor: `${colour}22`,
        color: colour,
        border: `1px solid ${colour}66`,
        fontWeight: 600,
        fontSize: '0.7rem',
        letterSpacing: 0.3,
      }}
    />
  )
}

export function ConfidenceBadge({
  value,
  band,
  explanation,
}: {
  value?: number | null
  band?: string
  explanation?: string
}) {
  if (value === null || value === undefined) {
    return <Typography variant="caption" color="text.secondary">n/a</Typography>
  }
  const colour = confidenceColour(value)
  const chip = (
    <Chip
      label={`${(value * 100).toFixed(0)}%${band ? ` · ${band}` : ''}`}
      sx={{ bgcolor: `${colour}22`, color: colour, border: `1px solid ${colour}66`, fontWeight: 600 }}
    />
  )
  if (!explanation) return chip
  return (
    <Tooltip
      title={<Box sx={{ whiteSpace: 'pre-line', fontSize: '0.75rem' }}>{explanation}</Box>}
      placement="left"
    >
      <span>{chip}</span>
    </Tooltip>
  )
}

export function LoadingBlock({ label = 'Loading…' }: { label?: string }) {
  return (
    <Stack alignItems="center" spacing={1.5} sx={{ py: 6 }}>
      <CircularProgress size={26} />
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
    </Stack>
  )
}

export function ErrorBanner({
  error,
  onRetry,
  title = 'Something went wrong',
}: {
  error: unknown
  onRetry?: () => void
  title?: string
}) {
  if (!error) return null
  const message = error instanceof Error ? error.message : String(error)
  return (
    <Alert
      severity="error"
      sx={{ mb: 2 }}
      action={
        onRetry ? (
          <Typography
            variant="button"
            sx={{ cursor: 'pointer', textDecoration: 'underline' }}
            onClick={onRetry}
          >
            RETRY
          </Typography>
        ) : undefined
      }
    >
      <AlertTitle>{title}</AlertTitle>
      <Box sx={{ whiteSpace: 'pre-line' }}>{message}</Box>
    </Alert>
  )
}

export function EmptyState({ title, hints }: { title: string; hints?: string[] }) {
  return (
    <Box sx={{ py: 5, textAlign: 'center' }}>
      <Typography variant="body1" sx={{ mb: 1 }}>
        {title}
      </Typography>
      {hints && hints.length > 0 && (
        <Stack spacing={0.5} sx={{ mt: 1 }}>
          {hints.map((hint) => (
            <Typography key={hint} variant="body2" color="text.secondary">
              • {hint}
            </Typography>
          ))}
        </Stack>
      )}
    </Box>
  )
}

export function Metric({
  label,
  value,
  hint,
  colour,
}: {
  label: string
  value: ReactNode
  hint?: string
  colour?: string
}) {
  return (
    <Box sx={{ minWidth: 120 }}>
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.6 }}>
        {label}
      </Typography>
      <Typography variant="h5" sx={{ color: colour, lineHeight: 1.3 }}>
        {value}
      </Typography>
      {hint && (
        <Typography variant="caption" color="text.secondary">
          {hint}
        </Typography>
      )}
    </Box>
  )
}

export function ProgressBar({ value, label }: { value: number; label?: string }) {
  return (
    <Box sx={{ width: '100%' }}>
      <LinearProgress variant="determinate" value={Math.max(0, Math.min(100, value))} />
      {label && (
        <Typography variant="caption" color="text.secondary">
          {label}
        </Typography>
      )}
    </Box>
  )
}

export function OriginChip({ origin }: { origin: 'source' | 'fallback' }) {
  return (
    <Tooltip
      title={
        origin === 'source'
          ? 'These values come from the connected data source.'
          : 'The source did not supply a vocabulary for this field, so the bundled fallback taxonomy is shown.'
      }
    >
      <Chip
        label={origin}
        sx={{
          height: 18,
          fontSize: '0.62rem',
          bgcolor: origin === 'source' ? '#48bb7822' : '#a0aec022',
          color: origin === 'source' ? '#48bb78' : '#a0aec0',
        }}
      />
    </Tooltip>
  )
}

export function formatDuration(seconds?: number | null): string {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  if (minutes < 60) return `${minutes}m ${rest}s`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

export function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString()
}

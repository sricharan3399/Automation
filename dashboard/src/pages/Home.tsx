import {
  Alert,
  Box,
  Button,
  Chip,
  Divider,
  Grid,
  Stack,
  Typography,
} from '@mui/material'
import { useNavigate } from 'react-router-dom'

import {
  ErrorBanner,
  LoadingBlock,
  Metric,
  PageHeader,
  SectionCard,
  StatusChip,
  formatDateTime,
  formatDuration,
} from '@/components/common'
import { useApi, usePolling } from '@/hooks/useApi'
import { api } from '@/services/api'

interface HomePayload {
  identity: { user: string; role: string; permissions: string[] }
  versions: Record<string, string>
  mode: {
    operating_mode: string
    source_access_mode: string
    production_submission_enabled: boolean
    demo: boolean
  }
  connections: {
    connection_id: string
    display_name: string
    kind: string
    enabled: boolean
    status: string
    connected: boolean
    latency_ms?: number | null
    error?: string | null
  }[]
  gpu: { available: boolean; detail: string; devices: { name: string; memory: string }[] }
  current_configuration?: Record<string, unknown> | null
  previous_run?: Record<string, unknown> | null
  queues: { review_required: number; blocked_data_errors: number }
  quick_actions: { id: string; label: string; enabled: boolean }[]
}

export function Home() {
  const navigate = useNavigate()
  const { data, error, loading, reload } = useApi<HomePayload>(
    () => api.home() as Promise<unknown> as Promise<HomePayload>,
  )
  usePolling(reload, 20_000)

  if (loading && !data) return <LoadingBlock label="Loading dashboard…" />
  if (error && !data) return <ErrorBanner error={error} onRetry={reload} title="Could not load the dashboard" />
  if (!data) return null

  const previous = data.previous_run as Record<string, unknown> | null
  const configuration = data.current_configuration as Record<string, unknown> | null

  const runQuickAction = async (id: string) => {
    if (id === 'new_run') navigate('/scout-setup')
    else if (id === 'review_queue') navigate('/review')
    else if (id === 'view_errors') navigate('/events?status=BLOCKED_DATA_ERROR')
    else if (id === 'download_csv') navigate('/reports')
    else if (id === 'connection_test') {
      await api.testAllConnections()
      reload()
    } else if (id === 'repeat_last' && previous) {
      await api.repeatRun(String(previous.run_id))
      navigate('/runs')
    }
  }

  return (
    <Box>
      <PageHeader
        title="Home"
        subtitle={`Signed in as ${data.identity.user} (${data.identity.role}) · software ${data.versions.software} · rules ${data.versions.rules}`}
        actions={data.quick_actions.map((action) => (
          <Button
            key={action.id}
            variant={action.id === 'new_run' ? 'contained' : 'outlined'}
            disabled={!action.enabled}
            onClick={() => runQuickAction(action.id)}
          >
            {action.label}
          </Button>
        ))}
      />

      {data.mode.demo && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          <strong>DEMO MODE.</strong> Synthetic datasets are selectable and every record produced is
          labelled synthetic. Switch to production mode before recording real results.
        </Alert>
      )}
      <Alert severity="info" sx={{ mb: 2 }}>
        Source access is <strong>{data.mode.source_access_mode.replace('_', ' ')}</strong> and production
        submission is <strong>{data.mode.production_submission_enabled ? 'ENABLED' : 'DISABLED'}</strong>.
        Machine findings are candidates until a reviewer confirms them.
      </Alert>

      <Grid container spacing={2}>
        <Grid item xs={12} lg={7}>
          <SectionCard title="Connection status" subtitle="All source integrations are read-only.">
            <Grid container spacing={1}>
              {data.connections.map((connection) => (
                <Grid item xs={12} sm={6} key={connection.connection_id}>
                  <Box
                    sx={{
                      p: 1.25,
                      border: '1px solid',
                      borderColor: 'divider',
                      borderRadius: 1,
                      height: '100%',
                    }}
                  >
                    <Stack direction="row" justifyContent="space-between" alignItems="center">
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {connection.display_name}
                      </Typography>
                      <StatusChip value={connection.status} />
                    </Stack>
                    <Typography variant="caption" color="text.secondary" component="div">
                      {connection.kind}
                      {connection.latency_ms !== null && connection.latency_ms !== undefined
                        ? ` · ${connection.latency_ms.toFixed(0)} ms`
                        : ''}
                    </Typography>
                    {connection.error && (
                      <Typography variant="caption" color="error" component="div" sx={{ mt: 0.5 }}>
                        {connection.error.split('\n')[0]}
                      </Typography>
                    )}
                  </Box>
                </Grid>
              ))}
              <Grid item xs={12} sm={6}>
                <Box sx={{ p: 1.25, border: '1px solid', borderColor: 'divider', borderRadius: 1 }}>
                  <Stack direction="row" justifyContent="space-between" alignItems="center">
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      GPU
                    </Typography>
                    <StatusChip value={data.gpu.available ? 'CONNECTED' : 'NOT_CONFIGURED'} label={data.gpu.available ? 'AVAILABLE' : 'NOT AVAILABLE'} />
                  </Stack>
                  <Typography variant="caption" color="text.secondary" component="div">
                    {data.gpu.devices.map((d) => `${d.name} (${d.memory})`).join(', ') || data.gpu.detail}
                  </Typography>
                </Box>
              </Grid>
            </Grid>
          </SectionCard>
        </Grid>

        <Grid item xs={12} lg={5}>
          <SectionCard title="Current configuration">
            {configuration ? (
              <Stack spacing={0.5}>
                {Object.entries(configuration)
                  .filter(([key]) => !['profile_id', 'name'].includes(key))
                  .map(([key, value]) => (
                    <Stack key={key} direction="row" justifyContent="space-between">
                      <Typography variant="body2" color="text.secondary">
                        {key.replace(/_/g, ' ')}
                      </Typography>
                      <Typography variant="body2" sx={{ textAlign: 'right' }}>
                        {formatValue(value)}
                      </Typography>
                    </Stack>
                  ))}
                <Divider sx={{ my: 1 }} />
                <Button variant="outlined" onClick={() => navigate('/scout-setup')}>
                  OPEN SCOUT SETUP
                </Button>
              </Stack>
            ) : (
              <Typography variant="body2" color="text.secondary">
                No configuration profile is loaded yet.
              </Typography>
            )}
          </SectionCard>
        </Grid>

        <Grid item xs={12}>
          <SectionCard title="Previous run">
            {previous ? (
              <Stack direction="row" spacing={4} flexWrap="wrap" useFlexGap>
                <Metric label="Run" value={String(previous.run_id).slice(0, 18)} hint={formatDateTime(previous.finished_at as string)} />
                <Metric label="Status" value={<StatusChip value={String(previous.status)} />} />
                <Metric label="Records scanned" value={Number(previous.records_scanned ?? 0)} />
                <Metric label="Country matches" value={Number(previous.records_matched_country ?? 0)} />
                <Metric label="Scenario matches" value={Number(previous.records_matched_scenario ?? 0)} />
                <Metric label="Candidate issues" value={Number(previous.candidate_issue_count ?? 0)} colour="#f6ad55" />
                <Metric label="Review required" value={Number(previous.review_required_count ?? 0)} colour="#f6ad55" />
                <Metric label="Blocking errors" value={Number(previous.blocking_error_count ?? 0)} colour="#fc8181" />
                <Metric label="Duplicates merged" value={Number(previous.duplicates_merged ?? 0)} />
                <Metric label="CSV rows" value={Number(previous.csv_rows_created ?? 0)} colour="#48bb78" />
                <Metric label="Runtime" value={formatDuration(previous.runtime_seconds as number)} />
                {Boolean(previous.dry_run) && <Chip label="DRY RUN" color="warning" />}
              </Stack>
            ) : (
              <Typography variant="body2" color="text.secondary">
                No run has been executed yet. Open Scout Setup to configure one.
              </Typography>
            )}
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={6}>
          <SectionCard title="Queues">
            <Stack direction="row" spacing={4}>
              <Metric label="Review required" value={data.queues.review_required} colour="#f6ad55" />
              <Metric label="Blocked data errors" value={data.queues.blocked_data_errors} colour="#fc8181" />
            </Stack>
          </SectionCard>
        </Grid>
      </Grid>
    </Box>
  )
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (Array.isArray(value)) return value.length ? value.join(', ') : 'Any'
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    const parts = Object.values(record).filter(Boolean)
    return parts.length ? parts.join(' → ') : 'Any'
  }
  return String(value)
}

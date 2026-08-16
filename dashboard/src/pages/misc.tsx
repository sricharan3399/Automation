/**
 * The remaining operational pages.
 *
 * Grouped in one module because each is a focused read-only view over a single
 * endpoint; splitting them into separate files would add ceremony without
 * adding clarity.
 */

import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Grid,
  LinearProgress,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { VirtualTable } from '@/components/VirtualTable'
import {
  EmptyState,
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
import { api, evidenceUrl } from '@/services/api'
import { useAppStore } from '@/stores/useAppStore'
import type { ScoutQuery, SensorConfiguration } from '@/types'

// ---------------------------------------------------------------------------
// Evidence Viewer
// ---------------------------------------------------------------------------
export function EvidenceViewer() {
  const { data: runs } = useApi(() => api.runs())
  const [runId, setRunId] = useState('')
  const { data, error, loading } = useApi(() => api.evidence({ run_id: runId || undefined }), [runId])
  const { data: policy } = useApi(() => api.redactionPolicy())
  const [open, setOpen] = useState<Record<string, unknown> | null>(null)

  const items = data?.items ?? []
  const available = items.filter((item) => item.available)
  const unavailable = items.filter((item) => !item.available)

  return (
    <Box>
      <PageHeader
        title="Evidence Viewer"
        subtitle={data?.note}
        actions={
          <TextField
            select
            size="small"
            label="Run"
            value={runId}
            onChange={(event) => setRunId(event.target.value)}
            sx={{ minWidth: 240 }}
          >
            <MenuItem value="">All runs</MenuItem>
            {(runs?.runs ?? []).map((run) => (
              <MenuItem key={run.run_id} value={run.run_id}>
                {run.run_id}
              </MenuItem>
            ))}
          </TextField>
        }
      />
      <ErrorBanner error={error} />

      {policy && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Redaction is <strong>{policy.enabled ? 'enabled' : 'disabled'}</strong> and{' '}
          <strong>{policy.fail_closed ? 'fail-closed' : 'fail-open'}</strong>. Coordinates are rounded to{' '}
          {String((policy.coordinate_precision as Record<string, unknown>)?.decimals)} decimals and{' '}
          {((policy.patterns as string[]) ?? []).length} sensitive patterns are scanned for.
        </Alert>
      )}

      {loading && !data ? (
        <LoadingBlock label="Loading evidence…" />
      ) : items.length === 0 ? (
        <EmptyState title="No evidence has been generated yet." hints={['Run a scout with evidence enabled']} />
      ) : (
        <Grid container spacing={2}>
          <Grid item xs={12} lg={7}>
            <SectionCard title={`Available (${available.length})`} dense>
              <Grid container spacing={1}>
                {available.map((item, index) => (
                  <Grid item xs={12} sm={6} key={index}>
                    <Box
                      sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 1, p: 1, cursor: 'pointer' }}
                      onClick={() => setOpen(item)}
                    >
                      <Stack direction="row" justifyContent="space-between" alignItems="center">
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>
                          {String(item.purpose)}
                        </Typography>
                        <Stack direction="row" spacing={0.5}>
                          <Chip label={String(item.kind)} sx={{ height: 18, fontSize: '0.6rem' }} />
                          {Boolean(item.redacted) && <Chip label="redacted" color="success" sx={{ height: 18, fontSize: '0.6rem' }} />}
                        </Stack>
                      </Stack>
                      <Typography variant="caption" color="text.secondary" component="div">
                        {String(item.event_reference)}
                        {item.t_rel_s !== null ? ` · t=${Number(item.t_rel_s).toFixed(2)}s` : ''}
                      </Typography>
                      <Typography variant="caption" color="text.secondary" component="div" noWrap sx={{ fontFamily: 'monospace' }}>
                        {String(item.content_hash ?? '')}
                      </Typography>
                    </Box>
                  </Grid>
                ))}
              </Grid>
            </SectionCard>
          </Grid>

          <Grid item xs={12} lg={5}>
            <SectionCard
              title={`Unavailable (${unavailable.length})`}
              subtitle="Recorded deliberately — a manifest that omits a capture point would read like evidence that was reviewed."
              dense
            >
              <Box sx={{ maxHeight: 480, overflowY: 'auto' }}>
                {unavailable.slice(0, 60).map((item, index) => (
                  <Box key={index} sx={{ py: 0.75, borderBottom: '1px solid', borderColor: 'divider' }}>
                    <Typography variant="caption" sx={{ fontWeight: 600 }}>
                      {String(item.purpose)} · {String(item.event_reference)}
                    </Typography>
                    <Typography variant="caption" color="text.secondary" component="div">
                      {String(item.unavailable_reason)}
                    </Typography>
                  </Box>
                ))}
              </Box>
            </SectionCard>
          </Grid>
        </Grid>
      )}

      {open && (
        <Dialog open onClose={() => setOpen(null)} maxWidth="lg" fullWidth>
          <DialogTitle>
            {String(open.purpose)} — {String(open.event_reference)}
          </DialogTitle>
          <DialogContent>
            <Stack spacing={1} sx={{ mb: 2 }}>
              <Typography variant="caption">Evidence ID: {String(open.evidence_id)}</Typography>
              <Typography variant="caption">Hash: {String(open.content_hash ?? '—')}</Typography>
              <Typography variant="caption">Redacted: {open.redacted ? 'yes' : 'no'}</Typography>
              <Typography variant="caption">Approved: {open.approved ? 'yes' : 'no'}</Typography>
            </Stack>
            {open.kind === 'svg' && open.run_id ? (
              <Box
                component="img"
                src={evidenceUrl(String(open.run_id), String(open.event_reference), String(open.relative_path).split(/[\\/]/).pop() ?? '')}
                alt={String(open.purpose)}
                sx={{ width: '100%', border: '1px solid', borderColor: 'divider', borderRadius: 1 }}
              />
            ) : (
              <Alert severity="info">
                This evidence item is stored as {String(open.kind)} at{' '}
                <code>{String(open.relative_path)}</code> inside the run output directory.
              </Alert>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setOpen(null)}>CLOSE</Button>
          </DialogActions>
        </Dialog>
      )}
    </Box>
  )
}

// ---------------------------------------------------------------------------
// Quality Analytics
// ---------------------------------------------------------------------------
export function QualityAnalytics() {
  const { data: overview, error, loading } = useApi(() => api.analyticsOverview(90))
  const { data: breakdown } = useApi(() => api.errorBreakdown({}))
  const { data: quality } = useApi(() => api.reviewQuality())
  const { data: performance } = useApi(() => api.performance())
  const { data: sensors } = useApi(() => api.sensorQuality())

  if (loading && !overview) return <LoadingBlock label="Loading analytics…" />

  const byCategory = (breakdown?.by_category ?? {}) as Record<string, number>
  const byRule = (breakdown?.by_rule ?? []) as Record<string, unknown>[]
  const maxCategory = Math.max(1, ...Object.values(byCategory))
  const review = (overview?.review ?? {}) as Record<string, unknown>

  return (
    <Box>
      <PageHeader title="Quality Analytics" subtitle={String(overview?.note ?? '')} />
      <ErrorBanner error={error} />

      <Grid container spacing={2}>
        <Grid item xs={12}>
          <SectionCard title="Overview">
            <Stack direction="row" spacing={4} flexWrap="wrap" useFlexGap>
              <Metric label="Events processed" value={Number(overview?.events_processed ?? 0)} />
              <Metric label="Events / hour" value={String(overview?.events_per_hour ?? '—')} />
              <Metric label="Runs" value={Number(overview?.runs ?? 0)} />
              <Metric
                label="Candidate error rate"
                value={formatRate(overview?.candidate_error_rate)}
                colour="#f6ad55"
              />
              <Metric
                label="Blocking data error rate"
                value={formatRate(overview?.blocking_data_error_rate)}
                colour="#fc8181"
              />
              <Metric label="Acceptance rate" value={formatRate(review.acceptance_rate)} colour="#48bb78" />
              <Metric label="Override rate" value={formatRate(review.override_rate)} colour="#f6ad55" />
              <Metric label="Duplicates merged" value={Number(overview?.duplicates_merged ?? 0)} />
              <Metric label="Mean sensor quality" value={String(overview?.mean_sensor_quality ?? '—')} />
              <Metric label="Mean confidence" value={String(overview?.mean_overall_confidence ?? '—')} />
            </Stack>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={5}>
          <SectionCard title="Findings by category">
            {Object.keys(byCategory).length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                No findings recorded yet.
              </Typography>
            ) : (
              <Stack spacing={1}>
                {Object.entries(byCategory)
                  .sort((a, b) => b[1] - a[1])
                  .map(([category, count]) => (
                    <Box key={category}>
                      <Stack direction="row" justifyContent="space-between">
                        <Typography variant="caption">{category}</Typography>
                        <Typography variant="caption">{count}</Typography>
                      </Stack>
                      <LinearProgress variant="determinate" value={(count / maxCategory) * 100} />
                    </Box>
                  ))}
              </Stack>
            )}
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={7}>
          <SectionCard title="Top rules by finding count" dense>
            <VirtualTable
              rows={byRule.slice(0, 100)}
              keyOf={(row, index) => `${row.rule_id}-${index}`}
              height={280}
              columns={[
                { key: 'rule', header: 'Rule', width: 300, render: (row) => String(row.rule_id) },
                { key: 'category', header: 'Category', width: 150, render: (row) => String(row.category) },
                { key: 'severity', header: 'Severity', width: 110, render: (row) => <StatusChip value={String(row.severity)} /> },
                { key: 'count', header: 'Count', width: 80, align: 'right', render: (row) => String(row.count) },
              ]}
              emptyMessage="No rule findings yet."
            />
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={6}>
          <SectionCard title="Review quality by field" dense>
            <VirtualTable
              rows={((quality?.per_field ?? []) as Record<string, unknown>[]).slice(0, 100)}
              keyOf={(row) => String(row.field)}
              height={280}
              columns={[
                { key: 'field', header: 'Field', width: 200, render: (row) => String(row.field) },
                { key: 'accept', header: 'Accept', width: 80, align: 'right', render: (row) => String(row.ACCEPT ?? 0) },
                { key: 'edit', header: 'Edit', width: 70, align: 'right', render: (row) => String(row.EDIT ?? 0) },
                { key: 'reject', header: 'Reject', width: 80, align: 'right', render: (row) => String(row.REJECT ?? 0) },
                { key: 'rate', header: 'Acceptance', width: 110, align: 'right', render: (row) => formatRate(row.acceptance_rate) },
              ]}
              emptyMessage="No reviewer decisions recorded yet."
            />
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={6}>
          <SectionCard title="Performance" dense>
            <VirtualTable
              rows={((performance?.runs ?? []) as Record<string, unknown>[]).slice(0, 100)}
              keyOf={(row) => String(row.run_id)}
              height={280}
              columns={[
                { key: 'run', header: 'Run', width: 200, render: (row) => String(row.run_id) },
                { key: 'processed', header: 'Events', width: 80, align: 'right', render: (row) => String(row.records_processed) },
                { key: 'elapsed', header: 'Elapsed', width: 100, align: 'right', render: (row) => formatDuration(row.elapsed_seconds as number) },
                { key: 'per', header: 's / event', width: 100, align: 'right', render: (row) => String(row.seconds_per_event ?? '—') },
                { key: 'csv', header: 'CSV rows', width: 90, align: 'right', render: (row) => String(row.csv_rows_created) },
              ]}
              emptyMessage="No runs yet."
            />
          </SectionCard>
        </Grid>

        <Grid item xs={12}>
          <SectionCard title="Sensor quality" dense>
            <VirtualTable
              rows={((sensors?.streams ?? []) as Record<string, unknown>[])}
              keyOf={(row, index) => `${row.stream_type}-${index}`}
              height={240}
              columns={[
                { key: 'stream', header: 'Stream', width: 200, render: (row) => `${row.stream_type}${row.camera_position ? `:${row.camera_position}` : ''}` },
                { key: 'quality', header: 'Mean quality', width: 130, align: 'right', render: (row) => String(row.mean_quality_score ?? '—') },
                { key: 'availability', header: 'Mean availability', width: 150, align: 'right', render: (row) => (row.mean_availability_pct ? `${row.mean_availability_pct}%` : '—') },
                { key: 'n', header: 'Samples', width: 90, align: 'right', render: (row) => String(row.sample_size) },
              ]}
              emptyMessage="No sensor statistics yet."
            />
          </SectionCard>
        </Grid>
      </Grid>
    </Box>
  )
}

function formatRate(value: unknown): string {
  if (value === null || value === undefined) return '—'
  return `${(Number(value) * 100).toFixed(1)}%`
}

// ---------------------------------------------------------------------------
// Configuration Profiles
// ---------------------------------------------------------------------------
export function ConfigurationProfiles() {
  const navigate = useNavigate()
  const store = useAppStore()
  const { data, error, loading, reload } = useApi(() => api.profiles())
  const [saveOpen, setSaveOpen] = useState(false)
  const [newId, setNewId] = useState('')
  const [newName, setNewName] = useState('')
  const [saveError, setSaveError] = useState<unknown>(null)

  const save = async () => {
    setSaveError(null)
    try {
      await api.saveProfile(newId, {
        profile_id: newId,
        name: newName || newId,
        description: 'Saved from the dashboard.',
        query: store.query,
        sensor_config: store.sensorConfig,
        csv_template_id: store.csvTemplateId,
        csv_columns: [],
        rule_overrides: store.ruleOverrides,
        threshold_overrides: {},
        evidence_config: {},
        connection_profile_id: store.connectionId,
      })
      setSaveOpen(false)
      reload()
    } catch (cause) {
      setSaveError(cause)
    }
  }

  return (
    <Box>
      <PageHeader
        title="Configuration Profiles"
        subtitle="A profile stores filters, sensor requirements, rules, thresholds and the CSV schema. Credentials are never stored in a profile."
        actions={
          <Button variant="contained" onClick={() => setSaveOpen(true)}>
            SAVE CURRENT AS PROFILE
          </Button>
        }
      />
      <ErrorBanner error={error} onRetry={reload} />

      {loading && !data ? (
        <LoadingBlock />
      ) : (
        <Grid container spacing={2}>
          {((data?.profiles ?? []) as Record<string, unknown>[]).map((profile) => (
            <Grid item xs={12} md={6} xl={4} key={String(profile.profile_id)}>
              <SectionCard
                title={String(profile.name)}
                subtitle={String(profile.description)}
                actions={profile.is_builtin ? <Chip label="bundled" /> : undefined}
              >
                <Stack spacing={0.5} sx={{ mb: 1.5 }}>
                  <Typography variant="caption" color="text.secondary">
                    id: {String(profile.profile_id)} · v{String(profile.version)} · executed{' '}
                    {String(profile.executed_count)}×
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    CSV template: {String(profile.csv_template_id)}
                  </Typography>
                </Stack>
                <Stack direction="row" spacing={1}>
                  <Button
                    variant="outlined"
                    onClick={() => {
                      store.loadProfile(
                        String(profile.profile_id),
                        profile.query as ScoutQuery,
                        profile.sensor_config as SensorConfiguration,
                        String(profile.csv_template_id),
                      )
                      navigate('/scout-setup')
                    }}
                  >
                    LOAD
                  </Button>
                  {!profile.is_builtin && (
                    <Button
                      color="error"
                      onClick={async () => {
                        await api.deleteProfile(String(profile.profile_id))
                        reload()
                      }}
                    >
                      DELETE
                    </Button>
                  )}
                </Stack>
              </SectionCard>
            </Grid>
          ))}
        </Grid>
      )}

      <Dialog open={saveOpen} onClose={() => setSaveOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Save configuration profile</DialogTitle>
        <DialogContent>
          <ErrorBanner error={saveError} />
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              label="Profile id"
              value={newId}
              onChange={(event) => setNewId(event.target.value.replace(/[^a-z0-9_]/gi, '_').toLowerCase())}
              helperText="Lower-case identifier, e.g. germany_bus_night"
            />
            <TextField label="Display name" value={newName} onChange={(event) => setNewName(event.target.value)} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSaveOpen(false)}>CANCEL</Button>
          <Button variant="contained" onClick={save} disabled={!newId}>
            SAVE
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

// ---------------------------------------------------------------------------
// Audit Logs
// ---------------------------------------------------------------------------
export function AuditLogs() {
  const [action, setAction] = useState('')
  const [runId, setRunId] = useState('')
  const { data, error, loading, reload } = useApi(
    () => api.audit({ action: action || undefined, run_id: runId || undefined, limit: 500 }),
    [action, runId],
  )

  return (
    <Box>
      <PageHeader
        title="Audit Logs"
        subtitle="Append-only. There is no API to update or delete an audit record."
        actions={<Button onClick={reload}>REFRESH</Button>}
      />
      <ErrorBanner error={error} onRetry={reload} />

      <SectionCard dense>
        <Stack direction="row" spacing={2}>
          <TextField label="Action" value={action} onChange={(event) => setAction(event.target.value)} />
          <TextField label="Run id" value={runId} onChange={(event) => setRunId(event.target.value)} />
        </Stack>
      </SectionCard>

      <Box sx={{ mt: 2 }}>
        {loading && !data ? (
          <LoadingBlock />
        ) : (
          <VirtualTable
            rows={(data?.entries ?? []) as Record<string, unknown>[]}
            keyOf={(row) => String(row.audit_pk)}
            height="calc(100vh - 330px)"
            columns={[
              { key: 'when', header: 'When', width: 180, render: (row) => formatDateTime(row.created_at as string) },
              { key: 'actor', header: 'Actor', width: 140, render: (row) => `${row.actor} (${row.actor_role})` },
              { key: 'action', header: 'Action', width: 190, render: (row) => String(row.action) },
              { key: 'entity', header: 'Entity', width: 240, render: (row) => `${row.entity_type} ${row.entity_ref}` },
              { key: 'run', header: 'Run', width: 200, render: (row) => String(row.run_id ?? '—') },
              { key: 'detail', header: 'Detail', render: (row) => String(row.detail ?? '') },
              { key: 'version', header: 'Versions', width: 150, render: (row) => `${row.software_version} / ${row.rule_version ?? '—'}` },
            ]}
            emptyMessage="No audit records match this filter."
          />
        )}
      </Box>
    </Box>
  )
}

// ---------------------------------------------------------------------------
// System Health
// ---------------------------------------------------------------------------
export function SystemHealth() {
  const { data, error, loading, reload } = useApi(() => api.systemHealth())
  const { data: environment } = useApi(() => api.environment())
  usePolling(reload, 5000)

  if (loading && !data) return <LoadingBlock label="Reading system health…" />

  const ram = (data?.ram ?? {}) as Record<string, number>
  const disk = (data?.disk ?? {}) as Record<string, number>
  const gpu = (data?.gpu ?? {}) as Record<string, unknown>
  const workers = (data?.workers ?? {}) as Record<string, unknown>
  const checks = ((environment?.checks ?? []) as Record<string, string>[]) ?? []

  return (
    <Box>
      <PageHeader title="System Health" subtitle="Live resource usage and the environment checks." actions={<Button onClick={reload}>REFRESH</Button>} />
      <ErrorBanner error={error} onRetry={reload} />

      <Grid container spacing={2}>
        <Grid item xs={12}>
          <SectionCard title="Resources">
            <Stack direction="row" spacing={4} flexWrap="wrap" useFlexGap>
              <Metric label="CPU" value={`${Number(data?.cpu_percent ?? 0).toFixed(0)}%`} hint={`${data?.cpu_cores} cores`} />
              <Metric label="RAM" value={`${ram.used_gb ?? 0} / ${ram.total_gb ?? 0} GB`} hint={`${ram.percent ?? 0}%`} />
              <Metric label="Disk free" value={`${disk.free_gb ?? 0} GB`} hint={`${disk.percent ?? 0}% used`} />
              <Metric
                label="GPU"
                value={gpu.available ? 'available' : 'not available'}
                hint={String(gpu.detail ?? '')}
                colour={gpu.available ? '#48bb78' : undefined}
              />
              <Metric label="Compute device" value={String(data?.compute_device ?? 'auto')} />
              <Metric label="Active runs" value={((workers.active_runs as string[]) ?? []).length} />
              <Metric label="Resumable" value={((workers.resumable_runs as string[]) ?? []).length} />
            </Stack>
          </SectionCard>
        </Grid>

        <Grid item xs={12}>
          <SectionCard title="Environment checks">
            <Grid container spacing={1}>
              {checks.map((check) => (
                <Grid item xs={12} md={6} lg={4} key={check.name}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <StatusChip
                      value={check.status === 'PASS' ? 'COMPLETED' : check.status === 'WARNING' ? 'PAUSED' : 'FAILED'}
                      label={check.status}
                    />
                    <Box>
                      <Typography variant="body2">{check.name}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {check.detail}
                      </Typography>
                    </Box>
                  </Stack>
                </Grid>
              ))}
            </Grid>
          </SectionCard>
        </Grid>
      </Grid>
    </Box>
  )
}

// ---------------------------------------------------------------------------
// Production Readiness (spec sections 95 and 96)
// ---------------------------------------------------------------------------
type ReadinessCheck = {
  key: string
  name: string
  status: 'PASS' | 'FAIL' | 'WAITING' | 'WARNING'
  detail: string
  mandatory: boolean
  remediation: string | null
}

/** PASS is the only green. WAITING is not a failure, but it is not readiness either. */
const READINESS_CHIP: Record<string, string> = {
  PASS: 'COMPLETED',
  FAIL: 'FAILED',
  WAITING: 'PENDING',
  WARNING: 'PAUSED',
}

export function ProductionReadiness() {
  const { data, error, loading, reload } = useApi(() => api.productionReadiness())

  if (loading && !data) return <LoadingBlock label="Evaluating production readiness…" />

  const checks = ((data?.checks ?? []) as ReadinessCheck[]) ?? []
  const summary = (data?.summary ?? {}) as Record<string, number>
  const ready = Boolean(data?.production_ready)
  const nextAction = data?.next_action as string | null

  return (
    <Box>
      <PageHeader
        title="Production Readiness"
        subtitle="Evaluated live against this installation. No stored flag; nothing is cached."
        actions={<Button onClick={reload}>RE-EVALUATE</Button>}
      />
      <ErrorBanner error={error} onRetry={reload} />

      <Alert severity={ready ? 'success' : 'warning'} sx={{ mb: 2 }}>
        <strong>PRODUCTION READY: {ready ? 'YES' : 'NO'}</strong>
        {!ready && nextAction && (
          <Typography variant="body2" sx={{ mt: 0.5 }}>
            Next required action: {nextAction}
          </Typography>
        )}
      </Alert>

      <Grid container spacing={2}>
        <Grid item xs={12}>
          <SectionCard title="Gate">
            <Stack direction="row" spacing={4} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
              <Metric label="Passed" value={summary.passed ?? 0} />
              <Metric label="Failed" value={summary.failed ?? 0} colour={summary.failed ? '#fc8181' : undefined} />
              <Metric label="Waiting" value={summary.waiting ?? 0} />
              <Metric label="Warnings" value={summary.warnings ?? 0} />
            </Stack>

            <Stack spacing={1}>
              {checks.map((check) => (
                <Stack key={check.key} direction="row" spacing={1.5} alignItems="flex-start">
                  <Box sx={{ minWidth: 96 }}>
                    <StatusChip value={READINESS_CHIP[check.status] ?? 'PENDING'} label={check.status} />
                  </Box>
                  <Box>
                    <Typography variant="body2">
                      {check.name}
                      {!check.mandatory && (
                        <Typography component="span" variant="caption" color="text.secondary">
                          {' '}
                          (advisory)
                        </Typography>
                      )}
                    </Typography>
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                      {check.detail}
                    </Typography>
                    {check.status !== 'PASS' && check.remediation && (
                      <Typography variant="caption" sx={{ display: 'block', color: 'warning.main' }}>
                        → {check.remediation}
                      </Typography>
                    )}
                  </Box>
                </Stack>
              ))}
            </Stack>
          </SectionCard>
        </Grid>
      </Grid>
    </Box>
  )
}

// ---------------------------------------------------------------------------
// Administration
// ---------------------------------------------------------------------------
export function Administration() {
  const { data, error, loading, reload } = useApi(() => api.adminSettings())
  const { data: roles } = useApi(() => api.adminRoles())
  const { data: retention } = useApi(() => api.adminRetention())
  const [message, setMessage] = useState<string | null>(null)

  if (loading && !data) return <LoadingBlock label="Loading administration…" />

  const mode = (data?.mode ?? {}) as Record<string, unknown>
  const matrix = (roles?.matrix ?? {}) as Record<string, string[]>

  return (
    <Box>
      <PageHeader
        title="Administration"
        subtitle={String(data?.editing_note ?? '')}
        actions={
          <Button
            variant="contained"
            onClick={async () => {
              const result = await api.reloadConfiguration()
              setMessage(`Configuration reloaded (rules ${JSON.stringify(result.after)}).`)
              reload()
            }}
          >
            RELOAD CONFIGURATION
          </Button>
        }
      />
      <ErrorBanner error={error} onRetry={reload} />
      {message && <Alert severity="success" sx={{ mb: 2 }}>{message}</Alert>}

      <Grid container spacing={2}>
        <Grid item xs={12} md={6}>
          <SectionCard title="Operating mode">
            <Stack spacing={0.75}>
              {Object.entries(mode).map(([key, value]) => (
                <Stack key={key} direction="row" justifyContent="space-between">
                  <Typography variant="body2" color="text.secondary">
                    {key.replace(/_/g, ' ')}
                  </Typography>
                  <Typography variant="body2">{String(value)}</Typography>
                </Stack>
              ))}
            </Stack>
            <Alert severity="warning" sx={{ mt: 2 }}>
              Production submission is disabled. Enabling it requires a separately approved
              configuration change and an approved integration; the platform implements no one-click
              submit.
            </Alert>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={6}>
          <SectionCard title="Roles and permissions" subtitle={String(roles?.note ?? '')}>
            <Stack spacing={1}>
              {Object.entries(matrix).map(([role, permissions]) => (
                <Box key={role}>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {role}
                  </Typography>
                  <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                    {permissions.map((permission) => (
                      <Chip key={permission} label={permission} sx={{ height: 18, fontSize: '0.6rem' }} />
                    ))}
                  </Stack>
                </Box>
              ))}
            </Stack>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={6}>
          <SectionCard title="Retention" subtitle={String(retention?.note ?? '')}>
            <Stack direction="row" spacing={4}>
              <Metric label="Retention days" value={Number(retention?.retention_days ?? 0)} />
              <Metric label="Runs past cutoff" value={Number(retention?.runs_older_than_cutoff ?? 0)} />
              <Metric label="Events past cutoff" value={Number(retention?.events_older_than_cutoff ?? 0)} />
            </Stack>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={6}>
          <SectionCard title="Versions">
            <Stack spacing={0.5}>
              {Object.entries((data?.application ?? {}) as Record<string, unknown>).map(([key, value]) => (
                <Stack key={key} direction="row" justifyContent="space-between">
                  <Typography variant="body2" color="text.secondary">
                    {key.replace(/_/g, ' ')}
                  </Typography>
                  <Typography variant="body2">{String(value)}</Typography>
                </Stack>
              ))}
            </Stack>
          </SectionCard>
        </Grid>

        <Grid item xs={12}>
          <SectionCard title="Effective configuration" dense>
            <Box component="pre" sx={{ fontSize: '0.72rem', maxHeight: 400, overflow: 'auto', m: 0 }}>
              {JSON.stringify(
                {
                  processing: data?.processing,
                  geometry: data?.geometry,
                  synchronization: data?.synchronization,
                  perception: data?.perception,
                  tracking: data?.tracking,
                  behavior: data?.behavior,
                  evidence: data?.evidence,
                  review: data?.review,
                  export: data?.export,
                  storage: data?.storage,
                },
                null,
                2,
              )}
            </Box>
          </SectionCard>
        </Grid>
      </Grid>
    </Box>
  )
}

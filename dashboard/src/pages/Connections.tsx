import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Grid,
  MenuItem,
  Stack,
  Switch,
  TextField,
  Typography,
} from '@mui/material'
import { useState } from 'react'

import {
  ErrorBanner,
  LoadingBlock,
  PageHeader,
  SectionCard,
  StatusChip,
  formatDateTime,
} from '@/components/common'
import { useApi } from '@/hooks/useApi'
import { api } from '@/services/api'
import type { ConnectionSummary } from '@/types'

const INTEGRATION_TYPES = [
  'rest_api',
  'sdk',
  'graphql',
  'cli',
  'database',
  'csv_export',
  'json_export',
  'browser',
]

interface SchemaField {
  source_field: string
  inferred_type: string
  sample_values: unknown[]
  canonical_field: string | null
  mapping_confidence: number
  mapping_method: string
}

export function Connections() {
  const { data, error, loading, reload } = useApi(() => api.connections())
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<unknown>(null)
  const [editing, setEditing] = useState<ConnectionSummary | null>(null)
  const [mappingFor, setMappingFor] = useState<ConnectionSummary | null>(null)

  const test = async (id: string) => {
    setBusy(id)
    setActionError(null)
    try {
      await api.testConnection(id)
      reload()
    } catch (cause) {
      setActionError(cause)
    } finally {
      setBusy(null)
    }
  }

  if (loading && !data) return <LoadingBlock label="Loading connections…" />
  if (error && !data) return <ErrorBanner error={error} onRetry={reload} />

  return (
    <Box>
      <PageHeader
        title="Connections"
        subtitle={data?.note}
        actions={
          <Button
            variant="contained"
            onClick={async () => {
              setBusy('all')
              try {
                await api.testAllConnections()
                reload()
              } finally {
                setBusy(null)
              }
            }}
            disabled={busy !== null}
          >
            TEST ALL
          </Button>
        }
      />

      <ErrorBanner error={actionError} />

      <Grid container spacing={2}>
        {(data?.connections ?? []).map((connection) => (
          <Grid item xs={12} md={6} xl={4} key={connection.connection_id}>
            <SectionCard
              title={connection.display_name}
              subtitle={`${connection.kind} · adapter ${connection.adapter} · ${connection.integration_type}`}
              actions={<StatusChip value={connection.last_status} />}
            >
              <Stack spacing={0.5} sx={{ mb: 1.5 }}>
                <Row label="Configured" value={connection.configured ? 'yes' : 'no'} />
                <Row label="Enabled" value={connection.enabled ? 'yes' : 'no'} />
                <Row label="Last tested" value={formatDateTime(connection.last_tested_at)} />
                <Row
                  label="Latency"
                  value={connection.last_latency_ms ? `${connection.last_latency_ms.toFixed(0)} ms` : '—'}
                />
                <Row label="API version" value={connection.api_version ?? '—'} />
                <Row label="Schema version" value={connection.schema_version ?? '—'} />
                <Row label="Permissions" value={connection.permissions.join(', ') || '—'} />
                <Row
                  label="Credential"
                  value={
                    connection.credential_available === null || connection.credential_available === undefined
                      ? 'not required'
                      : connection.credential_available
                        ? 'available'
                        : 'MISSING'
                  }
                />
                <Row label="Field mapping" value={connection.has_field_mapping ? 'confirmed' : 'auto-suggested'} />
              </Stack>

              {connection.last_error && (
                <Alert severity={connection.last_status === 'NOT_CONFIGURED' ? 'info' : 'error'} sx={{ mb: 1.5 }}>
                  <Box sx={{ whiteSpace: 'pre-line', fontSize: '0.78rem' }}>{connection.last_error}</Box>
                </Alert>
              )}

              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                <Button onClick={() => setEditing(connection)}>CONFIGURE</Button>
                <Button onClick={() => test(connection.connection_id)} disabled={busy !== null}>
                  TEST
                </Button>
                <Button onClick={() => setMappingFor(connection)}>SCHEMA</Button>
              </Stack>
            </SectionCard>
          </Grid>
        ))}
      </Grid>

      {editing && (
        <ConfigureDialog
          connection={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            reload()
          }}
        />
      )}
      {mappingFor && <SchemaDialog connection={mappingFor} onClose={() => setMappingFor(null)} />}
    </Box>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <Stack direction="row" justifyContent="space-between">
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="caption" sx={{ textAlign: 'right', maxWidth: '60%' }}>
        {value}
      </Typography>
    </Stack>
  )
}

function ConfigureDialog({
  connection,
  onClose,
  onSaved,
}: {
  connection: ConnectionSummary
  onClose: () => void
  onSaved: () => void
}) {
  const [enabled, setEnabled] = useState(connection.enabled)
  const [integrationType, setIntegrationType] = useState(connection.integration_type)
  const [settingsText, setSettingsText] = useState(JSON.stringify(connection.settings, null, 2))
  const [error, setError] = useState<unknown>(null)
  const [saving, setSaving] = useState(false)

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const parsed = settingsText.trim() ? JSON.parse(settingsText) : {}
      // Values the backend withheld are placeholders; sending them back would
      // overwrite the real configuration with the mask.
      Object.keys(parsed).forEach((key) => {
        if (parsed[key] === '***withheld***') delete parsed[key]
      })
      await api.updateConnection(connection.connection_id, {
        enabled,
        integration_type: integrationType,
        settings: parsed,
      })
      onSaved()
    } catch (cause) {
      setError(cause)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>Configure {connection.display_name}</DialogTitle>
      <DialogContent>
        <ErrorBanner error={error} />
        <Alert severity="warning" sx={{ mb: 2 }}>
          Never enter a token, password or API key here. Supply a{' '}
          <code>credential_key</code> naming the entry in the OS credential store or the injected
          environment variable; the platform rejects secret values in a connection profile.
        </Alert>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Switch checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
            <Typography variant="body2">Enabled</Typography>
          </Stack>
          <TextField
            select
            label="Integration type"
            value={integrationType}
            onChange={(event) => setIntegrationType(event.target.value)}
          >
            {INTEGRATION_TYPES.map((type) => (
              <MenuItem key={type} value={type}>
                {type}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            label="Settings (JSON)"
            multiline
            minRows={12}
            value={settingsText}
            onChange={(event) => setSettingsText(event.target.value)}
            InputProps={{ sx: { fontFamily: 'monospace', fontSize: '0.78rem' } }}
            helperText="Non-secret settings only: base_url, endpoints, response_paths, query_translation, credential_key."
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>CANCEL</Button>
        <Button variant="contained" onClick={save} disabled={saving}>
          SAVE
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function SchemaDialog({ connection, onClose }: { connection: ConnectionSummary; onClose: () => void }) {
  const [fields, setFields] = useState<SchemaField[] | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)

  const discover = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = (await api.discoverSchema(connection.connection_id)) as {
        fields: SchemaField[]
      }
      setFields(result.fields)
      setMapping(
        Object.fromEntries(
          result.fields
            .filter((field) => field.canonical_field)
            .map((field) => [field.source_field, field.canonical_field as string]),
        ),
      )
    } catch (cause) {
      setError(cause)
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.saveFieldMapping(connection.connection_id, mapping)
      setSaved(true)
    } catch (cause) {
      setError(cause)
    } finally {
      setBusy(false)
    }
  }

  const canonicalOptions = Array.from(
    new Set(['', ...(fields ?? []).map((f) => f.canonical_field).filter(Boolean)] as string[]),
  )

  return (
    <Dialog open onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle>Schema & field mapping — {connection.display_name}</DialogTitle>
      <DialogContent>
        <ErrorBanner error={error} />
        {saved && <Alert severity="success" sx={{ mb: 2 }}>Field mapping saved.</Alert>}
        <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
          <Button variant="contained" onClick={discover} disabled={busy}>
            DISCOVER SCHEMA
          </Button>
          <Button onClick={save} disabled={busy || !fields}>
            SAVE MAPPING
          </Button>
        </Stack>

        {!fields ? (
          <Typography variant="body2" color="text.secondary">
            Run discovery to inspect the fields this source actually returns and propose a mapping onto
            canonical field names.
          </Typography>
        ) : (
          <Box sx={{ maxHeight: 460, overflow: 'auto' }}>
            <Grid container spacing={1} sx={{ px: 1, py: 0.5, fontWeight: 700 }}>
              <Grid item xs={4}><Typography variant="caption">SOURCE FIELD</Typography></Grid>
              <Grid item xs={2}><Typography variant="caption">TYPE</Typography></Grid>
              <Grid item xs={3}><Typography variant="caption">CANONICAL FIELD</Typography></Grid>
              <Grid item xs={3}><Typography variant="caption">SAMPLE</Typography></Grid>
            </Grid>
            {fields.map((field) => (
              <Grid
                container
                spacing={1}
                key={field.source_field}
                alignItems="center"
                sx={{ px: 1, py: 0.25, borderTop: '1px solid', borderColor: 'divider' }}
              >
                <Grid item xs={4}>
                  <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                    {field.source_field}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {field.mapping_method} · {(field.mapping_confidence * 100).toFixed(0)}%
                  </Typography>
                </Grid>
                <Grid item xs={2}>
                  <Typography variant="caption">{field.inferred_type}</Typography>
                </Grid>
                <Grid item xs={3}>
                  <TextField
                    select
                    fullWidth
                    value={mapping[field.source_field] ?? ''}
                    onChange={(event) =>
                      setMapping((state) => {
                        const next = { ...state }
                        if (event.target.value) next[field.source_field] = event.target.value
                        else delete next[field.source_field]
                        return next
                      })
                    }
                  >
                    <MenuItem value="">(unmapped)</MenuItem>
                    {canonicalOptions.filter(Boolean).map((option) => (
                      <MenuItem key={option} value={option}>
                        {option}
                      </MenuItem>
                    ))}
                  </TextField>
                </Grid>
                <Grid item xs={3}>
                  <Typography variant="caption" color="text.secondary" noWrap>
                    {field.sample_values.slice(0, 2).map(String).join(', ')}
                  </Typography>
                </Grid>
              </Grid>
            ))}
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>CLOSE</Button>
      </DialogActions>
    </Dialog>
  )
}

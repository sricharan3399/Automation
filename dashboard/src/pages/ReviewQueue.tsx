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
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from '@mui/material'
import { useMemo, useState } from 'react'

import {
  ConfidenceBadge,
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  PageHeader,
  SectionCard,
  StatusChip,
} from '@/components/common'
import { useApi } from '@/hooks/useApi'
import { api } from '@/services/api'
import type { ReviewDetail, ReviewField } from '@/types'

const QUEUES = [
  'all',
  'high_confidence',
  'medium_confidence',
  'low_confidence',
  'blocking_errors',
  'safety_review',
  'data_issues',
  'completed',
  'rejected',
]

export function ReviewQueue() {
  const [queue, setQueue] = useState('all')
  const [selected, setSelected] = useState<string | null>(null)
  const { data: queues, reload: reloadQueues } = useApi(() => api.reviewQueues())
  const { data, error, loading, reload } = useApi(() => api.reviewQueue(queue, 200), [queue])

  const counts = (queues?.queues ?? {}) as Record<string, number>

  return (
    <Box>
      <PageHeader
        title="Review Queue"
        subtitle="Machine findings are candidates. A record becomes a confirmed result only when a reviewer decides."
        actions={<Button onClick={() => { reload(); reloadQueues() }}>REFRESH</Button>}
      />

      <ErrorBanner error={error} onRetry={reload} />

      <Tabs
        value={QUEUES.indexOf(queue)}
        onChange={(_, index) => setQueue(QUEUES[index])}
        variant="scrollable"
        scrollButtons="auto"
        sx={{ mb: 2 }}
      >
        {QUEUES.map((name) => (
          <Tab
            key={name}
            sx={{ minHeight: 42, fontSize: '0.75rem' }}
            label={
              <Stack direction="row" spacing={0.75} alignItems="center">
                <span>{name.replace(/_/g, ' ')}</span>
                <Chip label={counts[name] ?? 0} sx={{ height: 18, fontSize: '0.62rem' }} />
              </Stack>
            }
          />
        ))}
      </Tabs>

      {loading && !data ? (
        <LoadingBlock label="Loading review queue…" />
      ) : (data?.items ?? []).length === 0 ? (
        <EmptyState
          title="This queue is empty."
          hints={['Run a scout to populate the queue', 'Try the All tab', 'Check the Automation Runs page for a failed run']}
        />
      ) : (
        <Grid container spacing={1}>
          {(data?.items ?? []).map((item) => {
            const record = item as Record<string, unknown>
            const key = String(record.canonical_event_key)
            return (
              <Grid item xs={12} md={6} xl={4} key={key}>
                <SectionCard dense>
                  <Stack direction="row" justifyContent="space-between" alignItems="center">
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {String(record.event_reference)}
                    </Typography>
                    <StatusChip value={String(record.status)} />
                  </Stack>
                  <Typography variant="caption" color="text.secondary" component="div">
                    {String(record.country_code ?? '')} · {String(record.city ?? '')} ·{' '}
                    {String(record.road_type ?? '')} · {String(record.intersection_type ?? '')}
                  </Typography>
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ my: 1 }}>
                    <ConfidenceBadge value={record.overall_confidence as number} />
                    {Number(record.blocking_error_count) > 0 && (
                      <Chip label={`${record.blocking_error_count} blocking`} color="error" sx={{ height: 20 }} />
                    )}
                  </Stack>
                  <Typography variant="caption" color="text.secondary" component="div" sx={{ mb: 1 }}>
                    {String(record.automation_recommendation ?? '')}
                  </Typography>
                  <Button variant="outlined" fullWidth onClick={() => setSelected(key)}>
                    OPEN REVIEW
                  </Button>
                </SectionCard>
              </Grid>
            )
          })}
        </Grid>
      )}

      {selected && (
        <ReviewDialog
          eventKey={selected}
          onClose={() => setSelected(null)}
          onSaved={() => {
            setSelected(null)
            reload()
            reloadQueues()
          }}
        />
      )}
    </Box>
  )
}

interface PendingDecision {
  decision: 'ACCEPT' | 'REJECT' | 'EDIT'
  value?: unknown
  override_reason?: string
  comment?: string
}

function ReviewDialog({
  eventKey,
  onClose,
  onSaved,
}: {
  eventKey: string
  onClose: () => void
  onSaved: () => void
}) {
  const { data, error, loading } = useApi<ReviewDetail>(() => api.reviewDetail(eventKey), [eventKey])
  const [pending, setPending] = useState<Record<string, PendingDecision>>({})
  const [saveError, setSaveError] = useState<unknown>(null)
  const [saving, setSaving] = useState(false)

  const blocking = data?.blocking_error_count ?? 0

  const decidedCount = Object.keys(pending).length
  const safetyPending = useMemo(
    () =>
      Object.entries(pending).filter(([fieldName, decision]) => {
        const field = data?.fields.find((f) => f.field === fieldName)
        return field?.safety_critical && decision.decision !== 'ACCEPT'
      }),
    [pending, data],
  )

  const submit = async (finalize: boolean, finalStatus?: string) => {
    setSaving(true)
    setSaveError(null)
    try {
      await api.submitDecisions(eventKey, {
        canonical_event_key: eventKey,
        decisions: Object.entries(pending).map(([field_name, decision]) => ({
          field_name,
          decision: decision.decision,
          value: decision.value ?? null,
          override_reason: decision.override_reason ?? null,
          comment: decision.comment ?? null,
        })),
        finalize,
        final_status: finalStatus ?? null,
      })
      onSaved()
    } catch (cause) {
      setSaveError(cause)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open onClose={onClose} maxWidth="xl" fullWidth>
      <DialogTitle>
        Human review — {data?.event_reference ?? eventKey.slice(0, 12)}
        {data && (
          <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
            <StatusChip value={data.status} />
            <ConfidenceBadge value={data.overall_confidence} />
            {blocking > 0 && <Chip label={`${blocking} blocking error(s)`} color="error" />}
          </Stack>
        )}
      </DialogTitle>
      <DialogContent>
        {loading && !data && <LoadingBlock />}
        <ErrorBanner error={error} />
        <ErrorBanner error={saveError} title="The decision was not recorded" />

        {data && (
          <>
            <Alert severity="info" sx={{ mb: 2 }}>
              {data.automation_recommendation}
            </Alert>

            {data.failures.length > 0 && (
              <SectionCard title={`Rule findings (${data.failures.length})`} dense>
                <Stack spacing={0.5} sx={{ mb: 2 }}>
                  {data.failures.map((failure) => (
                    <Stack key={failure.rule_id} direction="row" spacing={1} alignItems="flex-start">
                      <StatusChip value={failure.severity} />
                      <Box>
                        <Typography variant="body2">{failure.message}</Typography>
                        {failure.recommended_correction && (
                          <Typography variant="caption" color="text.secondary">
                            {failure.recommended_correction}
                          </Typography>
                        )}
                      </Box>
                    </Stack>
                  ))}
                </Stack>
              </SectionCard>
            )}

            <Stack spacing={1} sx={{ mt: 2 }}>
              {data.fields.map((field) => (
                <FieldRow
                  key={field.field}
                  field={field}
                  pending={pending[field.field]}
                  onChange={(decision) =>
                    setPending((state) => {
                      const next = { ...state }
                      if (decision === null) delete next[field.field]
                      else next[field.field] = decision
                      return next
                    })
                  }
                />
              ))}
            </Stack>
          </>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Typography variant="caption" color="text.secondary" sx={{ flexGrow: 1 }}>
          {decidedCount} field decision(s) pending
          {safetyPending.length > 0
            ? ` · ${safetyPending.length} safety-critical override(s) need a reason and senior approval`
            : ''}
        </Typography>
        <Button onClick={onClose}>CANCEL</Button>
        <Button onClick={() => submit(false)} disabled={saving || decidedCount === 0}>
          SAVE DECISIONS
        </Button>
        <Button
          color="error"
          onClick={() => submit(true, 'REJECTED_BY_TESTER')}
          disabled={saving}
        >
          REJECT RECORD
        </Button>
        <Button
          variant="contained"
          onClick={() => submit(true, 'CONFIRMED_BY_TESTER')}
          disabled={saving || blocking > 0}
          title={blocking > 0 ? 'Resolve the blocking errors before confirming.' : undefined}
        >
          CONFIRM RECORD
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function FieldRow({
  field,
  pending,
  onChange,
}: {
  field: ReviewField
  pending?: PendingDecision
  onChange: (decision: PendingDecision | null) => void
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(String(field.recommended ?? ''))

  const requiresReason = field.safety_critical && pending && pending.decision !== 'ACCEPT'

  return (
    <Box
      sx={{
        border: '1px solid',
        borderColor: field.safety_critical ? 'warning.dark' : 'divider',
        borderRadius: 1,
        p: 1.25,
      }}
    >
      <Grid container spacing={1} alignItems="center">
        <Grid item xs={12} md={2}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {field.field}
          </Typography>
          {field.safety_critical && <Chip label="safety-critical" color="warning" sx={{ height: 18, fontSize: '0.6rem' }} />}
        </Grid>
        <Grid item xs={6} md={2}>
          <Typography variant="caption" color="text.secondary" component="div">
            ORIGINAL
          </Typography>
          <Typography variant="body2">{render(field.original)}</Typography>
        </Grid>
        <Grid item xs={6} md={2}>
          <Typography variant="caption" color="text.secondary" component="div">
            RECOMMENDED
          </Typography>
          <Typography variant="body2">{render(field.recommended)}</Typography>
        </Grid>
        <Grid item xs={6} md={1.5}>
          <ConfidenceBadge value={field.confidence} band={field.band} explanation={field.explanation?.narrative} />
        </Grid>
        <Grid item xs={6} md={2}>
          <Typography variant="caption" color="text.secondary" component="div">
            REVIEWER
          </Typography>
          <Typography variant="body2">
            {pending ? `${pending.decision}${pending.value !== undefined ? `: ${render(pending.value)}` : ''}` : render(field.reviewer_value)}
          </Typography>
        </Grid>
        <Grid item xs={12} md={2.5}>
          <Stack direction="row" spacing={0.5}>
            <Button
              size="small"
              variant={pending?.decision === 'ACCEPT' ? 'contained' : 'outlined'}
              onClick={() => onChange({ decision: 'ACCEPT' })}
            >
              ACCEPT
            </Button>
            <Button
              size="small"
              variant={pending?.decision === 'REJECT' ? 'contained' : 'outlined'}
              color="error"
              onClick={() => onChange({ decision: 'REJECT', override_reason: pending?.override_reason })}
            >
              REJECT
            </Button>
            <Button size="small" variant={pending?.decision === 'EDIT' ? 'contained' : 'outlined'} onClick={() => setEditing((v) => !v)}>
              EDIT
            </Button>
          </Stack>
        </Grid>
      </Grid>

      <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 0.5 }}>
        {field.reason}
      </Typography>

      {field.alternatives.length > 0 && (
        <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }} flexWrap="wrap" useFlexGap>
          {field.alternatives.slice(0, 4).map((alternative, index) => (
            <Chip
              key={index}
              label={`alt: ${render((alternative as Record<string, unknown>).value)}`}
              onClick={() => onChange({ decision: 'EDIT', value: (alternative as Record<string, unknown>).value })}
              sx={{ height: 20, fontSize: '0.65rem' }}
            />
          ))}
        </Stack>
      )}

      {editing && (
        <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
          <TextField
            fullWidth
            label="Reviewer value"
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
          <Button
            onClick={() => {
              onChange({ decision: 'EDIT', value, override_reason: pending?.override_reason })
              setEditing(false)
            }}
          >
            APPLY
          </Button>
        </Stack>
      )}

      {requiresReason && (
        <TextField
          fullWidth
          sx={{ mt: 1 }}
          label="Override reason (required)"
          value={pending?.override_reason ?? ''}
          onChange={(event) => onChange({ ...pending!, override_reason: event.target.value })}
          helperText="Safety-critical overrides need a recorded reason of at least 15 characters and senior approval."
          error={(pending?.override_reason ?? '').trim().length < 15}
        />
      )}
    </Box>
  )
}

function render(value: unknown): string {
  if (value === null || value === undefined || value === '') return '(blank)'
  if (Array.isArray(value)) return value.length > 4 ? `[${value.length} items]` : JSON.stringify(value)
  if (typeof value === 'object') return JSON.stringify(value).slice(0, 50)
  return String(value)
}

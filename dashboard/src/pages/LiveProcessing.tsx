import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import PendingIcon from '@mui/icons-material/RadioButtonUnchecked'
import PlayIcon from '@mui/icons-material/PlayCircleOutline'
import {
  Alert,
  Box,
  Button,
  Chip,
  LinearProgress,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useEffect, useState } from 'react'

import {
  ErrorBanner,
  Metric,
  PageHeader,
  SectionCard,
  StatusChip,
  formatDuration,
} from '@/components/common'
import { useApi } from '@/hooks/useApi'
import { api } from '@/services/api'
import { subscribeToRun } from '@/services/ws'
import { useAppStore } from '@/stores/useAppStore'
import type { ProgressFrame } from '@/types'

const STAGE_LABELS: Record<string, string> = {
  connection: 'Connection',
  metadata: 'Metadata',
  country_filter: 'Country filter',
  scenario_filter: 'Scenario filter',
  sensor_check: 'Sensor check',
  synchronization: 'Synchronisation',
  map_context: 'Map context',
  scene_analysis: 'Scene analysis',
  behavior_analysis: 'Behaviour analysis',
  validation: 'Validation',
  evidence: 'Evidence',
  csv: 'CSV',
}

export function LiveProcessing() {
  const activeRunId = useAppStore((state) => state.activeRunId)
  const setActiveRun = useAppStore((state) => state.setActiveRun)
  const [frame, setFrame] = useState<ProgressFrame | null>(null)
  const [actionError, setActionError] = useState<unknown>(null)

  const { data: runs, reload } = useApi(() => api.runs())
  const runId = activeRunId ?? runs?.runs?.[0]?.run_id ?? null
  const run = runs?.runs?.find((r) => r.run_id === runId) ?? null

  useEffect(() => {
    if (!runId) return undefined
    setFrame(null)
    return subscribeToRun(runId, (next) => setFrame(next))
  }, [runId])

  useEffect(() => {
    if (frame?.status && ['COMPLETED', 'FAILED', 'CANCELLED'].includes(frame.status)) reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frame?.status])

  const stageOrder = frame?.stage_order ?? run?.stage_order ?? Object.keys(STAGE_LABELS)
  const completed = new Set(frame?.completed_stages ?? run?.completed_stages ?? [])
  const currentStage = frame?.stage ?? run?.stage

  const discovered = frame?.records_discovered ?? run?.counters.records_discovered ?? 0
  const processed = frame?.records_processed ?? run?.counters.records_processed ?? 0
  const percent = discovered > 0 ? (processed / discovered) * 100 : 0

  const control = async (action: 'pause' | 'resume' | 'cancel') => {
    if (!runId) return
    setActionError(null)
    try {
      if (action === 'pause') await api.pauseRun(runId)
      else if (action === 'resume') await api.resumeRun(runId)
      else await api.cancelRun(runId)
      reload()
    } catch (cause) {
      setActionError(cause)
    }
  }

  return (
    <Box>
      <PageHeader
        title="Live Processing"
        subtitle="Live pipeline view. Progress is streamed over a WebSocket and survives a page reload."
        actions={
          <>
            <TextField
              select
              size="small"
              label="Run"
              value={runId ?? ''}
              onChange={(event) => setActiveRun(event.target.value || null)}
              sx={{ minWidth: 240 }}
            >
              {(runs?.runs ?? []).map((r) => (
                <MenuItem key={r.run_id} value={r.run_id}>
                  {r.run_id} · {r.status}
                </MenuItem>
              ))}
            </TextField>
            <Button onClick={() => control('pause')} disabled={!run?.active}>
              PAUSE
            </Button>
            <Button onClick={() => control('resume')}>RESUME</Button>
            <Button color="error" onClick={() => control('cancel')} disabled={!run?.active}>
              CANCEL
            </Button>
          </>
        }
      />

      <ErrorBanner error={actionError} />

      {!runId ? (
        <Alert severity="info">No run has been started yet. Configure one on the Scout Setup page.</Alert>
      ) : (
        <Stack spacing={2}>
          <SectionCard>
            <Stack direction="row" spacing={4} flexWrap="wrap" useFlexGap alignItems="center">
              <Metric label="Run" value={runId.slice(0, 20)} />
              <Metric label="Status" value={<StatusChip value={frame?.status ?? run?.status} />} />
              <Metric label="Discovered" value={discovered} />
              <Metric label="Processed" value={processed} colour="#63b3ed" />
              <Metric label="Candidate issues" value={frame?.candidate_issue_count ?? run?.counters.candidate_issue_count ?? 0} colour="#f6ad55" />
              <Metric label="Blocking" value={frame?.blocking_error_count ?? run?.counters.blocking_error_count ?? 0} colour="#fc8181" />
              <Metric label="Review required" value={frame?.review_required_count ?? run?.counters.review_required_count ?? 0} colour="#f6ad55" />
              <Metric label="Filtered out" value={frame?.filtered_out ?? 0} />
              <Metric label="Elapsed" value={formatDuration(frame?.elapsed_s ?? run?.elapsed_seconds)} />
              <Metric
                label="Remaining"
                value={formatDuration(frame?.estimated_remaining_s ?? null)}
                hint="estimated"
              />
              {run?.dry_run && <Chip label="DRY RUN" color="warning" />}
            </Stack>
            <Box sx={{ mt: 2 }}>
              <LinearProgress variant={discovered ? 'determinate' : 'indeterminate'} value={percent} />
              <Typography variant="caption" color="text.secondary">
                {discovered ? `${processed} / ${discovered} events` : 'discovering events…'}
                {frame?.current_event_ref ? ` · current: ${frame.current_event_ref} (${frame.current_status})` : ''}
              </Typography>
            </Box>
          </SectionCard>

          <SectionCard title="Pipeline">
            <Stack spacing={0.5}>
              {stageOrder.map((stage) => {
                const done = completed.has(stage)
                const active = currentStage === stage && !done
                return (
                  <Stack key={stage} direction="row" spacing={1} alignItems="center">
                    {done ? (
                      <CheckCircleIcon fontSize="small" sx={{ color: 'success.main' }} />
                    ) : active ? (
                      <PlayIcon fontSize="small" sx={{ color: 'primary.main' }} />
                    ) : (
                      <PendingIcon fontSize="small" sx={{ color: 'text.disabled' }} />
                    )}
                    <Typography
                      variant="body2"
                      sx={{ color: done ? 'success.main' : active ? 'primary.main' : 'text.secondary' }}
                    >
                      {STAGE_LABELS[stage] ?? stage}
                    </Typography>
                  </Stack>
                )
              })}
            </Stack>
          </SectionCard>

          {frame?.outputs && Object.keys(frame.outputs).length > 0 && (
            <SectionCard title="Outputs">
              <Box component="pre" sx={{ fontSize: '0.72rem', overflow: 'auto', maxHeight: 260 }}>
                {JSON.stringify(frame.outputs, null, 2)}
              </Box>
            </SectionCard>
          )}

          {run?.message && <Alert severity="info">{run.message}</Alert>}
        </Stack>
      )}
    </Box>
  )
}

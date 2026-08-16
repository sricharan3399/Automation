import { Box, Button, Chip, Stack, Typography } from '@mui/material'
import { useNavigate } from 'react-router-dom'

import { VirtualTable, type Column } from '@/components/VirtualTable'
import {
  ErrorBanner,
  LoadingBlock,
  PageHeader,
  SectionCard,
  StatusChip,
  formatDateTime,
  formatDuration,
} from '@/components/common'
import { useApi, usePolling } from '@/hooks/useApi'
import { api } from '@/services/api'
import { useAppStore } from '@/stores/useAppStore'
import type { RunSummary } from '@/types'

export function AutomationRuns() {
  const navigate = useNavigate()
  const setActiveRun = useAppStore((state) => state.setActiveRun)
  const { data, error, loading, reload } = useApi(() => api.runs())
  usePolling(reload, 5000)

  const control = async (runId: string, action: 'pause' | 'resume' | 'cancel' | 'repeat') => {
    if (action === 'pause') await api.pauseRun(runId)
    else if (action === 'resume') await api.resumeRun(runId)
    else if (action === 'cancel') await api.cancelRun(runId)
    else {
      const run = await api.repeatRun(runId)
      setActiveRun(run.run_id)
      navigate('/live')
      return
    }
    reload()
  }

  const columns: Column<RunSummary>[] = [
    { key: 'run', header: 'Run', width: 200, render: (row) => row.run_id },
    {
      key: 'status',
      header: 'Status',
      width: 130,
      render: (row) => (
        <Stack direction="row" spacing={0.5} alignItems="center">
          <StatusChip value={row.status} />
          {row.dry_run && <Chip label="DRY" sx={{ height: 18, fontSize: '0.6rem' }} />}
        </Stack>
      ),
    },
    { key: 'stage', header: 'Stage', width: 130, render: (row) => row.stage },
    { key: 'country', header: 'Country', width: 90, render: (row) => String((row.query as Record<string, unknown>)?.country_code ?? 'Any') },
    { key: 'discovered', header: 'Found', width: 80, align: 'right', render: (row) => row.counters.records_discovered },
    { key: 'processed', header: 'Processed', width: 90, align: 'right', render: (row) => row.counters.records_processed },
    { key: 'issues', header: 'Issues', width: 80, align: 'right', render: (row) => row.counters.candidate_issue_count },
    { key: 'blocking', header: 'Blocking', width: 90, align: 'right', render: (row) => row.counters.blocking_error_count },
    { key: 'review', header: 'Review', width: 80, align: 'right', render: (row) => row.counters.review_required_count },
    { key: 'csv', header: 'CSV rows', width: 90, align: 'right', render: (row) => row.counters.csv_rows_created },
    { key: 'elapsed', header: 'Elapsed', width: 100, align: 'right', render: (row) => formatDuration(row.elapsed_seconds) },
    { key: 'created', header: 'Created', width: 170, render: (row) => formatDateTime(row.created_at) },
    {
      key: 'actions',
      header: 'Actions',
      width: 280,
      render: (row) => (
        <Stack direction="row" spacing={0.5}>
          <Button
            size="small"
            onClick={() => {
              setActiveRun(row.run_id)
              navigate('/live')
            }}
          >
            WATCH
          </Button>
          {row.active && row.status === 'RUNNING' && (
            <Button size="small" onClick={() => control(row.run_id, 'pause')}>
              PAUSE
            </Button>
          )}
          {(row.status === 'PAUSED' || row.status === 'CANCELLED') && (
            <Button size="small" onClick={() => control(row.run_id, 'resume')}>
              RESUME
            </Button>
          )}
          {row.active && (
            <Button size="small" color="error" onClick={() => control(row.run_id, 'cancel')}>
              CANCEL
            </Button>
          )}
          <Button size="small" onClick={() => control(row.run_id, 'repeat')}>
            REPEAT
          </Button>
        </Stack>
      ),
    },
  ]

  return (
    <Box>
      <PageHeader
        title="Automation Runs"
        subtitle="A cancelled or paused run keeps its checkpoint and can be resumed without reprocessing."
        actions={
          <>
            <Button onClick={reload}>REFRESH</Button>
            <Button variant="contained" onClick={() => navigate('/scout-setup')}>
              NEW RUN
            </Button>
          </>
        }
      />

      <ErrorBanner error={error} onRetry={reload} />

      {(data?.resumable ?? []).length > 0 && (
        <SectionCard title="Resumable checkpoints" dense>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            {(data?.resumable ?? []).map((runId) => (
              <Chip key={runId} label={runId} onClick={() => control(runId, 'resume')} />
            ))}
          </Stack>
          <Typography variant="caption" color="text.secondary">
            Click a checkpoint to resume that run from where it stopped.
          </Typography>
        </SectionCard>
      )}

      <Box sx={{ mt: 2 }}>
        {loading && !data ? (
          <LoadingBlock label="Loading runs…" />
        ) : (
          <VirtualTable
            rows={data?.runs ?? []}
            columns={columns}
            keyOf={(row) => row.run_id}
            height="calc(100vh - 320px)"
            emptyMessage="No runs yet. Configure one on the Scout Setup page."
          />
        )}
      </Box>
    </Box>
  )
}

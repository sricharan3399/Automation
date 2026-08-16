import {
  Box,
  Button,
  Chip,
  Drawer,
  Grid,
  MenuItem,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from '@mui/material'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { MapView } from '@/components/MapView'
import { VirtualTable, type Column } from '@/components/VirtualTable'
import {
  ConfidenceBadge,
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  PageHeader,
  SectionCard,
  StatusChip,
  formatDateTime,
} from '@/components/common'
import { useApi } from '@/hooks/useApi'
import { api } from '@/services/api'
import type { EventSummary } from '@/types'

const STATUSES = [
  'CANDIDATE',
  'AUTO_PREPARED',
  'REVIEW_REQUIRED',
  'CONFIRMED_BY_TESTER',
  'REJECTED_BY_TESTER',
  'BLOCKED_DATA_ERROR',
  'SENIOR_REVIEW_REQUIRED',
]

export function EventExplorer() {
  const [params, setParams] = useSearchParams()
  const [search, setSearch] = useState(params.get('search') ?? '')
  const [status, setStatus] = useState(params.get('status') ?? '')
  const [countryCode, setCountryCode] = useState(params.get('country_code') ?? '')
  const [selected, setSelected] = useState<string | null>(null)

  const { data, error, loading, reload } = useApi(
    () => api.events({ search, status, country_code: countryCode, limit: 500 }),
    [search, status, countryCode],
  )

  const columns: Column<EventSummary>[] = [
    { key: 'ref', header: 'Event', width: 180, render: (row) => row.event_reference },
    { key: 'country', header: 'Country', width: 80, render: (row) => row.country_code ?? '—' },
    { key: 'city', header: 'City', width: 110, render: (row) => row.city ?? '—' },
    { key: 'object', header: 'Object', width: 110, render: (row) => row.object_types.join(', ') || '—' },
    { key: 'road', header: 'Road', width: 110, render: (row) => row.road_type ?? '—' },
    { key: 'lanes', header: 'Lanes', width: 60, align: 'right', render: (row) => row.lane_count ?? '—' },
    {
      key: 'intersection',
      header: 'Intersection',
      width: 150,
      render: (row) => row.intersection_type ?? '—',
    },
    {
      key: 'errors',
      header: 'Findings',
      width: 200,
      render: (row) =>
        row.abnormality_categories.length ? (
          <Stack direction="row" spacing={0.5}>
            {row.abnormality_categories.slice(0, 3).map((category) => (
              <Chip key={category} label={category} sx={{ height: 18, fontSize: '0.62rem' }} />
            ))}
            {row.abnormality_categories.length > 3 && (
              <Chip label={`+${row.abnormality_categories.length - 3}`} sx={{ height: 18, fontSize: '0.62rem' }} />
            )}
          </Stack>
        ) : (
          '—'
        ),
    },
    {
      key: 'confidence',
      header: 'Confidence',
      width: 110,
      render: (row) => <ConfidenceBadge value={row.overall_confidence} />,
    },
    {
      key: 'review',
      header: 'Review',
      width: 80,
      render: (row) => (row.review_required ? 'required' : '—'),
    },
    { key: 'status', header: 'Status', width: 190, render: (row) => <StatusChip value={row.status} /> },
  ]

  return (
    <Box>
      <PageHeader
        title="Event Explorer"
        subtitle={`${data?.total ?? 0} record(s) match the current filter.`}
        actions={<Button onClick={reload}>REFRESH</Button>}
      />

      <ErrorBanner error={error} onRetry={reload} />

      <SectionCard dense>
        <Grid container spacing={1.5}>
          <Grid item xs={12} md={4}>
            <TextField
              fullWidth
              label="Search"
              placeholder="event reference, key, city, region"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </Grid>
          <Grid item xs={6} md={3}>
            <TextField
              select
              fullWidth
              label="Status"
              value={status}
              onChange={(event) => {
                setStatus(event.target.value)
                setParams(event.target.value ? { status: event.target.value } : {})
              }}
            >
              <MenuItem value="">All</MenuItem>
              {STATUSES.map((value) => (
                <MenuItem key={value} value={value}>
                  {value}
                </MenuItem>
              ))}
            </TextField>
          </Grid>
          <Grid item xs={6} md={2}>
            <TextField
              fullWidth
              label="Country code"
              value={countryCode}
              onChange={(event) => setCountryCode(event.target.value.toUpperCase())}
            />
          </Grid>
        </Grid>
      </SectionCard>

      <Box sx={{ mt: 2 }}>
        {loading && !data ? (
          <LoadingBlock label="Loading events…" />
        ) : (
          <VirtualTable
            rows={data?.events ?? []}
            columns={columns}
            keyOf={(row) => row.canonical_event_key}
            selectedKey={selected ?? undefined}
            onRowClick={(row) => setSelected(row.canonical_event_key)}
            height="calc(100vh - 330px)"
            emptyMessage="No events match this filter. Run a scout, or widen the filter."
          />
        )}
      </Box>

      <Drawer
        anchor="right"
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        PaperProps={{ sx: { width: { xs: '100%', lg: '72%' } } }}
      >
        {selected && <EventDetail eventKey={selected} onClose={() => setSelected(null)} />}
      </Drawer>
    </Box>
  )
}

const TABS = [
  'Summary',
  'Timeline',
  'Sensors',
  'Map',
  'Objects',
  'Behavior',
  'Validation',
  'Recommendations',
  'Evidence',
  'Review History',
  'Audit',
]

export function EventDetail({ eventKey, onClose }: { eventKey: string; onClose?: () => void }) {
  const [tab, setTab] = useState(0)
  const { data, error, loading } = useApi(() => api.eventDetail(eventKey), [eventKey])

  if (loading && !data) return <LoadingBlock label="Loading event…" />
  if (error) return <Box sx={{ p: 2 }}><ErrorBanner error={error} /></Box>
  if (!data) return null

  const summary = data.summary as Record<string, unknown>
  const analysis = data.analysis as Record<string, unknown>
  const geometry = (analysis?.geometry ?? {}) as Record<string, unknown>

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
        <Box>
          <Typography variant="h5">{String(summary.event_reference)}</Typography>
          <Typography variant="caption" color="text.secondary">
            {String(summary.country_code ?? '')} · {String(summary.city ?? '')} · record v
            {String(summary.record_version)} · {formatDateTime(summary.updated_at as string)}
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} alignItems="center">
          <StatusChip value={String(summary.status)} />
          {onClose && <Button onClick={onClose}>CLOSE</Button>}
        </Stack>
      </Stack>

      <Tabs value={tab} onChange={(_, value) => setTab(value)} variant="scrollable" scrollButtons="auto">
        {TABS.map((label) => (
          <Tab key={label} label={label} sx={{ minHeight: 40, fontSize: '0.75rem' }} />
        ))}
      </Tabs>

      <Box sx={{ mt: 2 }}>
        {tab === 0 && <JsonBlock title="Summary" value={summary} />}
        {tab === 1 && <TrajectoryTab points={data.trajectory as never[]} />}
        {tab === 2 && <StreamsTab streams={data.streams as never[]} />}
        {tab === 3 && (
          <MapView
            trajectory={(data.trajectory ?? []) as never[]}
            features={(data.map_features ?? []) as never[]}
            selectedJunctionId={(geometry.target_junction as { feature_id?: string })?.feature_id}
            entryEdge={geometry.entry_edge as never}
            exitEdge={geometry.exit_edge as never}
            markers={(((geometry.markers ?? []) as { name: string; t: number; available: boolean }[]) ?? [])
              .filter((m) => m.available)
              .map((m) => ({ name: m.name, t: m.t }))}
          />
        )}
        {tab === 4 && <DetectionsTab detections={data.detections as never[]} />}
        {tab === 5 && <JsonBlock title="Behaviour" value={analysis?.behavior} />}
        {tab === 6 && <ValidationTab validation={data.validation as never[]} />}
        {tab === 7 && <RecommendationsTab recommendations={data.recommendations as never[]} />}
        {tab === 8 && <EvidenceTab evidence={data.evidence as never[]} />}
        {tab === 9 && <JsonBlock title="Review history" value={data.review_history} />}
        {tab === 10 && <JsonBlock title="Audit" value={data.audit} />}
      </Box>
    </Box>
  )
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <SectionCard title={title} dense>
      <Box
        component="pre"
        sx={{ fontSize: '0.72rem', maxHeight: 520, overflow: 'auto', m: 0, whiteSpace: 'pre-wrap' }}
      >
        {JSON.stringify(value, null, 2)}
      </Box>
    </SectionCard>
  )
}

function TrajectoryTab({ points }: { points: { t: number; speed_mps?: number | null; arc_length_m?: number | null }[] }) {
  if (!points?.length) return <EmptyState title="No trajectory was stored for this event." />
  const width = 900
  const height = 220
  const maxT = Math.max(...points.map((p) => p.t)) || 1
  const speeds = points.map((p) => p.speed_mps ?? 0)
  const maxV = Math.max(...speeds, 1)
  const path = points
    .map((p, index) => {
      const x = (p.t / maxT) * width
      const y = height - ((p.speed_mps ?? 0) / maxV) * (height - 20) - 10
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  return (
    <SectionCard title="Speed over time" subtitle={`${points.length} stored pose samples`}>
      <Box component="svg" viewBox={`0 0 ${width} ${height}`} sx={{ width: '100%' }}>
        <path d={path} stroke="#48bb78" strokeWidth={2} fill="none" />
        <text x={4} y={14} fill="#a0aec0" fontSize={11}>
          {maxV.toFixed(1)} m/s
        </text>
        <text x={width - 4} y={height - 4} fill="#a0aec0" fontSize={11} textAnchor="end">
          {maxT.toFixed(1)} s
        </text>
      </Box>
    </SectionCard>
  )
}

function StreamsTab({ streams }: { streams: Record<string, unknown>[] }) {
  if (!streams?.length) return <EmptyState title="No stream health was recorded." />
  return (
    <SectionCard title="Sensor streams" dense>
      <VirtualTable
        rows={streams}
        keyOf={(row, index) => `${row.stream_type}-${row.camera_position}-${index}`}
        height={420}
        columns={[
          { key: 'stream', header: 'Stream', width: 180, render: (row) => `${row.stream_type}${row.camera_position ? `:${row.camera_position}` : ''}` },
          { key: 'req', header: 'Requirement', width: 110, render: (row) => String(row.requirement) },
          { key: 'status', header: 'Status', width: 110, render: (row) => <StatusChip value={String(row.availability_status).toUpperCase()} label={String(row.availability_status)} /> },
          { key: 'avail', header: 'Availability', width: 110, align: 'right', render: (row) => (row.availability_pct === null ? '—' : `${Number(row.availability_pct).toFixed(1)}%`) },
          { key: 'offset', header: 'Offset ms', width: 100, align: 'right', render: (row) => (row.sync_offset_ms === null ? '—' : Number(row.sync_offset_ms).toFixed(1)) },
          { key: 'gap', header: 'Max gap ms', width: 110, align: 'right', render: (row) => (row.max_gap_ms === null ? '—' : Number(row.max_gap_ms).toFixed(0)) },
          { key: 'quality', header: 'Quality', width: 90, align: 'right', render: (row) => (row.quality_score === null ? '—' : Number(row.quality_score).toFixed(2)) },
          { key: 'issues', header: 'Issues', render: (row) => ((row.issues as string[]) ?? []).join(', ') || '—' },
        ]}
      />
    </SectionCard>
  )
}

function DetectionsTab({ detections }: { detections: Record<string, unknown>[] }) {
  if (!detections?.length) return <EmptyState title="No detections were supplied for this event." />
  return (
    <SectionCard title={`Detections (${detections.length})`} dense>
      <VirtualTable
        rows={detections}
        keyOf={(_, index) => String(index)}
        height={460}
        columns={[
          { key: 't', header: 't (s)', width: 80, align: 'right', render: (row) => Number(row.t).toFixed(2) },
          { key: 'source', header: 'Source', width: 100, render: (row) => String(row.source) },
          { key: 'type', header: 'Object', width: 120, render: (row) => String(row.object_type) },
          { key: 'track', header: 'Track', width: 160, render: (row) => String(row.track_id ?? '—') },
          { key: 'camera', header: 'Camera', width: 110, render: (row) => String(row.camera ?? '—') },
          { key: 'state', header: 'State', width: 100, render: (row) => String(row.state ?? '—') },
          { key: 'distance', header: 'Distance', width: 100, align: 'right', render: (row) => (row.distance_m === null ? '—' : `${Number(row.distance_m).toFixed(1)} m`) },
          { key: 'confidence', header: 'Confidence', width: 100, align: 'right', render: (row) => (row.confidence === null ? '—' : Number(row.confidence).toFixed(2)) },
        ]}
      />
    </SectionCard>
  )
}

function ValidationTab({ validation }: { validation: Record<string, unknown>[] }) {
  const failures = validation.filter((v) => !v.passed && !v.skipped)
  const skipped = validation.filter((v) => v.skipped)
  return (
    <Stack spacing={2}>
      <SectionCard title={`Findings (${failures.length})`} dense>
        {failures.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No rule failed for this event.
          </Typography>
        ) : (
          <Stack spacing={1}>
            {failures.map((failure, index) => (
              <Box key={index} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 1, p: 1 }}>
                <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                  <StatusChip value={String(failure.severity)} />
                  <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                    {String(failure.rule_id)}
                  </Typography>
                  <Chip label={String(failure.category)} sx={{ height: 18, fontSize: '0.62rem' }} />
                  {Boolean(failure.blocks_export) && <Chip label="blocks export" color="error" sx={{ height: 18, fontSize: '0.62rem' }} />}
                </Stack>
                <Typography variant="body2">{String(failure.message)}</Typography>
                {failure.recommended_correction ? (
                  <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 0.5 }}>
                    Recommended: {String(failure.recommended_correction)}
                  </Typography>
                ) : null}
              </Box>
            ))}
          </Stack>
        )}
      </SectionCard>

      <SectionCard
        title={`Not evaluated (${skipped.length})`}
        subtitle="A skipped rule is not a passing rule. These were not checked, and the reason is recorded."
        dense
      >
        <Stack spacing={0.5}>
          {skipped.map((entry, index) => (
            <Typography key={index} variant="caption" color="text.secondary">
              <strong>{String(entry.rule_id)}</strong>: {String(entry.skip_reason)}
            </Typography>
          ))}
          {skipped.length === 0 && (
            <Typography variant="body2" color="text.secondary">
              Every applicable rule was evaluated.
            </Typography>
          )}
        </Stack>
      </SectionCard>
    </Stack>
  )
}

function RecommendationsTab({ recommendations }: { recommendations: Record<string, unknown>[] }) {
  return (
    <SectionCard title={`Field recommendations (${recommendations.length})`} dense>
      <VirtualTable
        rows={recommendations}
        keyOf={(row) => String(row.field_name)}
        height={480}
        columns={[
          { key: 'field', header: 'Field', width: 200, render: (row) => String(row.field_name) },
          { key: 'recommended', header: 'Recommended', width: 220, render: (row) => formatValue(row.recommended_value) },
          {
            key: 'confidence',
            header: 'Confidence',
            width: 150,
            render: (row) => (
              <ConfidenceBadge
                value={Number(row.confidence)}
                band={String(row.band)}
                explanation={(row.explanation as { narrative?: string })?.narrative}
              />
            ),
          },
          { key: 'auto', header: 'Auto', width: 70, render: (row) => (row.auto_selected ? 'yes' : 'no') },
          { key: 'safety', header: 'Safety', width: 80, render: (row) => (row.safety_critical ? 'critical' : '—') },
          { key: 'reason', header: 'Reason', render: (row) => String(row.reason ?? '') },
        ]}
      />
    </SectionCard>
  )
}

function EvidenceTab({ evidence }: { evidence: Record<string, unknown>[] }) {
  return (
    <SectionCard title={`Evidence (${evidence.length})`} dense>
      <Stack spacing={1}>
        {evidence.map((item, index) => (
          <Box key={index} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 1, p: 1 }}>
            <Stack direction="row" justifyContent="space-between" alignItems="center">
              <Typography variant="body2">
                {String(item.purpose)} · {String(item.kind)}
                {item.t_rel_s !== null ? ` · t=${Number(item.t_rel_s).toFixed(2)}s` : ''}
              </Typography>
              <Stack direction="row" spacing={1}>
                {Boolean(item.redacted) && <Chip label="redacted" color="success" sx={{ height: 18, fontSize: '0.62rem' }} />}
                <StatusChip value={item.available ? 'CONNECTED' : 'NOT_CONFIGURED'} label={item.available ? 'available' : 'unavailable'} />
              </Stack>
            </Stack>
            {item.unavailable_reason ? (
              <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 0.5 }}>
                {String(item.unavailable_reason)}
              </Typography>
            ) : (
              <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 0.5, fontFamily: 'monospace' }}>
                {String(item.relative_path)} · {String(item.content_hash ?? '')}
              </Typography>
            )}
          </Box>
        ))}
      </Stack>
    </SectionCard>
  )
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '(blank)'
  if (Array.isArray(value)) return `[${value.length} items]`
  if (typeof value === 'object') return JSON.stringify(value).slice(0, 60)
  return String(value)
}

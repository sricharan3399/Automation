import {
  Alert,
  Box,
  Button,
  Chip,
  Grid,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useEffect, useState } from 'react'

import { MapView } from '@/components/MapView'
import {
  ConfidenceBadge,
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  PageHeader,
  SectionCard,
} from '@/components/common'
import { useApi } from '@/hooks/useApi'
import { api } from '@/services/api'

interface JunctionCandidate {
  feature_id: string
  score: number
  reasons: string[]
  distance_to_trajectory_m?: number | null
  trajectory_intersects: boolean
  polygon: number[][]
}

export function MapLaneSetup() {
  const [eventKey, setEventKey] = useState('')
  const { data: events } = useApi(() => api.events({ limit: 200 }))
  const { data, error, loading } = useApi(
    () => (eventKey ? api.eventDetail(eventKey) : Promise.resolve(null)),
    [eventKey],
  )

  useEffect(() => {
    if (!eventKey && events?.events?.length) setEventKey(events.events[0].canonical_event_key)
  }, [events, eventKey])

  const analysis = (data?.analysis ?? {}) as Record<string, unknown>
  const geometry = (analysis.geometry ?? {}) as Record<string, unknown>
  const target = geometry.target_junction as JunctionCandidate | null
  const alternatives = (geometry.alternatives ?? []) as JunctionCandidate[]
  const polygon = (geometry.polygon ?? {}) as Record<string, unknown>
  const markers = ((geometry.markers ?? []) as { name: string; t: number; available: boolean; confidence: number; distance_m?: number | null; unavailable_reason?: string | null }[])

  return (
    <Box>
      <PageHeader
        title="Map & Lane Setup"
        subtitle="Junction selection, polygon validation and entry/exit edges. Nothing here is applied automatically — the reviewer accepts, edits, redraws or rejects."
        actions={
          <TextField
            select
            size="small"
            label="Event"
            value={eventKey}
            onChange={(event) => setEventKey(event.target.value)}
            sx={{ minWidth: 260 }}
          >
            {(events?.events ?? []).map((event) => (
              <MenuItem key={event.canonical_event_key} value={event.canonical_event_key}>
                {event.event_reference} · {event.city ?? ''}
              </MenuItem>
            ))}
          </TextField>
        }
      />

      <ErrorBanner error={error} />

      {!eventKey ? (
        <EmptyState title="No processed events yet." hints={['Run a scout to populate the map view']} />
      ) : loading && !data ? (
        <LoadingBlock label="Loading geometry…" />
      ) : (
        <Grid container spacing={2}>
          <Grid item xs={12} xl={8}>
            <SectionCard title="Map">
              {geometry.available === false && (
                <Alert severity="warning" sx={{ mb: 2 }}>
                  {String(geometry.unavailable_reason ?? 'Geometry was not derived for this event.')}
                </Alert>
              )}
              <MapView
                trajectory={(data?.trajectory ?? []) as never[]}
                features={(data?.map_features ?? []) as never[]}
                selectedJunctionId={target?.feature_id}
                entryEdge={geometry.entry_edge as never}
                exitEdge={geometry.exit_edge as never}
                markers={markers.filter((m) => m.available).map((m) => ({ name: m.name, t: m.t }))}
              />
            </SectionCard>
          </Grid>

          <Grid item xs={12} xl={4}>
            <Stack spacing={2}>
              <SectionCard title="Target junction">
                {!target ? (
                  <Typography variant="body2" color="text.secondary">
                    No junction candidate could be ranked for this event.
                  </Typography>
                ) : (
                  <Stack spacing={1}>
                    <Stack direction="row" justifyContent="space-between" alignItems="center">
                      <Typography variant="h6">{target.feature_id}</Typography>
                      <ConfidenceBadge value={target.score} />
                    </Stack>
                    {target.reasons.map((reason) => (
                      <Typography key={reason} variant="caption" color="text.secondary">
                        • {reason}
                      </Typography>
                    ))}
                    <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                      <Button variant="contained" size="small">
                        ACCEPT
                      </Button>
                      <Button size="small">REJECT</Button>
                    </Stack>
                    <Typography variant="caption" color="text.secondary">
                      Decisions are recorded on the Review Queue, where they enter the audit trail.
                    </Typography>
                  </Stack>
                )}
              </SectionCard>

              {alternatives.length > 0 && (
                <SectionCard title="Alternative candidates" dense>
                  <Stack spacing={1}>
                    {alternatives.map((candidate) => (
                      <Box key={candidate.feature_id} sx={{ border: '1px solid', borderColor: 'divider', p: 1, borderRadius: 1 }}>
                        <Stack direction="row" justifyContent="space-between">
                          <Typography variant="body2">{candidate.feature_id}</Typography>
                          <ConfidenceBadge value={candidate.score} />
                        </Stack>
                        <Typography variant="caption" color="text.secondary">
                          {candidate.trajectory_intersects
                            ? 'trajectory intersects'
                            : `${candidate.distance_to_trajectory_m?.toFixed(1) ?? '?'} m from route`}
                        </Typography>
                      </Box>
                    ))}
                  </Stack>
                </SectionCard>
              )}

              <SectionCard title="Polygon" dense>
                <Stack spacing={0.5}>
                  <Row label="Unique points" value={String(polygon.unique_point_count ?? '—')} />
                  <Row label="Area" value={polygon.area_m2 ? `${Number(polygon.area_m2).toFixed(1)} m²` : '—'} />
                  <Row label="Valid" value={polygon.is_valid ? 'yes' : 'no'} />
                  <Row label="Self-intersecting" value={polygon.self_intersecting ? 'yes' : 'no'} />
                  <Row label="Trajectory crosses" value={polygon.trajectory_crosses ? 'yes' : 'no'} />
                  {((polygon.issues ?? []) as string[]).map((issue) => (
                    <Typography key={issue} variant="caption" color="error">
                      • {issue}
                    </Typography>
                  ))}
                  {Array.isArray(polygon.recommended_polygon) && (polygon.recommended_polygon as unknown[]).length > 0 && (
                    <Alert severity="info" sx={{ mt: 1 }}>
                      A corrected polygon ({(polygon.recommended_polygon as unknown[]).length} points) is
                      proposed. It is never applied automatically.
                    </Alert>
                  )}
                </Stack>
              </SectionCard>

              <SectionCard title="Derived timestamps" dense>
                <Stack spacing={0.5}>
                  {markers.map((marker) => (
                    <Stack key={marker.name} direction="row" justifyContent="space-between" alignItems="center">
                      <Typography variant="caption">{marker.name}</Typography>
                      {marker.available ? (
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Typography variant="caption">{marker.t?.toFixed(2)}s</Typography>
                          <ConfidenceBadge value={marker.confidence} />
                        </Stack>
                      ) : (
                        <Chip label="unavailable" sx={{ height: 18, fontSize: '0.6rem' }} />
                      )}
                    </Stack>
                  ))}
                </Stack>
              </SectionCard>
            </Stack>
          </Grid>
        </Grid>
      )}
    </Box>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <Stack direction="row" justifyContent="space-between">
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="caption">{value}</Typography>
    </Stack>
  )
}

import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  Grid,
  MenuItem,
  Stack,
  Switch,
  TextField,
  Typography,
} from '@mui/material'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { MultiSelectFilter } from '@/components/MultiSelectFilter'
import {
  ErrorBanner,
  LoadingBlock,
  Metric,
  PageHeader,
  SectionCard,
} from '@/components/common'
import { useApi } from '@/hooks/useApi'
import { api, type RunRequestPayload } from '@/services/api'
import { useAppStore } from '@/stores/useAppStore'
import type { FilterVocabulary, QueryPreview } from '@/types'

const LANE_OPTIONS = [1, 2, 3, 4, 5]

const ERROR_DETECTION = [
  'PERCEPTION',
  'TRACKING',
  'SENSOR',
  'SYNCHRONIZATION',
  'LOCALIZATION',
  'MAP',
  'GEOMETRY',
  'METADATA',
  'TIMESTAMP',
  'BEHAVIOR',
]

export function ScoutSetup() {
  const navigate = useNavigate()
  const store = useAppStore()
  const { query, connectionId, csvTemplateId, dryRun } = store

  const { data: vocabulary, error, loading, reload } = useApi<FilterVocabulary>(
    () => api.filters(connectionId ?? undefined),
    [connectionId],
  )
  const { data: connections } = useApi(() => api.connections())
  const { data: templates } = useApi(() => api.csvTemplates())

  const [estimate, setEstimate] = useState<{ count: number | null; exact: boolean; note: string } | null>(null)
  const [estimating, setEstimating] = useState(false)
  const [preview, setPreview] = useState<QueryPreview | null>(null)
  const [actionError, setActionError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const debounce = useRef<number>()

  const field = useCallback(
    (key: string) => vocabulary?.fields?.[key] ?? { values: [], origin: 'fallback' as const },
    [vocabulary],
  )

  // Live matching-record count. Debounced so every checkbox click does not
  // trigger a source query.
  useEffect(() => {
    if (debounce.current) window.clearTimeout(debounce.current)
    debounce.current = window.setTimeout(async () => {
      setEstimating(true)
      try {
        const result = await api.estimate(query, connectionId ?? undefined)
        setEstimate({ count: result.estimated_records, exact: result.is_exact, note: result.note })
        setActionError(null)
      } catch (cause) {
        setEstimate(null)
        setActionError(cause)
      } finally {
        setEstimating(false)
      }
    }, 450)
    return () => window.clearTimeout(debounce.current)
  }, [query, connectionId])

  const countries = vocabulary?.countries?.allowed ?? []
  const sourceCountryCodes = field('country_code').values

  const payload: RunRequestPayload = useMemo(
    () => ({
      profile_id: store.profileId,
      query,
      sensor_config: store.sensorConfig,
      connection_id: connectionId,
      csv_template_id: csvTemplateId,
      dry_run: dryRun,
      limit: query.limit ?? null,
      rule_overrides: store.ruleOverrides,
      error_detection: query.error_detection,
    }),
    [query, store.sensorConfig, connectionId, csvTemplateId, dryRun, store.profileId, store.ruleOverrides],
  )

  const runPreview = async () => {
    setActionError(null)
    try {
      setPreview(await api.previewRun(payload))
    } catch (cause) {
      setActionError(cause)
    }
  }

  const startRun = async () => {
    setStarting(true)
    setActionError(null)
    try {
      const run = await api.createRun(payload)
      store.setActiveRun(run.run_id)
      setPreview(null)
      navigate('/live')
    } catch (cause) {
      setActionError(cause)
    } finally {
      setStarting(false)
    }
  }

  if (loading && !vocabulary) return <LoadingBlock label="Loading filter vocabulary…" />

  return (
    <Box>
      <PageHeader
        title="Scout Setup"
        subtitle="Build the query visually. Every multi-select left empty means Any."
        actions={
          <>
            <Button onClick={runPreview}>PREVIEW QUERY</Button>
            <Button variant="contained" onClick={startRun} disabled={starting}>
              {dryRun ? 'RUN SCOUT (DRY RUN)' : 'RUN SCOUT'}
            </Button>
          </>
        }
      />

      <ErrorBanner error={error} onRetry={reload} title="Could not load the filter vocabulary" />
      <ErrorBanner error={actionError} />

      {vocabulary && !vocabulary.source_available && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {vocabulary.source_error ?? vocabulary.note} Values marked <strong>fallback</strong> come from the
          bundled taxonomy, not from the connected source.
        </Alert>
      )}

      <Grid container spacing={2}>
        <Grid item xs={12}>
          <SectionCard title="Source, mode and live count">
            <Grid container spacing={2} alignItems="center">
              <Grid item xs={12} md={3}>
                <TextField
                  select
                  fullWidth
                  label="Connection"
                  value={connectionId ?? ''}
                  onChange={(event) => store.setConnection(event.target.value || null)}
                >
                  <MenuItem value="">(automatic)</MenuItem>
                  {(connections?.connections ?? [])
                    .filter((c) => ['data_scout', 'metadata_api'].includes(c.kind))
                    .map((c) => (
                      <MenuItem key={c.connection_id} value={c.connection_id} disabled={!c.enabled}>
                        {c.display_name} {c.enabled ? '' : '(disabled)'}
                      </MenuItem>
                    ))}
                </TextField>
              </Grid>
              <Grid item xs={12} md={3}>
                <TextField
                  select
                  fullWidth
                  label="CSV template"
                  value={csvTemplateId}
                  onChange={(event) => store.setCsvTemplate(event.target.value)}
                >
                  {(((templates?.templates as { id: string; name: string }[]) ?? [])).map((t) => (
                    <MenuItem key={t.id} value={t.id}>
                      {t.name}
                    </MenuItem>
                  ))}
                </TextField>
              </Grid>
              <Grid item xs={12} md={2}>
                <TextField
                  fullWidth
                  type="number"
                  label="Max events"
                  value={query.limit ?? ''}
                  onChange={(event) =>
                    store.patchQuery({ limit: event.target.value ? Number(event.target.value) : null })
                  }
                />
              </Grid>
              <Grid item xs={12} md={2}>
                <FormControlLabel
                  control={
                    <Switch checked={dryRun} onChange={(event) => store.setDryRun(event.target.checked)} />
                  }
                  label={<Typography variant="body2">DRY RUN</Typography>}
                />
              </Grid>
              <Grid item xs={12} md={2}>
                <Metric
                  label="Matching events"
                  value={estimating ? '…' : estimate?.count ?? '—'}
                  hint={estimate ? (estimate.exact ? 'exact' : 'estimate') : undefined}
                  colour="#63b3ed"
                />
              </Grid>
            </Grid>
            {dryRun && (
              <Alert severity="info" sx={{ mt: 1.5 }}>
                A dry run tests the connection, validates the configuration, previews the query and
                processes a small sample in memory. It writes nothing and exports nothing.
              </Alert>
            )}
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={4}>
          <SectionCard title="Country">
            <TextField
              select
              fullWidth
              label="Country"
              value={query.country_code ?? ''}
              onChange={(event) => {
                const code = event.target.value || null
                const match = countries.find((c) => c.code === code)
                store.patchQuery({ country_code: code, country: match?.name ?? null, cities: [], regions: [] })
              }}
              helperText="Resolved against authoritative country_code metadata, never a filename."
            >
              <MenuItem value="">Any</MenuItem>
              {countries.map((country) => (
                <MenuItem key={country.code} value={country.code}>
                  {country.name} ({country.code})
                  {sourceCountryCodes.length > 0 && !sourceCountryCodes.includes(country.code)
                    ? ' — none in source'
                    : ''}
                </MenuItem>
              ))}
            </TextField>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={4}>
          <SectionCard title="Region">
            <MultiSelectFilter
              label="Region"
              values={field('region').values}
              origin={field('region').origin}
              selected={query.regions}
              onChange={(values) => store.setMulti('regions', values)}
              maxHeight={150}
            />
          </SectionCard>
        </Grid>
        <Grid item xs={12} md={4}>
          <SectionCard title="City / area">
            <Stack spacing={2}>
              <MultiSelectFilter
                label="City"
                values={field('city').values}
                origin={field('city').origin}
                selected={query.cities}
                onChange={(values) => store.setMulti('cities', values)}
                maxHeight={120}
              />
              <MultiSelectFilter
                label="Test area"
                values={field('test_area').values}
                origin={field('test_area').origin}
                selected={query.test_areas}
                onChange={(values) => store.setMulti('test_areas', values)}
                maxHeight={100}
              />
            </Stack>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={4}>
          <SectionCard title="Objects">
            <Stack spacing={2}>
              <MultiSelectFilter
                label="Object type"
                values={field('object_type').values}
                origin={field('object_type').origin}
                selected={query.object_types}
                onChange={(values) => store.setMulti('object_types', values)}
                maxHeight={170}
              />
              <MultiSelectFilter
                label="Bus subtype"
                values={field('bus_subtype').values}
                origin={field('bus_subtype').origin}
                selected={query.bus_subtypes}
                onChange={(values) => store.setMulti('bus_subtypes', values)}
                maxHeight={140}
              />
            </Stack>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={4}>
          <SectionCard title="Road & lanes">
            <Stack spacing={2}>
              <MultiSelectFilter
                label="Road type"
                values={field('road_type').values}
                origin={field('road_type').origin}
                selected={query.road_types}
                onChange={(values) => store.setMulti('road_types', values)}
                maxHeight={150}
              />
              <Box>
                <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                  Lane count
                </Typography>
                <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                  <Chip
                    label="Any"
                    color={query.lanes.lane_count_any ? 'primary' : 'default'}
                    onClick={() => store.setLanes({ lane_count_any: true, lane_count_exact: [] })}
                  />
                  {LANE_OPTIONS.map((count) => (
                    <Chip
                      key={count}
                      label={count === 5 ? '5+' : String(count)}
                      color={query.lanes.lane_count_exact.includes(count) ? 'primary' : 'default'}
                      onClick={() => {
                        const current = query.lanes.lane_count_exact
                        const next = current.includes(count)
                          ? current.filter((c) => c !== count)
                          : [...current, count]
                        store.setLanes({ lane_count_exact: next, lane_count_any: next.length === 0 })
                      }}
                    />
                  ))}
                </Stack>
                <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                  <TextField
                    type="number"
                    label="Min lanes"
                    value={query.lanes.min_lanes ?? ''}
                    onChange={(event) =>
                      store.setLanes({ min_lanes: event.target.value ? Number(event.target.value) : null })
                    }
                  />
                  <TextField
                    type="number"
                    label="Max lanes"
                    value={query.lanes.max_lanes ?? ''}
                    onChange={(event) =>
                      store.setLanes({ max_lanes: event.target.value ? Number(event.target.value) : null })
                    }
                  />
                </Stack>
              </Box>
            </Stack>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={4}>
          <SectionCard title="Lane configuration & relation">
            <Stack spacing={2}>
              <MultiSelectFilter
                label="Lane configuration"
                values={field('lane_configuration').values}
                origin={field('lane_configuration').origin}
                selected={query.lanes.lane_configuration}
                onChange={(values) => store.setLanes({ lane_configuration: values })}
                maxHeight={150}
              />
              <MultiSelectFilter
                label="Relation to ego"
                values={field('ego_lane_relation').values}
                origin={field('ego_lane_relation').origin}
                selected={query.lanes.ego_lane_relation}
                onChange={(values) => store.setLanes({ ego_lane_relation: values })}
                maxHeight={150}
              />
            </Stack>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={4}>
          <SectionCard title="Intersection">
            <Stack spacing={2}>
              <MultiSelectFilter
                label="Intersection type"
                values={field('intersection_type').values}
                origin={field('intersection_type').origin}
                selected={query.intersection_types}
                onChange={(values) => store.setMulti('intersection_types', values)}
                maxHeight={150}
              />
              <MultiSelectFilter
                label="Complexity"
                values={field('intersection_complexity').values}
                origin={field('intersection_complexity').origin}
                selected={query.intersection_complexity}
                onChange={(values) => store.setMulti('intersection_complexity', values)}
                helper="Derived from mapped branch, lane, control and turn counts; 'unknown' when the map does not state them."
                maxHeight={120}
              />
            </Stack>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={4}>
          <SectionCard title="Traffic control">
            <Stack spacing={2}>
              <MultiSelectFilter
                label="Control entity"
                values={field('traffic_control_entity').values}
                origin={field('traffic_control_entity').origin}
                selected={query.traffic_control_entities}
                onChange={(values) => store.setMulti('traffic_control_entities', values)}
                maxHeight={140}
              />
              <MultiSelectFilter
                label="Signal state"
                values={field('traffic_light_state').values}
                origin={field('traffic_light_state').origin}
                selected={query.traffic_light_states}
                onChange={(values) => store.setMulti('traffic_light_states', values)}
                maxHeight={140}
              />
            </Stack>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={4}>
          <SectionCard title="Manoeuvre, weather, lighting">
            <Stack spacing={2}>
              <MultiSelectFilter
                label="Vehicle manoeuvre"
                values={field('vehicle_maneuver').values}
                origin={field('vehicle_maneuver').origin}
                selected={query.vehicle_maneuvers}
                onChange={(values) => store.setMulti('vehicle_maneuvers', values)}
                maxHeight={120}
              />
              <MultiSelectFilter
                label="Weather"
                values={field('weather').values}
                origin={field('weather').origin}
                selected={query.weather}
                onChange={(values) => store.setMulti('weather', values)}
                maxHeight={110}
              />
              <MultiSelectFilter
                label="Lighting"
                values={field('lighting').values}
                origin={field('lighting').origin}
                selected={query.lighting}
                onChange={(values) => store.setMulti('lighting', values)}
                maxHeight={110}
              />
            </Stack>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={6}>
          <SectionCard title="Date & time">
            <Grid container spacing={2}>
              <Grid item xs={6}>
                <TextField
                  fullWidth
                  type="date"
                  label="Start date"
                  InputLabelProps={{ shrink: true }}
                  value={query.time_range.start_date ?? ''}
                  onChange={(event) => store.setTimeRange({ start_date: event.target.value || null })}
                />
              </Grid>
              <Grid item xs={6}>
                <TextField
                  fullWidth
                  type="date"
                  label="End date"
                  InputLabelProps={{ shrink: true }}
                  value={query.time_range.end_date ?? ''}
                  onChange={(event) => store.setTimeRange({ end_date: event.target.value || null })}
                />
              </Grid>
              <Grid item xs={6}>
                <TextField
                  fullWidth
                  type="time"
                  label="Start time"
                  InputLabelProps={{ shrink: true }}
                  value={query.time_range.start_time ?? ''}
                  onChange={(event) => store.setTimeRange({ start_time: event.target.value || null })}
                />
              </Grid>
              <Grid item xs={6}>
                <TextField
                  fullWidth
                  type="time"
                  label="End time"
                  InputLabelProps={{ shrink: true }}
                  value={query.time_range.end_time ?? ''}
                  onChange={(event) => store.setTimeRange({ end_time: event.target.value || null })}
                />
              </Grid>
              <Grid item xs={12}>
                <Stack direction="row" spacing={2} flexWrap="wrap">
                  {(
                    [
                      ['day_only', 'Day only'],
                      ['night_only', 'Night only'],
                      ['weekdays_only', 'Weekdays'],
                      ['weekends_only', 'Weekends'],
                    ] as const
                  ).map(([key, label]) => (
                    <FormControlLabel
                      key={key}
                      control={
                        <Switch
                          checked={Boolean(query.time_range[key])}
                          onChange={(event) => store.setTimeRange({ [key]: event.target.checked })}
                        />
                      }
                      label={<Typography variant="body2">{label}</Typography>}
                    />
                  ))}
                </Stack>
              </Grid>
            </Grid>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={6}>
          <SectionCard title="Dataset">
            <Grid container spacing={2}>
              {(
                [
                  ['project', 'Project'],
                  ['dataset', 'Dataset'],
                  ['dataset_version', 'Dataset version'],
                  ['drive_collection', 'Drive collection'],
                  ['vehicle_build', 'Vehicle build'],
                  ['software_version', 'Software version'],
                  ['map_version', 'Map version'],
                ] as const
              ).map(([key, label]) => {
                const options = field(key).values
                return (
                  <Grid item xs={12} sm={6} key={key}>
                    <TextField
                      select
                      fullWidth
                      label={label}
                      value={(query.dataset[key] as string) ?? ''}
                      onChange={(event) => store.setDataset({ [key]: event.target.value || null })}
                    >
                      <MenuItem value="">Any</MenuItem>
                      {options.map((option) => (
                        <MenuItem key={option} value={option}>
                          {option}
                        </MenuItem>
                      ))}
                    </TextField>
                  </Grid>
                )
              })}
            </Grid>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={6}>
          <SectionCard
            title="Bus scenario tags"
            subtitle="Tags supplied by the source. Detected tags are added automatically during processing."
          >
            <MultiSelectFilter
              label="Scenario tag"
              values={field('bus_scenario_tag').values}
              origin={field('bus_scenario_tag').origin}
              selected={query.scenario_tags}
              onChange={(values) => store.setMulti('scenario_tags', values)}
              maxHeight={240}
            />
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={6}>
          <SectionCard
            title="Error detection workbench"
            subtitle="Which candidate-finding families to emphasise on the review queue."
          >
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              {ERROR_DETECTION.map((category) => (
                <Chip
                  key={category}
                  label={category}
                  color={query.error_detection.includes(category) ? 'primary' : 'default'}
                  onClick={() => store.toggleMulti('error_detection', category)}
                />
              ))}
            </Stack>
            <Divider sx={{ my: 2 }} />
            <Typography variant="caption" color="text.secondary">
              Rules are enabled on the Validation Rules page. Selecting nothing here keeps every enabled
              rule active.
            </Typography>
          </SectionCard>
        </Grid>
      </Grid>

      {preview && (
        <Dialog open onClose={() => setPreview(null)} maxWidth="md" fullWidth>
          <DialogTitle>Query summary</DialogTitle>
          <DialogContent>
            {preview.warnings.map((warning) => (
              <Alert key={warning} severity="warning" sx={{ mb: 1 }}>
                {warning}
              </Alert>
            ))}
            <Stack spacing={0.5} sx={{ mb: 2 }}>
              {Object.entries(preview.summary).map(([key, value]) => (
                <Stack key={key} direction="row" justifyContent="space-between">
                  <Typography variant="body2" color="text.secondary">
                    {key}
                  </Typography>
                  <Typography variant="body2" sx={{ textAlign: 'right', maxWidth: '65%' }}>
                    {value}
                  </Typography>
                </Stack>
              ))}
            </Stack>
            <Divider sx={{ my: 1.5 }} />
            <Metric
              label="Estimated records"
              value={preview.estimated_records ?? 'unknown'}
              hint={preview.estimate_note}
              colour="#63b3ed"
            />
            {Object.keys(preview.native_query).length > 0 && (
              <Box sx={{ mt: 2 }}>
                <Typography variant="subtitle2">Native source query</Typography>
                <Box component="pre" sx={{ fontSize: '0.72rem', overflow: 'auto' }}>
                  {JSON.stringify(preview.native_query, null, 2)}
                </Box>
              </Box>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setPreview(null)}>BACK</Button>
            <Button onClick={() => navigate('/profiles')}>SAVE PROFILE</Button>
            <Button variant="contained" onClick={startRun} disabled={starting}>
              RUN
            </Button>
          </DialogActions>
        </Dialog>
      )}
    </Box>
  )
}

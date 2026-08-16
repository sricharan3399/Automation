import { Box, Chip, Grid, Stack, Typography } from '@mui/material'

import { MultiSelectFilter } from '@/components/MultiSelectFilter'
import { LoadingBlock, PageHeader, SectionCard } from '@/components/common'
import { useApi } from '@/hooks/useApi'
import { api } from '@/services/api'
import { useAppStore } from '@/stores/useAppStore'

export function ScenarioBuilder() {
  const store = useAppStore()
  const { query, connectionId } = store
  const { data, loading } = useApi(() => api.filters(connectionId ?? undefined), [connectionId])

  if (loading && !data) return <LoadingBlock label="Loading scenario vocabulary…" />
  const field = (key: string) => data?.fields?.[key] ?? { values: [], origin: 'fallback' as const }

  return (
    <Box>
      <PageHeader
        title="Scenario Builder"
        subtitle="Bus-specific scenario selection. These filter which events are retrieved; the scene engine also detects tags from the data and adds them to each record."
      />

      <Grid container spacing={2}>
        <Grid item xs={12} md={6}>
          <SectionCard title="Bus scenario tags">
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              {field('bus_scenario_tag').values.map((tag) => (
                <Chip
                  key={tag}
                  label={tag}
                  color={query.scenario_tags.includes(tag) ? 'primary' : 'default'}
                  onClick={() => store.toggleMulti('scenario_tags', tag)}
                />
              ))}
            </Stack>
            {field('bus_scenario_tag').values.length === 0 && (
              <Typography variant="body2" color="text.secondary">
                The source exposes no scenario tags for the current selection.
              </Typography>
            )}
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={6}>
          <Stack spacing={2}>
            <SectionCard title="Relationship to the ego lane">
              <MultiSelectFilter
                label="Object relation"
                values={field('ego_lane_relation').values}
                origin={field('ego_lane_relation').origin}
                selected={query.lanes.ego_lane_relation}
                onChange={(values) => store.setLanes({ ego_lane_relation: values })}
                maxHeight={200}
              />
            </SectionCard>

            <SectionCard title="Object and subtype">
              <Stack spacing={2}>
                <MultiSelectFilter
                  label="Object type"
                  values={field('object_type').values}
                  origin={field('object_type').origin}
                  selected={query.object_types}
                  onChange={(values) => store.setMulti('object_types', values)}
                  maxHeight={150}
                />
                <MultiSelectFilter
                  label="Bus subtype"
                  values={field('bus_subtype').values}
                  origin={field('bus_subtype').origin}
                  selected={query.bus_subtypes}
                  onChange={(values) => store.setMulti('bus_subtypes', values)}
                  maxHeight={150}
                />
              </Stack>
            </SectionCard>
          </Stack>
        </Grid>
      </Grid>
    </Box>
  )
}

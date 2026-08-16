import {
  Alert,
  Box,
  Chip,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material'

import { PageHeader, SectionCard } from '@/components/common'
import { useAppStore } from '@/stores/useAppStore'
import type { StreamRequirement } from '@/types'

const CAMERAS = ['front_narrow', 'front_wide', 'front_main', 'left', 'right', 'rear', 'fisheye', 'other']
const OTHER_STREAMS = [
  'lidar',
  'radar',
  'gps',
  'imu',
  'can',
  'vehicle_state',
  'localization',
  'perception',
  'prediction',
  'planning',
  'map',
  'trajectory',
]

const HEALTH_CHECKS = [
  'MISSING_SENSOR_STREAM',
  'MISSING_CAMERA_FRAMES',
  'DROPPED_FRAMES',
  'DUPLICATE_FRAMES',
  'FROZEN_VIDEO',
  'FRAME_TIMESTAMP_GAP',
  'CAMERA_DESYNC',
  'CAMERA_TO_TELEMETRY_DESYNC',
  'LIDAR_TIMESTAMP_GAP',
  'RADAR_TIMESTAMP_GAP',
  'GPS_GAP',
  'IMU_GAP',
  'CAN_DATA_GAP',
  'LOCALIZATION_GAP',
]

export function SensorConfiguration() {
  const store = useAppStore()

  const requirementOf = (streamType: string, camera: string | null): StreamRequirement => {
    const exact = store.sensorConfig.streams.find(
      (s) => s.stream_type === streamType && (s.camera_position ?? null) === camera,
    )
    return exact?.requirement ?? 'optional'
  }

  const requiredCount = store.sensorConfig.streams.filter((s) => s.requirement === 'required').length

  return (
    <Box>
      <PageHeader
        title="Sensor Configuration"
        subtitle="Choose which streams this run requires. A required stream that the source does not deliver blocks the event and routes it to data review."
        actions={<Chip label={`${requiredCount} required`} color={requiredCount ? 'primary' : 'default'} />}
      />

      <Alert severity="info" sx={{ mb: 2 }}>
        Defaults are deliberately minimal: only the master clock stream is required. The platform does
        not silently demand streams the project has not agreed to.
      </Alert>

      <Stack spacing={2}>
        <SectionCard title="Cameras">
          <Stack spacing={1}>
            {CAMERAS.map((camera) => (
              <StreamRow
                key={camera}
                label={`camera · ${camera}`}
                value={requirementOf('camera', camera)}
                onChange={(value) => store.setStreamRequirement('camera', camera, value)}
              />
            ))}
          </Stack>
        </SectionCard>

        <SectionCard title="Other streams">
          <Stack spacing={1}>
            {OTHER_STREAMS.map((stream) => (
              <StreamRow
                key={stream}
                label={stream}
                value={requirementOf(stream, null)}
                onChange={(value) => store.setStreamRequirement(stream, null, value)}
              />
            ))}
          </Stack>
        </SectionCard>

        <SectionCard
          title="Automatic sensor health checks"
          subtitle="Run automatically on every event; results appear on the Event Detail → Sensors tab."
        >
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            {HEALTH_CHECKS.map((check) => (
              <Chip key={check} label={check} variant="outlined" />
            ))}
          </Stack>
        </SectionCard>
      </Stack>
    </Box>
  )
}

function StreamRow({
  label,
  value,
  onChange,
}: {
  label: string
  value: StreamRequirement
  onChange: (value: StreamRequirement) => void
}) {
  return (
    <Stack direction="row" alignItems="center" justifyContent="space-between">
      <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
        {label}
      </Typography>
      <ToggleButtonGroup
        exclusive
        size="small"
        value={value}
        onChange={(_, next) => next && onChange(next as StreamRequirement)}
      >
        <ToggleButton value="required">Required</ToggleButton>
        <ToggleButton value="optional">Optional</ToggleButton>
        <ToggleButton value="ignore">Ignore</ToggleButton>
      </ToggleButtonGroup>
    </Stack>
  )
}

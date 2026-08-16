import { Box, Checkbox, FormControlLabel, Stack, Typography } from '@mui/material'
import { useMemo, useState } from 'react'

import type { MapFeature, TrajectoryPoint } from '@/types'

/**
 * Self-contained SVG map.
 *
 * Deliberately not a tile-based map: the platform must never send positional
 * data to an external tile server, and the corporate network would not permit
 * it. Everything is drawn from the event's own local metric frame, which is
 * also why no global coordinates appear here at all.
 */

const WIDTH = 900
const HEIGHT = 620
const PADDING = 40

type Point = [number, number]

interface Layer {
  key: string
  label: string
  colour: string
}

const LAYERS: Layer[] = [
  { key: 'route', label: 'Ego route', colour: '#48bb78' },
  { key: 'lanes', label: 'Lane centrelines', colour: '#4a5568' },
  { key: 'junctions', label: 'Junctions', colour: '#4299e1' },
  { key: 'signals', label: 'Signals & stop lines', colour: '#f6ad55' },
  { key: 'markers', label: 'Derived markers', colour: '#f6e05e' },
  { key: 'ids', label: 'Feature IDs', colour: '#a0aec0' },
]

export interface MapMarker {
  name: string
  t: number
  x_m?: number
  y_m?: number
}

export function MapView({
  trajectory,
  features,
  markers = [],
  selectedJunctionId,
  entryEdge,
  exitEdge,
  height = HEIGHT,
}: {
  trajectory: TrajectoryPoint[]
  features: MapFeature[]
  markers?: MapMarker[]
  selectedJunctionId?: string | null
  entryEdge?: { p1: number[]; p2: number[] } | null
  exitEdge?: { p1: number[]; p2: number[] } | null
  height?: number
}) {
  const [visible, setVisible] = useState<Record<string, boolean>>(
    Object.fromEntries(LAYERS.map((layer) => [layer.key, true])),
  )

  const points = useMemo(() => {
    const collected: Point[] = trajectory.map((p) => [p.x_m, p.y_m])
    features.forEach((feature) => {
      const coords = feature.geometry.coordinates
      if (feature.geometry.type === 'Point' && Array.isArray(coords)) {
        collected.push([Number(coords[0]), Number(coords[1])])
      } else if (feature.geometry.type === 'LineString' && Array.isArray(coords)) {
        const line = coords as number[][]
        line.forEach((c) => collected.push([Number(c[0]), Number(c[1])]))
      } else if (feature.geometry.type === 'Polygon' && Array.isArray(coords)) {
        const ring = (coords as number[][][])[0] ?? []
        ring.forEach((c) => collected.push([Number(c[0]), Number(c[1])]))
      }
    })
    return collected
  }, [trajectory, features])

  const project = useMemo(() => {
    if (points.length === 0) return null
    const xs = points.map((p) => p[0])
    const ys = points.map((p) => p[1])
    const minX = Math.min(...xs)
    const maxX = Math.max(...xs)
    const minY = Math.min(...ys)
    const maxY = Math.max(...ys)
    const spanX = Math.max(maxX - minX, 1)
    const spanY = Math.max(maxY - minY, 1)
    const scale = Math.min((WIDTH - 2 * PADDING) / spanX, (height - 2 * PADDING) / spanY)
    const offsetX = (WIDTH - spanX * scale) / 2 - minX * scale
    const offsetY = (height - spanY * scale) / 2 - minY * scale
    return {
      scale,
      to: (x: number, y: number): Point => [x * scale + offsetX, height - (y * scale + offsetY)],
    }
  }, [points, height])

  if (!project) {
    return (
      <Box sx={{ p: 4, textAlign: 'center', border: '1px solid', borderColor: 'divider', borderRadius: 1 }}>
        <Typography variant="body2" color="text.secondary">
          No geometry is available for this event, so nothing can be drawn. This usually means the
          event carries no map context or the ego trajectory was unusable.
        </Typography>
      </Box>
    )
  }

  const path = (coords: number[][]) =>
    coords
      .map((c, index) => {
        const [px, py] = project.to(Number(c[0]), Number(c[1]))
        return `${index === 0 ? 'M' : 'L'}${px.toFixed(1)},${py.toFixed(1)}`
      })
      .join(' ')

  const lanes = features.filter((f) => f.feature_type === 'lane_centerline' || f.feature_type === 'lane_boundary')
  const junctions = features.filter((f) => f.feature_type === 'junction')
  const stopLines = features.filter((f) => f.feature_type === 'stop_line')
  const signals = features.filter((f) => f.feature_type === 'traffic_signal' || f.feature_type === 'bus_stop')

  const markerPoints = markers
    .map((marker) => {
      if (marker.x_m !== undefined && marker.y_m !== undefined) {
        return { ...marker, position: project.to(marker.x_m, marker.y_m) }
      }
      const nearest = trajectory.reduce<TrajectoryPoint | null>((best, point) => {
        if (!best) return point
        return Math.abs(point.t - marker.t) < Math.abs(best.t - marker.t) ? point : best
      }, null)
      return nearest ? { ...marker, position: project.to(nearest.x_m, nearest.y_m) } : null
    })
    .filter(Boolean) as (MapMarker & { position: Point })[]

  return (
    <Box>
      <Stack direction="row" flexWrap="wrap" sx={{ mb: 1 }}>
        {LAYERS.map((layer) => (
          <FormControlLabel
            key={layer.key}
            control={
              <Checkbox
                size="small"
                checked={visible[layer.key]}
                onChange={(event) => setVisible((state) => ({ ...state, [layer.key]: event.target.checked }))}
                sx={{ color: layer.colour, '&.Mui-checked': { color: layer.colour } }}
              />
            }
            label={<Typography variant="caption">{layer.label}</Typography>}
          />
        ))}
      </Stack>

      <Box
        component="svg"
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label="Ego trajectory and junction geometry"
        sx={{ width: '100%', bgcolor: 'background.default', border: '1px solid', borderColor: 'divider', borderRadius: 1 }}
      >
        {visible.junctions &&
          junctions.map((feature) => {
            const ring = (feature.geometry.coordinates as number[][][])[0] ?? []
            if (ring.length < 3) return null
            const isSelected = feature.feature_id === selectedJunctionId
            const pts = ring.map((c) => project.to(Number(c[0]), Number(c[1])).join(',')).join(' ')
            return (
              <polygon
                key={feature.feature_id}
                points={pts}
                fill={isSelected ? 'rgba(66,153,225,0.16)' : 'none'}
                stroke={isSelected ? '#4299e1' : '#718096'}
                strokeWidth={isSelected ? 2 : 1.2}
                strokeDasharray={isSelected ? undefined : '4 4'}
              />
            )
          })}

        {visible.lanes &&
          lanes.map((feature) => (
            <path
              key={feature.feature_id}
              d={path(feature.geometry.coordinates as number[][])}
              stroke="#4a5568"
              strokeWidth={1.5}
              strokeDasharray="6 5"
              fill="none"
            />
          ))}

        {visible.signals &&
          stopLines.map((feature) => (
            <path
              key={feature.feature_id}
              d={path(feature.geometry.coordinates as number[][])}
              stroke="#f6ad55"
              strokeWidth={3}
              fill="none"
            />
          ))}

        {visible.route && trajectory.length > 1 && (
          <path
            d={path(trajectory.map((p) => [p.x_m, p.y_m]))}
            stroke="#48bb78"
            strokeWidth={2.6}
            fill="none"
          />
        )}

        {entryEdge && (
          <line
            x1={project.to(entryEdge.p1[0], entryEdge.p1[1])[0]}
            y1={project.to(entryEdge.p1[0], entryEdge.p1[1])[1]}
            x2={project.to(entryEdge.p2[0], entryEdge.p2[1])[0]}
            y2={project.to(entryEdge.p2[0], entryEdge.p2[1])[1]}
            stroke="#38b2ac"
            strokeWidth={5}
            strokeLinecap="round"
          />
        )}
        {exitEdge && (
          <line
            x1={project.to(exitEdge.p1[0], exitEdge.p1[1])[0]}
            y1={project.to(exitEdge.p1[0], exitEdge.p1[1])[1]}
            x2={project.to(exitEdge.p2[0], exitEdge.p2[1])[0]}
            y2={project.to(exitEdge.p2[0], exitEdge.p2[1])[1]}
            stroke="#ed64a6"
            strokeWidth={5}
            strokeLinecap="round"
          />
        )}

        {visible.signals &&
          signals.map((feature) => {
            const coords = feature.geometry.coordinates as number[]
            const [px, py] = project.to(Number(coords[0]), Number(coords[1]))
            return <circle key={feature.feature_id} cx={px} cy={py} r={5} fill="#fc8181" />
          })}

        {visible.markers &&
          markerPoints.map((marker) => (
            <g key={marker.name}>
              <circle cx={marker.position[0]} cy={marker.position[1]} r={4.5} fill="#f6e05e" stroke="#1a202c" />
              <text x={marker.position[0] + 8} y={marker.position[1] - 6} fill="#e2e8f0" fontSize={11}>
                {marker.name}
              </text>
            </g>
          ))}

        {visible.ids &&
          junctions.map((feature) => {
            const ring = (feature.geometry.coordinates as number[][][])[0] ?? []
            if (ring.length === 0) return null
            const cx = ring.reduce((sum, c) => sum + Number(c[0]), 0) / ring.length
            const cy = ring.reduce((sum, c) => sum + Number(c[1]), 0) / ring.length
            const [px, py] = project.to(cx, cy)
            return (
              <text key={`${feature.feature_id}-label`} x={px + 6} y={py - 6} fill="#a0aec0" fontSize={11}>
                {feature.feature_id}
              </text>
            )
          })}

        <text x={WIDTH - 16} y={height - 12} textAnchor="end" fill="#718096" fontSize={11}>
          {`1 px ≈ ${(1 / project.scale).toFixed(2)} m · local metric frame, no global coordinates`}
        </text>
      </Box>
    </Box>
  )
}

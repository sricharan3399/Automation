import { create } from 'zustand'

import type { ScoutQuery, SensorConfiguration, StreamRequirement } from '@/types'

export function emptyQuery(): ScoutQuery {
  return {
    country_code: null,
    country: null,
    regions: [],
    cities: [],
    test_areas: [],
    routes: [],
    object_types: [],
    bus_subtypes: [],
    scenario_tags: [],
    road_types: [],
    lanes: {
      lane_count_any: true,
      lane_count_exact: [],
      min_lanes: null,
      max_lanes: null,
      lane_configuration: [],
      ego_lane_relation: [],
    },
    intersection_types: [],
    intersection_complexity: [],
    traffic_control_entities: [],
    traffic_light_states: [],
    vehicle_maneuvers: [],
    weather: [],
    lighting: [],
    time_range: {
      start_date: null,
      end_date: null,
      start_time: null,
      end_time: null,
      day_only: false,
      night_only: false,
      weekdays_only: false,
      weekends_only: false,
    },
    dataset: {},
    error_detection: [],
    limit: null,
  }
}

export function defaultSensorConfig(): SensorConfiguration {
  return {
    streams: [
      { stream_type: 'vehicle_state', camera_position: null, requirement: 'required' },
      { stream_type: 'localization', camera_position: null, requirement: 'optional' },
      { stream_type: 'camera', camera_position: 'front_main', requirement: 'optional' },
      { stream_type: 'camera', camera_position: 'front_wide', requirement: 'optional' },
      { stream_type: 'perception', camera_position: null, requirement: 'optional' },
      { stream_type: 'map', camera_position: null, requirement: 'optional' },
    ],
  }
}

type MultiKey =
  | 'regions'
  | 'cities'
  | 'test_areas'
  | 'routes'
  | 'object_types'
  | 'bus_subtypes'
  | 'scenario_tags'
  | 'road_types'
  | 'intersection_types'
  | 'intersection_complexity'
  | 'traffic_control_entities'
  | 'traffic_light_states'
  | 'vehicle_maneuvers'
  | 'weather'
  | 'lighting'
  | 'error_detection'

interface AppState {
  query: ScoutQuery
  sensorConfig: SensorConfiguration
  csvTemplateId: string
  connectionId: string | null
  profileId: string | null
  dryRun: boolean
  ruleOverrides: Record<string, boolean>
  activeRunId: string | null

  patchQuery: (patch: Partial<ScoutQuery>) => void
  setMulti: (key: MultiKey, values: string[]) => void
  toggleMulti: (key: MultiKey, value: string) => void
  setLanes: (patch: Partial<ScoutQuery['lanes']>) => void
  setTimeRange: (patch: Partial<ScoutQuery['time_range']>) => void
  setDataset: (patch: Partial<ScoutQuery['dataset']>) => void
  setStreamRequirement: (
    streamType: string,
    cameraPosition: string | null,
    requirement: StreamRequirement,
  ) => void
  setCsvTemplate: (id: string) => void
  setConnection: (id: string | null) => void
  setDryRun: (value: boolean) => void
  setRuleOverride: (ruleId: string, enabled: boolean) => void
  setActiveRun: (runId: string | null) => void
  loadProfile: (
    profileId: string,
    query: ScoutQuery,
    sensorConfig: SensorConfiguration,
    csvTemplateId: string,
  ) => void
  reset: () => void
}

export const useAppStore = create<AppState>((set) => ({
  query: emptyQuery(),
  sensorConfig: defaultSensorConfig(),
  csvTemplateId: 'germany_bus_test',
  connectionId: null,
  profileId: null,
  // The first execution of a configuration is always a dry run; the backend
  // enforces this too, so the toggle can never bypass it.
  dryRun: true,
  ruleOverrides: {},
  activeRunId: null,

  patchQuery: (patch) => set((state) => ({ query: { ...state.query, ...patch } })),
  setMulti: (key, values) => set((state) => ({ query: { ...state.query, [key]: values } })),
  toggleMulti: (key, value) =>
    set((state) => {
      const current = state.query[key] as string[]
      const next = current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value]
      return { query: { ...state.query, [key]: next } }
    }),
  setLanes: (patch) => set((state) => ({ query: { ...state.query, lanes: { ...state.query.lanes, ...patch } } })),
  setTimeRange: (patch) =>
    set((state) => ({ query: { ...state.query, time_range: { ...state.query.time_range, ...patch } } })),
  setDataset: (patch) =>
    set((state) => ({ query: { ...state.query, dataset: { ...state.query.dataset, ...patch } } })),

  setStreamRequirement: (streamType, cameraPosition, requirement) =>
    set((state) => {
      const streams = [...state.sensorConfig.streams]
      const index = streams.findIndex(
        (s) => s.stream_type === streamType && (s.camera_position ?? null) === cameraPosition,
      )
      if (index >= 0) streams[index] = { ...streams[index], requirement }
      else streams.push({ stream_type: streamType, camera_position: cameraPosition, requirement })
      return { sensorConfig: { ...state.sensorConfig, streams } }
    }),

  setCsvTemplate: (id) => set({ csvTemplateId: id }),
  setConnection: (id) => set({ connectionId: id }),
  setDryRun: (value) => set({ dryRun: value }),
  setRuleOverride: (ruleId, enabled) =>
    set((state) => ({ ruleOverrides: { ...state.ruleOverrides, [ruleId]: enabled } })),
  setActiveRun: (runId) => set({ activeRunId: runId }),

  loadProfile: (profileId, query, sensorConfig, csvTemplateId) =>
    set({ profileId, query, sensorConfig, csvTemplateId }),

  reset: () =>
    set({
      query: emptyQuery(),
      sensorConfig: defaultSensorConfig(),
      csvTemplateId: 'germany_bus_test',
      profileId: null,
      ruleOverrides: {},
    }),
}))

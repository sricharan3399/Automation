import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MultiSelectFilter } from '@/components/MultiSelectFilter'
import { VirtualTable } from '@/components/VirtualTable'
import { ApiError } from '@/services/api'
import { emptyQuery, useAppStore } from '@/stores/useAppStore'

describe('scout query store', () => {
  beforeEach(() => {
    useAppStore.getState().reset()
  })

  it('starts with every multi-select empty, meaning Any', () => {
    const { query } = useAppStore.getState()
    expect(query.object_types).toEqual([])
    expect(query.road_types).toEqual([])
    expect(query.lanes.lane_count_any).toBe(true)
    expect(query.country_code).toBeNull()
  })

  it('defaults to a dry run so a new configuration cannot execute for real by accident', () => {
    expect(useAppStore.getState().dryRun).toBe(true)
  })

  it('toggles a multi-select value on and off', () => {
    const store = useAppStore.getState()
    store.toggleMulti('object_types', 'bus')
    expect(useAppStore.getState().query.object_types).toEqual(['bus'])
    useAppStore.getState().toggleMulti('object_types', 'bus')
    expect(useAppStore.getState().query.object_types).toEqual([])
  })

  it('keeps lane filters independent of the Any flag', () => {
    useAppStore.getState().setLanes({ lane_count_exact: [2, 3], lane_count_any: false })
    const { lanes } = useAppStore.getState().query
    expect(lanes.lane_count_exact).toEqual([2, 3])
    expect(lanes.lane_count_any).toBe(false)
  })

  it('changes a stream requirement without dropping the others', () => {
    useAppStore.getState().setStreamRequirement('camera', 'front_main', 'required')
    const streams = useAppStore.getState().sensorConfig.streams
    const camera = streams.find((s) => s.stream_type === 'camera' && s.camera_position === 'front_main')
    expect(camera?.requirement).toBe('required')
    expect(streams.length).toBeGreaterThan(1)
  })

  it('adds a stream that was not in the defaults', () => {
    useAppStore.getState().setStreamRequirement('lidar', null, 'required')
    const lidar = useAppStore.getState().sensorConfig.streams.find((s) => s.stream_type === 'lidar')
    expect(lidar?.requirement).toBe('required')
  })

  it('loads a profile into the query and sensor configuration', () => {
    const query = { ...emptyQuery(), country_code: 'DE', object_types: ['bus'] }
    useAppStore
      .getState()
      .loadProfile('germany_bus_validation', query, { streams: [] }, 'germany_bus_test')
    const state = useAppStore.getState()
    expect(state.profileId).toBe('germany_bus_validation')
    expect(state.query.country_code).toBe('DE')
    expect(state.csvTemplateId).toBe('germany_bus_test')
  })
})

describe('ApiError', () => {
  it('carries the actionable message rather than a status code', () => {
    const error = new ApiError(409, 'Data Scout has not been configured yet.', null, false)
    expect(error.message).toContain('not been configured')
    expect(error.status).toBe(409)
  })

  it('marks retryable failures', () => {
    expect(new ApiError(503, 'temporarily unavailable', null, true).retryable).toBe(true)
  })
})

describe('MultiSelectFilter', () => {
  it('shows Any when nothing is selected and reports the count otherwise', () => {
    const { rerender } = render(
      <MultiSelectFilter label="Road type" values={['urban', 'rural']} selected={[]} onChange={() => {}} />,
    )
    expect(screen.getByText('Any')).toBeInTheDocument()

    rerender(
      <MultiSelectFilter
        label="Road type"
        values={['urban', 'rural']}
        selected={['urban']}
        onChange={() => {}}
      />,
    )
    expect(screen.getByText('1 selected')).toBeInTheDocument()
  })

  it('selects a value on click', async () => {
    const onChange = vi.fn()
    render(
      <MultiSelectFilter label="Road type" values={['urban', 'rural']} selected={[]} onChange={onChange} />,
    )
    await userEvent.click(screen.getByText('urban'))
    expect(onChange).toHaveBeenCalledWith(['urban'])
  })

  it('labels fallback vocabulary so the tester knows it is not source-derived', () => {
    render(
      <MultiSelectFilter
        label="Weather"
        values={['clear']}
        selected={[]}
        origin="fallback"
        onChange={() => {}}
      />,
    )
    expect(screen.getByText('fallback')).toBeInTheDocument()
  })

  it('states plainly when a field has no values at all', () => {
    render(<MultiSelectFilter label="City" values={[]} selected={[]} onChange={() => {}} />)
    expect(screen.getByText(/No values available/i)).toBeInTheDocument()
  })
})

describe('VirtualTable', () => {
  const columns = [
    { key: 'name', header: 'Name', render: (row: { name: string }) => row.name },
  ]

  it('renders an explicit empty message instead of a blank table', () => {
    render(
      <VirtualTable
        rows={[]}
        columns={columns}
        keyOf={(row) => row.name}
        emptyMessage="No events match this filter."
      />,
    )
    expect(screen.getByText('No events match this filter.')).toBeInTheDocument()
  })

  it('renders headers for supplied rows', async () => {
    render(
      <VirtualTable rows={[{ name: 'EVT-1' }]} columns={columns} keyOf={(row) => row.name} />,
    )
    await waitFor(() => expect(screen.getByText('Name')).toBeInTheDocument())
  })
})

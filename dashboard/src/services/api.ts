/**
 * Typed API client.
 *
 * Every backend failure carries an actionable message; `ApiError.message` is
 * that text, so a page can surface it verbatim instead of showing a status code.
 */

import type {
  ConnectionSummary,
  EventSummary,
  ExportReadiness,
  FilterVocabulary,
  QueryPreview,
  ReviewDetail,
  RuleDefinition,
  RunSummary,
  ScoutQuery,
  SensorConfiguration,
} from '@/types'

export const API_PREFIX = '/api/v1'

export class ApiError extends Error {
  readonly status: number
  readonly payload: unknown
  readonly retryable: boolean

  constructor(status: number, message: string, payload: unknown, retryable = false) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
    this.retryable = retryable
  }
}

function extractMessage(status: number, payload: unknown): { message: string; retryable: boolean } {
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = (payload as { detail: unknown }).detail
    if (typeof detail === 'string') return { message: detail, retryable: false }
    if (detail && typeof detail === 'object') {
      const record = detail as Record<string, unknown>
      const message = typeof record.message === 'string' ? record.message : JSON.stringify(detail)
      return { message, retryable: Boolean(record.retryable) }
    }
  }
  return { message: `The request failed with status ${status}.`, retryable: status >= 500 }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_PREFIX}${path}`, {
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
      ...init,
    })
  } catch (cause) {
    throw new ApiError(
      0,
      'The backend is not reachable. Check that the application is running, then retry.',
      cause,
      true,
    )
  }

  const text = await response.text()
  const payload = text ? safeParse(text) : null

  if (!response.ok) {
    const { message, retryable } = extractMessage(response.status, payload)
    throw new ApiError(response.status, message, payload, retryable)
  }
  return payload as T
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

const get = <T,>(path: string) => request<T>(path)
const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
const put = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: 'PUT', body: JSON.stringify(body) })
const del = <T,>(path: string) => request<T>(path, { method: 'DELETE' })

export interface RunRequestPayload {
  profile_id?: string | null
  query: ScoutQuery
  sensor_config: SensorConfiguration
  connection_id?: string | null
  csv_template_id: string
  dry_run: boolean
  limit?: number | null
  rule_overrides: Record<string, boolean>
  error_detection: string[]
}

export const api = {
  health: () => get<Record<string, unknown>>('/health'),
  environment: () => get<Record<string, unknown>>('/system/environment'),
  systemHealth: () => get<Record<string, unknown>>('/system/health'),
  productionReadiness: () => get<Record<string, unknown>>('/system/production-readiness'),
  home: () => get<Record<string, unknown>>('/home'),

  connections: () =>
    get<{ connections: ConnectionSummary[]; available_adapters: unknown[]; note: string }>('/connections'),
  testConnection: (id: string) => post<Record<string, unknown>>(`/connections/${id}/test`),
  testAllConnections: () => post<Record<string, unknown>>('/connections/test-all'),
  updateConnection: (id: string, body: unknown) => put<ConnectionSummary>(`/connections/${id}`, body),
  discoverSchema: (id: string) => post<Record<string, unknown>>(`/connections/${id}/discover-schema`),
  getSchema: (id: string) => get<Record<string, unknown>>(`/connections/${id}/schema`),
  saveFieldMapping: (id: string, mapping: Record<string, string>) =>
    put<Record<string, unknown>>(`/connections/${id}/field-mapping`, { mapping }),

  filters: (connectionId?: string) =>
    get<FilterVocabulary>(`/taxonomy/filters${connectionId ? `?connection_id=${connectionId}` : ''}`),
  dependentFilters: (query: ScoutQuery, connectionId?: string) =>
    post<FilterVocabulary>(
      `/taxonomy/dependent-filters${connectionId ? `?connection_id=${connectionId}` : ''}`,
      query,
    ),
  estimate: (query: ScoutQuery, connectionId?: string) =>
    post<{ estimated_records: number | null; is_exact: boolean; note: string }>(
      `/taxonomy/estimate${connectionId ? `?connection_id=${connectionId}` : ''}`,
      query,
    ),

  profiles: () => get<{ profiles: Record<string, unknown>[] }>('/profiles'),
  saveProfile: (id: string, body: unknown) => put<Record<string, unknown>>(`/profiles/${id}`, body),
  deleteProfile: (id: string) => del<Record<string, unknown>>(`/profiles/${id}`),

  previewRun: (body: RunRequestPayload) => post<QueryPreview>('/runs/preview', body),
  createRun: (body: RunRequestPayload) => post<RunSummary>('/runs', body),
  runs: () => get<{ runs: RunSummary[]; resumable: string[] }>('/runs'),
  run: (id: string) => get<RunSummary>(`/runs/${id}`),
  latestRun: () => get<{ run: RunSummary | null; note?: string }>('/runs/latest'),
  pauseRun: (id: string) => post<Record<string, unknown>>(`/runs/${id}/pause`),
  resumeRun: (id: string) => post<Record<string, unknown>>(`/runs/${id}/resume`),
  cancelRun: (id: string) => post<Record<string, unknown>>(`/runs/${id}/cancel`),
  repeatRun: (id: string) => post<RunSummary>(`/runs/${id}/repeat`),
  runFiles: (id: string) =>
    get<{ directory: string; files: { name: string; size_bytes: number }[] }>(`/reports/runs/${id}/files`),
  submissionPolicy: () => get<Record<string, unknown>>('/runs/-/submission-policy'),

  events: (params: Record<string, string | number | boolean | undefined>) => {
    const search = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== '') search.set(key, String(value))
    })
    return get<{ events: EventSummary[]; total: number }>(`/events?${search.toString()}`)
  },
  eventDetail: (key: string) => get<Record<string, unknown>>(`/events/${key}`),

  reviewQueues: () => get<Record<string, unknown>>('/review/queues'),
  reviewQueue: (queue: string, limit = 50, offset = 0) =>
    get<{ queue: string; total: number; items: Record<string, unknown>[] }>(
      `/review/queue/${queue}?limit=${limit}&offset=${offset}`,
    ),
  reviewDetail: (key: string) => get<ReviewDetail>(`/review/${key}`),
  submitDecisions: (key: string, body: unknown) =>
    post<Record<string, unknown>>(`/review/${key}/decisions`, body),

  rules: () => get<{ rules: RuleDefinition[]; summary: Record<string, number>; note: string }>('/rules'),
  confidencePolicy: () => get<Record<string, unknown>>('/rules/confidence-policy'),
  reloadRules: () => post<Record<string, unknown>>('/rules/reload'),

  csvTemplates: () => get<Record<string, unknown>>('/reports/templates'),
  previewExport: (body: unknown) =>
    post<{
      headers: string[]
      rows: Record<string, string>[]
      total_rows: number
      readiness: ExportReadiness
      issues: Record<string, unknown>[]
      note?: string
    }>('/reports/preview', body),
  runExport: (body: unknown) => post<Record<string, unknown>>('/reports/export', body),

  evidence: (params: Record<string, string | undefined>) => {
    const search = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value) search.set(key, value)
    })
    return get<{ items: Record<string, unknown>[]; note: string }>(`/evidence?${search.toString()}`)
  },
  redactionPolicy: () => get<Record<string, unknown>>('/evidence/redaction-policy'),

  analyticsOverview: (days = 30) => get<Record<string, unknown>>(`/analytics/overview?days=${days}`),
  errorBreakdown: (params: Record<string, string | number | undefined>) => {
    const search = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== '') search.set(key, String(value))
    })
    return get<Record<string, unknown>>(`/analytics/error-breakdown?${search.toString()}`)
  },
  reviewQuality: () => get<Record<string, unknown>>('/analytics/review-quality'),
  performance: () => get<Record<string, unknown>>('/analytics/performance'),
  sensorQuality: () => get<Record<string, unknown>>('/analytics/sensor-quality'),

  audit: (params: Record<string, string | number | undefined>) => {
    const search = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== '') search.set(key, String(value))
    })
    return get<{ entries: Record<string, unknown>[]; total: number }>(`/audit?${search.toString()}`)
  },

  adminSettings: () => get<Record<string, unknown>>('/admin/settings'),
  adminRoles: () => get<Record<string, unknown>>('/admin/roles'),
  adminRetention: () => get<Record<string, unknown>>('/admin/retention'),
  reloadConfiguration: () => post<Record<string, unknown>>('/admin/reload', { reload_config: true }),
}

export function downloadUrl(runId: string, filename: string): string {
  return `${API_PREFIX}/reports/runs/${runId}/download/${encodeURIComponent(filename)}`
}

export function evidenceUrl(runId: string, eventRef: string, filename: string): string {
  return `${API_PREFIX}/evidence/file/${runId}/${eventRef}/${encodeURIComponent(filename)}`
}

import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  FormControlLabel,
  Grid,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useEffect, useState } from 'react'

import { VirtualTable } from '@/components/VirtualTable'
import {
  ErrorBanner,
  LoadingBlock,
  Metric,
  PageHeader,
  SectionCard,
} from '@/components/common'
import { useApi } from '@/hooks/useApi'
import { api, downloadUrl } from '@/services/api'
import type { ExportReadiness } from '@/types'

interface PreviewResult {
  headers: string[]
  rows: Record<string, string>[]
  total_rows: number
  readiness: ExportReadiness
  issues: Record<string, unknown>[]
  note?: string
  template?: { columns: { key: string; header: string; required: boolean }[] }
}

export function Reports() {
  const { data: templates } = useApi(() => api.csvTemplates())
  const { data: runs } = useApi(() => api.runs())

  const [templateId, setTemplateId] = useState('germany_bus_test')
  const [runId, setRunId] = useState('')
  // `null` means "every column in the template" — this avoids an effect that
  // would fight with the template list arriving asynchronously.
  const [selectedColumns, setSelectedColumns] = useState<string[] | null>(null)
  const [preview, setPreview] = useState<PreviewResult | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [exportResult, setExportResult] = useState<Record<string, unknown> | null>(null)
  const [search, setSearch] = useState('')

  const templateList = ((templates?.templates as { id: string; name: string; columns: { key: string; header: string; required: boolean }[] }[]) ?? [])
  const template = templateList.find((t) => t.id === templateId)

  const columns = selectedColumns ?? (template?.columns ?? []).map((column) => column.key)

  useEffect(() => {
    setSelectedColumns(null)
  }, [templateId])

  useEffect(() => {
    if (!runId && runs?.runs?.length) setRunId(runs.runs[0].run_id)
  }, [runs, runId])

  const loadPreview = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = (await api.previewExport({
        run_id: runId || null,
        template_id: templateId,
        columns,
        preview_only: true,
      })) as PreviewResult
      setPreview(result)
    } catch (cause) {
      setError(cause)
    } finally {
      setBusy(false)
    }
  }

  const runExport = async () => {
    setBusy(true)
    setError(null)
    setExportResult(null)
    try {
      setExportResult(
        await api.runExport({ run_id: runId || null, template_id: templateId, columns, preview_only: false }),
      )
    } catch (cause) {
      setError(cause)
    } finally {
      setBusy(false)
    }
  }

  const readiness = preview?.readiness
  const filteredRows = search
    ? (preview?.rows ?? []).filter((row) =>
        Object.values(row).some((value) => value.toLowerCase().includes(search.toLowerCase())),
      )
    : preview?.rows ?? []

  return (
    <Box>
      <PageHeader
        title="CSV / Reports"
        subtitle="Export readiness is checked before anything is written. A record with a blocking issue goes to rejected_records.csv with the rule that rejected it."
        actions={
          <>
            <Button onClick={loadPreview} disabled={busy}>
              PREVIEW
            </Button>
            <Button variant="contained" onClick={runExport} disabled={busy || !readiness?.ready}>
              EXPORT CSV
            </Button>
          </>
        }
      />

      <ErrorBanner error={error} title="Export blocked" />

      {exportResult && (
        <Alert severity="success" sx={{ mb: 2 }}>
          Wrote {String(exportResult.rows_written)} row(s) to <code>{String(exportResult.results_csv)}</code>.{' '}
          {Number(exportResult.rejected_records) > 0 &&
            `${exportResult.rejected_records} record(s) written to rejected_records.csv.`}
        </Alert>
      )}

      <Grid container spacing={2}>
        <Grid item xs={12} md={3}>
          <SectionCard title="Template">
            <Stack spacing={2}>
              <TextField select fullWidth label="Template" value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
                {templateList.map((t) => (
                  <MenuItem key={t.id} value={t.id}>
                    {t.name}
                  </MenuItem>
                ))}
              </TextField>
              <TextField select fullWidth label="Run" value={runId} onChange={(e) => setRunId(e.target.value)}>
                <MenuItem value="">All records</MenuItem>
                {(runs?.runs ?? []).map((run) => (
                  <MenuItem key={run.run_id} value={run.run_id}>
                    {run.run_id}
                  </MenuItem>
                ))}
              </TextField>
            </Stack>
          </SectionCard>

          <SectionCard title="Columns" subtitle="Mandatory columns cannot be removed." dense>
            <Box sx={{ maxHeight: 420, overflowY: 'auto' }}>
              {(template?.columns ?? []).map((column) => (
                <FormControlLabel
                  key={column.key}
                  sx={{ display: 'flex', ml: 0 }}
                  control={
                    <Checkbox
                      size="small"
                      checked={columns.includes(column.key) || column.required}
                      disabled={column.required}
                      onChange={(event) =>
                        setSelectedColumns(
                          event.target.checked
                            ? [...columns, column.key]
                            : columns.filter((key) => key !== column.key),
                        )
                      }
                    />
                  }
                  label={
                    <Typography variant="caption">
                      {column.header}
                      {column.required ? ' *' : ''}
                    </Typography>
                  }
                />
              ))}
            </Box>
          </SectionCard>
        </Grid>

        <Grid item xs={12} md={9}>
          <SectionCard
            title="Export readiness"
            actions={
              runId ? (
                <Button component="a" href={downloadUrl(runId, 'results.csv')} download>
                  DOWNLOAD results.csv
                </Button>
              ) : undefined
            }
          >
            {!readiness ? (
              <Typography variant="body2" color="text.secondary">
                Run a preview to check export readiness.
              </Typography>
            ) : (
              <>
                <Stack direction="row" spacing={4} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
                  <Metric label="Passed" value={readiness.passed} colour="#48bb78" />
                  <Metric label="Warnings" value={readiness.warnings} colour="#f6ad55" />
                  <Metric label="Blocking errors" value={readiness.blocking_errors} colour="#fc8181" />
                  <Metric label="Exportable rows" value={readiness.exportable_rows} />
                  <Metric label="Rejected rows" value={readiness.rejected_rows} colour="#fc8181" />
                </Stack>
                <Alert severity={readiness.ready ? 'success' : 'error'}>
                  {readiness.ready
                    ? 'CSV READY — every row passed export validation.'
                    : `CSV NOT READY — ${readiness.blocking_errors} blocking error(s) must be resolved. Blocked rows are written to rejected_records.csv with the rule that rejected them.`}
                </Alert>

                {readiness.issues.length > 0 && (
                  <Box sx={{ mt: 2, maxHeight: 200, overflowY: 'auto' }}>
                    {readiness.issues.slice(0, 50).map((issue, index) => {
                      const record = issue as Record<string, unknown>
                      return (
                        <Stack key={index} direction="row" spacing={1} alignItems="center" sx={{ py: 0.25 }}>
                          <Chip label={String(record.severity)} color="error" sx={{ height: 18, fontSize: '0.6rem' }} />
                          <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>
                            {String(record.rule_id)}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {String(record.message)}
                          </Typography>
                        </Stack>
                      )
                    })}
                  </Box>
                )}
              </>
            )}
          </SectionCard>

          <SectionCard
            title="Preview"
            subtitle={preview ? `${preview.total_rows} row(s) total` : undefined}
            actions={
              <TextField
                size="small"
                placeholder="Filter rows"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            }
            dense
          >
            {busy && !preview ? (
              <LoadingBlock label="Building preview…" />
            ) : !preview ? (
              <Typography variant="body2" color="text.secondary">
                {preview === null ? 'No preview yet.' : ''}
              </Typography>
            ) : preview.rows.length === 0 ? (
              <Alert severity="info">{preview.note ?? 'No rows match this selection.'}</Alert>
            ) : (
              <VirtualTable
                rows={filteredRows}
                keyOf={(_, index) => String(index)}
                height={420}
                columns={preview.headers
                  .slice(0, 14)
                  .map((header) => ({
                    key: header,
                    header,
                    width: 150,
                    render: (row: Record<string, string>) => row[header] ?? '',
                  }))}
              />
            )}
          </SectionCard>
        </Grid>
      </Grid>
    </Box>
  )
}

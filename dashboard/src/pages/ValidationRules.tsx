import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Grid,
  Stack,
  Switch,
  Typography,
} from '@mui/material'
import { useState } from 'react'

import {
  ErrorBanner,
  LoadingBlock,
  Metric,
  PageHeader,
  SectionCard,
  StatusChip,
} from '@/components/common'
import { useApi } from '@/hooks/useApi'
import { api } from '@/services/api'
import { useAppStore } from '@/stores/useAppStore'
import type { RuleDefinition } from '@/types'

export function ValidationRules() {
  const store = useAppStore()
  const { data, error, loading, reload } = useApi(() => api.rules())
  const { data: policy } = useApi(() => api.confidencePolicy())
  const [selected, setSelected] = useState<RuleDefinition | null>(null)

  if (loading && !data) return <LoadingBlock label="Loading rule catalogue…" />

  const rules = data?.rules ?? []
  const categories = Array.from(new Set(rules.map((rule) => rule.category)))

  return (
    <Box>
      <PageHeader
        title="Validation Rules"
        subtitle={data?.note}
        actions={
          <Button
            onClick={async () => {
              await api.reloadRules()
              reload()
            }}
          >
            RELOAD FROM DISK
          </Button>
        }
      />

      <ErrorBanner error={error} onRetry={reload} />

      <SectionCard dense>
        <Stack direction="row" spacing={4} flexWrap="wrap" useFlexGap>
          <Metric label="Rules" value={data?.summary?.total ?? 0} />
          <Metric label="Enabled" value={data?.summary?.enabled ?? 0} colour="#48bb78" />
          <Metric
            label="Awaiting project threshold"
            value={data?.summary?.awaiting_project_threshold ?? 0}
            colour="#f6ad55"
            hint="disabled until an approved value is supplied"
          />
          <Metric label="Not implemented" value={data?.summary?.not_implemented ?? 0} />
        </Stack>
      </SectionCard>

      <Box sx={{ mt: 2 }}>
        {categories.map((category) => (
          <SectionCard key={category} title={category} dense>
            <Grid container spacing={1}>
              {rules
                .filter((rule) => rule.category === category)
                .map((rule) => {
                  const override = store.ruleOverrides[rule.id]
                  const enabled = override ?? rule.enabled
                  return (
                    <Grid item xs={12} md={6} xl={4} key={rule.id}>
                      <Box sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 1, p: 1 }}>
                        <Stack direction="row" justifyContent="space-between" alignItems="center">
                          <Typography
                            variant="body2"
                            sx={{ fontFamily: 'monospace', cursor: 'pointer' }}
                            onClick={() => setSelected(rule)}
                          >
                            {rule.id}
                          </Typography>
                          <Stack direction="row" spacing={0.5} alignItems="center">
                            <StatusChip value={rule.severity} />
                            <Switch
                              size="small"
                              checked={enabled}
                              disabled={rule.awaiting_project_threshold}
                              onChange={(event) => store.setRuleOverride(rule.id, event.target.checked)}
                            />
                          </Stack>
                        </Stack>
                        <Typography variant="caption" color="text.secondary" component="div">
                          {rule.description}
                        </Typography>
                        <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }} flexWrap="wrap" useFlexGap>
                          <Chip label={`v${rule.version}`} sx={{ height: 18, fontSize: '0.6rem' }} />
                          {rule.blocks_processing && <Chip label="blocks processing" color="error" sx={{ height: 18, fontSize: '0.6rem' }} />}
                          {rule.blocks_export && <Chip label="blocks export" color="warning" sx={{ height: 18, fontSize: '0.6rem' }} />}
                          {rule.requires_review && <Chip label="review" sx={{ height: 18, fontSize: '0.6rem' }} />}
                          {rule.awaiting_project_threshold && (
                            <Chip label="AWAITING APPROVED THRESHOLD" color="warning" sx={{ height: 18, fontSize: '0.6rem' }} />
                          )}
                          {!rule.implemented && <Chip label="not implemented" sx={{ height: 18, fontSize: '0.6rem' }} />}
                        </Stack>
                      </Box>
                    </Grid>
                  )
                })}
            </Grid>
          </SectionCard>
        ))}
      </Box>

      {policy && (
        <Box sx={{ mt: 2 }}>
        <SectionCard title="Confidence routing policy">
          <Grid container spacing={2}>
            <Grid item xs={12} md={6}>
              <Stack spacing={0.5}>
                {Object.entries((policy.bands ?? {}) as Record<string, Record<string, unknown>>).map(
                  ([name, band]) => (
                    <Stack key={name} direction="row" justifyContent="space-between">
                      <Typography variant="body2">
                        {name} ({Number(band.min) * 100}–{Math.min(100, Number(band.max) * 100)}%)
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {String(band.action)} · {String(band.reviewer)}
                      </Typography>
                    </Stack>
                  ),
                )}
              </Stack>
            </Grid>
            <Grid item xs={12} md={6}>
              <Alert severity="info">
                Hard floor {String(policy.hard_floor)}. Safety-critical fields:{' '}
                {((policy.safety_critical_fields as string[]) ?? []).join(', ')}. A disagreement on any of
                these routes the record to senior review regardless of confidence.
              </Alert>
            </Grid>
          </Grid>
        </SectionCard>
        </Box>
      )}

      {selected && (
        <Dialog open onClose={() => setSelected(null)} maxWidth="sm" fullWidth>
          <DialogTitle sx={{ fontFamily: 'monospace' }}>{selected.id}</DialogTitle>
          <DialogContent>
            <Stack spacing={1}>
              <Detail label="Description" value={selected.description} />
              <Detail label="Category" value={selected.category} />
              <Detail label="Inputs" value={selected.inputs.join(', ') || '—'} />
              <Detail
                label="Threshold"
                value={
                  selected.threshold === null || selected.threshold === undefined
                    ? `none (source: ${selected.threshold_source})`
                    : `${selected.threshold} (source: ${selected.threshold_source})`
                }
              />
              <Detail label="Severity" value={selected.severity} />
              <Detail label="Reviewer required" value={selected.requires_review ? 'Yes' : 'No'} />
              <Detail label="Blocks processing" value={selected.blocks_processing ? 'Yes' : 'No'} />
              <Detail label="Blocks export" value={selected.blocks_export ? 'Yes' : 'No'} />
              <Detail label="Version" value={selected.version} />
              <Detail label="State" value={selected.state} />
            </Stack>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setSelected(null)}>CLOSE</Button>
          </DialogActions>
        </Dialog>
      )}
    </Box>
  )
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase' }}>
        {label}
      </Typography>
      <Typography variant="body2">{value}</Typography>
    </Box>
  )
}

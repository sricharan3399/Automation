import ClearIcon from '@mui/icons-material/Clear'
import DoneAllIcon from '@mui/icons-material/DoneAll'
import SearchIcon from '@mui/icons-material/Search'
import {
  Box,
  Button,
  Checkbox,
  Chip,
  InputAdornment,
  ListItemText,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useMemo, useState } from 'react'

import { OriginChip } from '@/components/common'

/**
 * Multi-select with search, Select All and Clear.
 *
 * An empty selection means "Any" — stated explicitly in the UI, because a blank
 * filter that silently meant "nothing" would produce empty runs with no
 * explanation.
 */
export function MultiSelectFilter({
  label,
  values,
  selected,
  origin,
  onChange,
  helper,
  maxHeight = 220,
}: {
  label: string
  values: string[]
  selected: string[]
  origin?: 'source' | 'fallback'
  onChange: (next: string[]) => void
  helper?: string
  maxHeight?: number
}) {
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase()
    if (!needle) return values
    return values.filter((value) => value.toLowerCase().includes(needle))
  }, [values, search])

  const toggle = (value: string) => {
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value])
  }

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
        <Typography variant="subtitle2">{label}</Typography>
        {origin && <OriginChip origin={origin} />}
        <Chip
          label={selected.length === 0 ? 'Any' : `${selected.length} selected`}
          sx={{ height: 18, fontSize: '0.62rem' }}
        />
      </Stack>
      {helper && (
        <Typography variant="caption" color="text.secondary" component="div" sx={{ mb: 0.5 }}>
          {helper}
        </Typography>
      )}

      {values.length === 0 ? (
        <Typography variant="caption" color="text.secondary">
          No values available for this field.
        </Typography>
      ) : (
        <>
          <Stack direction="row" spacing={1} sx={{ mb: 0.5 }}>
            <TextField
              placeholder="Search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              fullWidth
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" />
                  </InputAdornment>
                ),
              }}
            />
            <Button
              startIcon={<DoneAllIcon />}
              onClick={() => onChange(Array.from(new Set([...selected, ...filtered])))}
            >
              All
            </Button>
            <Button startIcon={<ClearIcon />} onClick={() => onChange([])} disabled={selected.length === 0}>
              Clear
            </Button>
          </Stack>

          <Box
            sx={{
              maxHeight,
              overflowY: 'auto',
              border: '1px solid',
              borderColor: 'divider',
              borderRadius: 1,
            }}
          >
            {filtered.map((value) => (
              <MenuItem key={value} dense onClick={() => toggle(value)} sx={{ py: 0 }}>
                <Checkbox checked={selected.includes(value)} size="small" sx={{ py: 0.25 }} />
                <ListItemText primaryTypographyProps={{ variant: 'body2' }} primary={value} />
              </MenuItem>
            ))}
            {filtered.length === 0 && (
              <Typography variant="caption" color="text.secondary" sx={{ p: 1, display: 'block' }}>
                No matches for “{search}”.
              </Typography>
            )}
          </Box>
        </>
      )}
    </Box>
  )
}

import { Box, Typography } from '@mui/material'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useRef } from 'react'
import type { ReactNode } from 'react'

export interface Column<T> {
  key: string
  header: string
  width?: number | string
  align?: 'left' | 'right' | 'center'
  render: (row: T, index: number) => ReactNode
}

/**
 * Row-virtualised table.
 *
 * A scout run routinely returns thousands of events; rendering them all would
 * make scrolling unusable, so only the visible window is mounted.
 */
export function VirtualTable<T>({
  rows,
  columns,
  rowHeight = 34,
  height = 520,
  onRowClick,
  selectedKey,
  keyOf,
  emptyMessage = 'No rows.',
}: {
  rows: T[]
  columns: Column<T>[]
  rowHeight?: number
  height?: number | string
  onRowClick?: (row: T, index: number) => void
  selectedKey?: string
  keyOf: (row: T, index: number) => string
  emptyMessage?: string
}) {
  const parentRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    overscan: 12,
  })

  const template = columns.map((c) => (typeof c.width === 'number' ? `${c.width}px` : c.width ?? '1fr')).join(' ')

  return (
    <Box sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 1, overflow: 'hidden' }}>
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: template,
          bgcolor: 'background.default',
          borderBottom: '1px solid',
          borderColor: 'divider',
          px: 1,
          py: 0.75,
          position: 'sticky',
          top: 0,
          zIndex: 1,
        }}
      >
        {columns.map((column) => (
          <Typography
            key={column.key}
            variant="caption"
            sx={{
              fontWeight: 700,
              textTransform: 'uppercase',
              letterSpacing: 0.5,
              textAlign: column.align ?? 'left',
              px: 0.5,
            }}
          >
            {column.header}
          </Typography>
        ))}
      </Box>

      {rows.length === 0 ? (
        <Typography variant="body2" color="text.secondary" sx={{ p: 3, textAlign: 'center' }}>
          {emptyMessage}
        </Typography>
      ) : (
        <Box ref={parentRef} sx={{ height, overflow: 'auto' }}>
          <Box sx={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
            {virtualizer.getVirtualItems().map((virtualRow) => {
              const row = rows[virtualRow.index]
              const key = keyOf(row, virtualRow.index)
              const selected = selectedKey === key
              return (
                <Box
                  key={key}
                  onClick={() => onRowClick?.(row, virtualRow.index)}
                  sx={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    width: '100%',
                    height: virtualRow.size,
                    transform: `translateY(${virtualRow.start}px)`,
                    display: 'grid',
                    gridTemplateColumns: template,
                    alignItems: 'center',
                    px: 1,
                    cursor: onRowClick ? 'pointer' : 'default',
                    bgcolor: selected ? 'action.selected' : undefined,
                    borderBottom: '1px solid',
                    borderColor: 'divider',
                    '&:hover': onRowClick ? { bgcolor: 'action.hover' } : undefined,
                  }}
                >
                  {columns.map((column) => (
                    <Box
                      key={column.key}
                      sx={{
                        px: 0.5,
                        textAlign: column.align ?? 'left',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        fontSize: '0.8rem',
                      }}
                    >
                      {column.render(row, virtualRow.index)}
                    </Box>
                  ))}
                </Box>
              )
            })}
          </Box>
        </Box>
      )}
    </Box>
  )
}

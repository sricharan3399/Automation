import AdminIcon from '@mui/icons-material/AdminPanelSettings'
import AnalyticsIcon from '@mui/icons-material/Insights'
import AuditIcon from '@mui/icons-material/FactCheck'
import ReadinessIcon from '@mui/icons-material/VerifiedUser'
import ConnectionsIcon from '@mui/icons-material/Cable'
import EvidenceIcon from '@mui/icons-material/PhotoLibrary'
import ExplorerIcon from '@mui/icons-material/TableView'
import HealthIcon from '@mui/icons-material/MonitorHeart'
import HomeIcon from '@mui/icons-material/Home'
import LiveIcon from '@mui/icons-material/Bolt'
import MapIcon from '@mui/icons-material/Map'
import ProfilesIcon from '@mui/icons-material/Bookmarks'
import ReportsIcon from '@mui/icons-material/Description'
import ReviewIcon from '@mui/icons-material/RateReview'
import RulesIcon from '@mui/icons-material/Gavel'
import RunsIcon from '@mui/icons-material/PlaylistPlay'
import ScenarioIcon from '@mui/icons-material/DirectionsBus'
import SearchIcon from '@mui/icons-material/TravelExplore'
import SensorsIcon from '@mui/icons-material/Sensors'
import {
  AppBar,
  Box,
  Chip,
  CssBaseline,
  Drawer,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Stack,
  ThemeProvider,
  Toolbar,
  Typography,
} from '@mui/material'
import type { ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

import { useApi } from '@/hooks/useApi'
import { AutomationRuns } from '@/pages/AutomationRuns'
import { Connections } from '@/pages/Connections'
import { EventExplorer } from '@/pages/EventExplorer'
import { Home } from '@/pages/Home'
import { LiveProcessing } from '@/pages/LiveProcessing'
import { MapLaneSetup } from '@/pages/MapLaneSetup'
import { Reports } from '@/pages/Reports'
import { ReviewQueue } from '@/pages/ReviewQueue'
import { ScenarioBuilder } from '@/pages/ScenarioBuilder'
import { ScoutSetup } from '@/pages/ScoutSetup'
import { SensorConfiguration } from '@/pages/SensorConfiguration'
import { ValidationRules } from '@/pages/ValidationRules'
import {
  Administration,
  AuditLogs,
  ConfigurationProfiles,
  EvidenceViewer,
  ProductionReadiness,
  QualityAnalytics,
  SystemHealth,
} from '@/pages/misc'
import { api } from '@/services/api'
import { theme } from '@/theme'

const DRAWER_WIDTH = 232

interface NavItem {
  path: string
  label: string
  icon: ReactNode
  element: ReactNode
}

const NAV: NavItem[] = [
  { path: '/home', label: 'Home', icon: <HomeIcon />, element: <Home /> },
  { path: '/connections', label: 'Connections', icon: <ConnectionsIcon />, element: <Connections /> },
  { path: '/scout-setup', label: 'Scout Setup', icon: <SearchIcon />, element: <ScoutSetup /> },
  { path: '/scenario', label: 'Scenario Builder', icon: <ScenarioIcon />, element: <ScenarioBuilder /> },
  { path: '/sensors', label: 'Sensor Configuration', icon: <SensorsIcon />, element: <SensorConfiguration /> },
  { path: '/map', label: 'Map & Lane Setup', icon: <MapIcon />, element: <MapLaneSetup /> },
  { path: '/rules', label: 'Validation Rules', icon: <RulesIcon />, element: <ValidationRules /> },
  { path: '/runs', label: 'Automation Runs', icon: <RunsIcon />, element: <AutomationRuns /> },
  { path: '/live', label: 'Live Processing', icon: <LiveIcon />, element: <LiveProcessing /> },
  { path: '/events', label: 'Event Explorer', icon: <ExplorerIcon />, element: <EventExplorer /> },
  { path: '/review', label: 'Review Queue', icon: <ReviewIcon />, element: <ReviewQueue /> },
  { path: '/evidence', label: 'Evidence Viewer', icon: <EvidenceIcon />, element: <EvidenceViewer /> },
  { path: '/reports', label: 'CSV / Reports', icon: <ReportsIcon />, element: <Reports /> },
  { path: '/analytics', label: 'Quality Analytics', icon: <AnalyticsIcon />, element: <QualityAnalytics /> },
  { path: '/profiles', label: 'Configuration Profiles', icon: <ProfilesIcon />, element: <ConfigurationProfiles /> },
  { path: '/audit', label: 'Audit Logs', icon: <AuditIcon />, element: <AuditLogs /> },
  { path: '/system', label: 'System Health', icon: <HealthIcon />, element: <SystemHealth /> },
  {
    path: '/readiness',
    label: 'Production Readiness',
    icon: <ReadinessIcon />,
    element: <ProductionReadiness />,
  },
  { path: '/admin', label: 'Administration', icon: <AdminIcon />, element: <Administration /> },
]

export function AppShell() {
  const navigate = useNavigate()
  const location = useLocation()
  const { data: health } = useApi(() => api.health())
  const { data: roles } = useApi(() => api.adminRoles())

  const mode = String(health?.operating_mode ?? '')
  const submission = Boolean(health?.production_submission_enabled)
  const identity = (roles?.current ?? {}) as { user?: string; role?: string }

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh' }}>
      <AppBar
        position="fixed"
        color="default"
        sx={{ zIndex: (t) => t.zIndex.drawer + 1, borderBottom: '1px solid', borderColor: 'divider' }}
      >
        <Toolbar variant="dense" sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>
            AV Test Automation
          </Typography>
          <Stack direction="row" spacing={1} sx={{ flexGrow: 1 }}>
            <Chip label={`v${health?.software_version ?? '—'}`} />
            <Chip label={`contract ${health?.contract_version ?? '—'}`} />
            <Chip label={String(health?.rule_version ?? 'rules —')} />
          </Stack>
          <Chip
            label={`${identity.user ?? '—'} · ${identity.role ?? '—'}`}
            title="Role determines which pages you may use. Set AV_LOCAL_ROLE to change it."
          />
          <Chip
            label={mode === 'demo' ? 'DEMO MODE' : 'PRODUCTION MODE'}
            color={mode === 'demo' ? 'warning' : 'default'}
          />
          <Chip label={`source: ${String(health?.source_access_mode ?? 'read only').replace('_', ' ')}`} />
          <Chip
            label={submission ? 'SUBMISSION ENABLED' : 'SUBMISSION DISABLED'}
            color={submission ? 'error' : 'success'}
          />
        </Toolbar>
      </AppBar>

      <Drawer
        variant="permanent"
        sx={{
          width: DRAWER_WIDTH,
          flexShrink: 0,
          '& .MuiDrawer-paper': { width: DRAWER_WIDTH, boxSizing: 'border-box' },
        }}
      >
        <Toolbar variant="dense" />
        <List dense sx={{ py: 0 }}>
          {NAV.map((item, index) => (
            <ListItemButton
              key={item.path}
              selected={location.pathname.startsWith(item.path)}
              onClick={() => navigate(item.path)}
              sx={{ py: 0.6 }}
            >
              <ListItemIcon sx={{ minWidth: 34, fontSize: 18 }}>{item.icon}</ListItemIcon>
              <ListItemText
                primary={`${index + 1}. ${item.label}`}
                primaryTypographyProps={{ fontSize: '0.78rem' }}
              />
            </ListItemButton>
          ))}
        </List>
      </Drawer>

      <Box component="main" sx={{ flexGrow: 1, p: 2, width: `calc(100% - ${DRAWER_WIDTH}px)` }}>
        <Toolbar variant="dense" />
        <Routes>
          <Route path="/" element={<Navigate to="/home" replace />} />
          {NAV.map((item) => (
            <Route key={item.path} path={item.path} element={item.element} />
          ))}
          <Route path="*" element={<Navigate to="/home" replace />} />
        </Routes>
      </Box>
    </Box>
  )
}

export function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <AppShell />
    </ThemeProvider>
  )
}

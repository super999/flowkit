import { useState, useEffect } from 'react'
import { BrowserRouter, NavLink, Routes, Route, useLocation, useParams, useSearchParams } from 'react-router-dom'
import { LayoutDashboard, FolderOpen, Film, ScrollText } from 'lucide-react'
import { TooltipProvider } from '@/components/ui/tooltip'
import { WebSocketProvider } from './api/WebSocketContext'
import { useWebSocketContext } from './api/useWebSocketContext'
import { LanguageProvider } from './i18n/LanguageContext'
import { useTranslation } from './i18n/useTranslation'
import { LANGS, LANG_LABELS, type Lang } from './i18n/translations'
import type { TranslationKey } from './i18n/translations'
import { fetchAPI } from './api/client'
import type { Project } from './types'
import DashboardPage from './pages/DashboardPage'
import ProjectsPage from './pages/ProjectsPage'
import LogsPage from './pages/LogsPage'
import GalleryPage from './pages/GalleryPage'
import GuidePage from './pages/GuidePage'
import Txt2ImgPage from './pages/Txt2ImgPage'
import ProviderSettingsPage from './pages/SettingsPage'

const BREADCRUMB_TAB_KEY: Record<string, TranslationKey> = {
  overview: 'app.breadcrumbTab.overview',
  characters: 'app.breadcrumbTab.characters',
  videos: 'app.breadcrumbTab.videos',
  pipeline: 'app.breadcrumbTab.pipeline',
}

function useClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  return now
}

function useBreadcrumbs() {
  const { t } = useTranslation()
  const loc = useLocation()
  const { id } = useParams<{ id?: string }>()
  const [searchParams] = useSearchParams()
  const [projectName, setProjectName] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    fetchAPI<Project>(`/api/projects/${id}`).then(p => setProjectName(p.name)).catch(() => setProjectName(null))
  }, [id])

  const crumbs: string[] = []
  if (loc.pathname === '/') crumbs.push(t('app.breadcrumb.dashboard'))
  else if (loc.pathname.startsWith('/projects')) {
    crumbs.push(t('app.breadcrumb.projects'))
    if (id) {
      crumbs.push(projectName ?? '…')
      const tab = searchParams.get('tab')
      const tabKey = tab ? BREADCRUMB_TAB_KEY[tab] : undefined
      if (tabKey) crumbs.push(t(tabKey))
    }
  } else if (loc.pathname.startsWith('/gallery')) crumbs.push(t('app.breadcrumb.gallery'))
  else if (loc.pathname.startsWith('/logs')) crumbs.push(t('app.breadcrumb.logs'))
  else if (loc.pathname.startsWith('/guide')) crumbs.push(t('app.breadcrumb.guide'))
  else if (loc.pathname.startsWith('/settings')) {
    crumbs.push(t('app.breadcrumb.settings'))
    if (loc.pathname.includes('/llm-test')) crumbs.push('LLM 接口测试')
    else if (loc.pathname.includes('/llm')) crumbs.push('大语言模型')
  }

  return crumbs
}

function LanguageSwitcher() {
  const { lang, setLang } = useTranslation()
  return (
    <select
      value={lang}
      onChange={e => setLang(e.target.value as Lang)}
      className="text-[10px] px-2 py-1 rounded outline-none w-full"
      style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
    >
      {LANGS.map(l => (
        <option key={l} value={l}>{LANG_LABELS[l]}</option>
      ))}
    </select>
  )
}

import ImageStudioPage from './pages/studio/ImageStudioPage'
import ReferenceLibraryPage from './pages/studio/ReferenceLibraryPage'
import LegacySettingsPage from './pages/settings/SettingsPage'
import LLMTestPage from './pages/settings/LLMTestPage'
import { Sparkles, Image as ImageIcon, Users, Zap, Settings, BrainCircuit, FlaskConical, BookOpen } from 'lucide-react'

interface NavItem {
  to: string
  label: string
  icon: React.ReactNode
  end?: boolean
  accent?: boolean
  children?: NavItem[]
}

const NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: <LayoutDashboard size={13} />, end: true },
  {
    to: '/studio',
    label: '🎨 AI 生图工坊',
    icon: <Sparkles size={13} />,
    accent: true,
    children: [
      { to: '/studio/scenes', label: '🎬 分镜生图', icon: <ImageIcon size={11} /> },
      { to: '/studio/characters', label: '👥 角色参考图', icon: <Users size={11} /> },
      { to: '/studio/refgen', label: '🖼️ 参考图生图', icon: <ImageIcon size={11} /> },
      { to: '/studio/ref-library', label: '📚 参考图库', icon: <BookOpen size={11} /> },
      { to: '/studio/batch', label: '⚡ 全套批生', icon: <Zap size={11} /> },
    ],
  },
  { to: '/projects', label: '项目列表', icon: <FolderOpen size={13} /> },
  { to: '/gallery', label: '媒体画廊', icon: <Film size={13} /> },
  { to: '/logs', label: '请求日志', icon: <ScrollText size={13} /> },
  { to: '/guide', label: '使用指南', icon: <BookOpen size={13} /> },
  {
    to: '/settings',
    label: '系统设置',
    icon: <Settings size={13} />,
    children: [
      { to: '/settings/llm', label: '大语言模型', icon: <BrainCircuit size={11} /> },
      { to: '/settings/llm-test', label: 'LLM 接口测试', icon: <FlaskConical size={11} /> },
    ],
  },
]

function SidebarNavItem({ item, depth }: { item: NavItem; depth: number }) {
  const loc = useLocation()
  const parentActive = depth === 0 && (loc.pathname === item.to || loc.pathname.startsWith(`${item.to}/`))

  return (
    <div className="flex flex-col gap-0.5">
      <NavLink
        to={item.to}
        end={item.end}
        className="flex items-center gap-2.5 px-2.5 py-2 rounded text-xs transition-colors hover:opacity-90"
        style={({ isActive }) => {
          const active = item.end ? isActive : parentActive
          return {
            background: item.accent
              ? active ? 'var(--card)' : 'rgba(139, 92, 246, 0.1)'
              : active ? 'var(--card)' : 'transparent',
            color: item.accent ? 'var(--accent)' : active ? 'var(--text)' : 'var(--muted)',
            fontWeight: item.accent ? '600' : '400',
            borderLeft: `2px solid ${active ? 'var(--accent)' : 'transparent'}`,
            paddingLeft: depth > 0 ? `${8 + depth * 12}px` : '10px',
          }
        }}
      >
        {item.icon}
        <span>{item.label}</span>
      </NavLink>

      {item.children && (
        <div
          className="flex flex-col gap-0.5 border-l ml-3 my-0.5"
          style={{ borderColor: 'var(--border)' }}
        >
          {item.children.map(child => (
            <SidebarNavItem key={child.to} item={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  )
}

function Sidebar() {
  const { t } = useTranslation()
  const { worker } = useWebSocketContext()
  const [health, setHealth] = useState<{ extension_connected: boolean } | null>(null)

  useEffect(() => {
    fetchAPI<{ extension_connected: boolean }>('/health').then(setHealth).catch(() => setHealth(null))
  }, [])

  return (
    <aside className="w-52 flex-shrink-0 flex flex-col border-r" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      <div className="px-4 py-4 flex items-center gap-2.5 border-b" style={{ borderColor: 'var(--border)' }}>
        <span className="w-[22px] h-[22px] rounded flex items-center justify-center text-xs font-bold" style={{ background: 'var(--accent)', color: 'var(--bg)' }}>F</span>
        <div className="flex flex-col">
          <span className="text-xs font-bold tracking-widest">{t('app.brandName')}</span>
          <span className="text-[9px] tracking-wide" style={{ color: 'var(--muted)' }}>{t('app.brandTag')}</span>
        </div>
      </div>

      <nav className="flex flex-col gap-0.5 px-2.5 py-3 overflow-y-auto">
        {NAV_ITEMS.map(item => (
          <SidebarNavItem key={item.to} item={item} depth={0} />
        ))}
      </nav>

      <div className="mt-auto px-4 py-3.5 border-t flex flex-col gap-2.5" style={{ borderColor: 'var(--border)' }}>
        <LanguageSwitcher />
        <div className="flex items-center justify-between text-[10px] tracking-wide" style={{ color: 'var(--muted)' }}>
          <span>{t('app.workers')}</span>
          <span style={{ color: 'var(--text)' }}>{worker ? `${worker.active}/${worker.active + worker.slots}` : '—'}</span>
        </div>
        <div className="flex items-center gap-1.5 text-[10px]" style={{ color: 'var(--muted)' }}>
          <span
            className="w-1.5 h-1.5 rounded-full"
            style={{ background: health?.extension_connected ? 'var(--green)' : 'var(--red)' }}
          />
          {health?.extension_connected ? t('app.extensionConnected') : health ? t('app.extensionDisconnected') : t('app.extensionChecking')}
        </div>
      </div>
    </aside>
  )
}

import ImageStudioModal from './components/studio/ImageStudioModal'

function Header({ onOpenStudio }: { onOpenStudio: () => void }) {
  const { t } = useTranslation()
  const { isConnected } = useWebSocketContext()
  const crumbs = useBreadcrumbs()
  const clock = useClock()
  const [activeProjName, setActiveProjName] = useState<string>('')

  useEffect(() => {
    fetchAPI<{ project_name?: string }>('/api/active-project')
      .then(res => setActiveProjName(res.project_name || ''))
      .catch(() => {})
  }, [])

  return (
    <header className="flex items-center gap-4 px-5 h-13 flex-shrink-0 border-b" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      <div className="flex items-center gap-2 text-[11px] tracking-wide" style={{ color: 'var(--muted)' }}>
        <span style={{ color: 'var(--accent)' }}>{t('app.breadcrumbRoot')}</span>
        {crumbs.map((c, i) => (
          <span key={i} className="flex items-center gap-2">
            <span>/</span>
            <span style={{ color: i === crumbs.length - 1 ? 'var(--text)' : 'var(--muted)' }}>{c}</span>
          </span>
        ))}
      </div>
      
      {activeProjName && (
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium border" style={{ background: 'var(--card)', borderColor: 'var(--border)' }}>
          <span>🎯 目标项目:</span>
          <span style={{ color: 'var(--accent)' }}>{activeProjName}</span>
        </div>
      )}

      <span className="ml-auto" />

      <button
        onClick={onOpenStudio}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-semibold text-white shadow transition-transform hover:scale-[1.02] active:scale-[0.98]"
        style={{ background: 'linear-gradient(135deg, #8b5cf6, #06b6d4)' }}
      >
        <span>🎨 生图控制台</span>
      </button>

      <div className="flex items-center gap-3.5 text-[10px]" style={{ color: 'var(--muted)' }}>
        <span className="tracking-wide">{clock.toLocaleTimeString()}</span>
        <span className="flex items-center gap-1.5 px-2.5 py-1 rounded border" style={{ borderColor: 'var(--border)', color: isConnected ? 'var(--green)' : 'var(--red)' }}>
          <span
            className="w-1.5 h-1.5 rounded-full"
            style={{ background: isConnected ? 'var(--green)' : 'var(--red)', animation: isConnected ? 'pulse 2s ease-in-out infinite' : 'none' }}
          />
          {isConnected ? t('app.wsLive') : t('app.wsDisconnected')}
        </span>
      </div>
    </header>
  )
}

function Layout() {
  const [studioOpen, setStudioOpen] = useState(false)

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
      <Sidebar />
      <div className="flex flex-col flex-1 overflow-hidden">
        <Header onOpenStudio={() => setStudioOpen(true)} />
        <main className="flex-1 overflow-auto p-5">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/studio" element={<ImageStudioPage />} />
            <Route path="/studio/scenes" element={<ImageStudioPage />} />
            <Route path="/studio/characters" element={<ImageStudioPage />} />
            <Route path="/studio/refgen" element={<ImageStudioPage />} />
            <Route path="/studio/ref-library" element={<ReferenceLibraryPage />} />
            <Route path="/studio/batch" element={<ImageStudioPage />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/projects/:id" element={<ProjectsPage />} />
            <Route path="/gallery" element={<GalleryPage />} />
            <Route path="/logs" element={<LogsPage />} />
            <Route path="/guide" element={<GuidePage />} />
            <Route path="/txt2img" element={<Txt2ImgPage />} />
            <Route path="/settings" element={<ProviderSettingsPage />} />
            <Route path="/settings/llm" element={<LegacySettingsPage />} />
            <Route path="/settings/llm-test" element={<LLMTestPage />} />
          </Routes>
        </main>
      </div>

      <ImageStudioModal
        open={studioOpen}
        onClose={() => setStudioOpen(false)}
      />
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <LanguageProvider>
        <WebSocketProvider>
          <TooltipProvider>
            <Layout />
          </TooltipProvider>
        </WebSocketProvider>
      </LanguageProvider>
    </BrowserRouter>
  )
}

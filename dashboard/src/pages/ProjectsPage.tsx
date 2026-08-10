import { useState, useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { fetchAPI } from '../api/client'
import type { Project } from '../types'
import ProjectDetailPage from './ProjectDetailPage'
import { useTranslation } from '../i18n/useTranslation'
import type { TranslationKey } from '../i18n/translations'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '../components/ui/card'
import { Badge } from '../components/ui/badge'
import { Tabs, TabsList, TabsTrigger } from '../components/ui/tabs'

type FilterTab = 'ACTIVE' | 'ARCHIVED' | 'ALL'

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString()
}

function TierBadge({ tier, t }: { tier: string | null; t: (key: TranslationKey) => string }) {
  if (!tier) return null
  const isTwo = tier.includes('TWO')
  return <Badge variant={isTwo ? 'default' : 'secondary'}>{isTwo ? t('projects.tier2') : t('projects.tier1')}</Badge>
}

import { putAPI } from '../api/client'
import ImageStudioModal from '../components/studio/ImageStudioModal'

function ProjectCard({
  project,
  isActive,
  onClick,
  onOpenStudio,
  onSetActive,
  t
}: {
  project: Project
  isActive: boolean
  onClick: () => void
  onOpenStudio: (pid: string) => void
  onSetActive: (pid: string) => void
  t: (key: TranslationKey, params?: Record<string, string | number>) => string
}) {
  return (
    <Card className="py-4 gap-3 h-full transition-all hover:border-accent" style={{ borderColor: isActive ? 'var(--accent)' : 'var(--border)' }}>
      <CardHeader onClick={onClick} className="cursor-pointer">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-bold flex items-center gap-1.5">
            {project.name}
            {isActive && <Badge variant="default" className="text-[10px]">🎯 激活中</Badge>}
          </CardTitle>
          <TierBadge tier={project.user_paygate_tier} t={t} />
        </div>
        {project.description && (
          <CardDescription className="text-[11px] leading-relaxed line-clamp-2">{project.description}</CardDescription>
        )}
      </CardHeader>
      <CardContent onClick={onClick} className="cursor-pointer">
        <div className="flex flex-wrap gap-1.5">
          {project.material && <Badge variant="outline">{project.material}</Badge>}
          <Badge variant="outline">{project.status}</Badge>
        </div>
      </CardContent>
      <CardFooter className="flex items-center justify-between pt-2 border-t" style={{ borderColor: 'var(--border)' }}>
        <span className="text-[10px] tracking-wide" style={{ color: 'var(--muted)' }}>
          {t('projects.footer', { date: formatDate(project.created_at), id: project.id.slice(0, 8) })}
        </span>
        <div className="flex items-center gap-1.5" onClick={e => e.stopPropagation()}>
          {!isActive && (
            <button
              onClick={() => onSetActive(project.id)}
              className="px-2 py-1 rounded text-[10px] font-medium border hover:bg-card"
              style={{ borderColor: 'var(--border)', color: 'var(--text)' }}
            >
              ⭐ 设为目标
            </button>
          )}
          <button
            onClick={() => onOpenStudio(project.id)}
            className="px-2 py-1 rounded text-[10px] font-semibold text-white"
            style={{ background: 'linear-gradient(135deg, #8b5cf6, #06b6d4)' }}
          >
            🎨 生图
          </button>
        </div>
      </CardFooter>
    </Card>
  )
}

export default function ProjectsPage() {
  const { t } = useTranslation()
  const { id } = useParams<{ id?: string }>()
  const navigate = useNavigate()
  const [tab, setTab] = useState<FilterTab>('ACTIVE')
  const [projects, setProjects] = useState<Project[]>([])
  const [activeProjectId, setActiveProjectId] = useState<string>('')
  const [studioTargetId, setStudioTargetId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const loadAll = () => {
    setLoading(true)
    Promise.all([
      fetchAPI<Project[]>('/api/projects'),
      fetchAPI<{ project_id?: string }>('/api/active-project').catch(() => ({ project_id: '' }))
    ]).then(([projs, active]) => {
      setProjects(projs)
      if (active.project_id) setActiveProjectId(active.project_id)
    }).catch(console.error).finally(() => setLoading(false))
  }

  useEffect(() => { loadAll() }, [])

  async function handleSetActive(pid: string) {
    await putAPI('/api/active-project', { project_id: pid })
    setActiveProjectId(pid)
  }

  // If there's an :id param, show detail page
  if (id) {
    return <ProjectDetailPage projectId={id} onBack={() => navigate('/projects')} />
  }

  const filtered = projects.filter(p => {
    if (tab === 'ALL') return p.status !== 'DELETED'
    return p.status === tab
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <Tabs value={tab} onValueChange={v => setTab(v as FilterTab)}>
          <TabsList>
            <TabsTrigger value="ACTIVE">{t('projects.tab.active')}</TabsTrigger>
            <TabsTrigger value="ARCHIVED">{t('projects.tab.archived')}</TabsTrigger>
            <TabsTrigger value="ALL">{t('projects.tab.all')}</TabsTrigger>
          </TabsList>
        </Tabs>
        <span className="ml-auto text-[11px]" style={{ color: 'var(--muted)' }}>{t('projects.count', { n: filtered.length })}</span>
      </div>

      {loading ? (
        <div className="text-xs" style={{ color: 'var(--muted)' }}>{t('projects.loading')}</div>
      ) : filtered.length === 0 ? (
        <div className="text-xs" style={{ color: 'var(--muted)' }}>{t(`projects.empty.${tab}` as TranslationKey)}</div>
      ) : (
        <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
          {filtered.map(p => (
            <ProjectCard
              key={p.id}
              project={p}
              isActive={p.id === activeProjectId}
              onClick={() => navigate(`/projects/${p.id}`)}
              onOpenStudio={pid => setStudioTargetId(pid)}
              onSetActive={handleSetActive}
              t={t}
            />
          ))}
        </div>
      )}

      {studioTargetId && (
        <ImageStudioModal
          open={!!studioTargetId}
          onClose={() => setStudioTargetId(null)}
          initialProjectId={studioTargetId}
          onSuccess={loadAll}
        />
      )}
    </div>
  )
}

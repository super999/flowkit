import { useState, useEffect, useCallback } from 'react'
import { fetchAPI } from '../api/client'
import type { Project, Video, Scene, Character } from '../types'
import VideoGallery from '../components/gallery/VideoGallery'
import { Badge } from '../components/ui/badge'

export default function GalleryPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProject, setSelectedProject] = useState<string>('')
  const [videos, setVideos] = useState<Video[]>([])
  const [scenes, setScenes] = useState<(Scene & { videoTitle: string })[]>([])
  const [characters, setCharacters] = useState<Character[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    fetchAPI<Project[]>('/api/projects')
      .then(ps => {
        const active = ps.filter(p => p.status !== 'DELETED')
        setProjects(active)
        if (active.length > 0) setSelectedProject(active[0].id)
      })
      .catch(console.error)
  }, [])

  const loadProjectMedia = useCallback(async (projectId: string) => {
    setLoading(true)
    const [vids, chars] = await Promise.all([
      fetchAPI<Video[]>(`/api/videos?project_id=${projectId}`).catch(() => []),
      fetchAPI<Character[]>(`/api/projects/${projectId}/characters`).catch(() => [])
    ])
    setVideos(vids)
    setCharacters(chars)
    const sceneLists = await Promise.all(vids.map(v => fetchAPI<Scene[]>(`/api/scenes?video_id=${v.id}`).catch(() => [])))
    const merged = vids.flatMap((v, i) => sceneLists[i].map(s => ({ ...s, videoTitle: v.title })))
    setScenes(merged)
    setLoading(false)
  }, [])

  useEffect(() => {
    if (!selectedProject) return
    Promise.resolve().then(() => loadProjectMedia(selectedProject).catch(console.error))
  }, [selectedProject, loadProjectMedia])

  async function handleDeleteCharacter(id: string) {
    if (!confirm('确定要删除此角色/实体数据吗？')) return
    try {
      await fetchAPI(`/api/characters/${id}`, { method: 'DELETE' })
      setCharacters(prev => prev.filter(c => c.id !== id))
    } catch (e: any) {
      alert(`删除失败: ${e.message || e}`)
    }
  }

  return (
    <div className="flex flex-col gap-5">
      {/* Filters */}
      <div className="flex gap-3 flex-wrap items-end">
        <div className="flex flex-col gap-1">
          <label className="text-xs font-semibold" style={{ color: 'var(--muted)' }}>选择项目:</label>
          <select
            value={selectedProject}
            onChange={e => setSelectedProject(e.target.value)}
            className="text-xs px-3 py-1.5 rounded outline-none font-bold"
            style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
          >
            {projects.map(p => (
              <option key={p.id} value={p.id}>{p.name} ({p.material || 'default'})</option>
            ))}
          </select>
        </div>
        <span className="text-[11px] ml-auto" style={{ color: 'var(--muted)' }}>
          {characters.length} 个角色参考图 • {videos.length} 个视频 • {scenes.length} 个分镜
        </span>
      </div>

      {loading ? (
        <div className="text-xs" style={{ color: 'var(--muted)' }}>加载媒体资产中...</div>
      ) : (
        <div className="flex flex-col gap-6">
          {/* Character Reference Images Section */}
          {characters.length > 0 && (
            <div className="flex flex-col gap-2">
              <span className="text-xs font-bold flex items-center gap-1.5" style={{ color: 'var(--accent)' }}>
                <span>👥 角色与实体参考图 ({characters.length})</span>
              </span>
              <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
                {characters.map(c => (
                  <div key={c.id} className="p-2 rounded border flex flex-col gap-1.5 relative group" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-semibold truncate">{c.name}</span>
                      <div className="flex items-center gap-1">
                        <Badge variant="outline" className="text-[9px]">{c.entity_type || 'char'}</Badge>
                        <button
                          onClick={() => handleDeleteCharacter(c.id)}
                          className="text-[11px] px-1 py-0.5 rounded border hover:bg-red-500/20 text-red-400 opacity-70 group-hover:opacity-100 transition-opacity"
                          title="删除此实体"
                        >
                          🗑️
                        </button>
                      </div>
                    </div>
                    <div className="w-full aspect-square rounded overflow-hidden bg-black flex items-center justify-center border relative" style={{ borderColor: 'var(--border)' }}>
                      {c.reference_image_url ? (
                        <img
                          src={c.reference_image_url}
                          alt={c.name}
                          referrerPolicy="no-referrer"
                          className="w-full h-full object-cover rounded cursor-pointer group-hover:scale-105 transition-transform"
                          onClick={() => window.open(c.reference_image_url!, '_blank')}
                          onError={(e) => {
                            (e.target as HTMLElement).style.display = 'none'
                            const parent = (e.target as HTMLElement).parentElement
                            if (parent) {
                              parent.innerHTML = `<div class="p-2 text-center flex flex-col items-center justify-center h-full gap-1"><span class="text-[10px] text-red-400 font-semibold">⚠️ 链接已失效</span><span class="text-[9px] text-muted">点击右上角🗑️删除</span></div>`
                            }
                          }}
                        />
                      ) : c.media_id ? (
                        <div className="flex flex-col items-center p-2 text-center">
                          <span className="text-xs font-semibold" style={{ color: 'var(--green)' }}>✓ 已生成</span>
                          <span className="text-[9px] truncate w-full mono" style={{ color: 'var(--muted)' }}>{c.media_id.slice(0, 8)}</span>
                        </div>
                      ) : (
                        <div className="flex flex-col items-center p-2 text-center gap-1">
                          <span className="text-[10px]" style={{ color: 'var(--yellow)' }}>⚠️ 未生成画面</span>
                          <span className="text-[9px]" style={{ color: 'var(--muted)' }}>点击右上角🗑️删除</span>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Scene Videos & Images */}
          <div className="flex flex-col gap-2">
            <span className="text-xs font-bold flex items-center gap-1.5" style={{ color: 'var(--cyan)' }}>
              <span>🎬 视频分镜画面 ({scenes.length})</span>
            </span>
            <VideoGallery scenes={scenes} />
          </div>
        </div>
      )}
    </div>
  )
}

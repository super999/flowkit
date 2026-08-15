import { useState, useEffect, useCallback } from 'react'
import { fetchAPI, postAPI } from '../../api/client'
import { Card, CardHeader, CardTitle, CardContent } from '../../components/ui/card'
import { Button } from '../../components/ui/button'
import { Badge } from '../../components/ui/badge'
import ImageDetailModal, {
  type MediaDetailInfo,
  formatModelName,
  formatAspectRatio
} from '../../components/studio/ImageDetailModal'

interface MediaLibraryItem {
  media_id: string
  project_id?: string | null
  project_title?: string | null
  name?: string | null
  prompt?: string | null
  model_name?: string | null
  aspect_ratio?: string | null
  media_type: string
  url?: string | null
  thumb?: string | null
  local_path?: string | null
  is_cached: number
  source: string
  created_at: string
  updated_at: string
}

interface FlowProject {
  projectId: string
  projectInfo?: { projectTitle?: string; thumbnailMediaKey?: string }
  creationTime?: string
}

interface MediaStats {
  total: number
  cached_count: number
  uncached_count: number
  project_count: number
}

export default function ReferenceLibraryPage() {
  const [items, setItems] = useState<MediaLibraryItem[]>([])
  const [totalCount, setTotalCount] = useState(0)
  const [totalPages, setTotalPages] = useState(1)
  const [stats, setStats] = useState<MediaStats>({ total: 0, cached_count: 0, uncached_count: 0, project_count: 0 })

  const [projects, setProjects] = useState<FlowProject[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState('')
  const [cacheFilter, setCacheFilter] = useState<'all' | 'cached' | 'uncached'>('all')
  const [mediaTypeFilter, setMediaTypeFilter] = useState<'all' | 'IMAGE' | 'VIDEO'>('all')
  const [sortBy, setSortBy] = useState<'created_desc' | 'created_asc' | 'updated_desc'>('created_desc')
  const [searchQuery, setSearchQuery] = useState('')

  const [loading, setLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [cachingAll, setCachingAll] = useState(false)
  const [cachingId, setCachingId] = useState<string | null>(null)
  const [uploading, setUploading] = useState(0)
  const [statusMsg, setStatusMsg] = useState('')

  // Modal lightbox detail state
  const [activeMediaDetail, setActiveMediaDetail] = useState<MediaDetailInfo | null>(null)

  // Pagination
  const [page, setPage] = useState(1)
  const pageSize = 60
  const [jumpPage, setJumpPage] = useState('')

  // Selected for refgen
  const [marked, setMarked] = useState<string[]>([])

  function readCurrentRefs(): any[] {
    try { return JSON.parse(localStorage.getItem('flowkit.refgen.uploads.v1') || '[]') } catch { return [] }
  }
  function writeCurrentRefs(list: any[]) {
    try {
      const total = list.reduce((s: number, u: any) => s + (u.dataUrl?.length || 0), 0)
      localStorage.setItem('flowkit.refgen.uploads.v1', JSON.stringify(total < 3500000 ? list : list.map(u => ({ ...u, dataUrl: '' }))))
    } catch { /* ignore */ }
  }

  // Load from local DB
  const loadMedia = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (selectedProjectId) params.set('project_id', selectedProjectId)
      if (cacheFilter === 'cached') params.set('is_cached', '1')
      if (cacheFilter === 'uncached') params.set('is_cached', '0')
      if (mediaTypeFilter !== 'all') params.set('media_type', mediaTypeFilter)
      if (searchQuery.trim()) params.set('search', searchQuery.trim())

      if (sortBy === 'created_desc') {
        params.set('sort_by', 'created_at')
        params.set('order', 'desc')
      } else if (sortBy === 'created_asc') {
        params.set('sort_by', 'created_at')
        params.set('order', 'asc')
      } else if (sortBy === 'updated_desc') {
        params.set('sort_by', 'updated_at')
        params.set('order', 'desc')
      }

      params.set('page', String(page))
      params.set('page_size', String(pageSize))

      const [resData, statsData] = await Promise.all([
        fetchAPI<{ items: MediaLibraryItem[]; total: number; total_pages: number }>(`/api/media-library?${params.toString()}`),
        fetchAPI<MediaStats>('/api/media-library/stats').catch(() => null)
      ])

      setItems(resData.items || [])
      setTotalCount(resData.total || 0)
      setTotalPages(Math.max(1, resData.total_pages || 1))
      if (statsData) setStats(statsData)
      setMarked(readCurrentRefs().map(u => u.mediaId))
    } catch (e: any) {
      setStatusMsg(`❌ 加载失败: ${e.message || e}`)
    } finally {
      setLoading(false)
    }
  }, [selectedProjectId, cacheFilter, mediaTypeFilter, sortBy, searchQuery, page])

  // Load Flow projects for dropdown and pre-select active project
  useEffect(() => {
    fetchAPI<{ projects: FlowProject[] }>('/api/flow/projects')
      .then(res => {
        setProjects(res.projects || [])
      })
      .catch(() => {})

    fetchAPI<{ project_id?: string }>('/api/active-project')
      .then(res => {
        if (res.project_id) {
          setSelectedProjectId(res.project_id)
        }
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    loadMedia()
  }, [loadMedia])

  // Trigger incremental sync from Flow
  async function handleSync(targetProjectId?: string) {
    const syncPid = targetProjectId !== undefined ? targetProjectId : (selectedProjectId || undefined)
    setSyncing(true)
    const currentProj = projects.find(p => p.projectId === syncPid)
    const projName = currentProj?.projectInfo?.projectTitle || '当前项目'
    setStatusMsg(syncPid ? `🔄 正在快速增量同步「${projName}」媒体...` : '🔄 正在并发增量同步 Flow 所有项目的媒体...')
    try {
      const res = await postAPI<{ status: string; synced?: number; projects_synced?: number; message?: string }>(
        '/api/media-library/sync',
        { project_id: syncPid, auto_cache: true }
      )
      setStatusMsg(`✅ ${res.message || '同步完成'}`)
      await loadMedia()
    } catch (e: any) {
      setStatusMsg(`❌ 同步失败: ${e.message || e}`)
    } finally {
      setSyncing(false)
    }
  }

  // Trigger batch caching for all uncached media
  async function handleCacheAll() {
    setCachingAll(true)
    setStatusMsg('💾 正在后台批量下载并持久化所有未缓存图片/视频...')
    try {
      const res = await postAPI<{ status: string; cached?: number; failed?: number; message?: string }>(
        '/api/media-library/cache-all',
        { project_id: selectedProjectId || undefined, max_concurrency: 5 }
      )
      setStatusMsg(`✅ ${res.message || '批量缓存任务已完成'}`)
      await loadMedia()
    } catch (e: any) {
      setStatusMsg(`❌ 批量缓存失败: ${e.message || e}`)
    } finally {
      setCachingAll(false)
    }
  }

  // Cache single media item
  async function handleCacheSingle(mediaId: string, mediaType = 'IMAGE') {
    setCachingId(mediaId)
    try {
      await postAPI(`/api/media-library/cache/${mediaId}`, {})
      const ext = mediaType.toUpperCase() === 'VIDEO' ? 'mp4' : 'jpg'
      setItems(prev => prev.map(item => item.media_id === mediaId ? { ...item, is_cached: 1, local_path: `/output/_cache/${mediaId}.${ext}` } : item))
      setStatusMsg(`✅ Media ${mediaId.slice(0, 8)} 已持久化缓存至本地`)
    } catch (e: any) {
      setStatusMsg(`❌ 缓存失败: ${e.message || e}`)
    } finally {
      setCachingId(null)
    }
  }

  function addToRefs(item: MediaLibraryItem) {
    const list = readCurrentRefs()
    if (list.some(u => u.mediaId === item.media_id)) {
      alert('这张图已经在当前参考列表中了')
      return
    }
    const thumbUrl = item.local_path || item.thumb || item.url || ''
    list.push({
      uid: `ref_${item.media_id.slice(0, 8)}`,
      mediaId: item.media_id,
      name: item.name || item.prompt || '参考图',
      dataUrl: thumbUrl,
      uploadMs: 0
    })
    writeCurrentRefs(list)
    setMarked(prev => [...prev, item.media_id])
    setStatusMsg(`✅ 已加入参考列表（共 ${list.length} 张），去「参考图生图」即可使用`)
  }

  function removeFromRefs(mediaId: string) {
    const list = readCurrentRefs().filter(u => u.mediaId !== mediaId)
    writeCurrentRefs(list)
    setMarked(prev => prev.filter(m => m !== mediaId))
    setStatusMsg('已从当前参考列表移除')
  }

  async function uploadImage(file: File) {
    if (!file.type.startsWith('image/')) {
      alert('请选择图片文件')
      return
    }
    setUploading(n => n + 1)
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(reader.result as string)
        reader.onerror = () => reject(new Error('读取文件失败'))
        reader.readAsDataURL(file)
      })

      const resp = await postAPI<{ media_id?: string; error?: string }>('/api/flow/upload-image-data', {
        data_url: dataUrl,
        project_id: selectedProjectId,
        file_name: file.name || 'upload.png',
      })

      if (!resp.media_id) {
        alert(`❌ 上传失败: ${resp.error || '未知错误'}`)
        return
      }

      const thumb = await compressImage(dataUrl)
      await postAPI('/api/media-library/upload', {
        media_id: resp.media_id,
        name: file.name || '本地上传图片',
        thumb,
        project_id: selectedProjectId,
        model_name: null,
        aspect_ratio: 'IMAGE_ASPECT_RATIO_PORTRAIT',
        source: 'upload'
      })

      setStatusMsg(`✅ 已上传并入库「${file.name || '本地图片'}」`)
      loadMedia()
    } catch (e: any) {
      alert(`❌ 上传失败: ${e.message || e}`)
    } finally {
      setUploading(n => n - 1)
    }
  }

  function handlePaste(e: React.ClipboardEvent) {
    const clipItems = e.clipboardData?.items
    if (!clipItems) return
    for (const item of clipItems) {
      if (item.type.startsWith('image/')) {
        const f = item.getAsFile()
        if (f) {
          e.preventDefault()
          uploadImage(f)
        }
        break
      }
    }
  }

  function compressImage(dataUrl: string, maxSize = 256, quality = 0.7): Promise<string> {
    return new Promise((resolve, reject) => {
      const img = new Image()
      img.onload = () => {
        try {
          const scale = Math.min(1, maxSize / Math.max(img.width, img.height))
          const canvas = document.createElement('canvas')
          canvas.width = Math.max(1, Math.round(img.width * scale))
          canvas.height = Math.max(1, Math.round(img.height * scale))
          canvas.getContext('2d')!.drawImage(img, 0, 0, canvas.width, canvas.height)
          resolve(canvas.toDataURL('image/jpeg', quality))
        } catch (err) {
          reject(err)
        }
      }
      img.onerror = () => reject(new Error('图片解码失败'))
      img.src = dataUrl
    })
  }

  async function deleteItem(item: MediaLibraryItem) {
    if (!confirm(`确定从本地媒体库移除「${item.name || item.media_id.slice(0, 8)}」？（云端媒体仍保留）`)) return
    await fetchAPI(`/api/media-library/${item.media_id}`, { method: 'DELETE' }).catch(() => {})
    setItems(prev => prev.filter(x => x.media_id !== item.media_id))
    setStatusMsg('🗑️ 已从本地媒体库移除')
  }

  return (
    <div className="flex flex-col gap-4 max-w-[1440px] mx-auto" onPaste={handlePaste}>
      {/* Top Header Card */}
      <Card className="py-4 border-accent">
        <CardContent className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center font-bold text-white text-lg shadow-md" style={{ background: 'linear-gradient(135deg, #10b981, #06b6d4)' }}>
              📚
            </div>
            <div className="flex flex-col">
              <div className="flex items-center gap-2">
                <span className="text-sm font-bold tracking-wide">统一参考图库 (Reference Library)</span>
                <Badge variant="outline" className="text-[10px] bg-emerald-950/40 text-emerald-300 border-emerald-700">
                  本地单源 SQLite
                </Badge>
              </div>
              <span className="text-[11px]" style={{ color: 'var(--muted)' }}>
                总收录 {stats.total} 条媒体 · 已本地持久化 {stats.cached_count} · 远端 {stats.uncached_count} · 覆盖 {stats.project_count} 个项目
              </span>
            </div>
          </div>

          <div className="flex items-center flex-wrap gap-2">
            {/* Sync Buttons */}
            {selectedProjectId ? (
              <>
                <Button
                  size="sm"
                  disabled={syncing}
                  onClick={() => handleSync(selectedProjectId)}
                  className="bg-emerald-600 hover:bg-emerald-500 text-white text-xs gap-1 shadow"
                  title={`仅对当前选择的项目执行高速增量同步（<1秒）`}
                >
                  {syncing ? '⏳ 同步中...' : `🔄 快速同步当前项目 (${projects.find(p => p.projectId === selectedProjectId)?.projectInfo?.projectTitle || '选定项目'})`}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={syncing}
                  onClick={() => handleSync('')}
                  className="text-xs text-zinc-300 border-zinc-700 hover:bg-zinc-800 gap-1"
                  title="并发同步当前 Flow 账号下的所有项目"
                >
                  🌐 同步全部项目
                </Button>
              </>
            ) : (
              <Button
                size="sm"
                disabled={syncing}
                onClick={() => handleSync('')}
                className="bg-emerald-600 hover:bg-emerald-500 text-white text-xs gap-1 shadow"
                title="并发增量同步当前 Flow 账号下的所有项目"
              >
                {syncing ? '⏳ 同步中...' : '🔄 并发增量同步 Flow 全部项目'}
              </Button>
            )}

            {/* Cache All Button */}
            {stats.uncached_count > 0 && (
              <Button
                size="sm"
                variant="outline"
                disabled={cachingAll}
                onClick={handleCacheAll}
                className="text-xs text-cyan-300 border-cyan-700 hover:bg-cyan-950/60 gap-1"
                title="自动将所有远端 CDN 图片下载持久化到本地 output/_cache/"
              >
                {cachingAll ? '⏳ 缓存中...' : `💾 缓存全部未缓存 (${stats.uncached_count})`}
              </Button>
            )}

            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                const zone = document.getElementById('lib-paste-zone')
                if (zone) zone.focus()
              }}
            >
              📋 点击后 Ctrl+V 粘贴
            </Button>

            <label className="px-3 py-1.5 rounded text-xs font-medium cursor-pointer text-white shadow bg-cyan-600 hover:bg-cyan-500 transition-colors">
              📁 上传本地图片
              <input
                type="file"
                accept="image/*"
                multiple
                className="hidden"
                onChange={e => {
                  Array.from(e.target.files || []).forEach(uploadImage)
                  e.target.value = ''
                }}
              />
            </label>
          </div>
        </CardContent>
      </Card>

      {/* Paste helper note */}
      <div
        id="lib-paste-zone"
        tabIndex={-1}
        className="p-2 rounded border border-dashed text-[10px] outline-none flex items-center justify-between"
        style={{ borderColor: 'var(--border)', color: 'var(--muted)' }}
      >
        <span>💡 点击「📋 点击后 Ctrl+V 粘贴」激活后可直接粘贴截图；或点「📁 上传本地图片」选择文件（支持多选入库）。</span>
        <span>数据库秒开直读，支持断网离线浏览已缓存媒体。</span>
      </div>

      {statusMsg && (
        <div className="p-2 rounded text-[11px] border animate-in fade-in" style={{ background: 'var(--card)', borderColor: 'var(--border)' }}>
          {statusMsg}
        </div>
      )}

      {/* Filter and Content Card */}
      <Card className="py-4">
        <CardHeader className="pb-3 border-b" style={{ borderColor: 'var(--border)' }}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider flex items-center gap-2">
              <span>全部参考图/视频 ({totalCount})</span>
              {uploading > 0 && <span className="text-amber-400">⏳ 上传中 ({uploading})...</span>}
            </CardTitle>

            {/* Filter Bar */}
            <div className="flex items-center flex-wrap gap-2 text-xs">
              {/* Project Filter */}
              <div className="flex items-center gap-1">
                <select
                  value={selectedProjectId}
                  onChange={e => {
                    setSelectedProjectId(e.target.value)
                    setPage(1)
                  }}
                  className="px-2 py-1.5 rounded text-xs outline-none max-w-48"
                  style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                >
                  <option value="">— 全部 Flow 项目 —</option>
                  {projects.map(p => (
                    <option key={p.projectId} value={p.projectId}>
                      {p.projectInfo?.projectTitle || p.projectId.slice(0, 8)}
                    </option>
                  ))}
                </select>
                {selectedProjectId && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={syncing}
                    onClick={() => handleSync(selectedProjectId)}
                    className="h-7 px-1.5 text-[10px] text-emerald-400 border-emerald-700 hover:bg-emerald-950/40"
                    title="一键快速增量同步当前选中的项目"
                  >
                    {syncing ? '⏳' : '🔄 同步此项目'}
                  </Button>
                )}
              </div>

              {/* Media Type Filter */}
              <select
                value={mediaTypeFilter}
                onChange={e => {
                  setMediaTypeFilter(e.target.value as any)
                  setPage(1)
                }}
                className="px-2 py-1.5 rounded text-xs outline-none"
                style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
              >
                <option value="all">全部类型 (图/视频)</option>
                <option value="IMAGE">🖼️ 仅图片</option>
                <option value="VIDEO">🎬 仅视频</option>
              </select>

              {/* Cache Status Filter */}
              <select
                value={cacheFilter}
                onChange={e => {
                  setCacheFilter(e.target.value as any)
                  setPage(1)
                }}
                className="px-2 py-1.5 rounded text-xs outline-none"
                style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
              >
                <option value="all">全部缓存状态</option>
                <option value="cached">✓ 仅已本地持久化 ({stats.cached_count})</option>
                <option value="uncached">☁️ 仅远端未缓存 ({stats.uncached_count})</option>
              </select>

              {/* Sort Dropdown */}
              <select
                value={sortBy}
                onChange={e => {
                  setSortBy(e.target.value as any)
                  setPage(1)
                }}
                className="px-2 py-1.5 rounded text-xs outline-none"
                style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                title="排序方式"
              >
                <option value="created_desc">⏱️ 生图时间：从新到旧 (Flow同序)</option>
                <option value="created_asc">⏳ 生图时间：从旧到新</option>
                <option value="updated_desc">🔄 最近更新/缓存时间</option>
              </select>

              {/* Keyword Search */}
              <input
                type="text"
                placeholder="🔍 搜索提示词/项目..."
                value={searchQuery}
                onChange={e => {
                  setSearchQuery(e.target.value)
                  setPage(1)
                }}
                className="w-40 px-2 py-1.5 rounded text-xs outline-none"
                style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
              />

              <Button size="sm" variant="outline" disabled={loading} onClick={() => loadMedia()}>
                {loading ? '加载中...' : '🔄 刷新'}
              </Button>
            </div>
          </div>
        </CardHeader>

        <CardContent className="flex flex-col gap-4 pt-4">
          {items.length === 0 ? (
            <div className="p-12 text-center text-xs border rounded-lg border-dashed flex flex-col items-center gap-2" style={{ color: 'var(--muted)', borderColor: 'var(--border)' }}>
              <span>本地数据库暂无符合条件的参考图。</span>
              <span className="text-[11px]">点击右上角「🔄 增量同步 Flow 媒体」即可一键将 Flow 云端所有项目的媒体增量拉取至本地数据库！</span>
            </div>
          ) : (
            <>
              {/* Media Grid */}
              <div className="grid grid-cols-3 md:grid-cols-6 lg:grid-cols-8 gap-3">
                {items.map(item => {
                  const isMarked = marked.includes(item.media_id)
                  const isCached = item.is_cached === 1 && Boolean(item.local_path)
                  const isVideo = item.media_type?.toUpperCase() === 'VIDEO' || Boolean(item.local_path?.endsWith('.mp4')) || Boolean(item.aspect_ratio?.includes('VIDEO'))
                  const displayThumb = isCached && item.local_path
                    ? item.local_path
                    : (item.url && item.url.startsWith('http')
                        ? item.url
                        : (item.thumb && (item.thumb.startsWith('http') || item.thumb.startsWith('data:'))
                            ? item.thumb
                            : (item.media_id ? `/api/flow/media/proxy?media_id=${item.media_id}` : '')))

                  const openDetail = () => setActiveMediaDetail({
                    title: item.name || item.prompt || '参考媒体元数据',
                    src: displayThumb,
                    mediaId: item.media_id,
                    prompt: item.prompt || item.name,
                    model: item.model_name,
                    aspect: item.aspect_ratio,
                    createdAt: item.created_at,
                    updatedAt: item.updated_at,
                    time: item.created_at ? new Date(item.created_at).toLocaleString() : undefined,
                    source: item.source,
                    mediaType: item.media_type,
                  })

                  return (
                    <div
                      key={item.media_id}
                      className="p-1.5 rounded-lg border flex flex-col gap-1.5 relative group transition-all hover:border-accent"
                      style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
                    >
                      {/* Image/Video Preview Box */}
                      <div
                        onClick={openDetail}
                        className="w-full aspect-square rounded overflow-hidden bg-black flex items-center justify-center border cursor-pointer relative"
                        style={{ borderColor: 'var(--border)' }}
                        title="点击查看大图/播放视频与完整元数据"
                      >
                        {displayThumb ? (
                          isVideo ? (
                            <video
                              src={displayThumb}
                              className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-200"
                              muted
                              loop
                              playsInline
                              onMouseEnter={e => e.currentTarget.play().catch(() => {})}
                              onMouseLeave={e => {
                                e.currentTarget.pause()
                                e.currentTarget.currentTime = 0
                              }}
                            />
                          ) : (
                            <img
                              src={displayThumb}
                              alt={item.name || 'reference'}
                              className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-200"
                              referrerPolicy="no-referrer"
                              onError={(e) => {
                                const target = e.currentTarget as HTMLImageElement
                                if (!target.dataset.triedProxy && item.media_id) {
                                  target.dataset.triedProxy = '1'
                                  target.src = `/api/flow/media/proxy?media_id=${item.media_id}`
                                } else {
                                  target.style.display = 'none'
                                  const parent = target.parentElement
                                  if (parent && !parent.querySelector('.expired-hint')) {
                                    const div = document.createElement('div')
                                    div.className = 'expired-hint text-[8px] text-zinc-500 text-center p-1'
                                    div.innerText = '待缓存'
                                    parent.appendChild(div)
                                  }
                                }
                              }}
                            />
                          )
                        ) : (
                          <span className="text-[9px]" style={{ color: 'var(--muted)' }}>无预览</span>
                        )}

                        {/* Top Left Badge: Cache Status */}
                        <div className="absolute top-1 left-1 flex gap-1 items-center z-10">
                          {isCached ? (
                            <span className="text-[8px] px-1 py-0.2 rounded bg-emerald-950/80 text-emerald-300 border border-emerald-700/80 backdrop-blur-sm" title="已持久化至本地磁盘 output/_cache/">
                              ✓ 本地
                            </span>
                          ) : (
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                handleCacheSingle(item.media_id, item.media_type)
                              }}
                              disabled={cachingId === item.media_id}
                              className="text-[8px] px-1 py-0.2 rounded bg-amber-950/80 text-amber-300 border border-amber-700/80 hover:bg-amber-900 backdrop-blur-sm transition-colors"
                              title="点击立即下载并持久化到本地"
                            >
                              {cachingId === item.media_id ? '⏳' : '☁️ 存本地'}
                            </button>
                          )}
                        </div>

                        {/* Top Right Badge: Video Icon */}
                        {isVideo && (
                          <div className="absolute top-1 right-1 z-10">
                            <span className="text-[8px] px-1 py-0.2 rounded bg-purple-950/80 text-purple-300 border border-purple-700/80 backdrop-blur-sm">
                              🎬 视频
                            </span>
                          </div>
                        )}

                        {/* Hover Overlay */}
                        <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center text-white text-xs font-semibold gap-1 backdrop-blur-[1px]">
                          <span>{isVideo ? '▶️ 播放/详情' : '🔍 详情/大图'}</span>
                        </div>
                      </div>

                      {/* Name / Prompt */}
                      <span
                        className="text-[9px] truncate flex items-center gap-1 cursor-pointer hover:text-accent transition-colors"
                        style={{ color: 'var(--text)' }}
                        title={item.prompt || item.name || '参考图'}
                        onClick={openDetail}
                      >
                        {item.name || item.prompt || '参考图'}
                      </span>

                      {/* Project Name (if available) */}
                      {item.project_title && (
                        <span className="text-[8px] truncate text-cyan-400/80" title={`所属项目: ${item.project_title}`}>
                          📁 {item.project_title}
                        </span>
                      )}

                      {/* Model & Aspect Ratio */}
                      <span className="text-[8px] truncate text-zinc-400">
                        {item.source === 'upload' || (!item.model_name && !item.prompt) || (item.name?.includes('本地上传') ?? false)
                          ? '📁 用户上传参考图'
                          : [
                              formatAspectRatio(item.aspect_ratio, isVideo ? 'VIDEO' : 'IMAGE'),
                              formatModelName(item.model_name, item.source, isVideo ? 'VIDEO' : 'IMAGE')
                            ].filter(Boolean).join(' · ')}
                      </span>

                      {/* Flow Creation Date */}
                      <span
                        className="text-[8px] flex items-center gap-1 cursor-help"
                        style={{ color: 'var(--muted)' }}
                        title={`⏱️ Flow 云端生图时间: ${item.created_at ? new Date(item.created_at).toLocaleString() : '未知'}\n💾 本地同步/缓存时间: ${item.updated_at ? new Date(item.updated_at).toLocaleString() : '未知'}`}
                      >
                        <span>⏱️</span>
                        <span>{item.created_at ? new Date(item.created_at).toLocaleString() : ''}</span>
                      </span>

                      {/* Action buttons */}
                      <div className="flex gap-1 mt-auto">
                        <Button
                          size="sm"
                          variant={isMarked ? 'outline' : 'default'}
                          className="flex-1 !text-[10px] !py-0.5"
                          onClick={() => isMarked ? removeFromRefs(item.media_id) : addToRefs(item)}
                        >
                          {isMarked ? '✓ 已加入' : '＋ 加入参考'}
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          className="!text-[10px] !py-0.5 !px-1.5 text-red-400 hover:bg-red-950/40"
                          onClick={() => deleteItem(item)}
                          title="从本地库移除"
                        >
                          🗑
                        </Button>
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* Pagination */}
              <div className="flex items-center justify-center gap-2 mt-2">
                <button
                  disabled={page <= 1}
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  className="px-2.5 py-1 rounded border text-[11px] hover:border-accent transition-colors disabled:opacity-40"
                  style={{ borderColor: 'var(--border)', color: 'var(--text)' }}
                >
                  ⬅️ 上一页
                </button>
                <span className="text-[11px]" style={{ color: 'var(--muted)' }}>
                  第 {page} / {totalPages} 页（共 {totalCount} 条）
                </span>
                <button
                  disabled={page >= totalPages}
                  onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                  className="px-2.5 py-1 rounded border text-[11px] hover:border-accent transition-colors disabled:opacity-40"
                  style={{ borderColor: 'var(--border)', color: 'var(--text)' }}
                >
                  下一页 ➡️
                </button>
                <span className="flex items-center gap-1 ml-2">
                  <input
                    type="number"
                    min={1}
                    max={totalPages}
                    value={jumpPage}
                    onChange={e => setJumpPage(e.target.value)}
                    placeholder="页"
                    className="w-14 px-1.5 py-1 rounded text-[11px] outline-none"
                    style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  />
                  <button
                    onClick={() => {
                      const n = parseInt(jumpPage, 10)
                      if (!Number.isNaN(n) && n >= 1 && n <= totalPages) setPage(n)
                      else alert(`请输入 1 ~ ${totalPages} 之间的页码`)
                    }}
                    className="px-2 py-1 rounded border text-[11px] hover:border-accent transition-colors"
                    style={{ borderColor: 'var(--border)', color: 'var(--text)' }}
                  >
                    跳转
                  </button>
                </span>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Lightbox Modal for Reference Library Images */}
      {activeMediaDetail && (
        <ImageDetailModal
          info={activeMediaDetail}
          onClose={() => {
            setActiveMediaDetail(null)
            loadMedia()
          }}
        />
      )}
    </div>
  )
}

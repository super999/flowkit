import { useState, useEffect } from 'react'
import { fetchAPI, postAPI } from '../../api/client'
import { Card, CardHeader, CardTitle, CardContent } from '../../components/ui/card'
import { Button } from '../../components/ui/button'

interface LibItem { id: string; media_id: string; name: string; thumb: string; created_at: string }
interface FlowProject { projectId: string; projectInfo?: { projectTitle?: string; thumbnailMediaKey?: string }; creationTime?: string }
interface FlowMedia { mediaKey: string; mediaType: string; prompt?: string; modelName?: string; aspectRatio?: string; workflowId?: string; createTime?: string }

// Merged view item: local library entries + live Flow project media
interface ViewItem {
  key: string
  mediaId: string
  name: string
  thumb: string
  source: 'library' | 'project'
  libId?: string
  created: string
  modelName?: string
  aspectRatio?: string
  mediaType?: string
}

const REDIRECT_URL = (key: string) => `https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=${key}`
const FLOW_CDN_URL = /^https:\/\/flow-content\.google\//i

const ASPECT_LABELS: Record<string, string> = {
  IMAGE_ASPECT_RATIO_PORTRAIT: '9:16',
  IMAGE_ASPECT_RATIO_LANDSCAPE: '16:9',
  IMAGE_ASPECT_RATIO_SQUARE: '1:1',
  IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR: '3:4',
  IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE: '4:3',
}

function sortByCreatedDesc(list: ViewItem[]): ViewItem[] {
  return [...list].sort((a, b) => {
    const timeA = a.created ? new Date(a.created).getTime() : 0
    const timeB = b.created ? new Date(b.created).getTime() : 0
    const validA = Number.isNaN(timeA) ? 0 : timeA
    const validB = Number.isNaN(timeB) ? 0 : timeB
    return validB - validA
  })
}

export default function ReferenceLibraryPage() {
  const [items, setItems] = useState<ViewItem[]>([])
  const [projects, setProjects] = useState<FlowProject[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState('')
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(0)
  const [statusMsg, setStatusMsg] = useState('')

  // Pagination (newest first)
  const [page, setPage] = useState(1)
  const [pageSize] = useState(60)
  const [jumpPage, setJumpPage] = useState('')
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize))
  const safePage = Math.min(page, totalPages)
  const pageItems = items.slice((safePage - 1) * pageSize, safePage * pageSize)

  // Mark which media_ids are already in the current refgen reference list
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

  async function loadAll(notify = false) {
    setLoading(true)
    try {
      const lib = await fetchAPI<LibItem[]>('/api/ref-images')
      let projMedia: FlowMedia[] = []
      let resolvedUrls: Record<string, string> = {}

      if (selectedProjectId) {
        const res = await fetchAPI<{ total: number; media: FlowMedia[] }>(`/api/flow/projects/${selectedProjectId}/media?limit=60`).catch(() => null)
        projMedia = res?.media || []
      }

      // Project-media URLs and Flow-backed library thumbnails are signed and
      // expire. Refresh both so previously saved references do not render as
      // black cards after their original URL expires.
      const unmappedKeys = [...new Set([
        ...projMedia.map(m => m.mediaKey),
        ...lib.filter(l => FLOW_CDN_URL.test(l.thumb)).map(l => l.media_id),
      ].filter(Boolean))]
      if (unmappedKeys.length > 0) {
        const resResolve = await postAPI<{ resolved: Record<string, string> }>('/api/flow/media/resolve', { media_ids: unmappedKeys }).catch(() => null)
        if (resResolve?.resolved) {
          resolvedUrls = resResolve.resolved
        }
      }

      const merged: ViewItem[] = sortByCreatedDesc([
        ...lib.map(l => ({
          key: `lib_${l.id}`,
          mediaId: l.media_id,
          name: l.name || '参考图',
          thumb: resolvedUrls[l.media_id] || l.thumb || '',
          source: 'library' as const,
          libId: l.id,
          created: l.created_at || '',
        })),
        ...projMedia
          .filter(p => p.mediaKey && !lib.some(l => l.media_id === p.mediaKey)) // dedupe with library
          .map(p => ({
            key: `proj_${p.mediaKey}`,
            mediaId: p.mediaKey,
            name: p.prompt
              ? (p.prompt.length > 40 ? p.prompt.slice(0, 40) + '…' : p.prompt)
              : (p.modelName || (p.mediaType === 'VIDEO' ? `Flow 视频 (${p.mediaKey.slice(0, 6)})` : `Flow 图片 (${p.mediaKey.slice(0, 6)})`)),
            thumb: resolvedUrls[p.mediaKey] || REDIRECT_URL(p.mediaKey),
            source: 'project' as const,
            created: p.createTime || '',
            modelName: p.modelName,
            aspectRatio: p.aspectRatio,
            mediaType: p.mediaType,
          })),
      ])
      setItems(merged)
      setMarked(readCurrentRefs().map(u => u.mediaId))

      // Reconcile: migrate old localStorage uploads into the DB library
      const local = readCurrentRefs()
      const missing = local.filter(u => u.mediaId && !lib.some(l => l.media_id === u.mediaId))
      if (missing.length > 0) {
        for (const u of missing) {
          await postAPI('/api/ref-images', {
            media_id: u.mediaId,
            name: u.name || '参考图',
            thumb: u.dataUrl || '',
            project_id: '',
          }).catch(() => {})
        }
        const lib2 = await fetchAPI<LibItem[]>('/api/ref-images').catch(() => lib)
        setItems(prev => sortByCreatedDesc([
          ...lib2.map(l => ({ key: `lib_${l.id}`, mediaId: l.media_id, name: l.name || '参考图', thumb: l.thumb || '', source: 'library' as const, libId: l.id, created: l.created_at || '' })),
          ...prev.filter(v => v.source === 'project'),
        ]))
        setStatusMsg(`✅ 已把 ${missing.length} 张历史上传图片迁移进图库`)
      } else if (notify) {
        setStatusMsg(`✅ 已刷新（Flow 项目媒体 ${projMedia.length} 条 + 本地图库 ${lib.length} 张）`)
      }
    } catch {
      setStatusMsg('❌ 加载失败，请确认后端已启动')
    } finally {
      setLoading(false)
    }
  }

  // Load real Flow projects for the dropdown
  useEffect(() => {
    let loadedProjects: FlowProject[] = []
    fetchAPI<{ projects: FlowProject[] }>('/api/flow/projects')
      .then(res => {
        loadedProjects = res.projects || []
        setProjects(loadedProjects)
        return fetchAPI<{ project_id?: string }>('/api/active-project').catch(() => ({ project_id: '' }))
      })
      .then(active => {
        if (active?.project_id && loadedProjects.some(p => p.projectId === active.project_id)) {
          setSelectedProjectId(active.project_id)
        } else if (loadedProjects.length > 0) {
          setSelectedProjectId(loadedProjects[0].projectId)
        }
      })
      .catch(() => setStatusMsg('❌ 项目列表加载失败（扩展是否已连接？）'))
  }, [])

  useEffect(() => { if (projects.length > 0 || selectedProjectId) loadAll() }, [selectedProjectId])

  function addToRefs(item: ViewItem) {
    const list = readCurrentRefs()
    if (list.some(u => u.mediaId === item.mediaId)) { alert('这张图已经在当前参考列表中了'); return }
    list.push({ uid: `ref_${item.mediaId.slice(0, 8)}`, mediaId: item.mediaId, name: item.name, dataUrl: item.thumb, uploadMs: 0 })
    writeCurrentRefs(list)
    setMarked(prev => [...prev, item.mediaId])
    setStatusMsg(`✅ 已加入参考列表（共 ${list.length} 张），去「参考图生图」即可使用`)
  }

  function removeFromRefs(mediaId: string) {
    const list = readCurrentRefs().filter(u => u.mediaId !== mediaId)
    writeCurrentRefs(list)
    setMarked(prev => prev.filter(m => m !== mediaId))
    setStatusMsg('已从当前参考列表移除')
  }

  async function uploadImage(file: File) {
    if (!file.type.startsWith('image/')) { alert('请选择图片文件'); return }
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
        file_name: file.name || 'paste.png',
      })
      if (!resp.media_id) { alert(`❌ 上传失败: ${resp.error || '未知错误'}`); return }
      const thumb = await compressImage(dataUrl)
      await postAPI('/api/ref-images', { media_id: resp.media_id, name: file.name || '剪贴板图片', thumb, project_id: selectedProjectId })
      setStatusMsg(`✅ 已上传并入库「${file.name || '剪贴板图片'}」`)
      loadAll()
    } catch (e: any) {
      alert(`❌ 上传失败: ${e.message || e}`)
    } finally {
      setUploading(n => n - 1)
    }
  }

  function handlePaste(e: React.ClipboardEvent) {
    const items = e.clipboardData?.items
    if (!items) return
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        const f = item.getAsFile()
        if (f) { e.preventDefault(); uploadImage(f) }
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
        } catch (e) { reject(e) }
      }
      img.onerror = () => reject(new Error('图片解码失败'))
      img.src = dataUrl
    })
  }

  async function deleteItem(item: ViewItem) {
    if (item.source !== 'library' || !item.libId) {
      alert('项目媒体来自 Flow 云端，不能直接删除（可在 Flow 网页中管理）')
      return
    }
    if (!confirm(`从图库删除「${item.name}」？（云端图片保留）`)) return
    await fetchAPI(`/api/ref-images/${item.libId}`, { method: 'DELETE' }).catch(() => {})
    setItems(prev => prev.filter(x => x.key !== item.key))
    setStatusMsg('🗑️ 已从图库删除（云端图片仍在）')
  }

  return (
    <div className="flex flex-col gap-5 max-w-[1400px] mx-auto" onPaste={handlePaste}>
      <Card className="py-4 border-accent">
        <CardContent className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg flex items-center justify-center font-bold text-white text-base shadow" style={{ background: 'linear-gradient(135deg, #10b981, #06b6d4)' }}>
              📚
            </div>
            <div className="flex flex-col">
              <span className="text-sm font-bold tracking-wide">参考图库 (Reference Library)</span>
              <span className="text-[11px]" style={{ color: 'var(--muted)' }}>
                本地上传图 + Flow 项目媒体（实时拉取，图片以缩略图形式直接显示）
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={selectedProjectId}
              onChange={e => { setSelectedProjectId(e.target.value); setPage(1) }}
              className="px-2 py-1.5 rounded text-xs outline-none max-w-56"
              style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
            >
              <option value="">— 选择 Flow 项目 —</option>
              {projects.map(p => (
                <option key={p.projectId} value={p.projectId}>
                  {p.projectInfo?.projectTitle || p.projectId.slice(0, 8)}
                </option>
              ))}
            </select>
            <Button size="sm" variant="outline" disabled={loading} onClick={() => loadAll(true)}>
              {loading ? '加载中...' : '🔄 刷新'}
            </Button>
            <Button
              size="sm"
              onClick={() => {
                const zone = document.getElementById('lib-paste-zone')
                if (zone) zone.focus()
              }}
            >
              📋 点击后按 Ctrl+V 粘贴图片
            </Button>
            <label className="px-3 py-1.5 rounded text-xs font-medium cursor-pointer text-white shadow" style={{ background: 'linear-gradient(135deg, #10b981, #06b6d4)' }}>
              📁 上传本地图片
              <input type="file" accept="image/*" multiple className="hidden" onChange={e => {
                Array.from(e.target.files || []).forEach(uploadImage)
                e.target.value = ''
              }} />
            </label>
          </div>
        </CardContent>
      </Card>

      <div
        id="lib-paste-zone"
        tabIndex={-1}
        className="p-2 rounded border border-dashed text-[10px] outline-none"
        style={{ borderColor: 'var(--border)', color: 'var(--muted)' }}
      >
        点击「📋 点击后按 Ctrl+V」激活后可直接粘贴截图；或点「📁 上传本地图片」选择文件（支持多选）
      </div>

      {statusMsg && (
        <div className="p-2 rounded text-[11px] border" style={{ background: 'var(--card)', borderColor: 'var(--border)' }}>
          {statusMsg}
        </div>
      )}

      <Card className="py-4">
        <CardHeader>
          <CardTitle className="text-xs font-semibold uppercase tracking-wider flex items-center justify-between">
            <span>
              全部参考图 ({items.length})
              {uploading > 0 ? ` — ⏳ 上传中 (${uploading})...` : ''}
              {selectedProjectId ? '' : ' — 请先选择一个 Flow 项目'}
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {items.length === 0 ? (
            <div className="p-8 text-center text-xs border rounded-lg border-dashed" style={{ color: 'var(--muted)', borderColor: 'var(--border)' }}>
              {projects.length === 0 ? (
                <>项目列表为空 — 请确认 Chrome 扩展已连接且已登录 labs.google/fx</>
              ) : (
                <>暂无图片。选择上方项目后可看到其全部媒体；粘贴/上传的图片会自动存入本地图库（显示在最上方）。<br />提示：Flow 接口单次最多返回 20 条媒体（API 限制）</>
              )}
            </div>
          ) : (
            <>
              <div className="grid grid-cols-3 md:grid-cols-6 lg:grid-cols-8 gap-3">
                {pageItems.map(item => {
                  const isMarked = marked.includes(item.mediaId)
                  return (
                    <div key={item.key} className="p-1.5 rounded-lg border flex flex-col gap-1.5" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
                      <div className="w-full aspect-square rounded overflow-hidden bg-black flex items-center justify-center border" style={{ borderColor: 'var(--border)' }}>
                        {item.thumb ? (
                          <img
                            src={item.thumb}
                            alt={item.name}
                            className="w-full h-full object-cover"
                            referrerPolicy="no-referrer"
                            onError={(e) => {
                              const target = e.currentTarget as HTMLImageElement
                              if (!target.dataset.triedProxy && item.mediaId) {
                                target.dataset.triedProxy = '1'
                                target.src = `/api/flow/media/proxy?media_id=${item.mediaId}&url=${encodeURIComponent(item.thumb)}`
                              } else {
                                target.style.display = 'none'
                                const parent = target.parentElement
                                if (parent && !parent.querySelector('.expired-hint')) {
                                  const div = document.createElement('div')
                                  div.className = 'expired-hint text-[8px] text-zinc-500 text-center p-1'
                                  div.innerText = '已过期'
                                  parent.appendChild(div)
                                }
                              }
                            }}
                          />
                        ) : (
                          <span className="text-[9px]" style={{ color: 'var(--muted)' }}>无缩略图</span>
                        )}
                      </div>
                      <span className="text-[9px] truncate flex items-center gap-1" style={{ color: 'var(--text)' }} title={item.name}>
                        {item.name || '参考图'}
                        {item.source === 'project' && (
                          <span className="text-[7px] px-1 rounded flex-shrink-0" style={{ background: 'color-mix(in srgb, var(--accent) 15%, transparent)', color: 'var(--accent)' }}>
                            {item.mediaType === 'VIDEO' ? '🎬' : 'Flow'}
                          </span>
                        )}
                      </span>
                      <span className="text-[8px] truncate" style={{ color: 'var(--muted)' }}>
                        {[item.aspectRatio ? (ASPECT_LABELS[item.aspectRatio] || item.aspectRatio.split('_').pop()) : '', item.modelName || ''].filter(Boolean).join(' · ')}
                      </span>
                      <span className="text-[8px]" style={{ color: 'var(--muted)' }}>
                        {item.created ? new Date(item.created).toLocaleString() : ''}
                      </span>
                      <div className="flex gap-1">
                        <Button size="sm" variant={isMarked ? 'outline' : 'default'} className="flex-1 !text-[10px] !py-0.5" onClick={() => isMarked ? removeFromRefs(item.mediaId) : addToRefs(item)}>
                          {isMarked ? '✓ 已加入' : '＋ 加入参考'}
                        </Button>
                        <Button size="sm" variant="outline" className="!text-[10px] !py-0.5 !px-1.5 text-red-400" onClick={() => deleteItem(item)}>
                          🗑
                        </Button>
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* Pagination */}
              <div className="flex items-center justify-center gap-2 mt-1">
                <button
                  disabled={safePage <= 1}
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  className="px-2.5 py-1 rounded border text-[11px] hover:border-accent transition-colors disabled:opacity-40"
                  style={{ borderColor: 'var(--border)', color: 'var(--text)' }}
                >
                  ⬅️ 上一页
                </button>
                <span className="text-[11px]" style={{ color: 'var(--muted)' }}>
                  第 {safePage} / {totalPages} 页（共 {items.length} 张）
                </span>
                <button
                  disabled={safePage >= totalPages}
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
    </div>
  )
}

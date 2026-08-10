import { useState, useEffect, useMemo } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { fetchAPI, postAPI, putAPI } from '../../api/client'
import type { Project, Character, Video, Scene } from '../../types'
import { Card, CardHeader, CardTitle, CardContent } from '../../components/ui/card'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Tabs, TabsList, TabsTrigger } from '../../components/ui/tabs'

const LENS_PRESETS = [
  { label: '特写镜头 (Close-up)', text: 'Close-up shot focused on subject details' },
  { label: '全景镜头 (Wide Shot)', text: 'Wide establishing shot showing full environment' },
  { label: '鸟瞰俯拍 (Bird-eye)', text: 'High angle bird-eye view from above' },
  { label: '电影质感 (Cinematic)', text: 'Cinematic lighting, dramatic composition, 8k resolution' }
]

const LIGHTING_PRESETS = [
  { label: '影棚柔光', text: 'Soft studio lighting' },
  { label: '霓虹夜景', text: 'Vibrant neon reflections, night scene' },
  { label: '黄金时刻', text: 'Golden hour warm sunlight' },
  { label: '赛博朋克', text: 'Cyberpunk moody volumetric light' }
]

const IMG_CACHE_NAME = 'flowkit-img'

// Local image cache: stores generated images by media_id (stable) so previews
// don't re-download from the cloud on every visit. Signed URLs rotate hourly —
// the cache key must NOT be the URL itself.
function CachedImage({ mediaId, src, alt, className, onClick, onError }: {
  mediaId?: string | null
  src?: string | null
  alt?: string
  className?: string
  onClick?: () => void
  onError?: (e: React.SyntheticEvent<HTMLImageElement>) => void
}) {
  const [displaySrc, setDisplaySrc] = useState<string | null | undefined>(src)

  useEffect(() => {
    let revokeUrl: string | null = null
    let cancelled = false
    const key = `https://local.flowkit.cache/${mediaId || 'none'}`
    async function load() {
      if (!mediaId || !src) { setDisplaySrc(src); return }
      try {
        const cache = await caches.open(IMG_CACHE_NAME)
        const hit = await cache.match(key)
        if (hit) {
          const blobUrl = URL.createObjectURL(await hit.blob())
          revokeUrl = blobUrl
          if (!cancelled) setDisplaySrc(blobUrl)
          return
        }
        const resp = await fetch(src)
        if (!resp.ok) { if (!cancelled) setDisplaySrc(src); return }
        await cache.put(key, resp.clone())
        const blobUrl = URL.createObjectURL(await resp.blob())
        revokeUrl = blobUrl
        if (!cancelled) setDisplaySrc(blobUrl)
      } catch {
        if (!cancelled) setDisplaySrc(src)
      }
    }
    load()
    return () => { cancelled = true; if (revokeUrl) URL.revokeObjectURL(revokeUrl) }
  }, [mediaId, src])

  return <img src={displaySrc || undefined} alt={alt || ''} className={className} onClick={onClick} onError={onError} referrerPolicy="no-referrer" />
}

export default function ImageStudioPage() {
  const location = useLocation()
  const navigate = useNavigate()

  // Determine active tab from URL path (/studio/scenes, /studio/characters, /studio/batch, /studio/refgen)
  const path = location.pathname
  let activeTab: 'scenes' | 'characters' | 'batch' | 'refgen' = 'scenes'
  if (path.includes('/characters')) activeTab = 'characters'
  if (path.includes('/batch')) activeTab = 'batch'
  if (path.includes('/refgen')) activeTab = 'refgen'

  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [statusMsg, setStatusMsg] = useState<string>('')

  // Linking / Creating Project state
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [createMode, setCreateMode] = useState<'link' | 'new'>('link')
  const [existingFlowId, setExistingFlowId] = useState('')
  const [newProjName, setNewProjName] = useState('')
  const [newProjStory, setNewProjStory] = useState('')
  const [newProjMaterial, setNewProjMaterial] = useState('realistic')

  // Scene generation state
  const [scenePrompt, setScenePrompt] = useState(() => localStorage.getItem('flowkit.prompt.scene') || '')
  const [videoTitle, setVideoTitle] = useState('未命名视频')
  const [orientation, setOrientation] = useState<'HORIZONTAL' | 'VERTICAL'>('VERTICAL')
  const [materials, setMaterials] = useState<{ id: string; name: string; scene_prefix?: string }[]>([])
  const [sceneMaterial, setSceneMaterial] = useState('')
  const [charMaterial, setCharMaterial] = useState('none')

  // Image model options (Google Flow)
  const IMAGE_MODELS: { id: string; label: string; hint: string }[] = [
    { id: '', label: '跟随默认（Banana Pro）', hint: 'GEM_PIX_2 — 画质最佳，适合精细构图' },
    { id: 'GEM_PIX_2', label: '🍌 Banana Pro', hint: 'GEM_PIX_2 — 画质最佳，复杂构图首选' },
    { id: 'NARWHAL', label: '🍌 Banana 2', hint: 'NARWHAL — 速度快 2-3x，常见场景接近 Pro 画质' },
    { id: 'HARBOR_SEAL', label: '🍌 Banana 2 Lite', hint: 'HARBOR_SEAL — 最快最省，适合批量出图' },
  ]
  const [sceneModel, setSceneModel] = useState('')
  const [charModel, setCharModel] = useState('')

  // AI prompt generation state
  const [llmLoading, setLlmLoading] = useState(false)
  const [llmKeywords, setLlmKeywords] = useState('')
  const [llmStatusMsg, setLlmStatusMsg] = useState('')
  const [llmLanguage, setLlmLanguage] = useState<'en' | 'zh'>('en')

  // Character ref state
  const [charName, setCharName] = useState('')
  const [charDescription, setCharDescription] = useState('')
  const [charType, setCharType] = useState<'character' | 'location' | 'prop'>('character')

  // RefGen tab state (generate image from reference images)
  const [refgenSelected, setRefgenSelected] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem('flowkit.refgen.selected.v1') || '[]') } catch { return [] }
  })
  const [refgenUploads, setRefgenUploads] = useState<{ uid: string; mediaId: string; name: string; dataUrl: string; uploadMs: number }[]>(() => {
    try {
      const parsed = JSON.parse(localStorage.getItem('flowkit.refgen.uploads.v1') || '[]')
      const total = parsed.reduce((s: number, u: any) => s + (u.dataUrl?.length || 0), 0)
      return total < 3500000 ? parsed.filter((u: any) => u.dataUrl) : []
    } catch { return [] }
  })
  const [refgenUploading, setRefgenUploading] = useState(0)
  const [refgenPrompt, setRefgenPrompt] = useState(() => localStorage.getItem('flowkit.prompt.refgen') || '')
  const [refgenModel, setRefgenModel] = useState(() => localStorage.getItem('flowkit.refgen.model.v1') || '')
  const [refgenAspect, setRefgenAspect] = useState(() => localStorage.getItem('flowkit.refgen.aspect.v1') || 'IMAGE_ASPECT_RATIO_PORTRAIT')
  const [refgenLoading, setRefgenLoading] = useState(false)
  const [refgenResults, setRefgenResults] = useState<{ id: string; url: string; mediaId: string; prompt: string; time: string; aspect?: string; durationMs?: number }[]>([])

  // Supported image aspect ratios (from Flow web UI enums)
  const ASPECT_RATIOS: { value: string; label: string }[] = [
    { value: 'IMAGE_ASPECT_RATIO_PORTRAIT', label: '📱 竖屏 9:16' },
    { value: 'IMAGE_ASPECT_RATIO_LANDSCAPE', label: '💻 横屏 16:9' },
    { value: 'IMAGE_ASPECT_RATIO_SQUARE', label: '⬜ 方形 1:1' },
    { value: 'IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR', label: '📱 竖屏 3:4' },
    { value: 'IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE', label: '💻 横屏 4:3' },
  ]
  // Enum → CSS aspect-ratio for result cards (card shape follows chosen ratio)
  const ASPECT_CSS: Record<string, string> = {
    IMAGE_ASPECT_RATIO_PORTRAIT: '9 / 16',
    IMAGE_ASPECT_RATIO_LANDSCAPE: '16 / 9',
    IMAGE_ASPECT_RATIO_SQUARE: '1 / 1',
    IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR: '3 / 4',
    IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE: '4 / 3',
  }

  // Assets data for active project
  const [characters, setCharacters] = useState<Character[]>([])
  const [scenes, setScenes] = useState<Scene[]>([])

  // Scene gallery pagination (newest first)
  const [scenePage, setScenePage] = useState(1)
  const [scenePageSize] = useState(8)
  const [jumpPage, setJumpPage] = useState('')

  // Load project list and initial project
  const loadProjects = async () => {
    try {
      const projs = await fetchAPI<Project[]>('/api/projects')
      setProjects(projs)
      if (projs.length > 0 && !selectedProjectId) {
        const active = await fetchAPI<{ project_id?: string }>('/api/active-project').catch(() => ({ project_id: '' }))
        if (active.project_id && projs.some(p => p.id === active.project_id)) {
          setSelectedProjectId(active.project_id)
        } else {
          setSelectedProjectId(projs[0].id)
        }
      }
    } catch (e) {
      console.error(e)
    }
  }

  useEffect(() => {
    loadProjects()
    fetchAPI<{ id: string; name: string }[]>('/api/materials')
      .then(setMaterials)
      .catch(() => setMaterials([]))
  }, [])

  // Load assets whenever target project changes
  const loadProjectAssets = async (pid: string) => {
    if (!pid) return
    try {
      const [chars, vids] = await Promise.all([
        fetchAPI<Character[]>(`/api/projects/${pid}/characters`).catch(() => []),
        fetchAPI<Video[]>(`/api/videos?project_id=${pid}`).catch(() => [])
      ])
      setCharacters(chars)

      if (vids.length > 0) {
        const scs = await fetchAPI<Scene[]>(`/api/scenes?video_id=${vids[0].id}`).catch(() => [])
        setScenes(scs)
        setScenePage(1)
        setJumpPage('')
      } else {
        setScenes([])
      }

      // Load persisted refgen results for this project
      fetchAPI<{ id: string; media_id: string; url: string; prompt: string; aspect?: string; duration_ms?: number; created_at: string }[]>(
        `/api/refgen/results?project_id=${pid}`
      )
        .then(rows => setRefgenResults(rows.map(r => ({
          id: r.id,
          url: r.url,
          mediaId: r.media_id,
          prompt: r.prompt,
          time: new Date(r.created_at || '').toLocaleString(),
          aspect: r.aspect || undefined,
          durationMs: r.duration_ms || undefined,
        }))))
        .catch(() => {})
    } catch (e) {
      console.error(e)
    }
  }

  useEffect(() => {
    if (selectedProjectId) loadProjectAssets(selectedProjectId)
  }, [selectedProjectId])

  const selectedProject = projects.find(p => p.id === selectedProjectId)

  // Auto-save prompts to localStorage so they survive page refreshes
  useEffect(() => {
    try { localStorage.setItem('flowkit.prompt.scene', scenePrompt) } catch { /* ignore */ }
  }, [scenePrompt])
  useEffect(() => {
    try { localStorage.setItem('flowkit.prompt.refgen', refgenPrompt) } catch { /* ignore */ }
  }, [refgenPrompt])
  // Persist refgen selections: uploaded refs, selected entities, model, aspect
  useEffect(() => {
    try {
      const total = refgenUploads.reduce((s, u) => s + (u.dataUrl?.length || 0), 0)
      const payload = total < 3500000
        ? refgenUploads
        : refgenUploads.map(u => ({ ...u, dataUrl: '' })) // drop oversized thumbs
      localStorage.setItem('flowkit.refgen.uploads.v1', JSON.stringify(payload))
    } catch { /* ignore */ }
  }, [refgenUploads])
  useEffect(() => {
    try { localStorage.setItem('flowkit.refgen.selected.v1', JSON.stringify(refgenSelected)) } catch { /* ignore */ }
  }, [refgenSelected])
  useEffect(() => {
    try { localStorage.setItem('flowkit.refgen.model.v1', refgenModel) } catch { /* ignore */ }
  }, [refgenModel])
  useEffect(() => {
    try { localStorage.setItem('flowkit.refgen.aspect.v1', refgenAspect) } catch { /* ignore */ }
  }, [refgenAspect])

  // Scene gallery: newest first (by created_at desc), paginated
  const sortedScenes = useMemo(
    () => [...scenes].sort((a, b) => (b.created_at || '').localeCompare(a.created_at || '')),
    [scenes]
  )
  const sceneTotalPages = Math.max(1, Math.ceil(sortedScenes.length / scenePageSize))
  const safePage = Math.min(scenePage, sceneTotalPages)
  const pageScenes = sortedScenes.slice((safePage - 1) * scenePageSize, safePage * scenePageSize)

  async function ensureVideo(pid: string, title: string): Promise<string> {
    const vids = await fetchAPI<Video[]>(`/api/videos?project_id=${pid}`)
    if (vids.length > 0) return vids[0].id
    const newVid = await postAPI<Video>('/api/videos', { project_id: pid, title })
    return newVid.id
  }

  async function handleSetActive(pid: string) {
    await putAPI('/api/active-project', { project_id: pid })
    setSelectedProjectId(pid)
    setStatusMsg(`已将项目 "${selectedProject?.name}" 锁定为全局目标项目！`)
  }

  async function handleCreateOrLinkProject() {
    if (!newProjName.trim()) {
      alert('请输入项目名称')
      return
    }
    if (createMode === 'link' && !existingFlowId.trim()) {
      alert('请输入或粘贴 Google Flow 网页项目 URL 或 项目 ID！')
      return
    }

    setLoading(true)
    setStatusMsg(createMode === 'link' ? '正在关联 Flow 网页项目...' : '正在创建 Flow 项目...')
    try {
      const payload: Record<string, any> = {
        name: newProjName.trim(),
        story: newProjStory.trim(),
        material: newProjMaterial
      }
      if (createMode === 'link' && existingFlowId.trim()) {
        payload.flow_project_id = existingFlowId.trim()
      }

      const created = await postAPI<Project>('/api/projects', payload)
      await putAPI('/api/active-project', { project_id: created.id })
      await loadProjects()
      setSelectedProjectId(created.id)
      setShowCreateForm(false)
      setNewProjName('')
      setNewProjStory('')
      setExistingFlowId('')
      setStatusMsg(`🎉 成功${createMode === 'link' ? '关联' : '创建'}并锁定项目 "${created.name}"！`)
    } catch (e: any) {
      setStatusMsg(`❌ 操作失败: ${e.message || e}`)
    } finally {
      setLoading(false)
    }
  }

  async function handleDeleteCharacter(id: string) {
    if (!confirm('确定要删除此角色/实体数据吗？')) return
    try {
      await fetchAPI(`/api/characters/${id}`, { method: 'DELETE' })
      setCharacters(prev => prev.filter(c => c.id !== id))
    } catch (e: any) {
      alert(`删除失败: ${e.message || e}`)
    }
  }

  // AI-assisted prompt generation via configured LLM
  async function handleAIGenerate(mode: 'random' | 'keyword', target: 'scene' | 'refgen' = 'scene') {
    if (mode === 'keyword' && !llmKeywords.trim()) { alert('请输入关键词或需求！'); return }
    setLlmLoading(true)
    setLlmStatusMsg(mode === 'random' ? '🎲 正在随机生成提示词...' : '✨ 正在根据关键词生成提示词...')
    try {
      const res = await postAPI<{ ok: boolean; prompt?: string; title?: string; error?: string }>('/api/llm/generate-prompt', {
        mode,
        keywords: llmKeywords.trim(),
        orientation: target === 'refgen'
          ? (refgenAspect.includes('PORTRAIT') ? 'VERTICAL' : 'HORIZONTAL')
          : orientation,
        language: llmLanguage,
      })
      if (!res.ok || !res.prompt) {
        setLlmStatusMsg(`❌ 生成失败: ${res.error || '未知错误'}（请检查系统设置 → LLM 接口测试）`)
        return
      }
      if (target === 'refgen') {
        setRefgenPrompt(res.prompt)
      } else {
        setScenePrompt(res.prompt)
        if (res.title) {
          setVideoTitle(prev => (!prev || prev === '未命名视频') ? res.title! : prev)
        }
      }
      setLlmStatusMsg(`✅ 已生成提示词${res.title ? `，建议标题: ${res.title}` : ''}，请预览后提交。`)
    } catch (e: any) {
      setLlmStatusMsg(`❌ 生成失败: ${e.message || e}`)
    } finally {
      setLlmLoading(false)
    }
  }

  // Full prompt preview = material scene_prefix + user prompt (mirrors backend merge)
  const materialPrefix = (() => {
    if (sceneMaterial === 'none') return ''
    const matId = sceneMaterial || projects.find(p => p.id === selectedProjectId)?.material || ''
    return materials.find(m => m.id === matId)?.scene_prefix ?? ''
  })()
  const fullPromptPreview = [materialPrefix, scenePrompt.trim()].filter(Boolean).join(' ')

  // Submit Scene Image Generation
  async function handleGenerateSceneImage() {
    if (!selectedProjectId) { alert('请先选择或关联目标归属项目！'); return }
    if (!scenePrompt.trim()) { alert('请输入生图提示词！'); return }

    setLoading(true)
    setStatusMsg('正在处理画图任务...')
    try {
      await putAPI('/api/active-project', { project_id: selectedProjectId })
      const vid = await ensureVideo(selectedProjectId, videoTitle.trim() || '未命名视频')

      const newScene = await postAPI<{ id: string }>('/api/scenes', {
        video_id: vid,
        prompt: scenePrompt.trim(),
        source: 'user',
        material: sceneMaterial || undefined,
        image_model: sceneModel || undefined
      })

      await postAPI('/api/requests/batch', {
        requests: [{
          type: 'GENERATE_IMAGE',
          project_id: selectedProjectId,
          video_id: vid,
          scene_id: newScene.id,
          orientation: orientation
        }]
      })

      setStatusMsg(`🎉 分镜画图任务已成功提交！归属于: ${selectedProject?.name}`)
      setScenePrompt('')
      loadProjectAssets(selectedProjectId)
    } catch (e: any) {
      setStatusMsg(`❌ 提交失败: ${e.message || e}`)
    } finally {
      setLoading(false)
    }
  }

  // Submit Character Ref Image Generation (GENERATE_CHARACTER_IMAGE)
  async function handleGenerateCharacterRef() {
    if (!selectedProjectId) { alert('请先选择或关联目标归属项目！'); return }
    if (!charName.trim() || !charDescription.trim()) { alert('请输入名称与特征描述！'); return }

    setLoading(true)
    setStatusMsg('正在创建实体并提交参考图生成...')
    try {
      await putAPI('/api/active-project', { project_id: selectedProjectId })

      const newChar = await postAPI<{ id: string }>('/api/characters', {
        project_id: selectedProjectId,
        name: charName.trim(),
        description: charDescription.trim(),
        type: charType,
        material: charMaterial === 'none' ? undefined : charMaterial,
        image_model: charModel || undefined
      })

      await postAPI('/api/requests/batch', {
        requests: [{
          type: 'GENERATE_CHARACTER_IMAGE',
          project_id: selectedProjectId,
          character_id: newChar.id
        }]
      })

      setStatusMsg(`🎉 实体 "${charName}" 参考图生成任务已提交！`)
      setCharName('')
      setCharDescription('')
      loadProjectAssets(selectedProjectId)
    } catch (e: any) {
      setStatusMsg(`❌ 提交失败: ${e.message || e}`)
    } finally {
      setLoading(false)
    }
  }

  function appendPreset(presetText: string) {
    setScenePrompt(prev => prev ? `${prev}, ${presetText}` : presetText)
  }

  // Download original-resolution image via backend proxy (same file Flow web downloads)
  function downloadImage(url: string, name: string) {
    const a = document.createElement('a')
    a.href = `/api/flow/media/image/download?url=${encodeURIComponent(url)}&name=${encodeURIComponent(name)}`
    a.download = name
    a.click()
  }

  // Download at 1K (original) / 2K / 4K — 2K/4K upsample is synchronous via /api/flow/upscale-image
  const [downloading, setDownloading] = useState<{ id: string; res: string } | null>(null)

  async function downloadAtResolution(url: string, mediaId: string | null | undefined, baseName: string, res: '1K' | '2K' | '4K', key: string) {
    if (!url) { alert('没有可下载的图片'); return }
    if (res === '1K') { downloadImage(url, `${baseName}.jpg`); return }
    if (!mediaId) { alert('缺少 media_id，无法放大，请先重新生成图片'); return }

    setDownloading({ id: key, res })
    try {
      const resp = await postAPI<{ media_id?: string; base64?: string; size_bytes?: number; error?: string }>(
        '/api/flow/upscale-image', {
          media_id: mediaId,
          project_id: selectedProjectId,
          target_resolution: res === '4K' ? 'UPSAMPLE_IMAGE_RESOLUTION_4K' : 'UPSAMPLE_IMAGE_RESOLUTION_2K',
        })
      if (!resp.base64) { alert(`❌ ${res} 放大失败: ${resp.error || '未知错误'}`); return }
      const bin = atob(resp.base64)
      const bytes = new Uint8Array(bin.length)
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
      const blobUrl = URL.createObjectURL(new Blob([bytes], { type: 'image/jpeg' }))
      const a = document.createElement('a')
      a.href = blobUrl
      a.download = `${baseName}_${res}.jpg`
      a.click()
      URL.revokeObjectURL(blobUrl)
      setStatusMsg(`✅ 已下载 ${res} 图片（${((resp.size_bytes || 0) / 1048576).toFixed(1)} MB）`)
    } catch (e: any) {
      alert(`❌ ${res} 放大失败: ${e.message || e}`)
    } finally {
      setDownloading(null)
    }
  }

  async function handleSceneDownload(s: Scene, idx: number, res: '1K' | '2K' | '4K') {
    const imageUrl = s.vertical_image_url || s.horizontal_image_url
    const mediaId = s.vertical_image_media_id || s.horizontal_image_media_id
    if (!imageUrl) { alert('该分镜还没有可下载的图片'); return }
    await downloadAtResolution(imageUrl, mediaId, `scene_${idx + 1}_${s.id.slice(0, 8)}`, res, s.id)
  }

  // ─── RefGen: generate image from reference images (direct API, synchronous) ───
  async function handleRefGenGenerate() {
    if (!selectedProjectId) { alert('请先选择或关联目标项目！'); return }
    if (!refgenPrompt.trim()) { alert('请输入画面提示词！'); return }
    const refMediaIds = [...refgenSelected, ...refgenUploads.map(u => u.mediaId)]
    if (refMediaIds.length === 0) { alert('请至少选择/上传一张参考图！'); return }

    setRefgenLoading(true)
    setStatusMsg('🖼️ 正在使用参考图生图（直接调用，约 5-20 秒）...')
    const t0 = Date.now()
    try {
      const data = await postAPI<any>('/api/flow/generate-image', {
        prompt: refgenPrompt.trim(),
        project_id: selectedProjectId,
        aspect_ratio: refgenAspect,
        user_paygate_tier: 'PAYGATE_TIER_TWO',
        character_media_ids: refMediaIds,
        image_model: refgenModel || undefined,
      })
      const media = data?.media?.[0]
      const gen = media?.image?.generatedImage || {}
      const url = gen.fifeUrl || media?.fifeUrl || ''
      const mediaId = gen.mediaId || media?.name || ''
      if (!url) { alert('生成响应中没有图片 URL'); return }
      const result = {
        id: mediaId || `${Date.now()}`,
        url,
        mediaId,
        prompt: refgenPrompt.trim(),
        time: new Date().toLocaleString(),
        aspect: refgenAspect,
        durationMs: Date.now() - t0,
      }
      setRefgenResults(prev => [result, ...prev])
      // Persist so results survive page reloads
      postAPI('/api/refgen/results', {
        project_id: selectedProjectId,
        media_id: mediaId,
        url,
        prompt: refgenPrompt.trim(),
        aspect: refgenAspect,
        model: refgenModel || undefined,
        duration_ms: result.durationMs,
      }).catch(() => {})
      setStatusMsg('🎉 参考图生图成功！结果已保存到数据库，刷新不会丢失')
    } catch (e: any) {
      alert(`❌ 生成失败: ${e.message || e}`)
    } finally {
      setRefgenLoading(false)
    }
  }

  function toggleRefgenSelect(id: string) {
    setRefgenSelected(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
  }

  // Ref image library (persisted in DB, reusable across sessions)
  const [refLibrary, setRefLibrary] = useState<{ id: string; media_id: string; name: string; thumb: string }[]>([])

  // Compress a data URL into a small JPEG thumbnail (for the library)
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

  const loadRefLibrary = () => {
    fetchAPI<{ id: string; media_id: string; name: string; thumb: string }[]>('/api/ref-images')
      .then(setRefLibrary)
      .catch(() => {})
  }

  useEffect(() => { loadRefLibrary() }, [])

  // Upload a local image (clipboard paste / file picker) to Flow → media_id
  async function uploadRefImage(file: File) {
    if (!selectedProjectId) { alert('请先选择或关联目标项目！'); return }
    if (!file.type.startsWith('image/')) { alert('请选择图片文件'); return }
    const t0 = Date.now()
    setRefgenUploading(n => n + 1)
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
      setRefgenUploads(prev => [...prev, {
        uid: `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
        mediaId: resp.media_id!,
        name: file.name || '剪贴板图片',
        dataUrl,
        uploadMs: Date.now() - t0,
      }])
      // Auto-save into the persistent library (compressed thumb) for reuse
      try {
        const thumb = await compressImage(dataUrl)
        postAPI('/api/ref-images', {
          media_id: resp.media_id,
          name: file.name || '剪贴板图片',
          thumb,
          project_id: selectedProjectId,
        }).then(() => loadRefLibrary()).catch(() => {})
      } catch { /* thumbnail failure is non-fatal */ }
      setStatusMsg(`✅ 已上传参考图「${file.name || '剪贴板'}」并已存入参考图库`)
    } catch (e: any) {
      alert(`❌ 上传失败: ${e.message || e}`)
    } finally {
      setRefgenUploading(n => n - 1)
    }
  }

  function handleRefgenPaste(e: React.ClipboardEvent) {
    if (activeTab !== 'refgen') return
    const items = e.clipboardData?.items
    if (!items) return
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile()
        if (file) {
          e.preventDefault()
          uploadRefImage(file)
        }
        break
      }
    }
  }

  return (
    <div className="flex flex-col gap-5 max-w-[1600px] mx-auto">
      {/* Top Header: Target Project Locking & Controller */}
      <Card className="py-4 border-accent">
        <CardContent className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg flex items-center justify-center font-bold text-white text-base shadow" style={{ background: 'linear-gradient(135deg, #8b5cf6, #06b6d4)' }}>
              🎨
            </div>
            <div className="flex flex-col">
              <span className="text-sm font-bold tracking-wide">AI 生图工坊 (Image Studio Workbench)</span>
              <span className="text-[11px]" style={{ color: 'var(--muted)' }}>
                所有的画面与角色资产生成均严格归属于您当前锁定的目标 Flow 项目
              </span>
            </div>
          </div>

          {/* Project Select & Linking Bar */}
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg border" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
              <span className="text-xs font-semibold">🎯 目标项目:</span>
              {projects.length > 0 ? (
                <select
                  value={selectedProjectId}
                  onChange={e => setSelectedProjectId(e.target.value)}
                  className="bg-transparent text-xs font-bold outline-none cursor-pointer"
                  style={{ color: 'var(--accent)' }}
                >
                  {projects.map(p => (
                    <option key={p.id} value={p.id} style={{ background: 'var(--card)', color: 'var(--text)' }}>
                      {p.name} ({p.material || 'default'})
                    </option>
                  ))}
                </select>
              ) : (
                <span className="text-xs" style={{ color: 'var(--yellow)' }}>未关联项目</span>
              )}
            </div>

            {selectedProjectId && (
              <Button size="sm" variant="outline" onClick={() => handleSetActive(selectedProjectId)}>
                ⭐ 锁为全局目标
              </Button>
            )}

            <Button
              size="sm"
              onClick={() => setShowCreateForm(!showCreateForm)}
              style={{ background: 'linear-gradient(135deg, #8b5cf6, #6d28d9)' }}
              className="text-white"
            >
              {showCreateForm ? '取消新建' : '🔗 关联/新建 Flow 项目'}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Inline Flow Project Link/Create Panel */}
      {showCreateForm && (
        <Card className="py-4 border-dashed border-accent" style={{ background: 'var(--surface)' }}>
          <CardHeader>
            <CardTitle className="text-sm flex items-center justify-between">
              <span>关联或新建 Google Flow 视频项目</span>
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-1 text-xs cursor-pointer">
                  <input
                    type="radio"
                    name="cMode"
                    value="link"
                    checked={createMode === 'link'}
                    onChange={() => setCreateMode('link')}
                  />
                  🔗 关联 Flow 网页已有项目 (推荐)
                </label>
                <label className="flex items-center gap-1 text-xs cursor-pointer">
                  <input
                    type="radio"
                    name="cMode"
                    value="new"
                    checked={createMode === 'new'}
                    onChange={() => setCreateMode('new')}
                  />
                  ➕ 创建全新 Flow 项目
                </label>
              </div>
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {createMode === 'link' && (
              <div className="flex flex-col gap-1">
                <label className="text-xs font-semibold" style={{ color: 'var(--accent)' }}>
                  粘贴 Google Flow 网页项目 URL 或 项目 ID:
                </label>
                <input
                  type="text"
                  placeholder="例如: https://labs.google/fx/zh/tools/flow/project/5a9157d5-2faa-479b-8d5d-435dd66a1dbb 或 5a9157d5-..."
                  value={existingFlowId}
                  onChange={e => setExistingFlowId(e.target.value)}
                  className="w-full px-3 py-2 rounded text-xs outline-none"
                  style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                />
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1">
                <label className="text-xs font-semibold" style={{ color: 'var(--accent)' }}>
                  项目名称 (Project Name):
                </label>
                <input
                  type="text"
                  placeholder="例如: 消失的证人 / 星际纪元"
                  value={newProjName}
                  onChange={e => setNewProjName(e.target.value)}
                  className="w-full px-3 py-2 rounded text-xs outline-none"
                  style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs font-semibold" style={{ color: 'var(--accent)' }}>
                  画风材质 (Material):
                </label>
                <select
                  value={newProjMaterial}
                  onChange={e => setNewProjMaterial(e.target.value)}
                  className="w-full px-3 py-2 rounded text-xs outline-none"
                  style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                >
                  <option value="realistic">Realistic (真实写实)</option>
                  <option value="3d_pixar">3D Pixar (皮克斯)</option>
                  <option value="anime">Anime (日系动漫)</option>
                  <option value="stop_motion">Stop Motion (定格动画)</option>
                  <option value="minecraft">Minecraft (像素)</option>
                  <option value="oil_painting">Oil Painting (油画)</option>
                </select>
              </div>
            </div>

            <div className="flex justify-end">
              <Button size="sm" disabled={loading} onClick={handleCreateOrLinkProject}>
                {loading ? '处理中...' : (createMode === 'link' ? '🔗 立即关联项目' : '➕ 立即创建项目')}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Main Studio Grid: Left Control Panel + Right Live Gallery */}
      <div className="grid grid-cols-12 gap-5">
        
        {/* Left Side: Generation Control Workbench (5 cols) */}
        <div className="col-span-12 lg:col-span-5 flex flex-col gap-4">
          
          <Tabs value={activeTab} onValueChange={v => navigate(`/studio/${v}`)}>
            <TabsList className="w-full grid grid-cols-4">
              <TabsTrigger value="scenes">🎬 分镜生图</TabsTrigger>
              <TabsTrigger value="characters">👥 角色参考图</TabsTrigger>
              <TabsTrigger value="refgen">🖼️ 参考图生图</TabsTrigger>
              <TabsTrigger value="batch">⚡ 全套批生</TabsTrigger>
            </TabsList>
          </Tabs>

          {/* TAB: Scenes Generator */}
          {activeTab === 'scenes' && (
            <Card className="py-4">
              <CardHeader>
                <CardTitle className="text-xs font-semibold uppercase tracking-wider">分镜生图画板 (Scene Image Studio)</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium">视频标题 (Video Title):</label>
                  <input
                    type="text"
                    value={videoTitle}
                    onChange={e => setVideoTitle(e.target.value)}
                    placeholder="仅在该项目尚无视频时用于自动创建视频"
                    className="w-full px-3 py-2 rounded text-xs outline-none"
                    style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  />
                </div>

                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium">画面描述提示词 (Prompt):</label>
                  <textarea
                    value={scenePrompt}
                    onChange={e => setScenePrompt(e.target.value)}
                    rows={4}
                    placeholder="输入画面描述，例如：雨夜的赛博朋克街道，高楼大厦耸立，车水马龙，主角站在檐下思考..."
                    className="w-full px-3 py-2.5 rounded text-xs outline-none resize-none leading-relaxed"
                    style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  />
                </div>

                {/* AI Prompt Generation */}
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium">✨ AI 生成提示词 (调用已配置的大模型):</label>
                  <div className="flex items-center gap-1.5">
                    <div className="flex rounded border overflow-hidden" style={{ borderColor: 'var(--border)' }}>
                      {(['en', 'zh'] as const).map(lang => (
                        <button
                          key={lang}
                          onClick={() => setLlmLanguage(lang)}
                          className="px-2 py-1 text-[10px] font-semibold transition-colors"
                          style={{
                            background: llmLanguage === lang ? 'var(--accent)' : 'var(--card)',
                            color: llmLanguage === lang ? '#fff' : 'var(--muted)',
                          }}
                          title={lang === 'en' ? '生成英文提示词' : '生成中文提示词'}
                        >
                          {lang === 'en' ? 'EN 英文' : '中 文'}
                        </button>
                      ))}
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={llmLoading}
                      onClick={() => handleAIGenerate('random')}
                      className="flex-shrink-0"
                    >
                      🎲 随机生成
                    </Button>
                    <input
                      type="text"
                      value={llmKeywords}
                      onChange={e => setLlmKeywords(e.target.value)}
                      placeholder="输入关键词或需求，如: 雨夜 赛博朋克 酒吧 霓虹灯"
                      className="flex-1 px-3 py-2 rounded text-xs outline-none"
                      style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                    />
                    <Button
                      size="sm"
                      disabled={llmLoading || !llmKeywords.trim()}
                      onClick={() => handleAIGenerate('keyword')}
                      className="flex-shrink-0"
                    >
                      🚀 按关键词生成
                    </Button>
                  </div>
                  <span className="text-[10px]" style={{ color: 'var(--muted)' }}>
                    {llmLanguage === 'zh' ? '当前输出中文提示词；' : '当前输出英文提示词；'}随机生成 = 点一下自动生成；关键词生成 = 给出题材/氛围/画面范围后生成
                  </span>
                  {llmStatusMsg && (
                    <div className="p-2 rounded text-[11px] border" style={{ background: 'var(--card)', borderColor: 'var(--border)' }}>
                      {llmStatusMsg}
                    </div>
                  )}
                </div>

                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium">画风材质 (Material):</label>
                  <select
                    value={sceneMaterial}
                    onChange={e => setSceneMaterial(e.target.value)}
                    className="w-full px-3 py-2 rounded text-xs outline-none"
                    style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  >
                    <option value="">跟随项目材质 (默认)</option>
                    <option value="none">不使用材质 — 自己写风格</option>
                    {materials.map(m => (
                      <option key={m.id} value={m.id}>{m.name}</option>
                    ))}
                  </select>
                </div>

                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium">生图模型 (Image Model):</label>
                  <select
                    value={sceneModel}
                    onChange={e => setSceneModel(e.target.value)}
                    className="w-full px-3 py-2 rounded text-xs outline-none"
                    style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  >
                    {IMAGE_MODELS.map(m => (
                      <option key={m.id || 'default'} value={m.id}>{m.label}</option>
                    ))}
                  </select>
                  <span className="text-[10px]" style={{ color: 'var(--muted)' }}>
                    {IMAGE_MODELS.find(m => m.id === sceneModel)?.hint || IMAGE_MODELS[0].hint}
                  </span>
                </div>

                {/* Full Prompt Preview (material prefix + prompt, mirrors backend merge) */}
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium">📋 提交后的完整提示词预览:</label>
                  <div
                    className="p-2.5 rounded text-[11px] border leading-relaxed whitespace-pre-wrap break-words min-h-[64px]"
                    style={{
                      background: fullPromptPreview ? 'var(--surface)' : 'var(--card)',
                      borderColor: fullPromptPreview ? 'var(--accent)' : 'var(--border)',
                      color: fullPromptPreview ? 'var(--text)' : 'var(--muted)',
                    }}
                  >
                    {fullPromptPreview || '填写提示词后，这里会自动预览提交给生图服务的完整提示词（含材质前缀）。'}
                  </div>
                  {materialPrefix && (
                    <span className="text-[10px]" style={{ color: 'var(--muted)' }}>
                      已包含材质前缀: <code style={{ color: 'var(--accent)' }}>{materialPrefix}</code>
                    </span>
                  )}
                </div>

                {/* Preset Prompt Modifiers */}
                <div className="flex flex-col gap-2">
                  <span className="text-[11px] font-medium" style={{ color: 'var(--muted)' }}>快捷修饰词 (镜头/光效):</span>
                  <div className="flex flex-wrap gap-1.5">
                    {LENS_PRESETS.map((p, i) => (
                      <button
                        key={i}
                        onClick={() => appendPreset(p.text)}
                        className="px-2 py-1 rounded text-[10px] border hover:border-accent transition-colors"
                        style={{ background: 'var(--card)', borderColor: 'var(--border)' }}
                      >
                        🎥 {p.label}
                      </button>
                    ))}
                    {LIGHTING_PRESETS.map((l, i) => (
                      <button
                        key={i}
                        onClick={() => appendPreset(l.text)}
                        className="px-2 py-1 rounded text-[10px] border hover:border-accent transition-colors"
                        style={{ background: 'var(--card)', borderColor: 'var(--border)' }}
                      >
                        💡 {l.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Orientation Selector */}
                <div className="flex items-center justify-between p-2.5 rounded border" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
                  <span className="text-xs font-medium">画幅方向:</span>
                  <div className="flex gap-3">
                    <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                      <input
                        type="radio"
                        name="studioOrient"
                        value="VERTICAL"
                        checked={orientation === 'VERTICAL'}
                        onChange={() => setOrientation('VERTICAL')}
                      />
                      📱 竖屏 (9:16)
                    </label>
                    <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                      <input
                        type="radio"
                        name="studioOrient"
                        value="HORIZONTAL"
                        checked={orientation === 'HORIZONTAL'}
                        onChange={() => setOrientation('HORIZONTAL')}
                      />
                      💻 横屏 (16:9)
                    </label>
                  </div>
                </div>

                <Button
                  disabled={loading}
                  onClick={handleGenerateSceneImage}
                  className="w-full py-2.5 font-bold text-white shadow"
                  style={{ background: 'linear-gradient(135deg, #8b5cf6, #06b6d4)' }}
                >
                  {loading ? '正在提交生图...' : '🎨 提交画图任务'}
                </Button>
              </CardContent>
            </Card>
          )}

          {/* TAB: Character Studio */}
          {activeTab === 'characters' && (
            <Card className="py-4">
              <CardHeader>
                <CardTitle className="text-xs font-semibold uppercase tracking-wider">角色/场景参考图绘制 (Entity Ref Studio)</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                <div className="grid grid-cols-3 gap-2">
                  <div className="col-span-2 flex flex-col gap-1">
                    <label className="text-xs font-medium">名称 (Entity Name):</label>
                    <input
                      type="text"
                      value={charName}
                      onChange={e => setCharName(e.target.value)}
                      placeholder="例如: 侦探 墨菲 / 赛博酒吧"
                      className="w-full px-3 py-2 rounded text-xs outline-none"
                      style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium">类型 (Type):</label>
                    <select
                      value={charType}
                      onChange={e => setCharType(e.target.value as any)}
                      className="w-full px-2 py-2 rounded text-xs outline-none"
                      style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                    >
                      <option value="character">角色</option>
                      <option value="location">场景</option>
                      <option value="prop">道具</option>
                    </select>
                  </div>
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium">画风材质 (Material):</label>
                  <select
                    value={charMaterial}
                    onChange={e => setCharMaterial(e.target.value)}
                    className="w-full px-2 py-2 rounded text-xs outline-none"
                    style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  >
                    <option value="none">不使用材质 — 自己写风格</option>
                    {materials.map(m => (
                      <option key={m.id} value={m.id}>{m.name}</option>
                    ))}
                  </select>
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium">生图模型 (Image Model):</label>
                  <select
                    value={charModel}
                    onChange={e => setCharModel(e.target.value)}
                    className="w-full px-2 py-2 rounded text-xs outline-none"
                    style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  >
                    {IMAGE_MODELS.map(m => (
                      <option key={m.id || 'default'} value={m.id}>{m.label}</option>
                    ))}
                  </select>
                </div>

                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium">外观特征描写 (Visual Description):</label>
                  <textarea
                    value={charDescription}
                    onChange={e => setCharDescription(e.target.value)}
                    rows={4}
                    placeholder="详细描述视觉特征，如：30岁男子，短黑发，穿深蓝色风衣，眼神犀利，戴圆框眼镜..."
                    className="w-full px-3 py-2.5 rounded text-xs outline-none resize-none leading-relaxed"
                    style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  />
                </div>

                <Button
                  disabled={loading}
                  onClick={handleGenerateCharacterRef}
                  className="w-full py-2.5 font-bold text-white shadow"
                  style={{ background: 'linear-gradient(135deg, #06b6d4, #3b82f6)' }}
                >
                  {loading ? '正在处理...' : '👤 创建实体并提交参考图'}
                </Button>
              </CardContent>
            </Card>
          )}

          {/* TAB: RefGen — generate from reference images */}
          {activeTab === 'refgen' && (
            <Card className="py-4">
              <CardHeader>
                <CardTitle className="text-xs font-semibold uppercase tracking-wider">🖼️ 参考图生图画板 (Reference-to-Image)</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-4" onPaste={handleRefgenPaste}>
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium">
                    参考图（共 {refgenSelected.length + refgenUploads.length} 张）
                  </label>

                  {/* Upload zone: clipboard paste + local file */}
                  <div className="flex gap-1.5 items-center">
                    <button
                      onClick={() => {
                        if (!selectedProjectId) { alert('请先选择或关联目标项目！'); return }
                        const zone = document.getElementById('refgen-paste-zone')
                        if (zone) zone.focus()
                      }}
                      className="px-2.5 py-1.5 rounded border text-[11px] font-medium hover:border-accent transition-colors"
                      style={{ borderColor: 'var(--border)', color: 'var(--text)' }}
                      title="先点击激活此按钮，再按 Ctrl+V 粘贴截图/抠图"
                    >
                      📋 点击后按 Ctrl+V 粘贴图片
                    </button>
                    <label
                      className="px-2.5 py-1.5 rounded border text-[11px] font-medium cursor-pointer hover:border-accent transition-colors"
                      style={{ borderColor: 'var(--border)', color: 'var(--text)' }}
                    >
                      📁 从本地选择图片
                      <input
                        type="file"
                        accept="image/*"
                        multiple
                        className="hidden"
                        onChange={e => {
                          const files = Array.from(e.target.files || [])
                          files.forEach(uploadRefImage)
                          e.target.value = ''
                        }}
                      />
                    </label>
                    {refgenUploading > 0 && (
                      <span className="text-[10px]" style={{ color: 'var(--yellow)' }}>⏳ 上传中 ({refgenUploading})...</span>
                    )}
                  </div>
                  <div
                    id="refgen-paste-zone"
                    tabIndex={-1}
                    className="p-2 rounded border border-dashed text-[10px] outline-none"
                    style={{ borderColor: 'var(--border)', color: 'var(--muted)' }}
                  >
                    粘贴区：点击上方按钮激活后，直接 Ctrl+V 粘贴剪贴板图片；或点击「从本地选择图片」上传文件
                  </div>

                  {/* Uploaded images */}
                  {refgenUploads.length > 0 && (
                    <div className="flex flex-col gap-1">
                      <span className="text-[10px] font-semibold" style={{ color: 'var(--cyan)' }}>
                        已上传图片 ({refgenUploads.length}):
                      </span>
                      <div className="flex flex-wrap gap-2">
                        {refgenUploads.map(u => (
                          <div key={u.uid} className="relative group">
                            <div className="w-16 h-16 rounded overflow-hidden bg-black border" style={{ borderColor: 'var(--accent)' }}>
                              <img src={u.dataUrl} alt={u.name} className="w-full h-full object-cover" />
                            </div>
                            <button
                              onClick={() => setRefgenUploads(prev => prev.filter(x => x.uid !== u.uid))}
                              className="absolute -top-1.5 -right-1.5 w-4.5 h-4.5 min-w-4 text-[9px] leading-none rounded-full bg-red-500 text-white flex items-center justify-center opacity-70 group-hover:opacity-100"
                              title="移除"
                            >
                              ✕
                            </button>
                            <span className="block text-[9px] truncate max-w-16" style={{ color: 'var(--muted)' }} title={u.name}>
                              {u.name}
                            </span>
                            <span className="block text-[8px]" style={{ color: 'var(--muted)' }}>
                              ⏱ 上传 {(u.uploadMs / 1000).toFixed(1)}s
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Persistent reference library — compact entry, full page for browsing */}
                  <div className="flex items-center justify-between p-2 rounded border" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
                    <span className="text-[10px]" style={{ color: 'var(--muted)' }}>
                      📚 参考图库：上传的图片 + Flow 项目媒体（生成/上传的都算）
                    </span>
                    <button
                      onClick={() => navigate('/studio/ref-library')}
                      className="px-2 py-1 rounded text-[11px] font-medium text-white shadow"
                      style={{ background: 'linear-gradient(135deg, #10b981, #06b6d4)' }}
                    >
                      打开完整图库 ({(refLibrary.length)})
                    </button>
                  </div>

                  {/* Project entity references */}
                  {characters.length === 0 ? (
                    <div className="p-3 text-center text-[11px] border rounded border-dashed" style={{ color: 'var(--muted)', borderColor: 'var(--border)' }}>
                      项目暂无角色/实体参考图，可先上传本地图片，或到【👥 角色参考图】tab 生成
                    </div>
                  ) : (
                    <div className="grid grid-cols-3 gap-2 max-h-52 overflow-auto pr-1">
                      {characters.map(c => {
                        const selected = refgenSelected.includes(c.media_id || c.id)
                        return (
                          <button
                            key={c.id}
                            onClick={() => c.media_id && toggleRefgenSelect(c.media_id)}
                            disabled={!c.media_id || !c.reference_image_url}
                            className="flex flex-col gap-1 p-1.5 rounded border transition-colors disabled:opacity-40 text-left"
                            style={{
                              borderColor: selected ? 'var(--accent)' : 'var(--border)',
                              background: selected ? 'color-mix(in srgb, var(--accent) 12%, var(--card))' : 'var(--card)',
                            }}
                            title={c.media_id ? c.name : `${c.name}（无参考图）`}
                          >
                            <div className="w-full aspect-square rounded overflow-hidden bg-black flex items-center justify-center">
                              {c.reference_image_url ? (
                                <CachedImage mediaId={c.media_id} src={c.reference_image_url} alt={c.name} className="w-full h-full object-cover" />
                              ) : (
                                <span className="text-[9px]" style={{ color: 'var(--muted)' }}>无图</span>
                              )}
                            </div>
                            <span className="text-[9px] truncate" style={{ color: selected ? 'var(--accent)' : 'var(--muted)' }}>
                              {selected ? '✓ ' : ''}{c.name}
                            </span>
                          </button>
                        )
                      })}
                    </div>
                  )}
                  {(refgenSelected.length > 0 || refgenUploads.length > 0) && (
                    <span className="text-[10px]" style={{ color: 'var(--muted)' }}>
                      已选 {refgenSelected.length} 个实体 + {refgenUploads.length} 张上传图片 — 将作为 IMAGE_INPUT_TYPE_REFERENCE 传入
                    </span>
                  )}
                </div>

                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium flex items-center justify-between">
                    <span>画面描述提示词 (Prompt):</span>
                    {refgenPrompt && (
                      <button
                        onClick={() => setRefgenPrompt('')}
                        className="text-[10px] px-1.5 py-0.5 rounded border hover:border-red-400 text-red-400 transition-colors"
                      >
                        🧹 清空
                      </button>
                    )}
                  </label>
                  <textarea
                    value={refgenPrompt}
                    onChange={e => setRefgenPrompt(e.target.value)}
                    rows={3}
                    placeholder="描述要生成的新画面：人物姿势、动作、场景、氛围...（参考图控制外观一致性）"
                    className="w-full px-3 py-2.5 rounded text-xs outline-none resize-none leading-relaxed"
                    style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  />
                </div>

                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium">✨ AI 生成提示词 (可选):</label>
                  <div className="flex gap-1.5">
                    <Button size="sm" variant="outline" disabled={llmLoading} onClick={() => handleAIGenerate('random', 'refgen')} className="flex-shrink-0">
                      🎲 随机生成
                    </Button>
                    <input
                      type="text"
                      value={llmKeywords}
                      onChange={e => setLlmKeywords(e.target.value)}
                      placeholder="输入关键词或需求，如: 角色站在雨夜霓虹街头"
                      className="flex-1 px-3 py-2 rounded text-xs outline-none"
                      style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                    />
                    <Button size="sm" disabled={llmLoading || !llmKeywords.trim()} onClick={() => handleAIGenerate('keyword', 'refgen')} className="flex-shrink-0">
                      🚀 生成
                    </Button>
                  </div>
                  {llmStatusMsg && (
                    <div className="p-2 rounded text-[11px] border" style={{ background: 'var(--card)', borderColor: 'var(--border)' }}>
                      {llmStatusMsg}
                    </div>
                  )}
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium">生图模型:</label>
                    <select
                      value={refgenModel}
                      onChange={e => setRefgenModel(e.target.value)}
                      className="w-full px-2 py-2 rounded text-xs outline-none"
                      style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                    >
                      {IMAGE_MODELS.map(m => (
                        <option key={m.id || 'default'} value={m.id}>{m.label}</option>
                      ))}
                    </select>
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium">画幅比例 (Aspect Ratio):</label>
                    <select
                      value={refgenAspect}
                      onChange={e => setRefgenAspect(e.target.value)}
                      className="w-full px-2 py-2 rounded text-xs outline-none"
                      style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                    >
                      {ASPECT_RATIOS.map(a => (
                        <option key={a.value} value={a.value}>{a.label}</option>
                      ))}
                    </select>
                  </div>
                </div>

                <Button
                  disabled={refgenLoading}
                  onClick={handleRefGenGenerate}
                  className="w-full py-2.5 font-bold text-white shadow"
                  style={{ background: 'linear-gradient(135deg, #f59e0b, #ef4444)' }}
                >
                  {refgenLoading ? '⏳ 正在生成（约 5-20 秒）...' : '🖼️ 用参考图生成新画面'}
                </Button>
              </CardContent>
            </Card>
          )}

          {/* TAB: Batch Hub */}
          {activeTab === 'batch' && (
            <Card className="py-4">
              <CardHeader>
                <CardTitle className="text-xs font-semibold uppercase tracking-wider">批量画面生成中心 (Batch Hub)</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                <div className="text-xs leading-relaxed" style={{ color: 'var(--muted)' }}>
                  一键扫描项目 [{selectedProject?.name || '-'}] 包含的所有分镜 Scene，自动挑选出尚未生图的分镜，批量向 Flow 调度器提交画图命令。
                </div>
                <div className="flex items-center justify-between p-3 rounded border" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
                  <span className="text-xs font-medium">目前场景数量:</span>
                  <Badge variant="outline">{scenes.length} Scenes</Badge>
                </div>
                <Button
                  disabled={loading || scenes.length === 0}
                  onClick={() => handleGenerateSceneImage()}
                  className="w-full py-2.5 font-bold text-white shadow"
                  style={{ background: 'linear-gradient(135deg, #ec4899, #8b5cf6)' }}
                >
                  ⚡ 一键全套生图
                </Button>
              </CardContent>
            </Card>
          )}

          {statusMsg && (
            <div className="p-3 rounded text-xs border animate-fadeIn" style={{ background: 'var(--card)', borderColor: 'var(--border)' }}>
              {statusMsg}
            </div>
          )}

        </div>

        {/* Right Side: Real-Time Gallery & Request Queue (7 cols) */}
        <div className="col-span-12 lg:col-span-7 flex flex-col gap-4">
          <Card className="py-4 h-full flex flex-col">
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="text-xs font-semibold uppercase tracking-wider flex items-center gap-2">
                <span>🖼️ 项目生成成果与分镜画廊</span>
                <span className="text-[10px] font-normal" style={{ color: 'var(--muted)' }}>
                  (归属: {selectedProject?.name || '未选择'})
                </span>
              </CardTitle>
              <Button size="sm" variant="outline" onClick={() => selectedProjectId && loadProjectAssets(selectedProjectId)}>
                刷新资产
              </Button>
            </CardHeader>

            <CardContent className="flex-1 flex flex-col gap-4 overflow-auto">

              {/* RefGen Results — shown instead of the gallery on the refgen tab */}
              {activeTab === 'refgen' && (
                <div className="flex flex-col gap-2">
                  <span className="text-xs font-bold flex items-center gap-1.5" style={{ color: 'var(--accent)' }}>
                    <span>🖼️ 参考图生图结果 ({refgenResults.length})</span>
                    {refgenResults.length > 0 && (
                      <button
                        onClick={() => {
                          if (!confirm('确定清空所有参考图生图结果？（仅删除本地记录，云端图片仍在）')) return
                          refgenResults.forEach(r => {
                            fetchAPI(`/api/refgen/results/${r.id}`, { method: 'DELETE' }).catch(() => {})
                          })
                          setRefgenResults([])
                        }}
                        className="text-[10px] px-1.5 py-0.5 rounded border hover:border-red-400 text-red-400 transition-colors"
                      >
                        清空
                      </button>
                    )}
                  </span>
                  {refgenResults.length === 0 ? (
                    <div className="p-8 text-center text-xs border rounded-lg border-dashed" style={{ color: 'var(--muted)', borderColor: 'var(--border)' }}>
                      暂无生成结果。左侧选择参考图 + 填写提示词，点击「用参考图生成新画面」！
                    </div>
                  ) : (
                    <div className="grid grid-cols-2 gap-3">
                      {refgenResults.map((r, idx) => {
                        const dlState = downloading?.id === r.id ? downloading.res : null
                        return (
                          <div key={r.id} className="p-2.5 rounded-lg border flex flex-col gap-2" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
                            <div className="flex items-center justify-between">
                              <span className="text-[10px]" style={{ color: 'var(--muted)' }}>
                                {r.time}{r.durationMs ? ` · ⏱ 生成 ${(r.durationMs / 1000).toFixed(1)}s` : ''}
                              </span>
                              <div className="flex gap-0.5 items-center">
                                {(['1K', '2K', '4K'] as const).map(res => (
                                  <button
                                    key={res}
                                    disabled={dlState !== null}
                                    onClick={() => downloadAtResolution(r.url, r.mediaId, `refgen_${r.id.slice(0, 8)}`, res, r.id)}
                                    className="text-[10px] px-1.5 py-0.5 rounded border hover:border-accent transition-colors disabled:opacity-50"
                                    style={{ color: 'var(--muted)', borderColor: 'var(--border)' }}
                                    title={`下载 ${res} 分辨率`}
                                  >
                                    {dlState === res ? '⏳' : `⬇️${res}`}
                                  </button>
                                ))}
                              </div>
                            </div>
                            <div
                              className="w-full rounded overflow-hidden bg-black flex items-center justify-center border"
                              style={{
                                borderColor: 'var(--border)',
                                aspectRatio: ASPECT_CSS[r.aspect || ''] || '16 / 9',
                              }}
                            >
                              <CachedImage mediaId={r.mediaId} src={r.url} alt={`refgen-${idx}`} className="w-full h-full object-cover" />
                            </div>
                            <span className="text-[11px] line-clamp-2 leading-relaxed" style={{ color: 'var(--text)' }}>{r.prompt}</span>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )}

              {/* Entities Reference Images Section */}
              {activeTab !== 'refgen' && characters.length > 0 && (
                <div className="flex flex-col gap-2">
                  <span className="text-xs font-bold flex items-center gap-1.5" style={{ color: 'var(--accent)' }}>
                    <span>👥 角色/实体参考图 ({characters.length})</span>
                  </span>
                  <div className="grid grid-cols-3 gap-3">
                    {characters.map(c => (
                      <div key={c.id} className="p-2.5 rounded-lg border flex flex-col gap-2 relative group" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-semibold truncate">{c.name}</span>
                            <div className="flex items-center gap-1">
                              <Badge variant="outline" className="text-[9px]">{c.entity_type || 'char'}</Badge>
                              {c.reference_image_url && (
                                <button
                                  onClick={() => downloadImage(c.reference_image_url!, `ref_${c.name}.jpg`)}
                                  className="text-[11px] px-1.5 py-0.5 rounded border hover:border-accent transition-colors"
                                  title="下载原图"
                                >
                                  ⬇️
                                </button>
                              )}
                              <button
                                onClick={() => handleDeleteCharacter(c.id)}
                                className="text-[11px] px-1.5 py-0.5 rounded border hover:bg-red-500/20 text-red-400 opacity-70 group-hover:opacity-100 transition-opacity"
                                title="删除此实体"
                              >
                                🗑️
                              </button>
                            </div>
                          </div>
                        <div className="w-full aspect-square rounded overflow-hidden bg-black flex items-center justify-center border relative" style={{ borderColor: 'var(--border)' }}>
                          {c.reference_image_url ? (
                            <CachedImage
                              mediaId={c.media_id}
                              src={c.reference_image_url}
                              alt={c.name}
                              className="w-full h-full object-cover rounded cursor-pointer group-hover:scale-105 transition-transform"
                              onClick={() => window.open(c.reference_image_url!, '_blank')}
                              onError={(e) => {
                                (e.target as HTMLElement).style.display = 'none'
                                const parent = (e.target as HTMLElement).parentElement
                                if (parent) {
                                  parent.innerHTML = `<div class="p-2 text-center flex flex-col items-center justify-center h-full gap-1"><span class="text-[10px] text-red-400 font-semibold">⚠️ 链接已失效</span><span class="text-[9px] text-muted">点击右上角🗑️可删除</span></div>`
                                }
                              }}
                            />
                          ) : c.media_id ? (
                            <div className="flex flex-col items-center p-2 text-center">
                              <span className="text-xs font-semibold" style={{ color: 'var(--green)' }}>✓ 已生成 MediaID</span>
                              <span className="text-[9px] truncate w-full mono" style={{ color: 'var(--muted)' }}>{c.media_id.slice(0, 8)}</span>
                            </div>
                          ) : (
                            <div className="flex flex-col items-center p-2 text-center gap-1">
                              <span className="text-[10px]" style={{ color: 'var(--yellow)' }}>⚠️ 未生成画面</span>
                              <span className="text-[9px]" style={{ color: 'var(--muted)' }}>点击右上角🗑️可删除</span>
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Scenes Video & Image Grid Section */}
              {activeTab !== 'refgen' && (
              <div className="flex flex-col gap-2 mt-2">
                <span className="text-xs font-bold flex items-center gap-1.5" style={{ color: 'var(--cyan)' }}>
                  <span>🎬 分镜画面 ({scenes.length})</span>
                </span>

                {scenes.length === 0 ? (
                  <div className="p-8 text-center text-xs border rounded-lg border-dashed" style={{ color: 'var(--muted)', borderColor: 'var(--border)' }}>
                    暂无生成的分镜画面。请在左侧【分镜生图】提交第一张画面！
                  </div>
                ) : (
                  <>
                  <div className="grid grid-cols-2 gap-3">
                    {pageScenes.map((s, idx) => {
                      const globalIdx = idx + (safePage - 1) * scenePageSize
                      const videoUrl = s.vertical_video_url || s.horizontal_video_url
                      const imageUrl = s.vertical_image_url || s.horizontal_image_url
                      const imageMediaId = s.vertical_image_media_id || s.horizontal_image_media_id
                      const status = s.vertical_image_status || s.horizontal_image_status || 'PENDING'
                      const dlState = downloading?.id === s.id ? downloading.res : null
                      return (
                        <div key={s.id} className="p-2.5 rounded-lg border flex flex-col gap-2" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-bold" style={{ color: 'var(--cyan)' }}>Scene #{s.display_order || globalIdx+1}</span>
                            <div className="flex gap-1">
                              <Badge variant="outline" className="text-[9px]">{status}</Badge>
                              {imageUrl && (
                                <div className="flex gap-0.5 items-center">
                                  {(['1K', '2K', '4K'] as const).map(res => (
                                    <button
                                      key={res}
                                      disabled={dlState !== null}
                                      onClick={() => handleSceneDownload(s, globalIdx, res)}
                                      className="text-[10px] px-1.5 py-0.5 rounded border hover:border-accent transition-colors disabled:opacity-50"
                                      style={{ color: 'var(--muted)', borderColor: 'var(--border)' }}
                                      title={`下载 ${res} 分辨率`}
                                    >
                                      {dlState === res ? '⏳' : `⬇️${res}`}
                                    </button>
                                  ))}
                                </div>
                              )}
                            </div>
                          </div>
                          <div className="w-full aspect-video rounded overflow-hidden bg-black flex items-center justify-center border" style={{ borderColor: 'var(--border)' }}>
                            {videoUrl ? (
                              <video controls src={videoUrl} className="w-full h-full object-cover" />
                            ) : imageUrl ? (
                              <CachedImage mediaId={imageMediaId} src={imageUrl} alt="scene" className="w-full h-full object-cover" />
                            ) : (
                              <span className="text-[10px]" style={{ color: 'var(--muted)' }}>📷 媒体生成中...</span>
                            )}
                          </div>
                          <span className="text-[11px] line-clamp-2 leading-relaxed" style={{ color: 'var(--text)' }}>{s.prompt}</span>
                        </div>
                      )
                    })}
                  </div>

                  {/* Pagination controls */}
                  <div className="flex items-center justify-center gap-2 mt-1">
                    <button
                      disabled={safePage <= 1}
                      onClick={() => setScenePage(p => Math.max(1, p - 1))}
                      className="px-2.5 py-1 rounded border text-[11px] hover:border-accent transition-colors disabled:opacity-40"
                      style={{ borderColor: 'var(--border)', color: 'var(--text)' }}
                    >
                      ⬅️ 上一页
                    </button>
                    <span className="text-[11px]" style={{ color: 'var(--muted)' }}>
                      第 {safePage} / {sceneTotalPages} 页（共 {scenes.length} 个分镜，按时间倒序）
                    </span>
                    <button
                      disabled={safePage >= sceneTotalPages}
                      onClick={() => setScenePage(p => Math.min(sceneTotalPages, p + 1))}
                      className="px-2.5 py-1 rounded border text-[11px] hover:border-accent transition-colors disabled:opacity-40"
                      style={{ borderColor: 'var(--border)', color: 'var(--text)' }}
                    >
                      下一页 ➡️
                    </button>
                    <span className="flex items-center gap-1 ml-2">
                      <input
                        type="number"
                        min={1}
                        max={sceneTotalPages}
                        value={jumpPage}
                        onChange={e => setJumpPage(e.target.value)}
                        placeholder="页"
                        className="w-14 px-1.5 py-1 rounded text-[11px] outline-none"
                        style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                      />
                      <button
                        onClick={() => {
                          const n = parseInt(jumpPage, 10)
                          if (!Number.isNaN(n) && n >= 1 && n <= sceneTotalPages) {
                            setScenePage(n)
                          } else {
                            alert(`请输入 1 ~ ${sceneTotalPages} 之间的页码`)
                          }
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
              </div>
              )}

            </CardContent>
          </Card>
        </div>

      </div>
    </div>
  )
}

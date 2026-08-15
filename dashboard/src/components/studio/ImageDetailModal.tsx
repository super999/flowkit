import React, { useState, useEffect } from 'react'
import { Badge } from '../ui/badge'
import { postAPI } from '../../api/client'

export interface MediaDetailInfo {
  title?: string
  src: string
  mediaId?: string | null
  prompt?: string | null
  translatedPrompt?: string | null
  aspect?: string | null
  model?: string | null
  durationMs?: number | null
  time?: string | null
  createdAt?: string | null
  updatedAt?: string | null
  status?: string | null
  entityType?: string | null
  sceneOrder?: number | null
  source?: string
  mediaType?: string | null
}

const IMG_CACHE_NAME = 'flowkit-img'

const ASPECT_RATIO_MAP: Record<string, string> = {
  IMAGE_ASPECT_RATIO_PORTRAIT: '9:16 (竖屏)',
  IMAGE_ASPECT_RATIO_LANDSCAPE: '16:9 (横屏)',
  IMAGE_ASPECT_RATIO_SQUARE: '1:1 (正方形)',
  IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR: '3:4',
  IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE: '4:3',
  VERTICAL: '9:16 (竖屏)',
  HORIZONTAL: '16:9 (横屏)',
  PORTRAIT: '9:16 (竖屏)',
  LANDSCAPE: '16:9 (横屏)',
  SQUARE: '1:1 (正方形)',
  '9:16': '9:16 (竖屏)',
  '16:9': '16:9 (横屏)',
  '1:1': '1:1 (正方形)',
}

const MODEL_NAME_MAP: Record<string, string> = {
  GEM_PIX_2: '🍌 Banana Pro (GEM_PIX_2)',
  IMAGE_MODEL_GEM_PIX_2: '🍌 Banana Pro (GEM_PIX_2)',
  NANO_BANANA_PRO: '🍌 Banana Pro (GEM_PIX_2)',
  'BANANA PRO': '🍌 Banana Pro (GEM_PIX_2)',
  NARWHAL: '🍌 Banana 2 (NARWHAL)',
  IMAGE_MODEL_NARWHAL: '🍌 Banana 2 (NARWHAL)',
  NANO_BANANA_2: '🍌 Banana 2 (NARWHAL)',
  'BANANA 2': '🍌 Banana 2 (NARWHAL)',
  HARBOR_SEAL: '🍌 Banana 2 Lite (HARBOR_SEAL)',
  IMAGE_MODEL_HARBOR_SEAL: '🍌 Banana 2 Lite (HARBOR_SEAL)',
  NANO_BANANA_2_LITE: '🍌 Banana 2 Lite (HARBOR_SEAL)',
  'BANANA 2 LITE': '🍌 Banana 2 Lite (HARBOR_SEAL)',
  IMAGE_MODEL_IMAGEN_3_0: 'Imagen 3 (IMAGEN_3_0)',
  IMAGEN_3_0: 'Imagen 3 (IMAGEN_3_0)',
  IMAGEN_3: 'Imagen 3 (IMAGEN_3)',
  IMAGE_MODEL_IMAGEN_3_FAST: 'Imagen 3 Fast (FAST)',
  IMAGEN_3_FAST: 'Imagen 3 Fast (FAST)',
  IMAGE_MODEL_IMAGEN_2: 'Imagen 2 (IMAGEN_2)',
  IMAGEN_2: 'Imagen 2 (IMAGEN_2)',
}

export function formatAspectRatio(aspect?: string | null, mediaType?: string | null): string {
  const isVideo = mediaType?.toUpperCase() === 'VIDEO'
  if (!aspect) return isVideo ? '16:9 (横屏视频)' : '9:16 (默认竖屏)'
  const upper = aspect.toUpperCase()
  if (upper === 'VIDEO_ASPECT_RATIO_LANDSCAPE') return '16:9 (横屏视频)'
  if (upper === 'VIDEO_ASPECT_RATIO_PORTRAIT') return '9:16 (竖屏视频)'
  if (upper === 'VIDEO_ASPECT_RATIO_SQUARE') return '1:1 (方形视频)'
  if (ASPECT_RATIO_MAP[upper]) return ASPECT_RATIO_MAP[upper]
  if (ASPECT_RATIO_MAP[aspect]) return ASPECT_RATIO_MAP[aspect]
  const clean = aspect.replace(/^(IMAGE|VIDEO)_ASPECT_RATIO_/, '')
  return ASPECT_RATIO_MAP[clean] || clean
}

export function formatModelName(model?: string | null, source?: string, mediaType?: string | null): string {
  const isVideo = mediaType?.toUpperCase() === 'VIDEO'
  if (isVideo) {
    if (model && (model.toUpperCase().includes('VEO') || model.toUpperCase().includes('3'))) {
      return `🎬 Google Veo 3.1 (${model})`
    }
    return '🎬 Google Veo 3.1'
  }

  const isUserUpload = source === 'upload' || source === 'user' || (!model && source !== 'project' && source !== 'library')
  if (isUserUpload) {
    return '📁 用户自主上传 (非模型生图)'
  }

  if (!model || !model.trim()) {
    if (source === 'library') return '🍌 Banana Pro (本地/图库)'
    if (source === 'project') return '🍌 Banana Pro (GEM_PIX_2)'
    return '— (未指定模型)'
  }

  const raw = model.trim()
  const upper = raw.toUpperCase()

  if (MODEL_NAME_MAP[upper]) return MODEL_NAME_MAP[upper]
  if (MODEL_NAME_MAP[raw]) return MODEL_NAME_MAP[raw]

  if (upper.includes('GEM_PIX_2') || upper.includes('BANANA_PRO') || upper.includes('BANANA PRO')) {
    return '🍌 Banana Pro (GEM_PIX_2)'
  }
  if (upper.includes('HARBOR') || upper.includes('SEAL') || upper.includes('LITE')) {
    return '🍌 Banana 2 Lite (HARBOR_SEAL)'
  }
  if (upper.includes('NARWHAL') || upper.includes('BANANA_2') || upper.includes('BANANA 2')) {
    return '🍌 Banana 2 (NARWHAL)'
  }
  if (upper.includes('IMAGEN_3_FAST') || upper.includes('IMAGEN 3 FAST')) {
    return 'Imagen 3 Fast (FAST)'
  }
  if (upper.includes('IMAGEN_3') || upper.includes('IMAGEN 3') || upper.includes('IMAGEN3')) {
    return 'Imagen 3 (IMAGEN_3_0)'
  }
  if (upper.includes('IMAGEN_2') || upper.includes('IMAGEN 2')) {
    return 'Imagen 2 (IMAGEN_2)'
  }
  if (upper.includes('VEO_3_1') || upper.includes('VEO')) {
    return `🎬 Google Veo 3.1 (${raw})`
  }

  return raw
}

export function CachedImage({ mediaId, src, alt, className, onClick, onError, onLoad }: {
  mediaId?: string | null
  src?: string | null
  alt?: string
  className?: string
  onClick?: () => void
  onError?: (e: React.SyntheticEvent<HTMLImageElement>) => void
  onLoad?: (e: React.SyntheticEvent<HTMLImageElement>) => void
}) {
  const [displaySrc, setDisplaySrc] = useState<string | null | undefined>(() => {
    if (src?.startsWith('/output/')) return src
    return src
  })
  const [, setHasFailed] = useState(false)
  const [triedProxy, setTriedProxy] = useState(false)

  useEffect(() => {
    setHasFailed(false)
    setTriedProxy(false)
    let revokeUrl: string | null = null
    let cancelled = false

    async function load() {
      if (!src && !mediaId) {
        setDisplaySrc(null)
        return
      }

      if (src && src.startsWith('/output/')) {
        setDisplaySrc(src)
        return
      }

      if (mediaId) {
        try {
          const cache = await caches.open(IMG_CACHE_NAME)
          const key = `https://local.flowkit.cache/${mediaId}`
          const hit = await cache.match(key)
          if (hit) {
            const blobUrl = URL.createObjectURL(await hit.blob())
            revokeUrl = blobUrl
            if (!cancelled) setDisplaySrc(blobUrl)
            return
          }
        } catch {
          /* ignore */
        }
      }

      if (!cancelled) setDisplaySrc(src || (mediaId ? `/api/flow/media/proxy?media_id=${mediaId}` : null))
    }

    load()
    return () => {
      cancelled = true
      if (revokeUrl) URL.revokeObjectURL(revokeUrl)
    }
  }, [mediaId, src])

  const handleImgError = (e: React.SyntheticEvent<HTMLImageElement>) => {
    if (!triedProxy && (mediaId || src)) {
      setTriedProxy(true)
      const proxyUrl = `/api/flow/media/proxy?${mediaId ? `media_id=${mediaId}&` : ''}${src ? `url=${encodeURIComponent(src)}` : ''}`
      setDisplaySrc(proxyUrl)
      return
    }

    setHasFailed(true)
    if (onError) onError(e)
  }

  if (!displaySrc) {
    return (
      <div className={`flex items-center justify-center bg-zinc-900 text-zinc-600 text-xs ${className || ''}`}>
        无图片数据
      </div>
    )
  }

  return (
    <img
      src={displaySrc}
      alt={alt || 'image'}
      className={className}
      onClick={onClick}
      onError={handleImgError}
      onLoad={onLoad}
      referrerPolicy="no-referrer"
    />
  )
}

interface ImageDetailModalProps {
  info: MediaDetailInfo
  onClose: () => void
  onDownloadAtResolution?: (url: string, mediaId: string | null | undefined, baseName: string, res: '1K' | '2K' | '4K', key: string) => Promise<void>
}

function formatBytes(bytes: number): string {
  if (!bytes || isNaN(bytes) || bytes <= 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`
}

export default function ImageDetailModal({
  info,
  onClose,
  onDownloadAtResolution,
}: ImageDetailModalProps) {
  const [copiedPrompt, setCopiedPrompt] = useState(false)
  const [copiedMediaId, setCopiedMediaId] = useState(false)
  const [promptTab, setPromptTab] = useState<'original' | 'translated'>('original')
  const [currentSrc, setCurrentSrc] = useState<string | null | undefined>(info.src)
  const [cachedLocally, setCachedLocally] = useState<boolean>(() => {
    return Boolean(
      info.src?.startsWith('/output/') ||
      info.src?.startsWith('data:')
    )
  })
  const [caching, setCaching] = useState(false)
  const [cacheError, setCacheError] = useState<string | null>(null)

  // Reset prompt tab when media changes
  useEffect(() => {
    setPromptTab('original')
  }, [info.mediaId, info.prompt, info.translatedPrompt])

  // Media dimensions (width x height) & file size in bytes
  const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null)
  const [fileSize, setFileSize] = useState<string | null>(null)
  const [measuringSize, setMeasuringSize] = useState(false)

  // Detect file size and preload dimensions whenever source changes
  useEffect(() => {
    let cancelled = false
    setDimensions(null)
    setFileSize(null)

    async function inspectMedia() {
      const activeSrc = currentSrc || info.src
      if (!activeSrc) return

      // 1. Data URI
      if (activeSrc.startsWith('data:')) {
        const approxBytes = Math.round((activeSrc.length - (activeSrc.indexOf(',') + 1)) * 3 / 4)
        if (!cancelled && approxBytes > 0) setFileSize(formatBytes(approxBytes))
        return
      }

      // 2. Cache Storage
      if (info.mediaId) {
        try {
          const cache = await caches.open(IMG_CACHE_NAME)
          const hit = await cache.match(`https://local.flowkit.cache/${info.mediaId}`)
          if (hit) {
            const blob = await hit.blob()
            if (!cancelled && blob.size > 0) {
              setFileSize(formatBytes(blob.size))
              return
            }
          }
        } catch {
          /* ignore */
        }
      }

      // 3. Remote HTTP HEAD / GET
      setMeasuringSize(true)
      try {
        const headRes = await fetch(activeSrc, { method: 'HEAD' }).catch(() => null)
        if (headRes && headRes.ok) {
          const cl = headRes.headers.get('content-length')
          if (cl) {
            const bytes = parseInt(cl, 10)
            if (!isNaN(bytes) && bytes > 0) {
              if (!cancelled) setFileSize(formatBytes(bytes))
              return
            }
          }
        }
        const getRes = await fetch(activeSrc).catch(() => null)
        if (getRes && getRes.ok) {
          const blob = await getRes.blob()
          if (!cancelled && blob.size > 0) {
            setFileSize(formatBytes(blob.size))
          }
        }
      } catch {
        /* ignore */
      } finally {
        if (!cancelled) setMeasuringSize(false)
      }
    }

    inspectMedia()

    // Preload image dimensions if not already provided
    const activeSrc = currentSrc || info.src
    if (activeSrc && !info.mediaType?.toUpperCase().includes('VIDEO') && !activeSrc.includes('.mp4')) {
      const img = new Image()
      img.src = activeSrc
      img.onload = () => {
        if (!cancelled && img.naturalWidth && img.naturalHeight) {
          setDimensions({ width: img.naturalWidth, height: img.naturalHeight })
        }
      }
    }

    return () => {
      cancelled = true
    }
  }, [currentSrc, info.src, info.mediaId, info.mediaType])

  const handleCacheToLocal = async () => {
    if (!info.mediaId && !info.src) return
    setCaching(true)
    setCacheError(null)
    try {
      if (info.mediaId) {
        const res = await postAPI<{ status: string; local_path?: string }>(`/api/media-library/cache/${info.mediaId}`, {})
        if (res?.status === 'success' || res?.local_path) {
          setCachedLocally(true)
          const newPath = res.local_path || `/output/_cache/${info.mediaId}.jpg`
          setCurrentSrc(`${newPath}?t=${Date.now()}`)
          return
        }
      }
      const resp = await fetch(`/api/flow/media/proxy?${info.mediaId ? `media_id=${info.mediaId}&` : ''}t=${Date.now()}`)
      if (resp.ok) {
        setCachedLocally(true)
        if (info.mediaId) setCurrentSrc(`/output/_cache/${info.mediaId}.jpg?t=${Date.now()}`)
      } else {
        setCacheError('获取失败：该图在 Google Flow 云端可能已过期或已被删除')
      }
    } catch (e: any) {
      setCacheError('获取失败：该图在 Google Flow 云端可能已过期或已被删除')
    } finally {
      setCaching(false)
    }
  }

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  const copyText = (text: string, type: 'prompt' | 'mediaId') => {
    navigator.clipboard.writeText(text)
    if (type === 'prompt') {
      setCopiedPrompt(true)
      setTimeout(() => setCopiedPrompt(false), 2000)
    } else {
      setCopiedMediaId(true)
      setTimeout(() => setCopiedMediaId(false), 2000)
    }
  }

  const defaultDownload = async (res: '1K' | '2K' | '4K') => {
    const activeUrl = currentSrc || info.src
    if (onDownloadAtResolution && activeUrl) {
      await onDownloadAtResolution(activeUrl, info.mediaId, `detail_${info.mediaId?.slice(0, 8) || 'img'}`, res, 'modal')
      return
    }
    // Fallback download logic
    try {
      const link = document.createElement('a')
      link.href = activeUrl || ''
      link.download = `flowkit_${info.mediaId || 'image'}_${res}.png`
      link.target = '_blank'
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
    } catch {
      if (activeUrl) window.open(activeUrl, '_blank')
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-3 md:p-6 bg-black/80 backdrop-blur-md animate-in fade-in duration-150"
      onClick={onClose}
    >
      <div
        className="bg-zinc-950 border border-zinc-800 rounded-2xl overflow-hidden shadow-2xl max-w-5xl w-full h-[88vh] max-h-[850px] flex flex-col md:flex-row relative"
        onClick={e => e.stopPropagation()}
      >
        {/* Left: Large Image/Video Stage */}
        <div className="flex-1 flex flex-col items-center justify-between bg-black/60 p-4 relative overflow-hidden border-b md:border-b-0 md:border-r border-zinc-800/80">
          <div className="w-full flex-1 flex items-center justify-center min-h-0 overflow-hidden">
            {info.mediaType?.toUpperCase() === 'VIDEO' || currentSrc?.includes('.mp4') || info.aspect?.includes('VIDEO') ? (
              <video
                key={currentSrc}
                src={currentSrc || ''}
                controls
                autoPlay
                loop
                playsInline
                onLoadedMetadata={e => {
                  const v = e.currentTarget
                  if (v.videoWidth && v.videoHeight) {
                    setDimensions({ width: v.videoWidth, height: v.videoHeight })
                  }
                }}
                className="max-w-full max-h-full object-contain rounded-lg shadow-lg"
              />
            ) : (
              <CachedImage
                mediaId={info.mediaId}
                src={currentSrc}
                alt={info.title || 'detail'}
                onLoad={e => {
                  const img = e.currentTarget
                  if (img.naturalWidth && img.naturalHeight) {
                    setDimensions({ width: img.naturalWidth, height: img.naturalHeight })
                  }
                }}
                className="max-w-full max-h-full object-contain rounded-lg shadow-lg cursor-zoom-in hover:scale-[1.01] transition-transform"
                onClick={() => currentSrc && window.open(currentSrc, '_blank')}
              />
            )}
          </div>

          {/* Bottom Download Bar */}
          <div className="w-full pt-3 mt-2 border-t border-zinc-800/60 flex items-center justify-between flex-wrap gap-2 text-xs">
            {/* Left side: Download Actions */}
            {info.mediaType?.toUpperCase() === 'VIDEO' || currentSrc?.includes('.mp4') || info.aspect?.includes('VIDEO') ? (
              <div className="flex items-center gap-2">
                <button
                  onClick={() => {
                    const link = document.createElement('a')
                    link.href = currentSrc || ''
                    link.download = `flowkit_video_${info.mediaId?.slice(0, 8) || 'clip'}.mp4`
                    link.target = '_blank'
                    document.body.appendChild(link)
                    link.click()
                    document.body.removeChild(link)
                  }}
                  className="text-[11px] px-3 py-1 rounded border border-cyan-700 bg-cyan-950/60 text-cyan-300 hover:bg-cyan-900 transition-all flex items-center gap-1 active:scale-95"
                >
                  ⬇️ 下载原始 MP4 视频
                </button>
                {currentSrc && (
                  <button
                    onClick={() => window.open(currentSrc, '_blank')}
                    className="text-[11px] px-2.5 py-1 rounded border border-zinc-700 bg-zinc-900 text-zinc-300 hover:border-cyan-400 hover:text-cyan-400 hover:bg-cyan-500/10 transition-all active:scale-95"
                  >
                    🔗 新标签页播放
                  </button>
                )}
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <div className="flex items-center gap-1.5">
                  <span className="text-[11px] text-zinc-400">下载分辨率:</span>
                  {(['1K', '2K', '4K'] as const).map(res => (
                    <button
                      key={res}
                      onClick={() => defaultDownload(res)}
                      className="text-[11px] px-2.5 py-1 rounded border border-zinc-700 bg-zinc-900 text-zinc-200 hover:border-cyan-400 hover:text-cyan-400 hover:bg-cyan-500/10 transition-all active:scale-95"
                    >
                      ⬇️ {res}
                    </button>
                  ))}
                </div>
                {info.src && (
                  <button
                    onClick={() => window.open(info.src, '_blank')}
                    className="text-[11px] px-2.5 py-1 rounded border border-zinc-700 bg-zinc-900 text-zinc-300 hover:border-cyan-400 hover:text-cyan-400 hover:bg-cyan-500/10 transition-all active:scale-95 flex items-center gap-1"
                  >
                    <span>🔗 新标签页打开原图</span>
                  </button>
                )}
              </div>
            )}

            {/* Right side: Resolution & Size Pill */}
            {(dimensions || fileSize || measuringSize) && (
              <div className="flex items-center gap-2 px-2.5 py-1 rounded-md bg-zinc-900/90 border border-zinc-800 text-[11px] text-zinc-300 shadow-sm ml-auto">
                {dimensions && (
                  <span className="font-mono text-cyan-300 flex items-center gap-1" title="实际分辨率 (宽 × 高)">
                    <span>📐</span>
                    <span>{dimensions.width} × {dimensions.height} px</span>
                  </span>
                )}
                {dimensions && (fileSize || measuringSize) && <span className="text-zinc-600">·</span>}
                {(fileSize || measuringSize) && (
                  <span className="font-mono text-emerald-300 flex items-center gap-1" title="文件体积大小">
                    <span>💾</span>
                    <span>{fileSize || (measuringSize ? '计算中...' : '')}</span>
                  </span>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Right: Metadata Panel */}
        <div className="w-full md:w-80 lg:w-96 p-5 flex flex-col gap-4 overflow-y-auto bg-zinc-900/60">
          {/* Header with clear window title */}
          <div className="flex items-start justify-between pb-3 border-b border-zinc-800">
            <div className="flex flex-col gap-1 min-w-0 pr-2">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-bold text-zinc-100 flex items-center gap-1.5">
                  {info.mediaType?.toUpperCase() === 'VIDEO' || currentSrc?.includes('.mp4') ? '🎬 视频详情查看器' : '🖼️ 媒体详情查看器'}
                </span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800/90 text-zinc-400 font-mono border border-zinc-700/60">
                  #MediaDetail
                </span>
                {info.status && (
                  <Badge variant="outline" className="text-[10px]">{info.status}</Badge>
                )}
              </div>
              {info.title && info.title !== '元数据信息' && info.title !== '参考媒体元数据' && (
                <span className="text-[11px] text-zinc-400 truncate max-w-[280px]" title={info.title}>
                  {info.title}
                </span>
              )}
            </div>
            <button
              onClick={onClose}
              className="text-zinc-400 hover:text-zinc-100 w-7 h-7 rounded-full bg-zinc-800/60 flex items-center justify-center text-sm transition-colors shrink-0"
              title="关闭 (Esc)"
            >
              ✕
            </button>
          </div>

          {/* Prompt Section (Supports Dual Prompt Tabs) */}
          {(() => {
            const hasDualPrompts = Boolean(
              info.prompt &&
              info.translatedPrompt &&
              info.translatedPrompt.trim() !== '' &&
              info.translatedPrompt.trim() !== info.prompt.trim()
            )
            const activePrompt = (promptTab === 'translated' && info.translatedPrompt)
              ? info.translatedPrompt
              : (info.prompt || info.title || '')

            if (!activePrompt && !info.translatedPrompt && !info.prompt) return null

            return (
              <div className="flex flex-col gap-1.5">
                <div className="flex items-center justify-between text-xs">
                  {hasDualPrompts ? (
                    <div className="flex items-center gap-1 bg-zinc-950 p-0.5 rounded-lg border border-zinc-800">
                      <button
                        onClick={() => setPromptTab('original')}
                        className={`text-[11px] px-2 py-0.5 rounded-md font-medium transition-all ${
                          promptTab === 'original'
                            ? 'bg-cyan-500/20 text-cyan-300 font-semibold shadow-sm border border-cyan-500/30'
                            : 'text-zinc-400 hover:text-zinc-200'
                        }`}
                        title="查看原始输入的提示词 (用户输入)"
                      >
                        🇨🇳 原始提示词
                      </button>
                      <button
                        onClick={() => setPromptTab('translated')}
                        className={`text-[11px] px-2 py-0.5 rounded-md font-medium transition-all ${
                          promptTab === 'translated'
                            ? 'bg-blue-500/20 text-blue-300 font-semibold shadow-sm border border-blue-500/30'
                            : 'text-zinc-400 hover:text-zinc-200'
                        }`}
                        title="查看 Google Flow 大模型翻译/底层接收的英文提示词"
                      >
                        🌐 英文/底层词
                      </button>
                    </div>
                  ) : (
                    <span className="font-semibold text-zinc-300">画面提示词 / 名称</span>
                  )}
                  <button
                    onClick={() => copyText(activePrompt, 'prompt')}
                    className="text-[10px] px-1.5 py-0.5 rounded border border-zinc-700 hover:border-cyan-500 text-zinc-400 hover:text-cyan-300 transition-colors shrink-0"
                    title="一键复制当前显示的提示词"
                  >
                    {copiedPrompt ? '✓ 已复制' : '📋 复制'}
                  </button>
                </div>
                <div className="p-2.5 rounded-lg bg-zinc-950/80 border border-zinc-800 text-[11px] text-zinc-300 leading-relaxed max-h-40 overflow-y-auto select-text whitespace-pre-wrap font-sans">
                  {activePrompt}
                </div>
              </div>
            )
          })()}

          {/* Metadata Key-Values */}
          <div className="flex flex-col gap-2.5 text-xs">
            <span className="font-semibold text-zinc-300">生成参数与详情</span>

            {/* Media ID */}
            {info.mediaId && (
              <div className="flex flex-col gap-0.5 p-2 rounded bg-zinc-950/50 border border-zinc-800/60">
                <div className="flex items-center justify-between text-[10px] text-zinc-400">
                  <span>Media ID (UUID)</span>
                  <button
                    onClick={() => copyText(info.mediaId!, 'mediaId')}
                    className="text-[9px] hover:text-cyan-400 underline"
                  >
                    {copiedMediaId ? '已复制' : '复制'}
                  </button>
                </div>
                <span className="font-mono text-[10px] text-cyan-400 break-all select-all">{info.mediaId}</span>
              </div>
            )}

            {/* Grid metrics */}
            <div className="grid grid-cols-2 gap-2 text-[11px]">
              {/* 分辨率 (Dimensions) */}
              <div className="p-2 rounded bg-zinc-950/50 border border-zinc-800/60 flex flex-col">
                <span className="text-[10px] text-zinc-400">分辨率 (宽 × 高)</span>
                <span className="font-semibold text-cyan-300 font-mono text-[11px] truncate" title={dimensions ? `${dimensions.width} × ${dimensions.height} 像素` : '检测中...'}>
                  {dimensions ? `${dimensions.width} × ${dimensions.height} px` : '检测中...'}
                </span>
              </div>

              {/* 文件大小 (File Size) */}
              <div className="p-2 rounded bg-zinc-950/50 border border-zinc-800/60 flex flex-col">
                <span className="text-[10px] text-zinc-400">文件大小 (体积)</span>
                <span className="font-semibold text-emerald-300 font-mono text-[11px] truncate" title={fileSize || (measuringSize ? '正在计算...' : '未知')}>
                  {fileSize || (measuringSize ? '计算中...' : '未知')}
                </span>
              </div>

              {/* 生图/视频模型 / 来源 */}
              <div className="p-2 rounded bg-zinc-950/50 border border-zinc-800/60 flex flex-col">
                <span className="text-[10px] text-zinc-400">
                  {info.mediaType?.toUpperCase() === 'VIDEO' ? '视频生成模型' : (info.source === 'upload' ? '来源类型' : '生图模型')}
                </span>
                <span
                  className={`font-semibold truncate ${
                    info.source === 'upload'
                      ? 'text-amber-300'
                      : info.mediaType?.toUpperCase() === 'VIDEO'
                      ? 'text-purple-300'
                      : 'text-emerald-400'
                  }`}
                  title={formatModelName(info.model, info.source, info.mediaType)}
                >
                  {formatModelName(info.model, info.source, info.mediaType)}
                </span>
              </div>

              {/* 画面比例 (Aspect Ratio) */}
              <div className="p-2 rounded bg-zinc-950/50 border border-zinc-800/60 flex flex-col">
                <span className="text-[10px] text-zinc-400">画面比例</span>
                <span className="font-semibold text-cyan-300 truncate" title={formatAspectRatio(info.aspect, info.mediaType)}>
                  {formatAspectRatio(info.aspect, info.mediaType)}
                </span>
              </div>

              {info.sceneOrder !== undefined && (
                <div className="p-2 rounded bg-zinc-950/50 border border-zinc-800/60 flex flex-col">
                  <span className="text-[10px] text-zinc-400">分镜序号</span>
                  <span className="font-semibold text-cyan-300">Scene #{info.sceneOrder}</span>
                </div>
              )}
              {info.entityType && (
                <div className="p-2 rounded bg-zinc-950/50 border border-zinc-800/60 flex flex-col">
                  <span className="text-[10px] text-zinc-400">实体类型</span>
                  <span className="font-semibold text-purple-300">{info.entityType}</span>
                </div>
              )}
              {info.durationMs != null && (
                <div className="p-2 rounded bg-zinc-950/50 border border-zinc-800/60 flex flex-col">
                  <span className="text-[10px] text-zinc-400">生成耗时</span>
                  <span className="font-semibold text-amber-300">{(info.durationMs / 1000).toFixed(1)}s</span>
                </div>
              )}
              <div className="p-2 rounded bg-zinc-950/50 border border-zinc-800/60 flex flex-col justify-between">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] text-zinc-400">缓存状态</span>
                  {!cachedLocally && (info.mediaId || info.src) && (
                    <button
                      onClick={handleCacheToLocal}
                      disabled={caching}
                      className="text-[9px] px-1.5 py-0.5 rounded border border-cyan-700 bg-cyan-950/60 text-cyan-300 hover:bg-cyan-900 transition-colors disabled:opacity-50"
                      title="下载并持久化保存至本地 output/_cache/"
                    >
                      {caching ? '缓存中...' : '💾 转为本地'}
                    </button>
                  )}
                </div>
                <span className={`font-semibold ${cachedLocally ? 'text-emerald-400' : 'text-zinc-300'}`}>
                  {cachedLocally ? '✓ 已本地持久化' : '☁️ 远端 CDN'}
                </span>
                {cacheError && (
                  <span className="text-[10px] text-rose-400 mt-1">
                    ⚠️ {cacheError}
                  </span>
                )}
              </div>
            </div>

            {/* Timestamps: Flow creation time & local sync time */}
            <div className="flex flex-col gap-1.5 pt-1 border-t border-zinc-800/60">
              {(info.createdAt || info.time) && (
                <div className="p-2 rounded bg-zinc-950/50 border border-zinc-800/60 flex flex-col text-[11px]">
                  <div className="flex items-center justify-between text-[10px] text-zinc-400">
                    <span className="font-medium text-zinc-300">⏱️ Flow 云端创建时间</span>
                    <span className="text-[9px] text-zinc-500">Flow 原生生成时间</span>
                  </div>
                  <span className="text-zinc-200 font-mono text-[10px] mt-0.5">
                    {info.createdAt ? new Date(info.createdAt).toLocaleString() : info.time}
                  </span>
                </div>
              )}

              {info.updatedAt && (
                <div className="p-2 rounded bg-zinc-950/50 border border-zinc-800/60 flex flex-col text-[11px]">
                  <div className="flex items-center justify-between text-[10px] text-zinc-400">
                    <span className="font-medium text-zinc-300">💾 本地同步/缓存时间</span>
                    <span className="text-[9px] text-zinc-500">FlowKit 获取落库时间</span>
                  </div>
                  <span className="text-zinc-400 font-mono text-[10px] mt-0.5">
                    {new Date(info.updatedAt).toLocaleString()}
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

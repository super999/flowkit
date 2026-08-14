import React, { useEffect, useState } from 'react'
import { useWebSocket } from '../api/useWebSocket'
import { Image as ImageIcon, Compass, Sliders, Play, RotateCcw, AlertTriangle } from 'lucide-react'
import type { Project, Scene } from '../types'

interface Material {
  id: string
  name: string
  style_instruction: string
  is_builtin: boolean
}

export default function Txt2ImgPage() {
  const [prompt, setPrompt] = useState('')
  const [orientation, setOrientation] = useState<'HORIZONTAL' | 'VERTICAL'>('HORIZONTAL')
  const [selectedMaterial, setSelectedMaterial] = useState('realistic')
  const [materials, setMaterials] = useState<Material[]>([])
  
  const [status, setStatus] = useState<string>('')
  const [generating, setGenerating] = useState(false)
  const [generatedImageUrl, setGeneratedImageUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  
  const [history, setHistory] = useState<Scene[]>([])
  const { lastEvent } = useWebSocket()

  // Load materials and history on mount
  useEffect(() => {
    fetchMaterials()
  }, [])

  useEffect(() => {
    fetchHistory()
  }, [selectedMaterial])

  // Listen to websocket updates for the active generation
  useEffect(() => {
    if (!lastEvent || !generating) return
    if (lastEvent.type === 'request_update') {
      const update = lastEvent.data as { status: string; error?: string }
      if (update.status === 'PROCESSING') {
        setStatus('Google Flow generating image (approx. 10-15s)...')
      } else if (update.status === 'FAILED') {
        setGenerating(false)
        setError(update.error || 'Generation failed')
      }
    }
  }, [lastEvent, generating])

  const fetchMaterials = async () => {
    try {
      const res = await fetch('/api/materials')
      if (res.ok) {
        const data = await res.json()
        setMaterials(data)
      }
    } catch (e) {
      console.error(e)
    }
  }

  const fetchHistory = async () => {
    try {
      const projName = `Txt2Img Sandbox (${selectedMaterial})`
      const projectsRes = await fetch('/api/projects')
      if (!projectsRes.ok) return
      
      const projects: Project[] = await projectsRes.json()
      const targetProj = projects.find(p => p.name === projName)
      if (!targetProj) {
        setHistory([])
        return
      }

      // Fetch scenes for videos in this project
      const videosRes = await fetch(`/api/videos?project_id=${targetProj.id}`)
      if (!videosRes.ok) return
      const videos = await videosRes.json()
      
      let allScenes: Scene[] = []
      for (const vid of videos) {
        const scenesRes = await fetch(`/api/scenes?video_id=${vid.id}`)
        if (scenesRes.ok) {
          const scenes: Scene[] = await scenesRes.json()
          allScenes = [...allScenes, ...scenes]
        }
      }
      
      // Sort history descending by created_at
      allScenes.sort((a, b) => new Date(b.created_at || '').getTime() - new Date(a.created_at || '').getTime())
      setHistory(allScenes)
    } catch (e) {
      console.error(e)
    }
  }

  const handleGenerate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!prompt.trim()) return

    setGenerating(true)
    setError(null)
    setGeneratedImageUrl(null)
    setStatus('Checking sandbox project...')

    try {
      const projName = `Txt2Img Sandbox (${selectedMaterial})`
      
      // 1. Get or create project
      const projectsRes = await fetch('/api/projects')
      const projects: Project[] = await projectsRes.json()
      let project = projects.find(p => p.name === projName)

      if (!project) {
        setStatus('Creating sandbox project...')
        const createRes = await fetch('/api/projects', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: projName,
            story: `Interactive text-to-image sandbox for style: ${selectedMaterial}`,
            material: selectedMaterial,
            characters: []
          })
        })
        if (!createRes.ok) {
          const errData = await createRes.json()
          throw new Error(errData.detail || 'Failed to create sandbox project')
        }
        project = await createRes.json()
      }

      const pid = project!.id

      // 2. Create video entry
      setStatus('Initializing canvas...')
      const videoRes = await fetch('/api/videos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: pid,
          title: `Txt2Img ${new Date().toLocaleString()}`,
          orientation: orientation
        })
      })
      if (!videoRes.ok) throw new Error('Failed to create video canvas')
      const video = await videoRes.json()
      const vid_id = video.id

      // 3. Create scene entry
      setStatus('Saving prompt details...')
      const sceneRes = await fetch('/api/scenes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          video_id: vid_id,
          display_order: 1,
          prompt: prompt,
          chain_type: 'ROOT'
        })
      })
      if (!sceneRes.ok) throw new Error('Failed to save scene prompt')
      const scene = await sceneRes.json()
      const sid = scene.id

      // 4. Submit image generation batch
      setStatus('Sending request to Google Flow...')
      const batchRes = await fetch('/api/requests/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          requests: [
            {
              type: 'GENERATE_IMAGE',
              scene_id: sid,
              project_id: pid,
              video_id: vid_id,
              orientation: orientation
            }
          ]
        })
      })
      if (!batchRes.ok) {
        const errData = await batchRes.json()
        throw new Error(errData.detail?.[0]?.msg || errData.detail || 'Failed to submit batch request')
      }

      setStatus('Queued. Waiting for extension worker...')

      // 5. Poll for completion (fallback in case websocket lag)
      let done = false
      let attempts = 0
      const maxAttempts = 60 // 2 minutes

      while (!done && attempts < maxAttempts) {
        await new Promise(resolve => setTimeout(resolve, 2000))
        attempts++

        const statusRes = await fetch(`/api/requests/batch-status?video_id=${vid_id}&type=GENERATE_IMAGE`)
        if (statusRes.ok) {
          const statusData = await statusRes.json()
          if (statusData.done) {
            done = true
            if (statusData.failed > 0) {
              throw new Error('Image generation worker returned failure')
            }
          }
        }
      }

      if (!done) {
        throw new Error('Generation timed out')
      }

      // 6. Fetch completed image
      setStatus('Retrieving generated image...')
      const finalScenesRes = await fetch(`/api/scenes?video_id=${vid_id}`)
      if (finalScenesRes.ok) {
        const finalScenes: Scene[] = await finalScenesRes.json()
        const targetScene = finalScenes[0]
        const imgUrl = orientation === 'HORIZONTAL' 
          ? targetScene.horizontal_image_url 
          : targetScene.vertical_image_url

        if (imgUrl) {
          setGeneratedImageUrl(imgUrl)
          setStatus('Generation complete!')
          fetchHistory() // refresh history
        } else {
          throw new Error('Image URL missing in completed response')
        }
      } else {
        throw new Error('Failed to retrieve final scene details')
      }

    } catch (e: any) {
      console.error(e)
      setError(e.message || 'An unexpected error occurred')
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="max-w-6xl mx-auto space-y-8 pb-12">
      {/* Header */}
      <div className="flex items-center gap-3 border-b pb-4" style={{ borderColor: 'var(--border)' }}>
        <div className="p-2 rounded-lg bg-blue-500/10 text-blue-400">
          <ImageIcon size={24} />
        </div>
        <div>
          <h1 className="text-xl font-bold text-white">Text-to-Image Sandbox (文生图)</h1>
          <p className="text-xs" style={{ color: 'var(--muted)' }}>
            Quickly render single test frames from Google Flow using styles and prompts
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        {/* Settings and Prompt Panel */}
        <div className="lg:col-span-3 space-y-6">
          <form onSubmit={handleGenerate} className="p-5 rounded-xl border space-y-5" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
            
            {/* Prompt input */}
            <div className="space-y-2">
              <label className="text-xs font-bold text-zinc-300 flex items-center gap-1.5">
                <Compass size={14} className="text-blue-400" />
                Prompt (提示词)
              </label>
              <textarea
                value={prompt}
                onChange={e => setPrompt(e.target.value)}
                placeholder="Describe your scene in detail (e.g. 'A futuristic cyberpunk street at night, neon signs, rainy reflection, highly detailed RPG style')"
                className="w-full h-32 p-3 rounded-lg border text-xs text-white focus:outline-none transition-colors"
                style={{
                  background: 'var(--bg)',
                  borderColor: 'var(--border)',
                  outline: 'none',
                }}
                disabled={generating}
              />
            </div>

            {/* Config controls */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Orientation selector */}
              <div className="space-y-2">
                <label className="text-xs font-bold text-zinc-300 flex items-center gap-1.5">
                  <Sliders size={14} className="text-blue-400" />
                  Aspect Ratio (纵横比)
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setOrientation('HORIZONTAL')}
                    className={`py-2 px-3 text-xs font-semibold rounded border transition-colors ${
                      orientation === 'HORIZONTAL'
                        ? 'border-blue-500/50 bg-blue-500/10 text-blue-400'
                        : 'border-zinc-800 bg-zinc-900/50 text-zinc-400 hover:text-zinc-300'
                    }`}
                    disabled={generating}
                  >
                    Landscape 16:9 (横版)
                  </button>
                  <button
                    type="button"
                    onClick={() => setOrientation('VERTICAL')}
                    className={`py-2 px-3 text-xs font-semibold rounded border transition-colors ${
                      orientation === 'VERTICAL'
                        ? 'border-blue-500/50 bg-blue-500/10 text-blue-400'
                        : 'border-zinc-800 bg-zinc-900/50 text-zinc-400 hover:text-zinc-300'
                    }`}
                    disabled={generating}
                  >
                    Portrait 9:16 (竖版)
                  </button>
                </div>
              </div>

              {/* Material style selector */}
              <div className="space-y-2">
                <label className="text-xs font-bold text-zinc-300 flex items-center gap-1.5">
                  <Sliders size={14} className="text-blue-400" />
                  Visual Style (渲染材质)
                </label>
                <select
                  value={selectedMaterial}
                  onChange={e => setSelectedMaterial(e.target.value)}
                  className="w-full py-2 px-3 text-xs font-semibold rounded border text-white bg-zinc-900/50 focus:outline-none transition-colors"
                  style={{ borderColor: 'var(--border)' }}
                  disabled={generating}
                >
                  {materials.map(mat => (
                    <option key={mat.id} value={mat.id}>
                      {mat.name} ({mat.id})
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Error view */}
            {error && (
              <div className="flex items-start gap-2 p-3 bg-red-500/10 border border-red-500/20 text-red-400 rounded-lg text-xs">
                <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
                <p className="break-words w-full">{error}</p>
              </div>
            )}

            {/* Action button / Status progress */}
            <div className="pt-2">
              {generating ? (
                <div className="w-full py-3 rounded-lg border border-blue-500/20 bg-blue-500/5 flex flex-col items-center justify-center gap-2">
                  <div className="w-5 h-5 rounded-full border-2 border-t-blue-500 border-blue-500/20 animate-spin" />
                  <span className="text-xs font-semibold text-blue-400">{status}</span>
                </div>
              ) : (
                <button
                  type="submit"
                  disabled={!prompt.trim()}
                  className={`w-full py-2.5 rounded-lg text-xs font-semibold flex items-center justify-center gap-1.5 transition ${
                    prompt.trim()
                      ? 'bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-500/20 cursor-pointer'
                      : 'bg-zinc-800 text-zinc-500 cursor-not-allowed border border-zinc-700/50'
                  }`}
                >
                  <Play size={14} fill="currentColor" />
                  Generate Image (生成渲染图)
                </button>
              )}
            </div>

          </form>
        </div>

        {/* Display Canvas Panel */}
        <div className="lg:col-span-2 flex flex-col justify-between border rounded-xl p-5" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
          <div className="space-y-3">
            <h3 className="text-xs font-bold text-zinc-300 uppercase tracking-wider">Canvas Output (渲染画布)</h3>
            
            {generatedImageUrl ? (
              <div className="relative rounded-lg overflow-hidden border border-zinc-800 group" style={{ background: 'var(--bg)' }}>
                <img 
                  src={generatedImageUrl} 
                  alt="Generated Sandbox Result" 
                  className="w-full object-contain mx-auto"
                  style={{ maxHeight: '350px' }} 
                />
                <a
                  href={generatedImageUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="absolute bottom-2 right-2 px-2 py-1 bg-black/60 hover:bg-black/80 text-[10px] text-white rounded transition backdrop-blur-xs font-semibold"
                >
                  Open Original
                </a>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center border border-dashed border-zinc-800 rounded-lg h-72 text-zinc-500 gap-2" style={{ background: 'var(--bg)' }}>
                {generating ? (
                  <>
                    <div className="w-8 h-8 rounded-full border-2 border-t-blue-500 border-blue-500/20 animate-spin" />
                    <span className="text-[10px] text-zinc-400">Rendering frame...</span>
                  </>
                ) : (
                  <>
                    <ImageIcon size={32} className="text-zinc-600" />
                    <span className="text-[10px] text-zinc-400">Your generated frame will appear here</span>
                  </>
                )}
              </div>
            )}
          </div>
          
          <div className="text-[10px] text-zinc-500 mt-4 leading-relaxed bg-zinc-950/30 p-3 rounded-lg border border-zinc-900">
            📍 <strong>Tip</strong>: Text-to-image uses reference-free base models from Google Flow. The layout, visual styling rules, and lighting configurations are handled automatically using the selected style.
          </div>
        </div>
      </div>

      {/* History section */}
      <div className="space-y-4">
        <div className="flex items-center justify-between border-b pb-2" style={{ borderColor: 'var(--border)' }}>
          <h2 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-1.5">
            <RotateCcw size={14} className="text-blue-400" />
            Generation History ({selectedMaterial})
          </h2>
          <button 
            onClick={fetchHistory} 
            className="text-[10px] text-blue-400 hover:underline"
          >
            Refresh History
          </button>
        </div>

        {history.length === 0 ? (
          <div className="flex items-center justify-center h-24 text-zinc-500 text-xs border border-dashed border-zinc-800 rounded-lg">
            No previous generations in this style sandbox.
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {history.map(scene => {
              const imgUrl = orientation === 'HORIZONTAL' ? scene.horizontal_image_url : scene.vertical_image_url
              if (!imgUrl) return null
              return (
                <div key={scene.id} className="group border border-zinc-800 rounded-lg overflow-hidden flex flex-col justify-between" style={{ background: 'var(--surface)' }}>
                  <div className="relative aspect-video bg-black overflow-hidden flex items-center justify-center">
                    <img 
                      src={imgUrl} 
                      alt="History Item" 
                      className="w-full object-cover group-hover:scale-105 transition duration-300" 
                    />
                    <a
                      href={imgUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="absolute opacity-0 group-hover:opacity-100 top-2 right-2 px-1.5 py-0.5 bg-black/70 hover:bg-black/90 text-[9px] text-white rounded transition"
                    >
                      View
                    </a>
                  </div>
                  <div className="p-2 space-y-1">
                    <p className="text-[10px] text-zinc-300 font-semibold line-clamp-2" title={scene.prompt}>
                      {scene.prompt}
                    </p>
                    <p className="text-[8px] text-zinc-500">
                      {new Date(scene.created_at || '').toLocaleDateString()}
                    </p>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

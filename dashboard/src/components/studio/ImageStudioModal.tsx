import { useState, useEffect } from 'react'
import { fetchAPI, postAPI, putAPI } from '../../api/client'
import type { Project, Video } from '../../types'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '../ui/dialog'
import { Button } from '../ui/button'
import { Badge } from '../ui/badge'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../ui/tabs'

interface Props {
  open: boolean
  onClose: () => void
  initialProjectId?: string
  onSuccess?: () => void
}

export default function ImageStudioModal({ open, onClose, initialProjectId, onSuccess }: Props) {
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<string>('')
  const [activeTab, setActiveTab] = useState<'scene' | 'character' | 'batch'>('scene')
  const [loading, setLoading] = useState(false)
  const [statusMsg, setStatusMsg] = useState<string>('')

  // Inline project creation/linking state
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [createMode, setCreateMode] = useState<'link' | 'new'>('link')
  const [existingFlowId, setExistingFlowId] = useState('')
  const [newProjName, setNewProjName] = useState('')
  const [newProjStory, setNewProjStory] = useState('')
  const [newProjMaterial, setNewProjMaterial] = useState('realistic')

  // Scene image state
  const [scenePrompt, setScenePrompt] = useState('')
  const [orientation, setOrientation] = useState<'HORIZONTAL' | 'VERTICAL'>('VERTICAL')
  const [materials, setMaterials] = useState<{ id: string; name: string }[]>([])
  const [sceneMaterial, setSceneMaterial] = useState('')
  const [charMaterial, setCharMaterial] = useState('none')

  // Character ref state
  const [charName, setCharName] = useState('')
  const [charDescription, setCharDescription] = useState('')
  const [charType, setCharType] = useState<'character' | 'location' | 'prop'>('character')

  // Fetch all projects when modal opens
  useEffect(() => {
    if (!open) return
    fetchAPI<{ id: string; name: string }[]>('/api/materials').then(setMaterials).catch(() => setMaterials([]))
    fetchAPI<Project[]>('/api/projects').then(data => {
      setProjects(data)
      if (data.length === 0) {
        setShowCreateForm(true)
      } else if (initialProjectId && data.some(p => p.id === initialProjectId)) {
        setSelectedProjectId(initialProjectId)
      } else {
        fetchAPI<{ project_id?: string }>('/api/active-project').then(active => {
          if (active.project_id && data.some(p => p.id === active.project_id)) {
            setSelectedProjectId(active.project_id)
          } else {
            setSelectedProjectId(data[0].id)
          }
        }).catch(() => setSelectedProjectId(data[0].id))
      }
    }).catch(console.error)
  }, [open, initialProjectId])

  async function handleCreateNewProject() {
    if (!newProjName.trim()) {
      alert('请输入项目名称')
      return
    }
    if (createMode === 'link' && !existingFlowId.trim()) {
      alert('请输入或粘贴 Google Flow 网页项目 URL 或 项目 ID！')
      return
    }

    setLoading(true)
    setStatusMsg(createMode === 'link' ? '正在关联 Flow 现有项目...' : '正在创建新项目...')
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
      const updated = await fetchAPI<Project[]>('/api/projects')
      setProjects(updated)
      setSelectedProjectId(created.id)
      setShowCreateForm(false)
      setNewProjName('')
      setNewProjStory('')
      setExistingFlowId('')
      setStatusMsg(`🎉 成功${createMode === 'link' ? '关联并锁定' : '创建'} Flow 项目 "${created.name}" (ID: ${created.id.slice(0, 8)})！`)
      if (onSuccess) onSuccess()
    } catch (e: any) {
      console.error(e)
      setStatusMsg(`❌ 操作失败: ${e.message || e}`)
    } finally {
      setLoading(false)
    }
  }

  const selectedProject = projects.find(p => p.id === selectedProjectId)

  async function ensureVideo(projectId: string): Promise<string> {
    const vids = await fetchAPI<Video[]>(`/api/videos?project_id=${projectId}`)
    if (vids.length > 0) return vids[0].id
    const newVid = await postAPI<Video>('/api/videos', { project_id: projectId, title: '未命名视频' })
    return newVid.id
  }

  async function handleSetAsActiveProject() {
    if (!selectedProjectId) return
    await putAPI('/api/active-project', { project_id: selectedProjectId })
    setStatusMsg(`已将项目 "${selectedProject?.name}" 设为当前激活目标`)
  }

  // Handle single scene image generation
  async function handleGenerateSceneImage() {
    if (!selectedProjectId) {
      alert('请先选择目标归属项目！')
      return
    }
    if (!scenePrompt.trim()) {
      alert('请输入生图提示词 (Prompt)')
      return
    }

    setLoading(true)
    setStatusMsg('正在处理画图请求...')
    try {
      // 1. Lock active project
      await putAPI('/api/active-project', { project_id: selectedProjectId })
      
      // 2. Ensure video chain exists
      const videoId = await ensureVideo(selectedProjectId)

      // 3. Create Scene
      const newScene = await postAPI<{ id: string }>('/api/scenes', {
        video_id: videoId,
        prompt: scenePrompt.trim(),
        source: 'user',
        material: sceneMaterial || undefined
      })

      // 4. Submit Batch GENERATE_IMAGE request
      await postAPI('/api/requests/batch', {
        requests: [{
          type: 'GENERATE_IMAGE',
          project_id: selectedProjectId,
          video_id: videoId,
          scene_id: newScene.id,
          orientation: orientation
        }]
      })

      setStatusMsg(`🎉 成功提交画图任务！已归属于项目: ${selectedProject?.name}`)
      setScenePrompt('')
      if (onSuccess) onSuccess()
    } catch (e: any) {
      console.error(e)
      setStatusMsg(`❌ 生图提交失败: ${e.message || e}`)
    } finally {
      setLoading(false)
    }
  }

  // Handle character reference image generation
  async function handleGenerateCharacterRef() {
    if (!selectedProjectId) {
      alert('请先选择目标归属项目！')
      return
    }
    if (!charName.trim() || !charDescription.trim()) {
      alert('请输入名称与外观特征描述！')
      return
    }

    setLoading(true)
    setStatusMsg('正在创建角色实体并提交参考图生成...')
    try {
      await putAPI('/api/active-project', { project_id: selectedProjectId })

      // 1. Create character
      const newChar = await postAPI<{ id: string }>('/api/characters', {
        project_id: selectedProjectId,
        name: charName.trim(),
        description: charDescription.trim(),
        type: charType,
        material: charMaterial === 'none' ? undefined : charMaterial
      })

      // 2. Submit GENERATE_CHARACTER_IMAGE request
      await postAPI('/api/requests/batch', {
        requests: [{
          type: 'GENERATE_CHARACTER_IMAGE',
          project_id: selectedProjectId,
          character_id: newChar.id
        }]
      })

      setStatusMsg(`🎉 实体 "${charName}" 参考图生成任务已提交！归属于: ${selectedProject?.name}`)
      setCharName('')
      setCharDescription('')
      if (onSuccess) onSuccess()
    } catch (e: any) {
      console.error(e)
      setStatusMsg(`❌ 参考图提交失败: ${e.message || e}`)
    } finally {
      setLoading(false)
    }
  }

  // Handle one-click batch generation of all missing images for project
  async function handleBatchGenerateAll() {
    if (!selectedProjectId) return
    setLoading(true)
    setStatusMsg('正在查询项目未完成生图的分镜...')
    try {
      await putAPI('/api/active-project', { project_id: selectedProjectId })
      const vids = await fetchAPI<Video[]>(`/api/videos?project_id=${selectedProjectId}`)
      if (vids.length === 0) {
        setStatusMsg('当前项目暂无分镜场景')
        setLoading(false)
        return
      }

      const videoId = vids[0].id
      const scenes = await fetchAPI<{ id: string; horizontal_image_status?: string; vertical_image_status?: string }[]>(`/api/scenes?video_id=${videoId}`)
      
      const reqs = scenes
        .filter(s => s.horizontal_image_status !== 'COMPLETED' && s.vertical_image_status !== 'COMPLETED')
        .map(s => ({
          type: 'GENERATE_IMAGE',
          project_id: selectedProjectId,
          video_id: videoId,
          scene_id: s.id,
          orientation: orientation
        }))

      if (reqs.length === 0) {
        setStatusMsg('当前项目所有分镜图片已全部生成完毕！')
        setLoading(false)
        return
      }

      await postAPI('/api/requests/batch', { requests: reqs })
      setStatusMsg(`🎉 已一键提交 ${reqs.length} 个分镜生图任务！`)
      if (onSuccess) onSuccess()
    } catch (e: any) {
      setStatusMsg(`❌ 批量生图失败: ${e.message || e}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span>🎨 生图控制台 (Image Studio)</span>
          </DialogTitle>
          <DialogDescription>
            在这里提交画面生成与实体参考图绘制，所有生成的资产将严格锁定并归属于您指定的 Flow 项目中。
          </DialogDescription>
        </DialogHeader>

        {/* Project Selector (Mandatory Asset Binding) */}
        <div className="p-3 rounded-lg border flex flex-col gap-2" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
          <div className="flex items-center justify-between text-xs font-semibold">
            <span>🎯 资产归属项目 (Target Project):</span>
            <div className="flex items-center gap-2">
              {selectedProject && (
                <button
                  onClick={handleSetAsActiveProject}
                  className="text-[11px] hover:underline"
                  style={{ color: 'var(--accent)' }}
                >
                  ⭐ 锁为当前目标
                </button>
              )}
              <button
                onClick={() => setShowCreateForm(!showCreateForm)}
                className="text-[11px] px-2 py-0.5 rounded border hover:bg-card"
                style={{ borderColor: 'var(--border)', color: 'var(--text)' }}
              >
                {showCreateForm ? '取消新建' : '➕ 快速新建项目'}
              </button>
            </div>
          </div>

          {showCreateForm ? (
            <div className="flex flex-col gap-2 p-2.5 rounded border mt-1" style={{ background: 'var(--card)', borderColor: 'var(--border)' }}>
              <div className="flex items-center gap-3 border-b pb-1.5" style={{ borderColor: 'var(--border)' }}>
                <label className="flex items-center gap-1 text-xs font-semibold cursor-pointer">
                  <input
                    type="radio"
                    name="createMode"
                    value="link"
                    checked={createMode === 'link'}
                    onChange={() => setCreateMode('link')}
                  />
                  🔗 关联 Flow 网页已有项目
                </label>
                <label className="flex items-center gap-1 text-xs font-semibold cursor-pointer">
                  <input
                    type="radio"
                    name="createMode"
                    value="new"
                    checked={createMode === 'new'}
                    onChange={() => setCreateMode('new')}
                  />
                  ➕ 创建全新 Flow 项目
                </label>
              </div>

              {createMode === 'link' && (
                <div className="flex flex-col gap-1">
                  <label className="text-[11px] font-medium" style={{ color: 'var(--accent)' }}>Flow 网页项目 URL 或 项目 ID:</label>
                  <input
                    type="text"
                    placeholder="粘贴 URL (例如 https://labs.google/.../project/5a9157d5-...) 或 ID"
                    value={existingFlowId}
                    onChange={e => setExistingFlowId(e.target.value)}
                    className="w-full px-2.5 py-1.5 rounded text-xs outline-none"
                    style={{ background: 'var(--bg)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  />
                </div>
              )}

              <div className="flex flex-col gap-1">
                <label className="text-[11px] font-medium" style={{ color: 'var(--accent)' }}>项目名称 (Project Name):</label>
                <input
                  type="text"
                  placeholder="项目名称 (例如: 消失的证人 / 赛博猫咪)"
                  value={newProjName}
                  onChange={e => setNewProjName(e.target.value)}
                  className="w-full px-2.5 py-1.5 rounded text-xs outline-none"
                  style={{ background: 'var(--bg)', color: 'var(--text)', border: '1px solid var(--border)' }}
                />
              </div>

              <div className="flex items-center justify-between pt-1">
                <div className="flex items-center gap-1.5">
                  <span className="text-[11px]">画风材质:</span>
                  <select
                    value={newProjMaterial}
                    onChange={e => setNewProjMaterial(e.target.value)}
                    className="px-2 py-1 rounded text-xs outline-none"
                    style={{ background: 'var(--bg)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  >
                    <option value="realistic">Realistic (真实写实)</option>
                    <option value="3d_pixar">3D Pixar (皮克斯)</option>
                    <option value="anime">Anime (动漫)</option>
                    <option value="stop_motion">Stop Motion (定格动画)</option>
                    <option value="minecraft">Minecraft (像素)</option>
                    <option value="oil_painting">Oil Painting (油画)</option>
                  </select>
                </div>
                <Button size="sm" disabled={loading} onClick={handleCreateNewProject}>
                  {loading ? '处理中...' : (createMode === 'link' ? '🔗 关联并锁定' : '➕ 创建并锁定')}
                </Button>
              </div>
            </div>
          ) : (
            <>
              {projects.length === 0 ? (
                <div className="text-xs p-2 text-center border rounded" style={{ color: 'var(--yellow)', borderColor: 'var(--border)' }}>
                  ⚠️ 暂无可用的项目，请点击上方【➕ 快速新建项目】开始画图。
                </div>
              ) : (
                <select
                  value={selectedProjectId}
                  onChange={e => setSelectedProjectId(e.target.value)}
                  className="w-full px-3 py-2 rounded text-xs outline-none"
                  style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                >
                  <option value="">-- 请选择目标归属项目 --</option>
                  {projects.map(p => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.material || 'default'}) — ID: {p.id.slice(0, 8)}
                    </option>
                  ))}
                </select>
              )}
              {selectedProject && (
                <div className="flex items-center gap-2 text-[11px]" style={{ color: 'var(--muted)' }}>
                  <span>画风材质: <Badge variant="outline">{selectedProject.material || 'realistic'}</Badge></span>
                  <span>状态: <Badge variant="outline">{selectedProject.status}</Badge></span>
                </div>
              )}
            </>
          )}
        </div>

        <Tabs value={activeTab} onValueChange={v => setActiveTab(v as any)} className="w-full mt-2">
          <TabsList className="w-full grid grid-cols-3">
            <TabsTrigger value="scene">🎬 分镜生图</TabsTrigger>
            <TabsTrigger value="character">👥 角色/地点参考图</TabsTrigger>
            <TabsTrigger value="batch">⚡ 项目全套批生</TabsTrigger>
          </TabsList>

          {/* TAB 1: Single Scene Image Generation */}
          <TabsContent value="scene" className="flex flex-col gap-3 py-2">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium">生图提示词 (Prompt):</label>
              <textarea
                value={scenePrompt}
                onChange={e => setScenePrompt(e.target.value)}
                placeholder="例如: 赛博朋克都市夜晚，霓虹灯光反光在湿润的路面上，主角站在高楼边缘远眺..."
                rows={3}
                className="w-full px-3 py-2 rounded text-xs outline-none resize-none"
                style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium">画风材质 (Material):</label>
              <select
                value={sceneMaterial}
                onChange={e => setSceneMaterial(e.target.value)}
                className="w-full px-3 py-1.5 rounded text-xs outline-none"
                style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
              >
                <option value="">跟随项目材质 (默认)</option>
                <option value="none">不使用材质 — 自己写风格</option>
                {materials.map(m => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
              </select>
            </div>

            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="text-xs font-medium">画幅比例:</span>
                <label className="flex items-center gap-1 text-xs cursor-pointer">
                  <input
                    type="radio"
                    name="orient"
                    value="VERTICAL"
                    checked={orientation === 'VERTICAL'}
                    onChange={() => setOrientation('VERTICAL')}
                  />
                  📱 竖屏 (9:16)
                </label>
                <label className="flex items-center gap-1 text-xs cursor-pointer">
                  <input
                    type="radio"
                    name="orient"
                    value="HORIZONTAL"
                    checked={orientation === 'HORIZONTAL'}
                    onChange={() => setOrientation('HORIZONTAL')}
                  />
                  💻 横屏 (16:9)
                </label>
              </div>

              <Button disabled={loading} onClick={handleGenerateSceneImage} className="gap-1">
                {loading ? '提交中...' : '🎨 提交画图'}
              </Button>
            </div>
          </TabsContent>

          {/* TAB 2: Character/Location Ref Image Generation */}
          <TabsContent value="character" className="flex flex-col gap-3 py-2">
            <div className="flex gap-2">
              <div className="flex-1 flex flex-col gap-1.5">
                <label className="text-xs font-medium">名称 (Entity Name):</label>
                <input
                  type="text"
                  value={charName}
                  onChange={e => setCharName(e.target.value)}
                  placeholder="例如: 探险家 Luna / 星际太空站"
                  className="w-full px-3 py-1.5 rounded text-xs outline-none"
                  style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                />
              </div>

              <div className="w-32 flex flex-col gap-1.5">
                <label className="text-xs font-medium">类型 (Type):</label>
                <select
                  value={charType}
                  onChange={e => setCharType(e.target.value as any)}
                  className="w-full px-2 py-1.5 rounded text-xs outline-none"
                  style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
                >
                  <option value="character">角色 (Character)</option>
                  <option value="location">场景 (Location)</option>
                  <option value="prop">道具 (Prop)</option>
                </select>
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium">外观特征描述 (Visual Features Description):</label>
              <textarea
                value={charDescription}
                onChange={e => setCharDescription(e.target.value)}
                placeholder="例如: 20岁女性探险家，银色短发，蓝色瞳孔，穿着白色高科技太空防护服..."
                rows={2}
                className="w-full px-3 py-2 rounded text-xs outline-none resize-none"
                style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium">画风材质 (Material):</label>
              <select
                value={charMaterial}
                onChange={e => setCharMaterial(e.target.value)}
                className="w-full px-3 py-1.5 rounded text-xs outline-none"
                style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
              >
                <option value="none">不使用材质 — 自己写风格</option>
                {materials.map(m => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
              </select>
            </div>

            <div className="flex justify-end">
              <Button disabled={loading} onClick={handleGenerateCharacterRef} className="gap-1">
                {loading ? '创建中...' : '👤 创建并生成参考图'}
              </Button>
            </div>
          </TabsContent>

          {/* TAB 3: One-click Batch Generation for Project */}
          <TabsContent value="batch" className="flex flex-col gap-3 py-2">
            <div className="text-xs leading-relaxed" style={{ color: 'var(--muted)' }}>
              一键扫描当前选中项目包含的所有分镜 Scene，自动过滤出未生图的分镜并一口气提交到 FlowKit 批处理队列中。
            </div>
            <div className="flex items-center justify-between pt-2">
              <div className="flex items-center gap-3 text-xs">
                <span>生成方向:</span>
                <Badge variant="outline">{orientation}</Badge>
              </div>
              <Button disabled={loading} onClick={handleBatchGenerateAll}>
                {loading ? '检测并提交中...' : '⚡ 一键生成全项目画面'}
              </Button>
            </div>
          </TabsContent>
        </Tabs>

        {statusMsg && (
          <div className="p-2.5 rounded text-xs mt-2 border" style={{ background: 'var(--card)', borderColor: 'var(--border)' }}>
            {statusMsg}
          </div>
        )}

        <DialogFooter className="mt-2">
          <Button variant="outline" onClick={onClose}>关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

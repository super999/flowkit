import { useState, useEffect } from 'react'
import { fetchAPI, putAPI } from '../../api/client'
import { Card, CardHeader, CardTitle, CardContent } from '../../components/ui/card'
import { Button } from '../../components/ui/button'
import { Badge } from '../../components/ui/badge'

const PROVIDERS = [
  { id: 'openai', label: 'OpenAI (GPT 系列)', placeholderUrl: 'https://api.openai.com/v1', defaultModel: 'gpt-4o-mini' },
  { id: 'deepseek', label: 'DeepSeek', placeholderUrl: 'https://api.deepseek.com/v1', defaultModel: 'deepseek-chat' },
  { id: 'anthropic', label: 'Anthropic (Claude 系列)', placeholderUrl: 'https://api.anthropic.com', defaultModel: 'claude-haiku-4-5-20251001' },
  { id: 'minimax', label: 'MiniMax (M3)', placeholderUrl: 'https://api.minimaxi.com/anthropic', defaultModel: 'MiniMax-M3' },
  { id: 'custom', label: '自定义 (OpenAI 兼容)', placeholderUrl: 'https://your-endpoint/v1', defaultModel: '' },
]

interface ProviderConfig {
  base_url: string
  api_key: string
  model: string
}

interface LlmSettings {
  active_provider: string
  providers: Record<string, ProviderConfig>
}

function emptyConfig(): ProviderConfig {
  return { base_url: '', api_key: '', model: '' }
}

// Load saved LLM settings, migrating the legacy single-provider format
function normalizeLlm(raw: Record<string, any> | undefined): LlmSettings {
  const providers: Record<string, ProviderConfig> = {}
  if (!raw) return { active_provider: 'openai', providers }

  const saved = raw.providers
  if (saved && typeof saved === 'object') {
    for (const [k, v] of Object.entries(saved as Record<string, any>)) {
      if (v && typeof v === 'object') {
        providers[k] = {
          base_url: v.base_url ?? '',
          api_key: v.api_key ?? '',
          model: v.model ?? '',
        }
      }
    }
  }

  // Legacy: {"provider": "minimax", "base_url": ..., "api_key": ..., "model": ...}
  if (Object.keys(providers).length === 0 && raw.provider) {
    providers[raw.provider] = {
      base_url: raw.base_url ?? '',
      api_key: raw.api_key ?? '',
      model: raw.model ?? '',
    }
  }

  const active = raw.active_provider || raw.provider || 'openai'
  return { active_provider: providers[active] ? active : 'openai', providers }
}

export default function SettingsPage() {
  const [loading, setLoading] = useState(false)
  const [statusMsg, setStatusMsg] = useState<string>('')

  const [activeProvider, setActiveProvider] = useState('openai')
  const [providers, setProviders] = useState<Record<string, ProviderConfig>>({})

  // Load saved configs on mount
  useEffect(() => {
    fetchAPI<Record<string, any>>('/api/settings')
      .then(s => {
        const llm = normalizeLlm(s?.llm)
        setProviders(llm.providers)
        setActiveProvider(llm.active_provider)
      })
      .catch(() => {})
  }, [])

  const providerMeta = PROVIDERS.find(p => p.id === activeProvider) ?? PROVIDERS[0]
  const config = providers[activeProvider] ?? emptyConfig()

  // Merge current provider's config back into the providers map
  function updateConfig(patch: Partial<ProviderConfig>) {
    setProviders(prev => ({
      ...prev,
      [activeProvider]: { ...(prev[activeProvider] ?? emptyConfig()), ...patch },
    }))
  }

  function switchProvider(id: string) {
    setActiveProvider(id)
    // Prefill defaults for providers that were never configured
    setProviders(prev => {
      if (prev[id]) return prev
      const meta = PROVIDERS.find(p => p.id === id)
      return { ...prev, [id]: { base_url: meta?.placeholderUrl ?? '', api_key: '', model: meta?.defaultModel ?? '' } }
    })
  }

  async function handleSave() {
    setLoading(true)
    setStatusMsg('正在保存设置...')
    try {
      await putAPI('/api/settings/llm', {
        active_provider: activeProvider,
        providers,
      } satisfies LlmSettings)
      setStatusMsg(`✅ 已保存「${providerMeta.label}」的配置，切换供应商时会自动带出。`)
    } catch (e: any) {
      setStatusMsg(`❌ 保存失败: ${e.message || e}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-5 max-w-3xl mx-auto">
      <Card className="py-4">
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle className="text-sm">⚙️ 系统设置</CardTitle>
            <span className="text-[11px]" style={{ color: 'var(--muted)' }}>
              平台级配置，按功能分区存储（settings.json）
            </span>
          </div>
          <Badge variant="outline">本地存储</Badge>
        </CardHeader>
      </Card>

      <Card className="py-4">
        <CardHeader>
          <CardTitle className="text-xs font-semibold uppercase tracking-wider">🧠 大语言模型 (LLM) 配置</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="text-xs leading-relaxed" style={{ color: 'var(--muted)' }}>
            每个供应商独立保存密钥、地址与模型，切换供应商时自动带出已保存的配置，无需重复填写。
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium">服务商 (Provider):</label>
            <select
              value={activeProvider}
              onChange={e => switchProvider(e.target.value)}
              className="w-full px-3 py-2 rounded text-xs outline-none"
              style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
            >
              {PROVIDERS.map(p => {
                const cfg = providers[p.id]
                const configured = !!cfg && (!!cfg.api_key || !!cfg.base_url)
                return (
                  <option key={p.id} value={p.id}>
                    {p.label}{configured ? ' ✓ 已配置' : ''}
                  </option>
                )
              })}
            </select>
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium">API 地址 (Base URL):</label>
            <input
              type="text"
              value={config.base_url}
              onChange={e => updateConfig({ base_url: e.target.value })}
              placeholder={providerMeta.placeholderUrl}
              className="w-full px-3 py-2 rounded text-xs outline-none"
              style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium">API 密钥 (API Key):</label>
            <input
              type="password"
              value={config.api_key}
              onChange={e => updateConfig({ api_key: e.target.value })}
              placeholder={config.api_key ? '••••••••（已保存，如需修改请重新输入）' : 'sk-...'}
              className="w-full px-3 py-2 rounded text-xs outline-none"
              style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
            />
            <span className="text-[10px]" style={{ color: 'var(--muted)' }}>
              仅保存在本机 settings.json 文件中，不会上传到任何第三方。
            </span>
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium">模型 (Model):</label>
            <input
              type="text"
              value={config.model}
              onChange={e => updateConfig({ model: e.target.value })}
              placeholder={providerMeta.defaultModel || '模型名称'}
              className="w-full px-3 py-2 rounded text-xs outline-none"
              style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
            />
          </div>

          <div className="flex justify-end">
            <Button disabled={loading} onClick={handleSave} className="gap-1">
              {loading ? '保存中...' : '💾 保存配置'}
            </Button>
          </div>

          {statusMsg && (
            <div className="p-3 rounded text-xs border" style={{ background: 'var(--card)', borderColor: 'var(--border)' }}>
              {statusMsg}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

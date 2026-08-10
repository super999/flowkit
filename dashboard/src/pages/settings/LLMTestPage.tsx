import { useState, useEffect } from 'react'
import { fetchAPI, postAPI } from '../../api/client'
import { Card, CardHeader, CardTitle, CardContent } from '../../components/ui/card'
import { Button } from '../../components/ui/button'
import { Badge } from '../../components/ui/badge'
import MarkdownView from '../../components/MarkdownView'

interface ProviderCfg {
  base_url: string
  api_key: string
  model: string
}

interface ChatResult {
  ok: boolean
  provider: string
  model: string
  base_url: string
  reply?: string
  latency_ms?: number
  error?: string
}

const QUICK_PROMPTS = [
  '你好，请用一句话介绍你自己',
  '请计算 23 × 47 等于多少，并告诉我计算过程',
  '用中文写一句元宵节祝福语',
]

export default function LLMTestPage() {
  const [providers, setProviders] = useState<Record<string, ProviderCfg>>({})
  const [activeProvider, setActiveProvider] = useState('')
  const [message, setMessage] = useState(QUICK_PROMPTS[0])
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<ChatResult | null>(null)
  const [showRaw, setShowRaw] = useState(false)

  useEffect(() => {
    fetchAPI<Record<string, any>>('/api/settings')
      .then(s => {
        const llm = s?.llm ?? {}
        const saved = (llm.providers ?? {}) as Record<string, ProviderCfg>
        setProviders(saved)
        const active = llm.active_provider || Object.keys(saved)[0] || ''
        setActiveProvider(saved[active] ? active : (Object.keys(saved)[0] ?? ''))
      })
      .catch(() => {})
  }, [])

  const configured = Object.entries(providers).filter(([, cfg]) => cfg.api_key && cfg.base_url)
  const cfg = providers[activeProvider]

  async function runTest(text: string) {
    if (!activeProvider) return
    setMessage(text)
    setResult(null)
    setLoading(true)
    try {
      const res = await postAPI<ChatResult>('/api/llm/chat', {
        provider: activeProvider,
        messages: [{ role: 'user', content: text }],
      })
      setResult(res)
    } catch (e: any) {
      setResult({ ok: false, provider: activeProvider, model: cfg?.model ?? '', base_url: cfg?.base_url ?? '', error: e.message || String(e) })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-5 max-w-3xl mx-auto">
      <Card className="py-4">
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle className="text-sm">🧪 LLM 接口测试</CardTitle>
            <span className="text-[11px]" style={{ color: 'var(--muted)' }}>
              发送简单对话，验证大模型供应商当前是否可用
            </span>
          </div>
          <Badge variant="outline">{configured.length} 个已配置</Badge>
        </CardHeader>
      </Card>

      <Card className="py-4">
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium">选择测试的供应商:</label>
            {configured.length === 0 ? (
              <div className="text-xs p-3 rounded border" style={{ color: 'var(--yellow)', borderColor: 'var(--border)' }}>
                ⚠️ 尚未配置任何 LLM 供应商，请先到「系统设置 → 大语言模型」保存 API 密钥。
              </div>
            ) : (
              <select
                value={activeProvider}
                onChange={e => setActiveProvider(e.target.value)}
                className="w-full px-3 py-2 rounded text-xs outline-none"
                style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
              >
                {configured.map(([id, c]) => (
                  <option key={id} value={id}>
                    {id} — {c.model || '默认模型'} ({c.base_url})
                  </option>
                ))}
              </select>
            )}
          </div>

          {cfg && (
            <div className="flex items-center gap-2 text-[11px]" style={{ color: 'var(--muted)' }}>
              <span>模型: <Badge variant="outline">{cfg.model || '未设置'}</Badge></span>
              <span>API: <Badge variant="outline">{cfg.base_url}</Badge></span>
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium">快捷测试语 (点击填入输入框):</label>
            <div className="flex flex-wrap gap-1.5">
              {QUICK_PROMPTS.map(p => (
                <button
                  key={p}
                  disabled={!activeProvider}
                  onClick={() => setMessage(p)}
                  className="px-2.5 py-1.5 rounded text-[11px] border transition-colors hover:border-accent disabled:opacity-50"
                  style={{ background: 'var(--card)', borderColor: 'var(--border)', color: 'var(--text)' }}
                >
                  {p.length > 14 ? `${p.slice(0, 14)}...` : p}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium">自定义消息:</label>
            <textarea
              value={message}
              onChange={e => setMessage(e.target.value)}
              rows={2}
              placeholder="输入一段话测试..."
              className="w-full px-3 py-2 rounded text-xs outline-none resize-none"
              style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
            />
          </div>

          <div className="flex justify-end">
            <Button
              disabled={loading || !activeProvider || !message.trim()}
              onClick={() => runTest(message.trim())}
              className="gap-1"
            >
              {loading ? '⏳ 请求中...' : '🚀 发送测试'}
            </Button>
          </div>

          {result && (
            <div
              className="flex flex-col gap-2 p-3 rounded border text-xs"
              style={{
                background: 'var(--card)',
                borderColor: result.ok ? 'var(--green)' : 'var(--red)',
              }}
            >
              <div className="flex items-center gap-2">
                <Badge variant="outline" style={{ color: result.ok ? 'var(--green)' : 'var(--red)' }}>
                  {result.ok ? '✅ 可用' : '❌ 不可用'}
                </Badge>
                {result.latency_ms != null && (
                  <span style={{ color: 'var(--muted)' }}>耗时 {result.latency_ms}ms</span>
                )}
                <span style={{ color: 'var(--muted)' }}>模型: {result.model}</span>
                <span className="ml-auto" />
                {result.ok && result.reply && (
                  <div className="flex items-center gap-1.5">
                    <label className="flex items-center gap-1 cursor-pointer" style={{ color: 'var(--muted)' }}>
                      <input
                        type="checkbox"
                        checked={showRaw}
                        onChange={e => setShowRaw(e.target.checked)}
                        className="cursor-pointer"
                      />
                      查看源码
                    </label>
                  </div>
                )}
              </div>

              {result.ok ? (
                showRaw ? (
                  <pre
                    className="overflow-auto max-h-80 p-3 rounded whitespace-pre-wrap"
                    style={{ background: 'var(--bg)', border: '1px solid var(--border)' }}
                  >
                    {result.reply}
                  </pre>
                ) : (
                  <MarkdownView source={result.reply ?? ''} />
                )
              ) : (
                <div className="leading-relaxed" style={{ color: 'var(--red)' }}>
                  {result.error}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

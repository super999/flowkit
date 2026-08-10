import { useMemo } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ breaks: true, gfm: true })

export default function MarkdownView({ source }: { source: string }) {
  const html = useMemo(() => {
    const raw = marked.parse(source ?? '') as string
    return DOMPurify.sanitize(raw)
  }, [source])

  return (
    <div
      className="markdown-body text-xs leading-relaxed"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}

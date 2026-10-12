import { useEffect, useRef, useState } from 'react'
import Markdown from '../lib/markdown.jsx'

// Reveals `content` progressively over a short, LENGTH-BOUNDED duration
// (never more than ~900ms, however long the message is) -- a perceived-
// typing effect for a reply that already fully arrived from the server,
// not a claim that it's live-streaming token-by-token.
function useTypewriter(content, enabled) {
  const [visibleChars, setVisibleChars] = useState(enabled ? 0 : content.length)
  const doneRef = useRef(!enabled)

  useEffect(() => {
    if (!enabled) {
      setVisibleChars(content.length)
      doneRef.current = true
      return
    }
    doneRef.current = false
    setVisibleChars(0)
    const duration = Math.min(900, Math.max(220, content.length * 3.5))
    const start = performance.now()
    let frame

    function tick(now) {
      const elapsed = now - start
      const ratio = Math.min(1, elapsed / duration)
      setVisibleChars(Math.round(content.length * ratio))
      if (ratio < 1) {
        frame = requestAnimationFrame(tick)
      } else {
        doneRef.current = true
      }
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [content, enabled])

  return { visibleText: content.slice(0, visibleChars), isRevealing: visibleChars < content.length }
}

function AssistantAvatar() {
  return (
    <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-soft">
      <div className="h-2 w-2 rounded-full bg-accent" />
    </div>
  )
}

export default function MessageBubble({ role, content, animate = false }) {
  const isUser = role === 'user'
  const { visibleText, isRevealing } = useTypewriter(content, !isUser && animate)

  if (isUser) {
    return (
      <div className="flex animate-fade-up justify-end">
        <div className="max-w-[70%] whitespace-pre-wrap rounded-lg bg-accent px-4 py-2.5 text-sm leading-relaxed text-white shadow-soft">
          {content}
        </div>
      </div>
    )
  }

  return (
    <div className="flex animate-fade-up items-start gap-2.5">
      <AssistantAvatar />
      <div className="min-w-0 max-w-[85%] rounded-lg border border-line bg-surface px-4 py-3 shadow-soft">
        <Markdown>{visibleText}</Markdown>
        {isRevealing && <span className="type-caret" aria-hidden="true" />}
      </div>
    </div>
  )
}

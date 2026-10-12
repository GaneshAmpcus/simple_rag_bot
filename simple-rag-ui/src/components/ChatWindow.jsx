import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext.jsx'
import { getSessionMessages, sendChatMessage, ApiError } from '../api/client'
import MessageBubble from './MessageBubble.jsx'

// Normalizes whatever shape the backend Message model returns into { id, role, content }
function normalizeMessage(raw, fallbackId) {
  return {
    id: raw.id ?? fallbackId,
    role: raw.role ?? raw.sender ?? 'assistant',
    content: raw.content ?? raw.message ?? raw.text ?? '',
  }
}

export default function ChatWindow({ sessionId, botId, onSessionCreated }) {
  const { token } = useAuth()
  const [messages, setMessages] = useState([])
  const [historyState, setHistoryState] = useState(sessionId ? 'loading' : 'ready')
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const scrollRef = useRef(null)
  const textareaRef = useRef(null)

  // Ids of assistant messages created during THIS session (as opposed
  // to loaded from history) -- only these get the typewriter reveal in
  // MessageBubble, so re-opening an old thread doesn't replay it.
  const [animatedIds, setAnimatedIds] = useState(() => new Set())

  useEffect(() => {
    let cancelled = false
    if (!sessionId) {
      setMessages([])
      setHistoryState('ready')
      return
    }
    async function loadHistory() {
      setHistoryState('loading')
      try {
        const data = await getSessionMessages(token, sessionId)
        if (!cancelled) {
          setMessages(data.map((m, i) => normalizeMessage(m, `hist-${i}`)))
          setHistoryState('ready')
        }
      } catch {
        if (!cancelled) setHistoryState('error')
      }
    }
    loadHistory()
    return () => {
      cancelled = true
    }
  }, [sessionId, token])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, sending])

  function autoGrow() {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }

  useEffect(autoGrow, [draft])

  async function handleSend(e) {
    e.preventDefault()
    const text = draft.trim()
    if (!text || sending) return

    setError('')
    const userMsg = { id: `local-${Date.now()}`, role: 'user', content: text }
    setMessages((prev) => [...prev, userMsg])
    setDraft('')
    setSending(true)

    try {
      const res = await sendChatMessage(token, { sessionId, message: text, botId })
      const assistantId = `local-${Date.now()}-a`
      setMessages((prev) => [...prev, { id: assistantId, role: 'assistant', content: res.answer }])
      setAnimatedIds((prev) => new Set(prev).add(assistantId))
      if (!sessionId && res.session_id) {
        onSessionCreated(res.session_id)
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Message failed to send. Try again.')
      // roll back the optimistic user message on failure
      setMessages((prev) => prev.filter((m) => m.id !== userMsg.id))
      setDraft(text)
    } finally {
      setSending(false)
    }
  }

  return (
    <main className="flex min-w-0 flex-1 flex-col bg-paper">
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto flex max-w-2xl flex-col gap-4">
          {historyState === 'loading' && (
            <p className="text-center text-xs text-muted font-mono">loading thread…</p>
          )}
          {historyState === 'error' && (
            <p className="text-center text-xs text-danger">Couldn't load this thread.</p>
          )}
          {historyState === 'ready' && messages.length === 0 && (
            <div className="mt-24 animate-fade-in text-center">
              <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-accent-soft">
                <div className="h-2.5 w-2.5 rounded-full bg-accent" />
              </div>
              <p className="font-display text-lg font-semibold text-ink">Start a new thread</p>
              <p className="mt-1 text-sm text-muted">
                Ask a question — answers draw on the shared knowledge base.
              </p>
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble
              key={m.id}
              role={m.role}
              content={m.content}
              animate={animatedIds.has(m.id)}
            />
          ))}
          {sending && (
            <div className="flex animate-fade-in items-start gap-2.5">
              <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-soft">
                <div className="h-2 w-2 rounded-full bg-accent" />
              </div>
              <div className="rounded-lg border border-line bg-surface px-4 py-3 shadow-soft">
                <span className="flex gap-1">
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted [animation-delay:-0.3s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted [animation-delay:-0.15s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted" />
                </span>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-line bg-surface px-6 py-4">
        <form onSubmit={handleSend} className="mx-auto max-w-2xl">
          <div className="flex items-end gap-2 rounded-md border border-line bg-paper px-1 py-1 transition focus-within:border-accent focus-within:shadow-glow">
            <textarea
              ref={textareaRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  handleSend(e)
                }
              }}
              rows={1}
              placeholder="Message simple-rag…"
              className="max-h-40 flex-1 resize-none bg-transparent px-2.5 py-2 text-sm text-ink outline-none placeholder:text-muted"
            />
            <button
              type="submit"
              disabled={sending || !draft.trim()}
              className="mb-0.5 mr-0.5 shrink-0 rounded-sm bg-accent px-4 py-2 text-sm font-medium text-white transition hover:bg-accent-deep disabled:opacity-40"
            >
              Send
            </button>
          </div>
          <p className="mt-1.5 px-1 text-[11px] text-muted">
            Enter to send · Shift + Enter for a new line
          </p>
        </form>
        {error && <p className="mx-auto mt-1 max-w-2xl px-1 text-xs text-danger">{error}</p>}
      </div>
    </main>
  )
}

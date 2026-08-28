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
      setMessages((prev) => [
        ...prev,
        { id: `local-${Date.now()}-a`, role: 'assistant', content: res.answer },
      ])
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
    <main className="flex min-w-0 flex-1 flex-col">
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto flex max-w-2xl flex-col gap-3">
          {historyState === 'loading' && (
            <p className="text-center text-xs text-muted font-mono">loading thread…</p>
          )}
          {historyState === 'error' && (
            <p className="text-center text-xs text-danger">Couldn't load this thread.</p>
          )}
          {historyState === 'ready' && messages.length === 0 && (
            <div className="mt-24 text-center">
              <p className="font-display text-lg font-semibold text-ink">Start a new thread</p>
              <p className="mt-1 text-sm text-muted">
                Ask a question — answers draw on the shared knowledge base.
              </p>
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.id} role={m.role} content={m.content} />
          ))}
          {sending && (
            <div className="flex justify-start">
              <div className="rounded-lg border border-line bg-surface px-4 py-2.5">
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
        <form onSubmit={handleSend} className="mx-auto flex max-w-2xl items-end gap-2">
          <textarea
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
            className="max-h-32 flex-1 resize-none rounded-md border border-line bg-paper px-3 py-2.5 text-sm text-ink outline-none focus:border-accent"
          />
          <button
            type="submit"
            disabled={sending || !draft.trim()}
            className="shrink-0 rounded-md bg-accent px-4 py-2.5 text-sm font-medium text-white transition hover:bg-accent-deep disabled:opacity-50"
          >
            Send
          </button>
        </form>
        {error && <p className="mx-auto mt-2 max-w-2xl text-xs text-danger">{error}</p>}
      </div>
    </main>
  )
}

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { listSessions, ApiError } from '../api/client'

function relativeTime(isoString) {
  const then = new Date(isoString).getTime()
  const now = Date.now()
  const diffMs = now - then
  const mins = Math.round(diffMs / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.round(hrs / 24)
  return `${days}d ago`
}

export default function Sidebar({ activeSessionId, onSelectSession, onNewChat, version }) {
  const { token, user, logout } = useAuth()
  const navigate = useNavigate()
  const [sessions, setSessions] = useState([])
  const [loadState, setLoadState] = useState('loading') // loading | ready | error

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoadState('loading')
      try {
        const data = await listSessions(token)
        if (!cancelled) {
          setSessions(data)
          setLoadState('ready')
        }
      } catch {
        if (!cancelled) setLoadState('error')
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [token, version])

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-surface">
      <div className="p-3">
        <button
          onClick={onNewChat}
          className="flex w-full items-center gap-2 rounded-sm border border-line bg-paper px-3 py-2 text-sm font-medium text-ink transition hover:border-accent hover:text-accent"
        >
          <span className="text-base leading-none">+</span> New chat
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {loadState === 'loading' && (
          <p className="px-2 py-3 text-xs text-muted font-mono">loading threads…</p>
        )}
        {loadState === 'error' && (
          <p className="px-2 py-3 text-xs text-danger">Couldn't load sessions.</p>
        )}
        {loadState === 'ready' && sessions.length === 0 && (
          <p className="px-2 py-3 text-xs text-muted">
            No threads yet. Start a new chat to begin.
          </p>
        )}
        <ul className="space-y-0.5">
          {sessions.map((s) => {
            const isActive = s.id === activeSessionId
            return (
              <li key={s.id}>
                <button
                  onClick={() => onSelectSession(s.id)}
                  className={`group relative w-full rounded-sm py-2 pl-3 pr-2 text-left text-sm transition ${
                    isActive ? 'bg-accent-soft text-accent-deep' : 'text-ink hover:bg-paper'
                  }`}
                >
                  <span
                    className={`absolute left-0 top-1/2 h-4/5 w-[3px] -translate-y-1/2 rounded-full transition-all ${
                      isActive ? 'bg-accent' : 'bg-transparent group-hover:bg-line'
                    }`}
                  />
                  <p className="truncate font-medium">{s.title || 'Untitled thread'}</p>
                  <p className="mt-0.5 truncate font-mono text-[11px] text-muted">
                    {relativeTime(s.created_at)}
                  </p>
                </button>
              </li>
            )
          })}
        </ul>
      </div>

      <div className="border-t border-line p-3">
        <button
          onClick={() => navigate('/mcp/tools')}
          className="mb-3 flex w-full items-center justify-between rounded-sm border border-line bg-paper px-3 py-2 text-left text-xs font-medium text-ink transition hover:border-accent hover:text-accent"
        >
          <span>MCP integration</span>
          <span aria-hidden="true">→</span>
        </button>
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-ink">{user?.email}</p>
          </div>
          <button
            onClick={logout}
            className="shrink-0 rounded-sm px-2 py-1 text-xs font-medium text-muted transition hover:bg-paper hover:text-danger"
          >
            Log out
          </button>
        </div>
      </div>
    </aside>
  )
}

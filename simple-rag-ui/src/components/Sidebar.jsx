import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { listSessions, listBots, ApiError } from '../api/client'

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

const DEFAULT_GROUP_KEY = '__default__'

export default function Sidebar({ activeSessionId, onSelectSession, onNewChat, version }) {
  const { token, user, logout } = useAuth()
  const navigate = useNavigate()
  const [sessions, setSessions] = useState([])
  const [loadState, setLoadState] = useState('loading') // loading | ready | error
  const [bots, setBots] = useState([])
  const [botsLoaded, setBotsLoaded] = useState(false)
  const [newChatBotId, setNewChatBotId] = useState('')
  const [collapsed, setCollapsed] = useState(() => new Set())

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

  useEffect(() => {
    let cancelled = false
    listBots(token)
      .then((data) => {
        if (!cancelled) {
          setBots(data)
          setBotsLoaded(true)
        }
      })
      .catch(() => {
        // Bot picker just won't show options -- not worth a full error state.
      })
    return () => {
      cancelled = true
    }
  }, [token, version])

  const botNameById = Object.fromEntries(bots.map((b) => [b.id, b.name]))

  // Groups sessions by bot -- "Default assistant" first (matches the
  // pre-bots experience for anyone who hasn't adopted bots yet), then
  // one group per bot in the order the sessions first mention it, so
  // the group order tracks recent activity rather than bot-creation
  // order.
  const groups = useMemo(() => {
    const byKey = new Map()
    byKey.set(DEFAULT_GROUP_KEY, { key: DEFAULT_GROUP_KEY, botId: null, label: 'Default assistant', sessions: [] })
    for (const s of sessions) {
      const key = s.bot_id || DEFAULT_GROUP_KEY
      if (!byKey.has(key)) {
        byKey.set(key, {
          key,
          botId: s.bot_id,
          label: botNameById[s.bot_id] || 'Deleted bot',
          sessions: [],
        })
      }
      byKey.get(key).sessions.push(s)
    }
    return [...byKey.values()].filter((g) => g.sessions.length > 0)
  }, [sessions, botNameById])

  function toggleGroup(key) {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const showFirstRunNudge =
    botsLoaded && bots.length === 0 && loadState === 'ready' && sessions.length === 0

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-surface">
      <div className="space-y-2 p-3">
        {bots.length > 0 && (
          <select
            value={newChatBotId}
            onChange={(e) => setNewChatBotId(e.target.value)}
            className="w-full rounded-sm border border-line bg-paper px-2 py-1.5 text-xs text-ink outline-none focus:border-accent"
          >
            <option value="">Default assistant</option>
            {bots.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        )}
        <button
          onClick={() => onNewChat(newChatBotId || null)}
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

        {showFirstRunNudge && (
          <div className="mx-1 mb-3 rounded-md border border-dashed border-accent/40 bg-accent-soft px-3 py-3">
            <p className="text-xs font-medium text-accent-deep">New here?</p>
            <p className="mt-1 text-xs leading-5 text-accent-deep/80">
              Create your first bot to give it its own instructions, knowledge base, and tools.
            </p>
            <button
              onClick={() => navigate('/bots')}
              className="mt-2 rounded-sm bg-accent px-2.5 py-1 text-xs font-medium text-white transition hover:bg-accent-deep"
            >
              Create a bot
            </button>
          </div>
        )}

        {loadState === 'ready' && sessions.length === 0 && !showFirstRunNudge && (
          <p className="px-2 py-3 text-xs text-muted">
            No threads yet. Start a new chat to begin.
          </p>
        )}

        {loadState === 'ready' && groups.length > 0 && (
          <div className="space-y-3">
            {groups.map((group) => {
              const isCollapsed = collapsed.has(group.key)
              return (
                <div key={group.key}>
                  {groups.length > 1 && (
                    <button
                      onClick={() => toggleGroup(group.key)}
                      className="flex w-full items-center justify-between px-2 py-1 text-left"
                    >
                      <span className="font-mono text-[10px] uppercase tracking-wider text-muted">
                        {group.label}
                      </span>
                      <span
                        className={`font-mono text-[10px] text-muted transition-transform ${isCollapsed ? '-rotate-90' : ''}`}
                        aria-hidden="true"
                      >
                        ▾
                      </span>
                    </button>
                  )}
                  {!isCollapsed && (
                    <ul className="space-y-0.5">
                      {group.sessions.map((s) => {
                        const isActive = s.id === activeSessionId
                        return (
                          <li key={s.id}>
                            <button
                              onClick={() => onSelectSession(s.id, s.bot_id)}
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
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="border-t border-line p-3">
        <button
          onClick={() => navigate('/bots')}
          className="mb-2 flex w-full items-center justify-between rounded-sm border border-line bg-paper px-3 py-2 text-left text-xs font-medium text-ink transition hover:border-accent hover:text-accent"
        >
          <span>Your bots</span>
          <span aria-hidden="true">→</span>
        </button>
        <button
          onClick={() => navigate('/mcp/tools')}
          className="mb-3 flex w-full items-center justify-between rounded-sm border border-line bg-paper px-3 py-2 text-left text-xs font-medium text-ink transition hover:border-accent hover:text-accent"
        >
          <span>Default assistant tools</span>
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

import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import {
  ApiError,
  addMcpServer,
  authorizeCalendar,
  authorizeGmail,
  authorizeMcpServer,
  getBot,
  getCalendarStatus,
  getGmailStatus,
  getMcpServerTools,
  listMcpServers,
  removeMcpServer,
  selectMcpServerTools,
} from '../api/client'

// Must match GMAIL_TOOL_NAMES in the backend (tools/gmail_tool.py) and
// the @mcp.tool() function names on the MCP server (server.py). These
// only ever appear on the built-in Horizon server's tool list.
const GMAIL_TOOL_NAMES = new Set(['list_gmail_messages', 'get_gmail_message', 'send_gmail_message'])

// Must match CALENDAR_AND_MEET_TOOL_NAMES in the backend
// (tools/calendar_tool.py). One Calendar connection powers both
// families -- Meet links are created through the Calendar API.
const CALENDAR_TOOL_NAMES = new Set([
  'list_calendar_events',
  'get_calendar_event',
  'create_calendar_event',
  'delete_calendar_event',
  'create_meet_event',
  'get_meet_link',
])

function ServerCard({
  server,
  tools,
  selected,
  authorizing,
  saving,
  message,
  gmailConnected,
  gmailAuthorizing,
  calendarConnected,
  calendarAuthorizing,
  onAuthorize,
  onAuthorizeGmail,
  onAuthorizeCalendar,
  onToggleTool,
  onSave,
  onRemove,
}) {
  return (
    <section className="mt-5 rounded-md border border-line bg-surface">
      <div className="flex items-start justify-between gap-4 border-b border-line p-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-display text-base font-semibold text-ink">{server.name}</h3>
            {server.is_builtin && (
              <span className="shrink-0 rounded-full bg-paper px-2 py-0.5 font-mono text-[10px] text-muted">
                Built-in
              </span>
            )}
            <span
              className={`shrink-0 rounded-full px-2.5 py-1 font-mono text-[11px] ${
                server.connected ? 'bg-accent-soft text-accent-deep' : 'bg-paper text-muted'
              }`}
            >
              {server.connected ? 'Connected' : 'Not connected'}
            </span>
          </div>
          <p className="mt-1 truncate font-mono text-xs text-muted">{server.url}</p>
        </div>
        {!server.is_builtin && onRemove && (
          <button
            onClick={onRemove}
            className="shrink-0 rounded-md px-2 py-1 text-xs font-medium text-muted transition hover:bg-paper hover:text-danger"
          >
            Remove
          </button>
        )}
      </div>

      <div className="p-4">
        {!server.connected && (
          <p className="text-sm leading-6 text-muted">
            Authorize this server to see and select its tools.
          </p>
        )}
        <button
          onClick={onAuthorize}
          disabled={authorizing}
          className={`${server.connected ? 'border border-line text-accent hover:border-accent' : 'bg-accent text-white hover:bg-accent-deep'} mt-3 rounded-md px-4 py-2.5 text-sm font-medium transition disabled:opacity-50`}
        >
          {authorizing
            ? 'Opening authorization…'
            : server.connected
              ? 'Reauthorize'
              : 'Authorize'}
        </button>
      </div>

      {server.connected && (
        <form onSubmit={onSave} className="p-4">
          <div className="divide-y divide-line rounded-md border border-line bg-paper">
            {(!tools || tools.length === 0) && (
              <p className="p-4 text-sm text-muted">No tools available.</p>
            )}
            {tools?.map((tool) => {
              const isGmailTool = GMAIL_TOOL_NAMES.has(tool.name)
              const isCalendarTool = CALENDAR_TOOL_NAMES.has(tool.name)
              const locked = (isGmailTool && !gmailConnected) || (isCalendarTool && !calendarConnected)
              return (
                <label
                  key={tool.name}
                  className={`flex gap-3 p-4 transition ${locked ? 'opacity-60' : 'cursor-pointer hover:bg-surface'}`}
                >
                  <input
                    type="checkbox"
                    checked={selected.has(tool.name)}
                    onChange={() => !locked && onToggleTool(tool.name)}
                    disabled={locked}
                    className="mt-1 h-4 w-4 accent-accent"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="block text-sm font-medium text-ink">{tool.name}</span>
                      {isGmailTool && (
                        <span className={`shrink-0 rounded-full px-2 py-0.5 font-mono text-[10px] ${gmailConnected ? 'bg-accent-soft text-accent-deep' : 'bg-surface text-muted'}`}>
                          {gmailConnected ? 'Gmail connected' : 'Gmail required'}
                        </span>
                      )}
                      {isCalendarTool && (
                        <span className={`shrink-0 rounded-full px-2 py-0.5 font-mono text-[10px] ${calendarConnected ? 'bg-accent-soft text-accent-deep' : 'bg-surface text-muted'}`}>
                          {calendarConnected ? 'Calendar connected' : 'Calendar required'}
                        </span>
                      )}
                    </span>
                    <span className="mt-1 block text-sm leading-5 text-muted">{tool.description}</span>
                    {locked && isGmailTool && (
                      <button
                        type="button"
                        onClick={onAuthorizeGmail}
                        disabled={gmailAuthorizing}
                        className="mt-2 rounded-md border border-line px-3 py-1.5 text-xs font-medium text-accent transition hover:border-accent disabled:opacity-50"
                      >
                        {gmailAuthorizing ? 'Opening authorization…' : 'Connect Gmail to enable'}
                      </button>
                    )}
                    {locked && isCalendarTool && (
                      <button
                        type="button"
                        onClick={onAuthorizeCalendar}
                        disabled={calendarAuthorizing}
                        className="mt-2 rounded-md border border-line px-3 py-1.5 text-xs font-medium text-accent transition hover:border-accent disabled:opacity-50"
                      >
                        {calendarAuthorizing ? 'Opening authorization…' : 'Connect Calendar to enable'}
                      </button>
                    )}
                  </span>
                </label>
              )
            })}
          </div>
          <button
            type="submit"
            disabled={saving}
            className="mt-4 rounded-md bg-accent px-4 py-2.5 text-sm font-medium text-white transition hover:bg-accent-deep disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save selection'}
          </button>
          {message && <p className="mt-3 text-sm text-accent-deep">{message}</p>}
        </form>
      )}
    </section>
  )
}

export default function McpTools({ embedded = false }) {
  const { token } = useAuth()
  const params = useParams()
  const botId = params.botId // undefined on /mcp/tools; set on /bots/:botId/mcp/tools and, embedded, inherited from BotEditor's /bots/:botId route
  const [bot, setBot] = useState(null)
  const [servers, setServers] = useState([])
  const [serverTools, setServerTools] = useState({})
  const [serverSelected, setServerSelected] = useState({})
  const [serverAuthorizing, setServerAuthorizing] = useState({})
  const [serverSaving, setServerSaving] = useState({})
  const [serverMessage, setServerMessage] = useState({})
  const [loadState, setLoadState] = useState('loading')
  const [error, setError] = useState('')

  const [gmailConnected, setGmailConnected] = useState(false)
  const [gmailAuthorizing, setGmailAuthorizing] = useState(false)
  const [calendarConnected, setCalendarConnected] = useState(false)
  const [calendarAuthorizing, setCalendarAuthorizing] = useState(false)

  const [newServerName, setNewServerName] = useState('')
  const [newServerUrl, setNewServerUrl] = useState('')
  const [addingServer, setAddingServer] = useState(false)

  useEffect(() => {
    let cancelled = false

    async function load() {
      setLoadState('loading')
      setError('')
      try {
        const [botInfo, srvList, gmailStatus, calendarStatus] = await Promise.all([
          botId ? getBot(token, botId) : Promise.resolve(null),
          listMcpServers(token, botId),
          getGmailStatus(token, botId),
          getCalendarStatus(token, botId),
        ])
        if (cancelled) return
        setBot(botInfo)
        setServers(srvList)
        setGmailConnected(gmailStatus.connected)
        setCalendarConnected(calendarStatus.connected)

        const connectedServers = srvList.filter((s) => s.connected)
        const toolResults = await Promise.all(
          connectedServers.map((s) => getMcpServerTools(token, s.id, botId).catch(() => [])),
        )
        if (cancelled) return
        const toolsMap = {}
        connectedServers.forEach((s, i) => {
          toolsMap[s.id] = toolResults[i]
        })
        setServerTools(toolsMap)
        setLoadState('ready')
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : 'Could not load MCP settings.')
          setLoadState('error')
        }
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [token, botId])

  async function handleAddServer(event) {
    event.preventDefault()
    if (!newServerName.trim() || !newServerUrl.trim()) return
    setAddingServer(true)
    setError('')
    try {
      const server = await addMcpServer(token, { name: newServerName.trim(), url: newServerUrl.trim() })
      setServers((prev) => [...prev, server])
      setNewServerName('')
      setNewServerUrl('')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add server.')
    } finally {
      setAddingServer(false)
    }
  }

  async function handleRemoveServer(serverId) {
    setError('')
    try {
      await removeMcpServer(token, serverId)
      setServers((prev) => prev.filter((s) => s.id !== serverId))
      setServerTools((prev) => {
        const next = { ...prev }
        delete next[serverId]
        return next
      })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove server.')
    }
  }

  async function handleAuthorizeServer(serverId) {
    setServerAuthorizing((prev) => ({ ...prev, [serverId]: true }))
    setError('')
    try {
      const { auth_url: authUrl } = await authorizeMcpServer(token, serverId, botId)
      window.location.href = authUrl
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start authorization.')
      setServerAuthorizing((prev) => ({ ...prev, [serverId]: false }))
    }
  }

  async function handleAuthorizeGmail() {
    setGmailAuthorizing(true)
    setError('')
    try {
      const { auth_url: authUrl } = await authorizeGmail(token, botId)
      window.location.href = authUrl
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start Gmail authorization.')
      setGmailAuthorizing(false)
    }
  }

  async function handleAuthorizeCalendar() {
    setCalendarAuthorizing(true)
    setError('')
    try {
      const { auth_url: authUrl } = await authorizeCalendar(token, botId)
      window.location.href = authUrl
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start Calendar authorization.')
      setCalendarAuthorizing(false)
    }
  }

  function toggleServerTool(serverId, name) {
    setServerSelected((prev) => {
      const current = prev[serverId] || new Set()
      const next = new Set(current)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return { ...prev, [serverId]: next }
    })
    setServerMessage((prev) => ({ ...prev, [serverId]: '' }))
  }

  async function handleSaveServer(serverId, event) {
    event.preventDefault()
    setServerSaving((prev) => ({ ...prev, [serverId]: true }))
    setError('')
    try {
      const names = [...(serverSelected[serverId] || [])]
      const result = await selectMcpServerTools(token, serverId, names, botId)
      setServerMessage((prev) => ({
        ...prev,
        [serverId]: `Saved ${result.selected.length} tool${result.selected.length === 1 ? '' : 's'}.`,
      }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save tool selection.')
    } finally {
      setServerSaving((prev) => ({ ...prev, [serverId]: false }))
    }
  }

  return (
    <main className={embedded ? '' : 'min-h-screen bg-paper px-5 py-8 text-ink sm:px-8'}>
      <div className={embedded ? '' : 'mx-auto max-w-2xl'}>
        {!embedded && (
          <>
            <Link
              to={botId ? '/bots' : '/'}
              className="font-mono text-xs text-muted transition hover:text-accent"
            >
              {botId ? '← Back to bots' : '← Back to chat'}
            </Link>
            <div className="mt-8 border-b border-line pb-5">
              <p className="font-mono text-xs uppercase tracking-wider text-accent">
                {botId ? `Integrations for ${bot?.name || 'this bot'}` : 'Integrations'}
              </p>
              <h1 className="mt-2 font-display text-2xl font-semibold">MCP servers</h1>
              <p className="mt-2 max-w-lg text-sm leading-6 text-muted">
                {botId
                  ? "Connect servers for this bot specifically -- its tokens and tool selections are independent of your other bots and your default assistant."
                  : 'Connect any number of MCP servers and choose which tools from each are available in chat.'}
              </p>
            </div>
          </>
        )}

        {loadState === 'loading' && (
          <p className="py-8 font-mono text-xs text-muted">loading servers…</p>
        )}
        {loadState === 'error' && <p className="py-8 text-sm text-danger">{error}</p>}

        {loadState === 'ready' && (
          <>
            <form onSubmit={handleAddServer} className={`${embedded ? '' : 'mt-8'} rounded-md border border-line bg-surface p-4`}>
              <h2 className="font-display text-base font-semibold">Add a server</h2>
              <div className="mt-3 flex flex-col gap-3 sm:flex-row">
                <input
                  type="text"
                  placeholder="Name"
                  value={newServerName}
                  onChange={(e) => setNewServerName(e.target.value)}
                  className="w-full rounded-md border border-line bg-paper px-3 py-2 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none sm:w-40"
                />
                <input
                  type="text"
                  placeholder="https://your-mcp-server.example.com/mcp"
                  value={newServerUrl}
                  onChange={(e) => setNewServerUrl(e.target.value)}
                  className="w-full flex-1 rounded-md border border-line bg-paper px-3 py-2 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none"
                />
                <button
                  type="submit"
                  disabled={addingServer || !newServerName.trim() || !newServerUrl.trim()}
                  className="shrink-0 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white transition hover:bg-accent-deep disabled:opacity-50"
                >
                  {addingServer ? 'Adding…' : 'Add server'}
                </button>
              </div>
            </form>

            {servers.length === 0 && (
              <p className="mt-6 text-sm text-muted">No servers yet -- add one above.</p>
            )}

            {servers.map((server) => (
              <ServerCard
                key={server.id}
                server={server}
                tools={serverTools[server.id]}
                selected={serverSelected[server.id] || new Set()}
                authorizing={!!serverAuthorizing[server.id]}
                saving={!!serverSaving[server.id]}
                message={serverMessage[server.id]}
                gmailConnected={gmailConnected}
                gmailAuthorizing={gmailAuthorizing}
                calendarConnected={calendarConnected}
                calendarAuthorizing={calendarAuthorizing}
                onAuthorize={() => handleAuthorizeServer(server.id)}
                onAuthorizeGmail={handleAuthorizeGmail}
                onAuthorizeCalendar={handleAuthorizeCalendar}
                onToggleTool={(name) => toggleServerTool(server.id, name)}
                onSave={(e) => handleSaveServer(server.id, e)}
                onRemove={botId ? undefined : () => handleRemoveServer(server.id)}
              />
            ))}
          </>
        )}
        {error && loadState === 'ready' && <p className="mt-4 text-sm text-danger">{error}</p>}
      </div>
    </main>
  )
}

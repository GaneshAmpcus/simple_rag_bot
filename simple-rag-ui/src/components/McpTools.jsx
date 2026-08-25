import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import {
  ApiError,
  authorizeGmail,
  authorizeMcp,
  getGmailStatus,
  getMcpStatus,
  getMcpTools,
  selectMcpTools,
} from '../api/client'

// Must match GMAIL_TOOL_NAMES in the backend (tools/gmail_tool.py) and
// the @mcp.tool() function names on the MCP server (server.py).
const GMAIL_TOOL_NAMES = new Set(['list_gmail_messages', 'get_gmail_message', 'send_gmail_message'])

export default function McpTools() {
  const { token } = useAuth()
  const [connected, setConnected] = useState(false)
  const [tools, setTools] = useState([])
  const [loadState, setLoadState] = useState('loading')
  const [selected, setSelected] = useState(() => new Set())
  const [saving, setSaving] = useState(false)
  const [authorizing, setAuthorizing] = useState(false)
  const [gmailConnected, setGmailConnected] = useState(false)
  const [gmailAuthorizing, setGmailAuthorizing] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    async function load() {
      setLoadState('loading')
      setError('')
      try {
        const [status, gmailStatus] = await Promise.all([
          getMcpStatus(token),
          getGmailStatus(token),
        ])
        if (cancelled) return
        setConnected(status.connected)
        setGmailConnected(gmailStatus.connected)
        if (!status.connected) {
          setLoadState('ready')
          return
        }

        const availableTools = await getMcpTools(token)
        if (!cancelled) {
          setTools(availableTools)
          setLoadState('ready')
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiError && err.status === 401) {
            setConnected(false)
            setLoadState('ready')
          } else {
            setError(err instanceof ApiError ? err.message : 'Could not load MCP settings.')
            setLoadState('error')
          }
        }
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [token])

  async function handleAuthorize() {
    setAuthorizing(true)
    setError('')
    try {
      const { auth_url: authUrl } = await authorizeMcp(token)
      window.location.href = authUrl
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start MCP authorization.')
      setAuthorizing(false)
    }
  }

  async function handleAuthorizeGmail() {
    setGmailAuthorizing(true)
    setError('')
    try {
      const { auth_url: authUrl } = await authorizeGmail(token)
      window.location.href = authUrl
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start Gmail authorization.')
      setGmailAuthorizing(false)
    }
  }

  function toggleTool(name) {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
    setMessage('')
  }

  async function handleSave(event) {
    event.preventDefault()
    setSaving(true)
    setMessage('')
    setError('')
    try {
      const result = await selectMcpTools(token, [...selected])
      setMessage(`Saved ${result.selected.length} tool${result.selected.length === 1 ? '' : 's'}.`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save tool selection.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <main className="min-h-screen bg-paper px-5 py-8 text-ink sm:px-8">
      <div className="mx-auto max-w-2xl">
        <Link to="/" className="font-mono text-xs text-muted transition hover:text-accent">
          ← Back to chat
        </Link>
        <div className="mt-8 border-b border-line pb-5">
          <p className="font-mono text-xs uppercase tracking-wider text-accent">Integrations</p>
          <h1 className="mt-2 font-display text-2xl font-semibold">MCP tools</h1>
          <p className="mt-2 max-w-lg text-sm leading-6 text-muted">
            Connect Horizon and choose which tools can assist with future chat messages.
          </p>
        </div>

        {loadState === 'loading' && (
          <p className="py-8 font-mono text-xs text-muted">checking connection…</p>
        )}
        {loadState === 'error' && <p className="py-8 text-sm text-danger">{error}</p>}

        {loadState === 'ready' && !connected && (
          <section className="mt-8 rounded-md border border-line bg-surface p-5">
            <h2 className="font-display text-base font-semibold">MCP is not connected</h2>
            <p className="mt-2 text-sm leading-6 text-muted">
              Authorize your Horizon account to make MCP tools available in chat.
            </p>
            <button
              onClick={handleAuthorize}
              disabled={authorizing}
              className="mt-5 rounded-md bg-accent px-4 py-2.5 text-sm font-medium text-white transition hover:bg-accent-deep disabled:opacity-50"
            >
              {authorizing ? 'Opening authorization…' : 'Authorize MCP'}
            </button>
          </section>
        )}

        {loadState === 'ready' && connected && (
          <form onSubmit={handleSave} className="mt-8">
            <div className="flex items-center justify-between gap-4">
              <div>
                <h2 className="font-display text-base font-semibold">Available tools</h2>
                <p className="mt-1 text-sm text-muted">Select the tools available to your chats.</p>
              </div>
              <span className="shrink-0 rounded-full bg-accent-soft px-2.5 py-1 font-mono text-[11px] text-accent-deep">
                Connected
              </span>
            </div>
            <div className="mt-5 divide-y divide-line rounded-md border border-line bg-surface">
              {tools.length === 0 && <p className="p-5 text-sm text-muted">No tools available.</p>}
              {tools.map((tool) => {
                const isGmailTool = GMAIL_TOOL_NAMES.has(tool.name)
                const locked = isGmailTool && !gmailConnected
                return (
                  <label
                    key={tool.name}
                    className={`flex gap-3 p-4 transition ${locked ? 'opacity-60' : 'cursor-pointer hover:bg-paper'}`}
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(tool.name)}
                      onChange={() => !locked && toggleTool(tool.name)}
                      disabled={locked}
                      className="mt-1 h-4 w-4 accent-accent"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-2">
                        <span className="block text-sm font-medium text-ink">{tool.name}</span>
                        {isGmailTool && (
                          <span className={`shrink-0 rounded-full px-2 py-0.5 font-mono text-[10px] ${gmailConnected ? 'bg-accent-soft text-accent-deep' : 'bg-paper text-muted'}`}>
                            {gmailConnected ? 'Gmail connected' : 'Gmail required'}
                          </span>
                        )}
                      </span>
                      <span className="mt-1 block text-sm leading-5 text-muted">{tool.description}</span>
                      {locked && (
                        <button
                          type="button"
                          onClick={handleAuthorizeGmail}
                          disabled={gmailAuthorizing}
                          className="mt-2 rounded-md border border-line px-3 py-1.5 text-xs font-medium text-accent transition hover:border-accent disabled:opacity-50"
                        >
                          {gmailAuthorizing ? 'Opening authorization…' : 'Connect Gmail to enable'}
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
              className="mt-5 rounded-md bg-accent px-4 py-2.5 text-sm font-medium text-white transition hover:bg-accent-deep disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save selection'}
            </button>
            {message && <p className="mt-3 text-sm text-accent-deep">{message}</p>}
          </form>
        )}
        {error && loadState === 'ready' && <p className="mt-4 text-sm text-danger">{error}</p>}
      </div>
    </main>
  )
}
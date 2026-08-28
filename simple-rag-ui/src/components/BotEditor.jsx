import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { ApiError, getBot, updateBot } from '../api/client'
import KBPanel from './KBPanel.jsx'
import McpTools from './McpTools.jsx'

const TABS = [
  { id: 'details', label: 'Instructions' },
  { id: 'kb', label: 'Knowledge base' },
  { id: 'integrations', label: 'Integrations' },
]

export default function BotEditor() {
  const { token } = useAuth()
  const { botId } = useParams()
  const navigate = useNavigate()

  const [bot, setBot] = useState(null)
  const [loadState, setLoadState] = useState('loading')
  const [error, setError] = useState('')
  const [tab, setTab] = useState('details')

  const [form, setForm] = useState({ name: '', description: '', systemPrompt: '' })
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoadState('loading')
      setError('')
      try {
        const data = await getBot(token, botId)
        if (cancelled) return
        setBot(data)
        setForm({
          name: data.name,
          description: data.description || '',
          systemPrompt: data.system_prompt || '',
        })
        setLoadState('ready')
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : 'Could not load this bot.')
          setLoadState('error')
        }
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [token, botId])

  async function handleSave(e) {
    e.preventDefault()
    if (!form.name.trim()) return
    setSaving(true)
    setSaved(false)
    setError('')
    try {
      const updated = await updateBot(token, botId, form)
      setBot(updated)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save changes.')
    } finally {
      setSaving(false)
    }
  }

  function handleChatNow() {
    navigate('/', { state: { openBotId: botId } })
  }

  return (
    <main className="min-h-screen bg-paper px-5 py-8 text-ink sm:px-8">
      <div className="mx-auto max-w-2xl">
        <Link to="/bots" className="font-mono text-xs text-muted transition hover:text-accent">
          ← Back to bots
        </Link>

        {loadState === 'loading' && (
          <p className="mt-8 py-8 font-mono text-xs text-muted">loading bot…</p>
        )}
        {loadState === 'error' && <p className="mt-8 py-8 text-sm text-danger">{error}</p>}

        {loadState === 'ready' && (
          <>
            <div className="mt-8 flex items-start justify-between gap-4 border-b border-line pb-5">
              <div className="min-w-0">
                <p className="font-mono text-xs uppercase tracking-wider text-accent">
                  Manage bot
                </p>
                <h1 className="mt-2 truncate font-display text-2xl font-semibold">{bot.name}</h1>
                {bot.description && (
                  <p className="mt-1 max-w-lg text-sm leading-6 text-muted">{bot.description}</p>
                )}
              </div>
              <button
                onClick={handleChatNow}
                className="shrink-0 rounded-md bg-accent px-4 py-2.5 text-sm font-medium text-white transition hover:bg-accent-deep"
              >
                Chat with this bot
              </button>
            </div>

            <div className="mt-6 flex gap-1 border-b border-line">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={`-mb-px rounded-t-md border-b-2 px-3 py-2.5 text-sm font-medium transition ${
                    tab === t.id
                      ? 'border-accent text-accent-deep'
                      : 'border-transparent text-muted hover:text-ink'
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {error && <p className="mt-4 text-sm text-danger">{error}</p>}

            <div className="mt-6">
              {tab === 'details' && (
                <form onSubmit={handleSave} className="space-y-4">
                  <div>
                    <label className="mb-1 block text-xs font-medium text-muted">Name</label>
                    <input
                      value={form.name}
                      onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                      className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-medium text-muted">
                      Description (optional)
                    </label>
                    <input
                      value={form.description}
                      onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                      placeholder="What is this bot for?"
                      className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-medium text-muted">
                      Instructions / persona (optional)
                    </label>
                    <textarea
                      value={form.systemPrompt}
                      onChange={(e) => setForm((f) => ({ ...f, systemPrompt: e.target.value }))}
                      rows={8}
                      placeholder="e.g. You are a friendly onboarding assistant for Acme Corp. Keep answers short and point to the docs KB when relevant."
                      className="w-full resize-none rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent"
                    />
                  </div>
                  <div className="flex items-center gap-3">
                    <button
                      type="submit"
                      disabled={saving || !form.name.trim()}
                      className="rounded-md bg-accent px-4 py-2.5 text-sm font-medium text-white transition hover:bg-accent-deep disabled:opacity-50"
                    >
                      {saving ? 'Saving…' : 'Save changes'}
                    </button>
                    {saved && <span className="text-sm text-accent-deep">Saved.</span>}
                  </div>
                </form>
              )}

              {tab === 'kb' && (
                <div>
                  <p className="mb-4 text-sm leading-6 text-muted">
                    Documents added here are only available to <strong>{bot.name}</strong>'s chats
                    — they never surface in your other bots' answers or the default assistant.
                  </p>
                  <KBPanel botId={botId} embedded />
                </div>
              )}

              {tab === 'integrations' && (
                <div>
                  <p className="mb-4 text-sm leading-6 text-muted">
                    Connect servers, Gmail, or Calendar for <strong>{bot.name}</strong>{' '}
                    specifically — its tokens and tool selections are independent of your other
                    bots and your default assistant.
                  </p>
                  <McpTools embedded />
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </main>
  )
}

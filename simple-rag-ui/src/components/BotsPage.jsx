import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { ApiError, createBot, deleteBot, listBots } from '../api/client'

const EMPTY_FORM = { name: '', description: '', systemPrompt: '' }

export default function BotsPage() {
  const { token } = useAuth()
  const navigate = useNavigate()
  const [bots, setBots] = useState([])
  const [loadState, setLoadState] = useState('loading')
  const [error, setError] = useState('')

  const [creating, setCreating] = useState(false)
  const [createForm, setCreateForm] = useState(EMPTY_FORM)
  const [saving, setSaving] = useState(false)

  async function load() {
    setLoadState('loading')
    setError('')
    try {
      const data = await listBots(token)
      setBots(data)
      setLoadState('ready')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load bots.')
      setLoadState('error')
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleCreate(e) {
    e.preventDefault()
    if (!createForm.name.trim()) return
    setSaving(true)
    setError('')
    try {
      const bot = await createBot(token, createForm)
      setCreateForm(EMPTY_FORM)
      setCreating(false)
      await load()
      // Straight into the full editor -- instructions/KB/integrations
      // all live there now, so there's no reason to make a first-time
      // creator hunt for a second "manage" click.
      navigate(`/bots/${bot.id}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create bot.')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(botId) {
    if (
      !confirm(
        'Delete this bot? Its chat threads will keep their history but fall back to the default assistant. Any MCP servers, Gmail, or Calendar connections authorized specifically for this bot will also be removed.',
      )
    )
      return
    setError('')
    try {
      await deleteBot(token, botId)
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete bot.')
    }
  }

  function handleChat(botId) {
    navigate('/', { state: { openBotId: botId } })
  }

  return (
    <main className="min-h-screen bg-paper px-6 py-8 text-ink">
      <div className="mx-auto max-w-3xl">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <p className="font-mono text-xs uppercase tracking-wider text-accent">Your bots</p>
            <h1 className="mt-1 font-display text-xl font-semibold">Custom assistants</h1>
          </div>
          <Link to="/" className="text-sm font-medium text-muted transition hover:text-accent">
            ← Back to chat
          </Link>
        </div>

        {error && (
          <p className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}

        <div className="mb-6 rounded-md border border-line bg-surface">
          {!creating ? (
            <button
              onClick={() => setCreating(true)}
              className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-medium text-accent transition hover:bg-paper"
            >
              <span className="text-base leading-none">+</span> Create a new bot
            </button>
          ) : (
            <form onSubmit={handleCreate} className="space-y-3 p-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-muted">Name</label>
                <input
                  autoFocus
                  value={createForm.name}
                  onChange={(e) => setCreateForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="e.g. Support triage bot"
                  className="w-full rounded-md border border-line bg-paper px-3 py-2 text-sm text-ink outline-none focus:border-accent"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-muted">Description (optional)</label>
                <input
                  value={createForm.description}
                  onChange={(e) => setCreateForm((f) => ({ ...f, description: e.target.value }))}
                  placeholder="What is this bot for?"
                  className="w-full rounded-md border border-line bg-paper px-3 py-2 text-sm text-ink outline-none focus:border-accent"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-muted">
                  Instructions / persona (optional)
                </label>
                <textarea
                  value={createForm.systemPrompt}
                  onChange={(e) => setCreateForm((f) => ({ ...f, systemPrompt: e.target.value }))}
                  rows={4}
                  placeholder="e.g. You are a friendly onboarding assistant for Acme Corp. Keep answers short and point to the docs KB when relevant."
                  className="w-full resize-none rounded-md border border-line bg-paper px-3 py-2 text-sm text-ink outline-none focus:border-accent"
                />
                <p className="mt-1 text-xs text-muted">
                  You can also add a knowledge base and connect tools once the bot is created.
                </p>
              </div>
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setCreating(false)
                    setCreateForm(EMPTY_FORM)
                  }}
                  className="rounded-md border border-line px-3 py-2 text-sm font-medium text-muted transition hover:border-accent hover:text-accent"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={saving || !createForm.name.trim()}
                  className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white transition hover:bg-accent-deep disabled:opacity-50"
                >
                  {saving ? 'Creating…' : 'Create bot'}
                </button>
              </div>
            </form>
          )}
        </div>

        {loadState === 'loading' && <p className="text-sm text-muted font-mono">loading bots…</p>}

        {loadState === 'ready' && bots.length === 0 && (
          <div className="rounded-md border border-dashed border-line bg-surface px-6 py-10 text-center">
            <div className="mx-auto mb-3 h-10 w-10 rounded-md border-2 border-accent" />
            <p className="font-display text-base font-semibold text-ink">
              Create your first bot
            </p>
            <p className="mx-auto mt-1 max-w-sm text-sm leading-6 text-muted">
              A bot is its own assistant — its own instructions, knowledge base, and tool
              connections, separate from the default assistant and from your other bots.
            </p>
          </div>
        )}

        {loadState === 'ready' && bots.length > 0 && (
          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {bots.map((bot) => (
              <li
                key={bot.id}
                className="flex flex-col rounded-md border border-line bg-surface p-4"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-ink">{bot.name}</p>
                  {bot.description ? (
                    <p className="mt-0.5 line-clamp-2 text-sm text-muted">{bot.description}</p>
                  ) : (
                    <p className="mt-0.5 text-sm italic text-muted">No description yet.</p>
                  )}
                  {bot.system_prompt && (
                    <p className="mt-2 line-clamp-2 rounded-sm bg-paper px-2 py-1 font-mono text-xs text-muted">
                      {bot.system_prompt}
                    </p>
                  )}
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    onClick={() => handleChat(bot.id)}
                    className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent-deep"
                  >
                    Chat
                  </button>
                  <Link
                    to={`/bots/${bot.id}`}
                    className="rounded-md border border-line px-3 py-1.5 text-xs font-medium text-ink transition hover:border-accent hover:text-accent"
                  >
                    Manage
                  </Link>
                  <button
                    onClick={() => handleDelete(bot.id)}
                    className="ml-auto rounded-md border border-line px-3 py-1.5 text-xs font-medium text-danger transition hover:border-danger"
                  >
                    Delete
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </main>
  )
}

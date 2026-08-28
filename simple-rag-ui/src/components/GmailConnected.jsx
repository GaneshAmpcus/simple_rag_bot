import { Link, useSearchParams } from 'react-router-dom'

export default function GmailConnected() {
  const [searchParams] = useSearchParams()
  const success = searchParams.get('status') === 'success'
  // README.md Phase 4: bot_id is included in this redirect when the
  // Gmail authorization that just completed was for a specific bot
  // (see routers/gmail.py's /callback), so "Choose tools" routes back
  // to that bot's own integrations page instead of the generic one.
  const botId = searchParams.get('bot_id')
  const toolsPath = botId ? `/bots/${botId}/mcp/tools` : '/mcp/tools'

  return (
    <main className="flex min-h-screen items-center justify-center bg-paper px-5 py-8 text-ink">
      <section className="w-full max-w-md rounded-md border border-line bg-surface p-6 text-center">
        <p className="font-mono text-xs uppercase tracking-wider text-accent">Gmail connection</p>
        <h1 className="mt-3 font-display text-xl font-semibold">
          {success ? 'Gmail connected' : 'Gmail connection failed'}
        </h1>
        <p className="mt-2 text-sm leading-6 text-muted">
          {success
            ? 'Your Gmail account is connected. Select the Gmail tools you want to use in chat.'
            : 'The Gmail authorization did not complete. You can return to the app and try again.'}
        </p>
        <div className="mt-6 flex justify-center gap-3">
          <Link to="/" className="rounded-md border border-line px-4 py-2.5 text-sm font-medium text-ink transition hover:border-accent hover:text-accent">
            Back to chat
          </Link>
          {success && (
            <Link to={toolsPath} className="rounded-md bg-accent px-4 py-2.5 text-sm font-medium text-white transition hover:bg-accent-deep">
              Choose tools
            </Link>
          )}
        </div>
      </section>
    </main>
  )
}

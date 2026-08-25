import { Link, useSearchParams } from 'react-router-dom'

export default function McpConnected() {
  const [searchParams] = useSearchParams()
  const success = searchParams.get('status') === 'success'

  return (
    <main className="flex min-h-screen items-center justify-center bg-paper px-5 py-8 text-ink">
      <section className="w-full max-w-md rounded-md border border-line bg-surface p-6 text-center">
        <p className="font-mono text-xs uppercase tracking-wider text-accent">MCP connection</p>
        <h1 className="mt-3 font-display text-xl font-semibold">
          {success ? 'MCP connected' : 'MCP connection failed'}
        </h1>
        <p className="mt-2 text-sm leading-6 text-muted">
          {success
            ? 'Your Horizon account is connected. Choose the tools you want to use in chat.'
            : 'The Horizon authorization did not complete. You can return to the app and try again.'}
        </p>
        <div className="mt-6 flex justify-center gap-3">
          <Link to="/" className="rounded-md border border-line px-4 py-2.5 text-sm font-medium text-ink transition hover:border-accent hover:text-accent">
            Back to chat
          </Link>
          {success && (
            <Link to="/mcp/tools" className="rounded-md bg-accent px-4 py-2.5 text-sm font-medium text-white transition hover:bg-accent-deep">
              Choose tools
            </Link>
          )}
        </div>
      </section>
    </main>
  )
}
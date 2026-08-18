import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { ApiError } from '../api/client'

export default function Register() {
  const { register } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    if (password !== confirm) {
      setError('Passwords do not match.')
      return
    }
    setBusy(true)
    try {
      await register(email, password)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create account. Try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-paper px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-3 h-9 w-9 rounded-md bg-accent" />
          <h1 className="font-display text-xl font-semibold text-ink">simple-rag</h1>
          <p className="mt-1 text-sm text-muted">Create an account to get started</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="rounded-lg border border-line bg-surface p-6 shadow-sm"
        >
          <label className="mb-1 block text-xs font-medium text-muted">Email</label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mb-4 w-full rounded-sm border border-line bg-paper px-3 py-2 text-sm text-ink outline-none focus:border-accent"
            placeholder="you@example.com"
          />

          <label className="mb-1 block text-xs font-medium text-muted">Password</label>
          <input
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mb-4 w-full rounded-sm border border-line bg-paper px-3 py-2 text-sm text-ink outline-none focus:border-accent"
            placeholder="At least 8 characters"
          />

          <label className="mb-1 block text-xs font-medium text-muted">Confirm password</label>
          <input
            type="password"
            required
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            className="mb-4 w-full rounded-sm border border-line bg-paper px-3 py-2 text-sm text-ink outline-none focus:border-accent"
            placeholder="Repeat password"
          />

          {error && (
            <p className="mb-4 rounded-sm bg-danger/10 px-3 py-2 text-xs text-danger">{error}</p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-sm bg-accent py-2 text-sm font-medium text-white transition hover:bg-accent-deep disabled:opacity-60"
          >
            {busy ? 'Creating account…' : 'Create account'}
          </button>
        </form>

        <p className="mt-4 text-center text-sm text-muted">
          Already have an account?{' '}
          <Link to="/login" className="font-medium text-accent hover:text-accent-deep">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  )
}

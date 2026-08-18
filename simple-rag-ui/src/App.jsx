import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from './context/AuthContext.jsx'
import Login from './components/Login.jsx'
import Register from './components/Register.jsx'
import Workspace from './components/Workspace.jsx'

function FullScreenNotice({ children }) {
  return (
    <div className="flex h-screen w-screen items-center justify-center bg-paper text-muted font-mono text-sm">
      {children}
    </div>
  )
}

function RequireAuth({ children }) {
  const { status } = useAuth()
  if (status === 'checking') return <FullScreenNotice>loading session…</FullScreenNotice>
  if (status === 'guest') return <Navigate to="/login" replace />
  return children
}

function RedirectIfAuthed({ children }) {
  const { status } = useAuth()
  if (status === 'checking') return <FullScreenNotice>loading session…</FullScreenNotice>
  if (status === 'authed') return <Navigate to="/" replace />
  return children
}

export default function App() {
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <RedirectIfAuthed>
            <Login />
          </RedirectIfAuthed>
        }
      />
      <Route
        path="/register"
        element={
          <RedirectIfAuthed>
            <Register />
          </RedirectIfAuthed>
        }
      />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Workspace />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

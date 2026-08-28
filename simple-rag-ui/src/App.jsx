import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from './context/AuthContext.jsx'
import Login from './components/Login.jsx'
import Register from './components/Register.jsx'
import Workspace from './components/Workspace.jsx'
import McpTools from './components/McpTools.jsx'
import McpConnected from './components/McpConnected.jsx'
import GmailConnected from './components/GmailConnected.jsx'
import CalendarConnected from './components/CalendarConnected.jsx'
import BotsPage from './components/BotsPage.jsx'
import BotEditor from './components/BotEditor.jsx'

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
      <Route
        path="/mcp/tools"
        element={
          <RequireAuth>
            <McpTools />
          </RequireAuth>
        }
      />
      <Route
        path="/bots/:botId/mcp/tools"
        element={
          <RequireAuth>
            <McpTools />
          </RequireAuth>
        }
      />
      <Route
        path="/mcp/connected"
        element={
          <RequireAuth>
            <McpConnected />
          </RequireAuth>
        }
      />
      <Route
        path="/gmail/connected"
        element={
          <RequireAuth>
            <GmailConnected />
          </RequireAuth>
        }
      />
      <Route
        path="/calendar/connected"
        element={
          <RequireAuth>
            <CalendarConnected />
          </RequireAuth>
        }
      />
      <Route
        path="/bots"
        element={
          <RequireAuth>
            <BotsPage />
          </RequireAuth>
        }
      />
      <Route
        path="/bots/:botId"
        element={
          <RequireAuth>
            <BotEditor />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

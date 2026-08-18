import { useCallback, useState } from 'react'
import Sidebar from './Sidebar.jsx'
import ChatWindow from './ChatWindow.jsx'
import KBPanel from './KBPanel.jsx'

export default function Workspace() {
  // null = "new chat" not yet persisted; string = active session id
  const [activeSessionId, setActiveSessionId] = useState(null)
  // bumped whenever a chat turn creates/renames a session, to refresh the sidebar list
  const [sessionsVersion, setSessionsVersion] = useState(0)

  const refreshSessions = useCallback(() => {
    setSessionsVersion((v) => v + 1)
  }, [])

  const handleNewChat = useCallback(() => {
    setActiveSessionId(null)
  }, [])

  const handleSessionCreated = useCallback(
    (sessionId) => {
      setActiveSessionId(sessionId)
      refreshSessions()
    },
    [refreshSessions],
  )

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-paper">
      <Sidebar
        activeSessionId={activeSessionId}
        onSelectSession={setActiveSessionId}
        onNewChat={handleNewChat}
        version={sessionsVersion}
      />
      <ChatWindow
        key={activeSessionId ?? 'new'}
        sessionId={activeSessionId}
        onSessionCreated={handleSessionCreated}
      />
      <KBPanel />
    </div>
  )
}

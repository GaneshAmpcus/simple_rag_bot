import { useCallback, useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import Sidebar from './Sidebar.jsx'
import ChatWindow from './ChatWindow.jsx'
import KBPanel from './KBPanel.jsx'

export default function Workspace() {
  const location = useLocation()
  const navigate = useNavigate()

  // null = "new chat" not yet persisted; string = active session id
  const [activeSessionId, setActiveSessionId] = useState(null)
  // which bot is in play -- either picked for a not-yet-created session,
  // or inherited from the session that's currently selected (Sidebar
  // passes it along in onSelectSession since it already has bot_id from
  // the session list). null = default assistant, no bot.
  const [activeBotId, setActiveBotId] = useState(null)
  // bumped whenever a chat turn creates/renames a session, to refresh the sidebar list
  const [sessionsVersion, setSessionsVersion] = useState(0)

  // "Chat with this bot" from BotsPage/BotEditor navigates here with
  // { state: { openBotId } } instead of a prop, since those live on a
  // different route -- pick it up once on arrival and start a fresh
  // chat with that bot, then clear the location state so it doesn't
  // re-trigger on a later re-render (e.g. after picking a session).
  useEffect(() => {
    const openBotId = location.state?.openBotId
    if (openBotId) {
      setActiveSessionId(null)
      setActiveBotId(openBotId)
      navigate(location.pathname, { replace: true, state: null })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state])

  const refreshSessions = useCallback(() => {
    setSessionsVersion((v) => v + 1)
  }, [])

  const handleNewChat = useCallback((botId) => {
    setActiveSessionId(null)
    setActiveBotId(botId ?? null)
  }, [])

  const handleSelectSession = useCallback((sessionId, botId) => {
    setActiveSessionId(sessionId)
    setActiveBotId(botId ?? null)
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
        onSelectSession={handleSelectSession}
        onNewChat={handleNewChat}
        version={sessionsVersion}
      />
      <ChatWindow
        key={activeSessionId ?? `new-${activeBotId ?? 'default'}`}
        sessionId={activeSessionId}
        botId={activeBotId}
        onSessionCreated={handleSessionCreated}
      />
      <KBPanel botId={activeBotId} />
    </div>
  )
}

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

async function request(path, { method = 'GET', body, token, isForm = false } = {}) {
  const headers = {}
  if (!isForm) headers['Content-Type'] = 'application/json'
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    body: isForm ? body : body ? JSON.stringify(body) : undefined,
  })

  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = await res.json()
      detail = data.detail || detail
    } catch {
      // no JSON body
    }
    throw new ApiError(detail, res.status)
  }

  if (res.status === 204) return null
  return res.json()
}

// ---- Auth ----
export function registerUser({ email, password }) {
  return request('/auth/register', { method: 'POST', body: { email, password } })
}

export async function loginUser({ email, password }) {
  // Backend uses OAuth2 password flow -> expects form-encoded username/password
  const form = new URLSearchParams()
  form.append('username', email)
  form.append('password', password)

  const res = await fetch(`${BASE_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form,
  })

  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = await res.json()
      detail = data.detail || detail
    } catch {
      // ignore
    }
    throw new ApiError(detail, res.status)
  }
  return res.json() // { access_token, token_type }
}

export function fetchMe(token) {
  return request('/auth/me', { token })
}

// ---- Sessions ----
export function listSessions(token) {
  return request('/sessions', { token })
}

export function createSession(token, { title, botId } = {}) {
  const body = {}
  if (title) body.title = title
  if (botId) body.bot_id = botId
  return request('/sessions', { method: 'POST', token, body })
}

export function getSessionMessages(token, sessionId) {
  return request(`/sessions/${sessionId}/messages`, { token })
}

// ---- Chat ----
export function sendChatMessage(token, { sessionId, message, botId }) {
  const body = { session_id: sessionId, message }
  if (!sessionId && botId) body.bot_id = botId
  return request('/chat', {
    method: 'POST',
    token,
    body,
  })
}

// ---- Knowledge base ----
export function ingestDocument(token, file, botId) {
  const form = new FormData()
  form.append('files', file)
  if (botId) form.append('bot_id', botId)
  return request('/ingest', { method: 'POST', token, body: form, isForm: true })
}

// ---- Bots ----
export function listBots(token) {
  return request('/bots', { token })
}

export function createBot(token, { name, description, systemPrompt }) {
  return request('/bots', {
    method: 'POST',
    token,
    body: { name, description: description || null, system_prompt: systemPrompt || null },
  })
}

export function getBot(token, botId) {
  return request(`/bots/${botId}`, { token })
}

export function updateBot(token, botId, { name, description, systemPrompt } = {}) {
  const body = {}
  if (name !== undefined) body.name = name
  if (description !== undefined) body.description = description
  if (systemPrompt !== undefined) body.system_prompt = systemPrompt
  return request(`/bots/${botId}`, { method: 'PATCH', token, body })
}

export function deleteBot(token, botId) {
  return request(`/bots/${botId}`, { method: 'DELETE', token })
}

// ---- MCP (generic multi-server) ----
// The server catalog (add/remove/list) is always user-scoped -- a
// server a user adds is shared across all their bots, see
// mcp_tools.py's module docstring. Only the CONNECTION (authorize/
// status/tools/select) is bot-scoped, per README.md Phase 3: pass an
// optional botId to route to that bot's own connection instead of the
// user-level ("no bot") one. Omitting botId (or passing null/undefined)
// keeps hitting the original /mcp/... routes, unchanged.
export function listMcpServers(token, botId) {
  return request(botId ? `/bots/${botId}/mcp/servers` : '/mcp/servers', { token })
}

export function addMcpServer(token, { name, url }) {
  // Adding a server is never bot-scoped -- see module docstring above.
  return request('/mcp/servers', { method: 'POST', token, body: { name, url } })
}

export function removeMcpServer(token, serverId) {
  return request(`/mcp/servers/${serverId}`, { method: 'DELETE', token })
}

export function authorizeMcpServer(token, serverId, botId) {
  const path = botId
    ? `/bots/${botId}/mcp/servers/${serverId}/authorize`
    : `/mcp/servers/${serverId}/authorize`
  return request(path, { method: 'POST', token })
}

export function getMcpServerStatus(token, serverId, botId) {
  const path = botId
    ? `/bots/${botId}/mcp/servers/${serverId}/status`
    : `/mcp/servers/${serverId}/status`
  return request(path, { token })
}

export function getMcpServerTools(token, serverId, botId) {
  const path = botId
    ? `/bots/${botId}/mcp/servers/${serverId}/tools`
    : `/mcp/servers/${serverId}/tools`
  return request(path, { token })
}

export function selectMcpServerTools(token, serverId, toolNames, botId) {
  const path = botId
    ? `/bots/${botId}/mcp/servers/${serverId}/tools/select`
    : `/mcp/servers/${serverId}/tools/select`
  return request(path, {
    method: 'POST',
    token,
    body: { tool_names: toolNames },
  })
}

// ---- Gmail ----
// README.md Phase 4: same botId-optional pattern as MCP above -- omit
// botId for the user-level ("no bot") connection, pass it for a
// specific bot's own independent Gmail connection.
export function authorizeGmail(token, botId) {
  const path = botId ? `/bots/${botId}/gmail/authorize` : '/gmail/authorize'
  return request(path, { method: 'POST', token })
}

export function getGmailStatus(token, botId) {
  const path = botId ? `/bots/${botId}/gmail/status` : '/gmail/status'
  return request(path, { token })
}

// ---- Calendar / Meet ----
export function authorizeCalendar(token, botId) {
  const path = botId ? `/bots/${botId}/calendar/authorize` : '/calendar/authorize'
  return request(path, { method: 'POST', token })
}

export function getCalendarStatus(token, botId) {
  const path = botId ? `/bots/${botId}/calendar/status` : '/calendar/status'
  return request(path, { token })
}

export { ApiError }

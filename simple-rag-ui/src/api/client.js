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

export function createSession(token, title) {
  return request('/sessions', { method: 'POST', token, body: title ? { title } : {} })
}

export function getSessionMessages(token, sessionId) {
  return request(`/sessions/${sessionId}/messages`, { token })
}

// ---- Chat ----
export function sendChatMessage(token, { sessionId, message }) {
  return request('/chat', {
    method: 'POST',
    token,
    body: { session_id: sessionId, message },
  })
}

// ---- Knowledge base ----
export function ingestDocument(token, file) {
  const form = new FormData()
  form.append('files', file)
  return request('/ingest', { method: 'POST', token, body: form, isForm: true })
}

export { ApiError }

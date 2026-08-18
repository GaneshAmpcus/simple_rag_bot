import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { loginUser, registerUser, fetchMe } from '../api/client'

const AuthContext = createContext(null)

const TOKEN_KEY = 'simple_rag_token'

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY))
  const [user, setUser] = useState(null)
  // 'checking' | 'authed' | 'guest'
  const [status, setStatus] = useState('checking')

  useEffect(() => {
    let cancelled = false

    async function validate() {
      if (!token) {
        setStatus('guest')
        return
      }
      try {
        const me = await fetchMe(token)
        if (!cancelled) {
          setUser(me)
          setStatus('authed')
        }
      } catch {
        if (!cancelled) {
          localStorage.removeItem(TOKEN_KEY)
          setToken(null)
          setStatus('guest')
        }
      }
    }
    validate()
    return () => {
      cancelled = true
    }
  }, [token])

  const login = useCallback(async (email, password) => {
    const { access_token } = await loginUser({ email, password })
    localStorage.setItem(TOKEN_KEY, access_token)
    setToken(access_token)
  }, [])

  const register = useCallback(async (email, password) => {
    await registerUser({ email, password })
    // auto-login after successful registration
    const { access_token } = await loginUser({ email, password })
    localStorage.setItem(TOKEN_KEY, access_token)
    setToken(access_token)
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY)
    setToken(null)
    setUser(null)
    setStatus('guest')
  }, [])

  return (
    <AuthContext.Provider value={{ token, user, status, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

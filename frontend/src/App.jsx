import { useState, useEffect } from 'react'
import LoginPage from './pages/LoginPage'
import LoadingPage from './pages/LoadingPage'
import OnboardingPage from './pages/OnboardingPage'
import HomePage from './pages/HomePage'

const API = import.meta.env.VITE_API_URL || '/api'

// Attaches stored uid as a header on every request (needed for iOS Safari
// which blocks third-party cookies set via Vercel → Railway redirects)
function apiFetch(path, options = {}) {
  const uid = localStorage.getItem('nudge_uid')
  return fetch(`${API}${path}`, {
    credentials: 'include',
    ...options,
    headers: {
      ...(uid ? { 'X-Nudge-UID': uid } : {}),
      ...(options.headers || {}),
    },
  })
}

export default function App() {
  // 'init' while we check the session — prevents login page flashing
  const [screen, setScreen] = useState('init')
  const [rec, setRec] = useState(null)
  const [skipsRemaining, setSkipsRemaining] = useState(3)

  useEffect(() => {
    // Grab uid from URL if redirected back from Spotify OAuth
    const params = new URLSearchParams(window.location.search)
    const uid = params.get('uid')
    if (uid) {
      localStorage.setItem('nudge_uid', uid)
      window.history.replaceState({}, '', window.location.pathname)
    }
    checkSession()
  }, [])

  async function handleInviteParam() {
    const params = new URLSearchParams(window.location.search)
    const code = params.get('invite') || localStorage.getItem('pending_invite')
    if (!code) return
    window.history.replaceState({}, '', window.location.pathname)
    localStorage.removeItem('pending_invite')
    try {
      await apiFetch(`/accept-invite?code=${encodeURIComponent(code)}`, { method: 'POST' })
    } catch {}
  }

  async function checkSession() {
    try {
      const res = await apiFetch('/pool-status')
      if (!res.ok) { setScreen('login'); return }
      const data = await res.json()
      if (data.ready) {
        await loadToday()
      } else {
        setScreen('loading')
        pollPool()
      }
    } catch {
      setScreen('login')
    }
  }

  function pollPool() {
    const interval = setInterval(async () => {
      try {
        const res = await apiFetch('/pool-status')
        const data = await res.json()
        if (data.ready) {
          clearInterval(interval)
          await loadToday()
        }
      } catch {
        clearInterval(interval)
        setScreen('login')
      }
    }, 3000)
  }

  async function loadToday() {
    await handleInviteParam()

    const res = await apiFetch('/today')
    const data = await res.json()
    setRec(data.todays_nudge)
    setSkipsRemaining(data.skips_remaining)

    const prefRes = await apiFetch('/preferences')
    const prefs = await prefRes.json()
    const hasPrefs = prefs.liked_genres?.length > 0

    setScreen(hasPrefs ? 'home' : 'onboarding')

    apiFetch('/check-listened', { method: 'POST' }).catch(() => {})
  }

  async function onboardingComplete() {
    // Rebuild the pool now that genre preferences are saved, then get a fresh rec
    await apiFetch('/rebuild-pool', { method: 'POST' })
    setScreen('loading')
    const interval = setInterval(async () => {
      try {
        const res = await apiFetch('/pool-status')
        const data = await res.json()
        if (data.ready) {
          clearInterval(interval)
          const res2 = await apiFetch('/today?force=true')
          const data2 = await res2.json()
          setRec(data2.todays_nudge)
          setSkipsRemaining(data2.skips_remaining)
          setScreen('home')
        }
      } catch {
        clearInterval(interval)
        setScreen('login')
      }
    }, 3000)
  }

  async function handleHeardIt() {
    const res = await apiFetch('/heard-it', { method: 'POST' })
    const data = await res.json()
    if (res.ok) {
      setRec(data.todays_nudge)
      setSkipsRemaining(data.skips_remaining)
    } else {
      alert(data.detail)
    }
  }

  if (screen === 'init') return null  // blank while session check runs
  if (screen === 'login') return <LoginPage />
  if (screen === 'loading') return <LoadingPage />
  if (screen === 'onboarding') return <OnboardingPage onComplete={() => onboardingComplete()} />
  if (screen === 'home') return <HomePage rec={rec} skipsRemaining={skipsRemaining} onHeardIt={handleHeardIt} />
  return null
}

import { useState, useEffect } from 'react'
import LoginPage from './pages/LoginPage'
import LoadingPage from './pages/LoadingPage'
import OnboardingPage from './pages/OnboardingPage'
import HomePage from './pages/HomePage'

const API = '/api'

export default function App() {
  // 'init' while we check the session — prevents login page flashing
  const [screen, setScreen] = useState('init')
  const [rec, setRec] = useState(null)
  const [skipsRemaining, setSkipsRemaining] = useState(3)

  useEffect(() => {
    checkSession()
  }, [])

  async function handleInviteParam() {
    const params = new URLSearchParams(window.location.search)
    const code = params.get('invite')
    if (!code) return
    // Clear it from the URL without a reload
    window.history.replaceState({}, '', window.location.pathname)
    try {
      await fetch(`${API}/accept-invite?code=${encodeURIComponent(code)}`, { method: 'POST' })
    } catch {}
  }

  async function checkSession() {
    try {
      const res = await fetch(`${API}/pool-status`)
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
        const res = await fetch(`${API}/pool-status`)
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
    // Handle ?invite= param if present before loading
    await handleInviteParam()

    const res = await fetch(`${API}/today`)
    const data = await res.json()
    setRec(data.todays_nudge)
    setSkipsRemaining(data.skips_remaining)

    const prefRes = await fetch(`${API}/preferences`)
    const prefs = await prefRes.json()
    const hasPrefs = prefs.liked_genres?.length > 0

    setScreen(hasPrefs ? 'home' : 'onboarding')

    // Silently check if they actually listened to their last rec via recently_played
    fetch(`${API}/check-listened`, { method: 'POST' }).catch(() => {})
  }

  async function handleHeardIt() {
    const res = await fetch(`${API}/heard-it`, { method: 'POST' })
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
  if (screen === 'onboarding') return <OnboardingPage onComplete={() => loadToday()} />
  if (screen === 'home') return <HomePage rec={rec} skipsRemaining={skipsRemaining} onHeardIt={handleHeardIt} />
  return null
}

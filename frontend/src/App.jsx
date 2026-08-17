import { useState, useEffect } from 'react'
import { apiFetch } from './api'
import LoginPage from './pages/LoginPage'
import LoadingPage from './pages/LoadingPage'
import OnboardingPage from './pages/OnboardingPage'
import HomePage from './pages/HomePage'

const POOL_POLL_MS = 3000

export default function App() {
  // 'init' while we check the session — prevents login page flashing
  const [screen, setScreen] = useState('init')
  const [rec, setRec] = useState(null)
  const [skipsRemaining, setSkipsRemaining] = useState(3)

  async function checkSession() {
    try {
      const res = await apiFetch('/pool-status')
      if (!res.ok) { setScreen('login'); return }
      const data = await res.json()
      if (data.ready) {
        await loadToday()
      } else {
        setScreen('loading')
        pollPoolUntilReady(loadToday)
      }
    } catch {
      setScreen('login')
    }
  }

  // The pool is built in a background thread on the server, so we wait it out
  function pollPoolUntilReady(onReady) {
    const interval = setInterval(async () => {
      try {
        const res = await apiFetch('/pool-status')
        const data = await res.json()
        if (data.ready) {
          clearInterval(interval)
          await onReady()
        }
      } catch {
        clearInterval(interval)
        setScreen('login')
      }
    }, POOL_POLL_MS)
  }

  async function loadToday() {
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
    pollPoolUntilReady(async () => {
      const res = await apiFetch('/today?force=true')
      const data = await res.json()
      setRec(data.todays_nudge)
      setSkipsRemaining(data.skips_remaining)
      setScreen('home')
    })
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

  switch (screen) {
    case 'login':
      return <LoginPage />
    case 'loading':
      return <LoadingPage />
    case 'onboarding':
      return <OnboardingPage onComplete={onboardingComplete} />
    case 'home':
      return <HomePage rec={rec} skipsRemaining={skipsRemaining} onHeardIt={handleHeardIt} />
    default:
      return null  // 'init' — blank while the session check runs
  }
}

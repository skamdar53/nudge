import { useState } from 'react'
import { apiFetch } from '../api'

const GENRES = [
  'hip-hop', 'rap', 'r&b', 'soul', 'jazz', 'blues',
  'indie', 'alternative', 'rock', 'metal', 'punk',
  'pop', 'electronic', 'house', 'techno', 'ambient',
  'folk', 'country', 'classical', 'reggae', 'latin',
  'afrobeats', 'lo-fi', 'experimental',
]

const ACCENT = '#5b23ba'

function GenreGrid({ selected, onToggle }) {
  return (
    <div style={{
      display: 'flex',
      flexWrap: 'wrap',
      gap: '10px',
      justifyContent: 'center',
      maxWidth: '480px',
    }}>
      {GENRES.map(genre => {
        const active = selected.includes(genre)
        return (
          <button
            key={genre}
            onClick={() => onToggle(genre)}
            style={{
              padding: '8px 18px',
              borderRadius: '100px',
              border: `1.5px solid ${active ? ACCENT : 'rgba(255,255,255,0.15)'}`,
              background: active ? 'rgba(91, 35, 186, 0.25)' : 'rgba(255,255,255,0.05)',
              color: active ? '#fff' : 'rgba(255,255,255,0.5)',
              fontSize: '14px',
              fontWeight: active ? '600' : '400',
              cursor: 'pointer',
              transition: 'all 0.15s ease',
              boxShadow: active ? '0 0 12px rgba(91, 35, 186, 0.4)' : 'none',
            }}
          >
            {genre}
          </button>
        )
      })}
    </div>
  )
}

export default function OnboardingPage({ onComplete }) {
  const [step, setStep] = useState(1)
  const [liked, setLiked] = useState([])
  const [explore, setExplore] = useState([])
  const [saving, setSaving] = useState(false)

  function toggleGenre(setList, genre) {
    setList(prev => prev.includes(genre) ? prev.filter(g => g !== genre) : [...prev, genre])
  }

  async function handleDone() {
    setSaving(true)
    await apiFetch('/preferences', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ liked_genres: liked, explore_genres: explore })
    })
    onComplete()
  }

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      height: '100vh',
      background: '#000',
      padding: '24px',
      gap: '32px',
      animation: 'fade-in 0.4s ease',
    }}>
      {/* Step indicator */}
      <div style={{ display: 'flex', gap: '8px' }}>
        {[1, 2].map(s => (
          <div key={s} style={{
            width: '24px', height: '3px', borderRadius: '2px',
            background: s <= step ? ACCENT : 'rgba(255,255,255,0.2)',
            transition: 'background 0.3s',
          }} />
        ))}
      </div>

      {step === 1 ? (
        <>
          <div style={{ textAlign: 'center' }}>
            <h2 style={{ fontSize: '24px', fontWeight: '700', marginBottom: '8px' }}>
              What do you already love?
            </h2>
            <p style={{ color: 'rgba(255,255,255,0.4)', fontSize: '14px' }}>
              Pick any genres you're into
            </p>
          </div>
          <GenreGrid selected={liked} onToggle={g => toggleGenre(setLiked, g)} />
          <button
            onClick={() => setStep(2)}
            disabled={liked.length === 0}
            style={{
              background: liked.length > 0 ? ACCENT : 'rgba(255,255,255,0.1)',
              color: liked.length > 0 ? '#fff' : 'rgba(255,255,255,0.3)',
              border: 'none',
              borderRadius: '100px',
              padding: '14px 36px',
              fontSize: '15px',
              fontWeight: '600',
              cursor: liked.length > 0 ? 'pointer' : 'default',
              transition: 'all 0.2s',
              boxShadow: liked.length > 0 ? '0 0 30px rgba(91,35,186,0.4)' : 'none',
            }}
          >
            Next
          </button>
        </>
      ) : (
        <>
          <div style={{ textAlign: 'center' }}>
            <h2 style={{ fontSize: '24px', fontWeight: '700', marginBottom: '8px' }}>
              What do you want to explore?
            </h2>
            <p style={{ color: 'rgba(255,255,255,0.4)', fontSize: '14px' }}>
              We'll nudge you toward these occasionally
            </p>
          </div>
          <GenreGrid selected={explore} onToggle={g => toggleGenre(setExplore, g)} />
          <div style={{ display: 'flex', gap: '12px' }}>
            <button
              onClick={() => setStep(1)}
              style={{
                background: 'transparent',
                color: 'rgba(255,255,255,0.4)',
                border: '1.5px solid rgba(255,255,255,0.15)',
                borderRadius: '100px',
                padding: '14px 28px',
                fontSize: '15px',
                cursor: 'pointer',
              }}
            >
              Back
            </button>
            <button
              onClick={handleDone}
              disabled={saving}
              style={{
                background: ACCENT,
                color: '#fff',
                border: 'none',
                borderRadius: '100px',
                padding: '14px 36px',
                fontSize: '15px',
                fontWeight: '600',
                cursor: 'pointer',
                boxShadow: '0 0 30px rgba(91,35,186,0.4)',
              }}
            >
              {saving ? 'Setting up...' : "Let's go"}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

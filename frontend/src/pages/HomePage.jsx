import { useState, useEffect, useRef } from 'react'
import { apiFetch } from '../api'

// ─── Vinyl components ────────────────────────────────────────────────────────

const VINYL_SIZE = '280px'
const GROOVE_RADII = [52, 84, 112, 132]

const centered = {
  position: 'absolute', top: '50%', left: '50%',
  transform: 'translate(-50%, -50%)',
}

function VinylGrooves({ opacity }) {
  return GROOVE_RADII.map(r => (
    <div key={r} style={{
      ...centered,
      width: `${r * 2}px`, height: `${r * 2}px`,
      borderRadius: '50%', border: `1px solid rgba(255,255,255,${opacity})`,
    }} />
  ))
}

function SpindleHole() {
  return (
    <div style={{
      ...centered,
      width: '14px', height: '14px', borderRadius: '50%',
      background: '#000', zIndex: 2,
    }} />
  )
}

function VinylDisc({ glow, children }) {
  return (
    <div style={{
      width: VINYL_SIZE, height: VINYL_SIZE, borderRadius: '50%',
      background: 'radial-gradient(circle at 40% 38%, #140826, #000)',
      position: 'relative', overflow: 'hidden',
      boxShadow: `0 8px 50px rgba(91,35,186,${glow}), 0 24px 60px rgba(0,0,0,0.9)`,
    }}>
      {children}
      <SpindleHole />
    </div>
  )
}

function MysteryVinyl() {
  return (
    <VinylDisc glow={0.35}>
      <VinylGrooves opacity={0.045} />
      <div style={{
        position: 'absolute', inset: 0, borderRadius: '50%',
        background: 'conic-gradient(from 0deg, transparent 15%, rgba(139,92,246,0.14) 30%, rgba(96,165,250,0.09) 48%, rgba(167,243,208,0.07) 66%, rgba(251,191,36,0.05) 80%, transparent 92%)',
        animation: 'sheen-rotate 7s linear infinite',
      }} />
      <div style={{
        ...centered,
        width: '108px', height: '108px', borderRadius: '50%',
        overflow: 'hidden',
        background: 'radial-gradient(circle, #3b1a6a 0%, #1a0830 65%, #0a0515 100%)',
        zIndex: 1,
      }}>
        <div style={{
          width: '100%', height: '100%',
          background: 'conic-gradient(from 0deg, transparent, rgba(124,58,237,0.55), transparent, rgba(139,92,246,0.3), transparent)',
          animation: 'sheen-rotate 4s linear infinite',
        }} />
      </div>
    </VinylDisc>
  )
}

function AlbumVinyl({ image }) {
  return (
    <VinylDisc glow={0.3}>
      <VinylGrooves opacity={0.05} />
      <div style={{
        ...centered,
        width: '108px', height: '108px', borderRadius: '50%',
        overflow: 'hidden', background: '#1a0a3a', zIndex: 1,
      }}>
        {image && <img src={image} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />}
      </div>
    </VinylDisc>
  )
}

// ─── Feel Search ─────────────────────────────────────────────────────────────

function TrackRow({ track }) {
  return (
    <a
      href={track.spotify_url}
      target="_blank"
      rel="noreferrer"
      style={{
        display: 'flex', alignItems: 'center', gap: '12px',
        padding: '10px 14px', borderRadius: '14px',
        background: 'rgba(255,255,255,0.04)',
        border: '1px solid rgba(255,255,255,0.07)',
        textDecoration: 'none', color: '#fff',
        transition: 'background 0.15s',
      }}
      onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.08)'}
      onMouseLeave={e => e.currentTarget.style.background = 'rgba(255,255,255,0.04)'}
    >
      {track.image && (
        <img src={track.image} alt="" style={{ width: '42px', height: '42px', borderRadius: '8px', flexShrink: 0 }} />
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <p style={{ fontSize: '14px', fontWeight: '500', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {track.name}
        </p>
        <p style={{ fontSize: '12px', color: 'rgba(255,255,255,0.4)', marginTop: '2px' }}>{track.artist}</p>
      </div>
      <span style={{ color: 'rgba(255,255,255,0.2)', fontSize: '16px', flexShrink: 0 }}>↗</span>
    </a>
  )
}

function SectionLabel({ text, sub }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px', marginTop: '24px', marginBottom: '10px' }}>
      <p style={{ color: 'rgba(255,255,255,0.9)', fontSize: '13px', fontWeight: '600', letterSpacing: '0.3px' }}>{text}</p>
      {sub && <p style={{ color: 'rgba(255,255,255,0.3)', fontSize: '11px' }}>{sub}</p>}
    </div>
  )
}

function FeelTab() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [showDrop, setShowDrop] = useState(false)
  const debounceRef = useRef(null)

  // Fetch autocomplete suggestions as user types
  function handleInputChange(e) {
    const val = e.target.value
    setQuery(val)
    clearTimeout(debounceRef.current)
    if (val.trim().length < 2) { setSuggestions([]); setShowDrop(false); return }
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await apiFetch(`/search-tracks?q=${encodeURIComponent(val.trim())}`)
        const data = await res.json()
        setSuggestions(data.tracks || [])
        setShowDrop((data.tracks || []).length > 0)
      } catch { setSuggestions([]); setShowDrop(false) }
    }, 350)
  }

  function selectSuggestion(track) {
    const filled = `${track.name} · ${track.artist}`
    setQuery(filled)
    setSuggestions([])
    setShowDrop(false)
    runSearch(track.name, track.artist)
  }

  async function runSearch(song, artist) {
    setLoading(true)
    setError('')
    setResults(null)
    try {
      const res = await apiFetch(`/feel?song=${encodeURIComponent(song)}&artist=${encodeURIComponent(artist)}`)
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Not found')
      setResults(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleSearch(e) {
    e.preventDefault()
    const trimmed = query.trim()
    if (!trimmed) return
    setSuggestions([])
    setShowDrop(false)
    const parts = trimmed.split(/\s*[·\-–—]\s*/)
    await runSearch(parts[0], parts[1] || '')
  }

  const mainstream = results?.similar.filter(t => t.popularity >= 45) ?? []
  const niche      = results?.similar.filter(t => t.popularity < 45)  ?? []

  return (
    <div style={{
      flex: 1, minHeight: 0, overflowY: 'auto',
      padding: '52px 24px 24px',
      display: 'flex', flexDirection: 'column',
    }}>
      {/* Header */}
      <div style={{ marginBottom: '24px' }}>
        <h2 style={{ fontSize: '22px', fontWeight: '700', letterSpacing: '-0.5px' }}>Find the feel</h2>
        <p style={{ color: 'rgba(255,255,255,0.35)', fontSize: '13px', marginTop: '4px' }}>
          Type a song · get songs with the same vibe
        </p>
      </div>

      {/* Search */}
      <form onSubmit={handleSearch} style={{ display: 'flex', gap: '10px', marginBottom: '8px', position: 'relative' }}>
        <div style={{ flex: 1, position: 'relative' }}>
          <input
            value={query}
            onChange={handleInputChange}
            onBlur={() => setTimeout(() => setShowDrop(false), 150)}
            onFocus={() => suggestions.length > 0 && setShowDrop(true)}
            placeholder="e.g.  Ivy · Frank Ocean"
            style={{
              width: '100%', boxSizing: 'border-box',
              background: 'rgba(255,255,255,0.07)',
              border: `1px solid ${showDrop ? 'rgba(91,35,186,0.5)' : 'rgba(255,255,255,0.12)'}`,
              borderRadius: showDrop ? '14px 14px 0 0' : '14px',
              padding: '13px 16px',
              color: '#fff', fontSize: '15px', outline: 'none',
              transition: 'border 0.15s',
            }}
          />
          {/* Dropdown */}
          {showDrop && (
            <div style={{
              position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10,
              background: '#0e0e0e',
              border: '1px solid rgba(91,35,186,0.4)',
              borderTop: 'none',
              borderRadius: '0 0 14px 14px',
              overflow: 'hidden',
            }}>
              {suggestions.map((t, i) => (
                <button
                  key={i}
                  type="button"
                  onMouseDown={() => selectSuggestion(t)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: '10px',
                    width: '100%', padding: '10px 14px',
                    background: 'none', border: 'none',
                    borderBottom: i < suggestions.length - 1 ? '1px solid rgba(255,255,255,0.05)' : 'none',
                    color: '#fff', cursor: 'pointer', textAlign: 'left',
                    transition: 'background 0.1s',
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = 'rgba(91,35,186,0.15)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'none'}
                >
                  {t.image && <img src={t.image} alt="" style={{ width: '36px', height: '36px', borderRadius: '6px', flexShrink: 0 }} />}
                  <div style={{ minWidth: 0 }}>
                    <p style={{ fontSize: '13px', fontWeight: '500', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{t.name}</p>
                    <p style={{ fontSize: '11px', color: 'rgba(255,255,255,0.4)', marginTop: '1px' }}>{t.artist}</p>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
        <button
          type="submit"
          disabled={loading || !query.trim()}
          style={{
            background: query.trim() ? '#5b23ba' : 'rgba(255,255,255,0.07)',
            border: 'none', borderRadius: '14px',
            padding: '13px 22px',
            color: query.trim() ? '#fff' : 'rgba(255,255,255,0.25)',
            fontSize: '15px', fontWeight: '600',
            cursor: query.trim() ? 'pointer' : 'default',
            transition: 'all 0.2s', whiteSpace: 'nowrap',
            alignSelf: 'flex-start',
          }}
        >
          {loading ? '···' : 'Go'}
        </button>
      </form>

      <p style={{ color: 'rgba(255,255,255,0.2)', fontSize: '12px', marginBottom: '20px' }}>
        Start typing — or use · to separate song and artist
      </p>

      {error && (
        <p style={{ color: 'rgba(255,100,100,0.75)', fontSize: '13px', marginBottom: '16px' }}>{error}</p>
      )}

      {results && (
        <>
          {/* Seed */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: '12px',
            padding: '12px 14px', marginBottom: '4px',
            background: 'rgba(91,35,186,0.15)', borderRadius: '14px',
            border: '1px solid rgba(91,35,186,0.35)',
          }}>
            {results.seed.image && (
              <img src={results.seed.image} alt="" style={{ width: '42px', height: '42px', borderRadius: '8px', flexShrink: 0 }} />
            )}
            <div>
              <p style={{ fontSize: '15px', fontWeight: '600' }}>{results.seed.name}</p>
              <p style={{ fontSize: '12px', color: 'rgba(255,255,255,0.45)', marginTop: '2px' }}>{results.seed.artist}</p>
            </div>
          </div>

          {/* Mainstream section */}
          {mainstream.length > 0 && (
            <>
              <SectionLabel text="Same feel" sub="songs in the same lane" />
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {mainstream.map((t, i) => <TrackRow key={i} track={t} />)}
              </div>
            </>
          )}

          {/* Niche section */}
          {niche.length > 0 && (
            <>
              <SectionLabel text="Under the radar" sub="lesser known, same energy" />
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {niche.map((t, i) => <TrackRow key={i} track={t} />)}
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}

// ─── Today's Nudge tab ───────────────────────────────────────────────────────

function NudgeTab({ rec, skipsRemaining, onHeardIt }) {
  const [phase, setPhase] = useState('mystery')
  const [rating, setRating] = useState(null)  // 'liked' | 'disliked' | null
  const [loadingNext, setLoadingNext] = useState(false)
  const skipToReveal = useRef(false)
  const todayKey = `nudge_revealed_${new Date().toDateString()}`

  useEffect(() => {
    if (skipToReveal.current) {
      skipToReveal.current = false
      setPhase('revealed')  // skip mystery animation after heard-it
    } else if (localStorage.getItem(todayKey)) {
      setPhase('revealed')  // already revealed today — skip straight to it
    } else {
      setPhase('mystery')
    }
    setRating(null)  // reset rating when rec changes
  }, [rec?.spotify_url])

  async function handleHeardIt() {
    setLoadingNext(true)
    skipToReveal.current = true
    await onHeardIt()
    setLoadingNext(false)
  }

  // Fire-and-forget — a dropped signal just means slightly weaker future picks
  function sendSignal(signalType) {
    apiFetch('/signal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        artist_name: rec.artist,
        album_name: rec.album_name,
        spotify_url: rec.spotify_url,
        signal_type: signalType,
      }),
    }).catch(() => {})
  }

  function handleRate(type) {
    if (rating === type) return  // already rated this
    setRating(type)
    sendSignal(type)
  }

  function handleTap() {
    if (phase !== 'mystery') return
    localStorage.setItem(todayKey, '1')
    setPhase('popping')
    setTimeout(() => setPhase('spinning'), 380)
    setTimeout(() => setPhase('fading'),   380 + 4200)
    setTimeout(() => setPhase('revealed'), 380 + 4200 + 900)
  }

  if (!rec) return null

  const isRevealed = phase === 'revealed'
  const isSpinning = phase === 'spinning' || phase === 'fading'

  return (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column',
      alignItems: 'center', width: '100%',
      padding: '16px 24px 40px',
      overflowY: 'auto',
    }}>
      {/* Main content — flex:1 so it fills space and centers the vinyl */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '32px', flex: 1, justifyContent: 'center', paddingBottom: '16px' }}>

        {/* Mystery vinyl */}
        {(phase === 'mystery' || phase === 'popping') && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '24px' }}>
            <div
              onClick={handleTap}
              style={{
                position: 'relative',
                cursor: phase === 'mystery' ? 'pointer' : 'default',
                animation: phase === 'popping' ? 'vinyl-pop 0.38s cubic-bezier(0.34, 1.56, 0.64, 1) forwards' : 'none',
              }}
            >
              {phase === 'mystery' && (
                <div style={{
                  position: 'absolute', inset: '-4px', borderRadius: '50%',
                  border: '2px solid rgba(91,35,186,0.5)',
                  animation: 'pulse-ring 2s ease-out infinite',
                  pointerEvents: 'none',
                }} />
              )}
              <MysteryVinyl />
            </div>
            {phase === 'mystery' && (
              <p style={{
                color: 'rgba(255,255,255,0.35)', fontSize: '13px',
                fontWeight: '500', letterSpacing: '1px', textTransform: 'uppercase',
                animation: 'fade-in 1s ease 1s both',
              }}>
                tap to reveal
              </p>
            )}
          </div>
        )}

        {/* Spinning */}
        {isSpinning && (
          <div style={{ position: 'relative', width: VINYL_SIZE, height: VINYL_SIZE }}>
            {/* Album art fades in underneath */}
            {phase === 'fading' && (
              <img
                src={rec.image} alt={rec.album_name}
                style={{
                  position: 'absolute', inset: 0,
                  width: VINYL_SIZE, height: VINYL_SIZE,
                  borderRadius: '16px', objectFit: 'cover',
                  boxShadow: '0 0 70px rgba(91,35,186,0.55), 0 28px 70px rgba(0,0,0,0.9)',
                  animation: 'art-fade-in 0.9s ease forwards',
                }}
              />
            )}
            {/* Fade wrapper — separate from spin so spin animation never restarts */}
            <div style={{
              position: 'absolute', inset: 0,
              animation: phase === 'fading' ? 'vinyl-fade-out 0.9s ease forwards' : 'none',
            }}>
              {/* Spin wrapper — animation string never changes, no glitch */}
              <div style={{ animation: 'spin-vinyl 3s linear infinite' }}>
                <AlbumVinyl image={rec.image} />
              </div>
            </div>
          </div>
        )}

        {/* Revealed */}
        {isRevealed && (
          <>
            <div style={{ animation: 'art-fade-in 0.4s ease' }}>
              <img
                src={rec.image} alt={rec.album_name}
                style={{
                  width: VINYL_SIZE, height: VINYL_SIZE, borderRadius: '16px',
                  objectFit: 'cover', display: 'block',
                  boxShadow: '0 0 80px rgba(91,35,186,0.6), 0 30px 80px rgba(0,0,0,0.9)',
                }}
              />
            </div>
            <div style={{ textAlign: 'center', animation: 'fade-in-up 0.5s ease 0.1s both' }}>
              <h2 style={{ fontSize: '22px', fontWeight: '700', letterSpacing: '-0.5px', marginBottom: '6px', maxWidth: '300px' }}>
                {rec.album_name}
              </h2>
              <p style={{ color: 'rgba(255,255,255,0.5)', fontSize: '15px', marginBottom: '20px' }}>
                {rec.artist}
              </p>
              <a
                href={rec.spotify_url}
                target="_blank"
                rel="noreferrer"
                onClick={() => sendSignal('clicked_spotify')}
                style={{
                  display: 'inline-block', color: '#fff',
                  fontSize: '13px', fontWeight: '600', letterSpacing: '0.5px',
                  padding: '10px 24px', borderRadius: '100px',
                  border: '1.5px solid rgba(255,255,255,0.2)',
                  textDecoration: 'none', transition: 'border-color 0.2s',
                }}
              >
                Open in Spotify →
              </a>

              {/* Thumbs up / down */}
              <div style={{ display: 'flex', gap: '12px', marginTop: '20px', justifyContent: 'center', animation: 'fade-in 0.4s ease 0.3s both' }}>
                {[{ type: 'liked', emoji: '👍' }, { type: 'disliked', emoji: '👎' }].map(({ type, emoji }) => (
                  <button
                    key={type}
                    onClick={() => handleRate(type)}
                    style={{
                      background: rating === type ? 'rgba(91,35,186,0.35)' : 'rgba(255,255,255,0.06)',
                      border: `1.5px solid ${rating === type ? 'rgba(91,35,186,0.7)' : 'rgba(255,255,255,0.1)'}`,
                      borderRadius: '100px',
                      padding: '8px 20px',
                      fontSize: '18px',
                      cursor: 'pointer',
                      transition: 'all 0.2s',
                      opacity: rating && rating !== type ? 0.35 : 1,
                    }}
                  >
                    {emoji}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}
      </div>

      {/* Bottom controls — marginTop:auto pins this to bottom of the flex column */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px', width: '100%', maxWidth: '320px', marginTop: 'auto' }}>
        {isRevealed && skipsRemaining > 0 && (
          <button
            onClick={handleHeardIt}
            disabled={loadingNext}
            style={{
              width: '100%', padding: '14px', borderRadius: '16px',
              border: '1px solid rgba(255,255,255,0.12)',
              background: 'rgba(255,255,255,0.06)',
              backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)',
              color: 'rgba(255,255,255,0.6)', fontSize: '14px', fontWeight: '500',
              cursor: loadingNext ? 'default' : 'pointer', transition: 'all 0.2s',
              animation: 'fade-in-up 0.5s ease 0.3s both',
            }}
            onMouseEnter={e => { if (!loadingNext) { e.currentTarget.style.background = 'rgba(255,255,255,0.1)'; e.currentTarget.style.color = 'rgba(255,255,255,0.9)' } }}
            onMouseLeave={e => { e.currentTarget.style.background = 'rgba(255,255,255,0.06)'; e.currentTarget.style.color = 'rgba(255,255,255,0.6)' }}
          >
            {loadingNext ? 'Finding something new...' : `Already heard it · ${skipsRemaining} ${skipsRemaining === 1 ? 'skip' : 'skips'} left`}
          </button>

        )}
        {isRevealed && skipsRemaining === 0 && (
          <p style={{ color: 'rgba(255,255,255,0.25)', fontSize: '13px', animation: 'fade-in 0.4s ease' }}>
            Come back tomorrow for a new nudge
          </p>
        )}
      </div>
    </div>
  )
}

// ─── Tab bar ─────────────────────────────────────────────────────────────────

const TABS = [
  { id: 'nudge', label: 'nudge', title: "Today's Nudge" },
  { id: 'feel',  label: 'feel',  title: 'Discover' },
]

function TabBar({ tab, setTab }) {
  return (
    <div style={{
      display: 'flex',
      borderTop: '1px solid rgba(255,255,255,0.08)',
      background: 'rgba(0,0,0,0.95)',
      backdropFilter: 'blur(20px)',
      WebkitBackdropFilter: 'blur(20px)',
      paddingBottom: 'env(safe-area-inset-bottom, 20px)',
    }}>
      {TABS.map(t => (
        <button
          key={t.id}
          onClick={() => setTab(t.id)}
          style={{
            flex: 1, padding: '16px 0 14px',
            background: 'none', border: 'none',
            color: tab === t.id ? '#fff' : 'rgba(255,255,255,0.28)',
            fontSize: '14px', fontWeight: tab === t.id ? '700' : '400',
            letterSpacing: '0.3px', cursor: 'pointer',
            transition: 'color 0.2s',
            position: 'relative',
          }}
        >
          {/* Active indicator — full-width purple bar at top of tab */}
          <span style={{
            position: 'absolute', top: 0, left: '16px', right: '16px',
            height: '3px',
            background: tab === t.id ? '#5b23ba' : 'transparent',
            borderRadius: '0 0 3px 3px',
            boxShadow: tab === t.id ? '0 0 12px rgba(91,35,186,0.8)' : 'none',
            transition: 'background 0.2s, box-shadow 0.2s',
          }} />
          {t.label}
        </button>
      ))}
    </div>
  )
}

// ─── Main ────────────────────────────────────────────────────────────────────

// Every tab stays mounted so its state survives switching away and back.
// minHeight:0 is the flex trick that lets the inner scroll containers work.
function TabPanel({ active, children }) {
  return (
    <div style={{
      display: active ? 'flex' : 'none',
      flex: 1, flexDirection: 'column', width: '100%', minHeight: 0,
    }}>
      {children}
    </div>
  )
}

export default function HomePage({ rec, skipsRemaining, onHeardIt }) {
  const [tab, setTab] = useState('nudge')
  const title = TABS.find(t => t.id === tab)?.title

  return (
    <div style={{
      display: 'flex', flexDirection: 'column',
      height: '100dvh', background: '#000',
    }}>
      {/* Header */}
      <div style={{
        textAlign: 'center', padding: 'max(44px, env(safe-area-inset-top)) 24px 0',
        animation: 'fade-in 0.6s ease', flexShrink: 0,
      }}>
        <p style={{ color: 'rgba(255,255,255,0.3)', fontSize: '12px', letterSpacing: '2px', textTransform: 'uppercase' }}>
          {title}
        </p>
        <h1 style={{ fontSize: '28px', fontWeight: '700', letterSpacing: '-1px', marginTop: '4px' }}>
          nudge
        </h1>
      </div>

      <TabPanel active={tab === 'nudge'}>
        <NudgeTab rec={rec} skipsRemaining={skipsRemaining} onHeardIt={onHeardIt} />
      </TabPanel>
      <TabPanel active={tab === 'feel'}>
        <FeelTab />
      </TabPanel>

      <TabBar tab={tab} setTab={setTab} />
    </div>
  )
}

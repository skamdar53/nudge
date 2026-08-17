import { loginUrl } from '../api'

export default function LoginPage() {
  function handleLogin() {
    window.location.href = loginUrl()
  }

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      height: '100dvh',
      background: '#000',
      gap: '48px',
    }}>
      {/* Logo */}
      <div style={{ textAlign: 'center' }}>
        <h1 style={{
          fontSize: '64px',
          fontWeight: '700',
          letterSpacing: '-2px',
          color: '#fff',
          marginBottom: '8px',
        }}>
          nudge
        </h1>
        <p style={{
          color: 'rgba(255,255,255,0.4)',
          fontSize: '16px',
          letterSpacing: '0.5px',
        }}>
          one album a day, tailored to you
        </p>
      </div>

      {/* Connect button */}
      <button
        onClick={handleLogin}
        style={{
          background: '#5b23ba',
          color: '#fff',
          border: 'none',
          borderRadius: '100px',
          padding: '16px 40px',
          fontSize: '16px',
          fontWeight: '600',
          cursor: 'pointer',
          letterSpacing: '0.3px',
          boxShadow: '0 0 40px rgba(91, 35, 186, 0.5)',
          transition: 'transform 0.15s ease, box-shadow 0.15s ease',
        }}
        onMouseEnter={e => {
          e.target.style.transform = 'scale(1.04)'
          e.target.style.boxShadow = '0 0 60px rgba(124, 58, 237, 0.7)'
        }}
        onMouseLeave={e => {
          e.target.style.transform = 'scale(1)'
          e.target.style.boxShadow = '0 0 40px rgba(91, 35, 186, 0.5)'
        }}
      >
        Connect Spotify
      </button>
    </div>
  )
}

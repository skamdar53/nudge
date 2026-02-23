import { useEffect, useState } from 'react'

const messages = [
  'Digging through your taste...',
  'Finding your sonic fingerprint...',
  'Mapping your music DNA...',
  'Curating your world...',
]

export default function LoadingPage() {
  const [msgIndex, setMsgIndex] = useState(0)

  useEffect(() => {
    const interval = setInterval(() => {
      setMsgIndex(i => (i + 1) % messages.length)
    }, 2500)
    return () => clearInterval(interval)
  }, [])

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      height: '100vh',
      background: '#000',
      gap: '32px',
    }}>
      {/* Spinning vinyl placeholder */}
      <div style={{
        width: '80px',
        height: '80px',
        borderRadius: '50%',
        background: 'conic-gradient(from 0deg, #5b23ba, #1a0a3a, #5b23ba)',
        animation: 'spin-vinyl 2s linear infinite',
        boxShadow: '0 0 40px rgba(91, 35, 186, 0.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}>
        <div style={{
          width: '24px',
          height: '24px',
          borderRadius: '50%',
          background: '#000',
        }} />
      </div>

      <div style={{ textAlign: 'center' }}>
        <p style={{
          color: 'rgba(255,255,255,0.8)',
          fontSize: '16px',
          fontWeight: '500',
          transition: 'opacity 0.5s',
          marginBottom: '8px',
        }}>
          {messages[msgIndex]}
        </p>
        <p style={{ color: 'rgba(255,255,255,0.3)', fontSize: '13px' }}>
          This only happens once
        </p>
      </div>
    </div>
  )
}

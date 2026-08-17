const API = import.meta.env.VITE_API_URL || '/api'

// Attaches stored uid as a header on every request (needed for iOS Safari
// which blocks third-party cookies set via Vercel → Railway redirects)
export function apiFetch(path, options = {}) {
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

export function loginUrl() {
  return `${API}/login`
}

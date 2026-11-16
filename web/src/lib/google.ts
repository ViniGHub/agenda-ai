// Loads Google's official "Sign in with Google" library (Google Identity Services) on demand.
// The library returns an ID token (a JWT signed by Google); the API verifies it, never the browser.

interface CredentialResponse {
  credential: string
}

interface GoogleIdentity {
  initialize(options: {
    client_id: string
    callback: (response: CredentialResponse) => void
    ux_mode?: 'popup' | 'redirect'
    auto_select?: boolean
    use_fedcm_for_button?: boolean
  }): void
  renderButton(
    parent: HTMLElement,
    options: { theme?: string; size?: string; text?: string; shape?: string; locale?: string; width?: number },
  ): void
  disableAutoSelect(): void
}

declare global {
  interface Window {
    google?: { accounts: { id: GoogleIdentity } }
  }
}

let loading: Promise<GoogleIdentity> | null = null

export function loadGoogleIdentity(): Promise<GoogleIdentity> {
  if (window.google?.accounts?.id) return Promise.resolve(window.google.accounts.id)
  loading ??= new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = 'https://accounts.google.com/gsi/client'
    script.async = true
    script.onload = () => (window.google ? resolve(window.google.accounts.id) : reject(new Error('Google indisponível')))
    script.onerror = () => {
      loading = null
      reject(new Error('Não consegui carregar o login do Google.'))
    }
    document.head.appendChild(script)
  })
  return loading
}

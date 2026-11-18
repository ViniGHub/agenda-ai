import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { cn, STAMP_BUTTON } from '@/lib/utils'
import { login } from './api'
import { WarmupChecklist } from './components/WarmupChecklist'
import { loadGoogleIdentity } from './lib/google'
import type { Warmup } from './lib/warmup'
import type { Config, Me } from './types'
import { Footer } from './Workspace'

interface Props {
  config: Config | null
  configError: string | null
  warmup: Warmup
  opening: boolean // this device was signed in: the agenda opens as soon as the server confirms
  onDemo: () => void
  onSignedIn: (me: Me) => void
}

// Why a redirect sign-in came back without a session (the server sends ?erro_login=...).
const LOGIN_ERRORS: Record<string, string> = {
  cancelado: 'O login foi cancelado.',
  'sem-acesso': 'Esta conta não tem acesso à área privada. Use o modo de teste com a agenda fictícia.',
  falhou: 'Não consegui confirmar o login com o Google. Tente de novo.',
  limite: 'Muitas tentativas de login. Tente de novo em alguns minutos.',
  indisponivel: 'O login não está disponível neste servidor agora.',
}

function loginErrorFromUrl(): string | null {
  const code = new URLSearchParams(window.location.search).get('erro_login')
  if (!code) return null
  window.history.replaceState(null, '', window.location.pathname + window.location.hash) // read once
  return LOGIN_ERRORS[code] ?? LOGIN_ERRORS.falhou
}

/** The notebook's cover: sign in for your own agenda, or try the fictitious one without an account. */
export function Landing({ config, configError, warmup, opening, onDemo, onSignedIn }: Props) {
  const buttonRef = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(loginErrorFromUrl)
  const [signingIn, setSigningIn] = useState(false)
  const clientId = config?.login ? config.google_client_id : null
  const redirect = config?.login_redirect ?? false

  useEffect(() => {
    // The popup button is only for servers without the client secret (local development).
    if (!clientId || redirect || !buttonRef.current) return
    let cancelled = false
    loadGoogleIdentity()
      .then((google) => {
        if (cancelled || !buttonRef.current) return
        google.initialize({
          client_id: clientId,
          ux_mode: 'popup',
          auto_select: false,
          use_fedcm_for_button: true,
          callback: async ({ credential }) => {
            setError(null)
            setSigningIn(true)
            try {
              onSignedIn(await login(credential))
            } catch (e) {
              setError(e instanceof Error ? e.message : String(e))
            } finally {
              setSigningIn(false)
            }
          },
        })
        buttonRef.current.replaceChildren()
        google.renderButton(buttonRef.current, {
          theme: 'outline',
          size: 'large',
          text: 'signin_with',
          shape: 'pill',
          locale: 'pt-BR',
          width: 260,
        })
      })
      .catch((e: Error) => setError(e.message))
    return () => {
      cancelled = true
    }
  }, [clientId, redirect, onSignedIn, opening]) // the popup button mounts when the cover leaves "opening"

  return (
    <div className="mx-auto flex min-h-dvh max-w-[1280px] flex-col gap-6 px-4 py-6 sm:px-6">
      <main className="flex flex-1 items-center justify-center py-6">
        {/* Hardcover notebook: cloth cover, elastic band, paper label in the middle. */}
        <div className="relative w-full max-w-[34rem] overflow-hidden rounded-[14px] bg-[#7d3326] p-6 shadow-[0_30px_60px_-28px_rgb(0_0_0/0.6),inset_0_0_0_1px_rgb(0_0_0/0.25)] sm:p-10 dark:bg-[#4a2019]">
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0 opacity-30 [background-image:radial-gradient(rgb(255_255_255/0.18)_0.7px,transparent_0.7px)] [background-size:4px_4px]"
          />
          <div aria-hidden className="absolute top-0 right-7 bottom-0 w-3 bg-black/35 shadow-[inset_1px_0_0_rgb(255_255_255/0.08)]" />

          <div className="paper-plain relative mr-6 rounded-[4px] px-6 py-[var(--line)] shadow-[0_2px_0_rgb(0_0_0/0.15)] sm:mr-8 sm:px-8">
            <p className="font-mono text-[11px] leading-[var(--line)] tracking-[0.2em] text-pencil uppercase">caderno nº 1</p>
            <h1 className="font-heading text-[2.75rem] leading-[calc(var(--line)*1.75)] font-semibold tracking-tight [font-variation-settings:'SOFT'_100,'WONK'_1] sm:text-[3.25rem]">
              Agenda <em className="font-medium text-primary">ia</em>
            </h1>
            <p className="leading-[var(--line)] text-muted-foreground">
              Pergunte em português sobre a agenda. Cada resposta diz de qual compromisso saiu.
            </p>

            {opening ? (
              <div className="mt-[var(--line)]">
                <p className="font-hand text-[1.4rem] leading-[var(--line)] text-primary">abrindo a sua agenda…</p>
                <WarmupChecklist warmup={warmup} title="acordando o servidor" className="mt-1" />
              </div>
            ) : (
              <div className="mt-[var(--line)] flex flex-col gap-[calc(var(--line)/2)]">
                <section>
                  <Button
                    onClick={onDemo}
                    variant="ghost"
                    className={cn(STAMP_BUTTON, '-rotate-1 px-4 py-2.5 text-[12px]')}
                  >
                    Testar com a agenda fictícia →
                  </Button>
                  <p className="mt-1 font-hand text-[1.2rem] leading-tight text-pencil">
                    sem login. uma agenda inventada, refeita em volta desta semana.
                  </p>
                </section>

                <section aria-label="Entrar">
                  <p className="font-mono text-[11px] leading-[var(--line)] tracking-wide text-pencil uppercase">ou, se você tem acesso</p>
                  {clientId && redirect ? (
                    <GoogleSignIn onClick={() => setSigningIn(true)} />
                  ) : clientId ? (
                    <div ref={buttonRef} className="min-h-[44px]" />
                  ) : (
                    <p className="font-hand text-[1.2rem] leading-tight text-pencil">
                      {configError ?? (config ? 'o login ainda não foi configurado neste servidor.' : 'abrindo…')}
                    </p>
                  )}
                  <p className="mt-1 font-hand text-[1.2rem] leading-tight text-pencil">
                    {signingIn ? 'indo para o Google…' : 'para a sua agenda privada. só contas autorizadas entram.'}
                  </p>
                  {error && (
                    <p role="alert" className="mt-1 font-hand text-[1.25rem] leading-tight text-primary">
                      {error}
                    </p>
                  )}
                </section>

                {/* While the server wakes up, say so: the buttons work, the pages behind them wait. */}
                <WarmupChecklist warmup={warmup} title="acordando o servidor" className="border-t border-dashed border-border pt-2" />
              </div>
            )}
          </div>
        </div>
      </main>
      <Footer />
    </div>
  )
}

/**
 * "Fazer login com o Google", drawn after Google's own button (white, the four-color G, pill shape).
 * A plain link: the page goes to Google and comes back signed in, which works in the installed app.
 */
function GoogleSignIn({ onClick }: { onClick: () => void }) {
  return (
    <a
      href="/api/login/google"
      onClick={onClick}
      className="inline-flex h-11 items-center gap-3 rounded-full border border-[#747775] bg-white px-4 whitespace-nowrap font-[Roboto,Arial,sans-serif] text-[14px] font-medium text-[#1f1f1f] no-underline shadow-sm hover:bg-[#f8f9fa] focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
    >
      <svg aria-hidden width="18" height="18" viewBox="0 0 48 48">
        <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
        <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
        <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
        <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
      </svg>
      Fazer login com o Google
    </a>
  )
}

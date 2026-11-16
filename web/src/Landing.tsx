import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { cn, STAMP_BUTTON } from '@/lib/utils'
import { login } from './api'
import { loadGoogleIdentity } from './lib/google'
import type { Config, Me } from './types'
import { Footer } from './Workspace'

interface Props {
  config: Config | null
  configError: string | null
  onDemo: () => void
  onSignedIn: (me: Me) => void
}

/** The notebook's cover: sign in for your own agenda, or try the fictitious one without an account. */
export function Landing({ config, configError, onDemo, onSignedIn }: Props) {
  const buttonRef = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)
  const [signingIn, setSigningIn] = useState(false)
  const clientId = config?.login ? config.google_client_id : null

  useEffect(() => {
    if (!clientId || !buttonRef.current) return
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
  }, [clientId, onSignedIn])

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
                {clientId ? (
                  <div ref={buttonRef} className="min-h-[44px]" />
                ) : (
                  <p className="font-hand text-[1.2rem] leading-tight text-pencil">
                    {configError ?? (config ? 'o login ainda não foi configurado neste servidor.' : 'abrindo…')}
                  </p>
                )}
                <p className="mt-1 font-hand text-[1.2rem] leading-tight text-pencil">
                  {signingIn ? 'conferindo o seu login…' : 'para a sua agenda privada. só contas autorizadas entram.'}
                </p>
                {error && (
                  <p role="alert" className="mt-1 font-hand text-[1.25rem] leading-tight text-primary">
                    {error}
                  </p>
                )}
              </section>
            </div>
          </div>
        </div>
      </main>
      <Footer />
    </div>
  )
}

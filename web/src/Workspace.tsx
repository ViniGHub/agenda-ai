import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { InkLink } from '@/components/ui/skiper-ui/ink-link'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn, STAMP_BUTTON } from '@/lib/utils'
import { ApiError, askQuestion, deleteEvent, getAgenda, getStatus, importIcs } from './api'
import { AgendaPanel } from './components/AgendaPanel'
import { Chat, type ChatMessage } from './components/Chat'
import { DetailsPanel } from './components/DetailsPanel'
import { EventForm } from './components/EventForm'
import type { AgendaEvent, Me, Space, Status } from './types'

type Tab = 'detalhes' | 'agenda'

const DEMO_IDEAS = [
  'O que tenho amanhã?',
  'Quando é o dentista?',
  'Quantas aulas de inglês tenho esta semana?',
  'O que preciso levar no veterinário?',
  'O que ficou de ponto a melhorar no meu feedback?',
  'Tenho algo com a padaria semana que vem?',
  'Tenho show marcado?',
]

const OWN_IDEAS = ['O que tenho amanhã?', 'Como está a minha semana?', 'Qual o meu próximo compromisso?']

interface Props {
  space: Space
  user: Me | null
  onLeave: () => void // back to the cover (demo) or sign out (private)
  onSessionLost: () => void
}

/** The open notebook: conversation on the left page, how each answer was built on the right. */
export function Workspace({ space, user, onLeave, onSessionLost }: Props) {
  const [status, setStatus] = useState<Status | null>(null)
  const [agenda, setAgenda] = useState<AgendaEvent[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [pending, setPending] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [tab, setTab] = useState<Tab>('detalhes')
  const [focusedEventId, setFocusedEventId] = useState<number | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const nextId = useRef(1)
  const fileRef = useRef<HTMLInputElement>(null)

  function handleError(error: unknown): string {
    if (error instanceof ApiError && error.status === 401) onSessionLost()
    return error instanceof Error ? error.message : String(error)
  }

  function load() {
    setLoadError(null)
    Promise.all([getStatus(space), getAgenda(space)])
      .then(([s, a]) => {
        setStatus(s)
        setAgenda(a)
      })
      .catch((error) => setLoadError(handleError(error)))
  }

  useEffect(load, [space])

  async function send(question: string) {
    const text = question.trim()
    if (!text || pending) return
    setMessages((m) => [...m, { id: nextId.current++, role: 'user', text }])
    setPending(true)
    try {
      const answer = await askQuestion(space, text)
      const id = nextId.current++
      setMessages((m) => [...m, { id, role: 'assistant', answer }])
      setSelectedId(id)
      setTab('detalhes')
    } catch (error) {
      setMessages((m) => [...m, { id: nextId.current++, role: 'error', text: handleError(error) }])
    } finally {
      setPending(false)
    }
  }

  function showEvent(eventId: number) {
    setFocusedEventId(eventId)
    setTab('agenda')
  }

  function onSaved(event: AgendaEvent) {
    setNotice(`"${event.titulo}" anotado: ${event.quando}.`)
    load()
    showEvent(event.id)
  }

  async function remove(event: AgendaEvent) {
    try {
      await deleteEvent(space, event.id)
      setNotice(`"${event.titulo}" apagado.`)
      load()
    } catch (error) {
      setNotice(handleError(error))
    }
  }

  async function importFile(file: File) {
    setNotice('lendo o arquivo…')
    try {
      const result = await importIcs(await file.text())
      const repeated = result.encontrados - result.importados
      setNotice(
        `${result.importados} compromisso${result.importados === 1 ? '' : 's'} importado${result.importados === 1 ? '' : 's'}` +
          (repeated > 0 ? ` (${repeated} já estava${repeated === 1 ? '' : 'm'} na agenda).` : '.'),
      )
      load()
      setTab('agenda')
    } catch (error) {
      setNotice(handleError(error))
    }
  }

  const selected = messages.find((m) => m.id === selectedId)
  const selectedAnswer = selected?.role === 'assistant' ? selected.answer : null
  const citedIds = new Set(selectedAnswer?.fontes.map((f) => f.id) ?? [])
  const isDemo = space === 'demo'
  const emptyOwn = !isDemo && status !== null && status.eventos === 0

  return (
    <div className="mx-auto flex min-h-dvh max-w-[1280px] flex-col gap-5 px-4 py-6 sm:px-6 lg:h-dvh">
      <header className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
        <div>
          <h1 className="font-heading text-[2.6rem] leading-none font-semibold tracking-tight [font-variation-settings:'SOFT'_100,'WONK'_1] sm:text-5xl">
            {isDemo ? (
              <>
                Agenda <em className="font-medium text-primary">ia</em>
              </>
            ) : (
              <>
                Sua agenda<em className="font-medium text-primary">, {firstName(user)}</em>
              </>
            )}
          </h1>
          <p className="mt-2 max-w-xl text-muted-foreground">
            Pergunte em português. Cada resposta diz de qual compromisso saiu.
          </p>
        </div>
        <div className="flex flex-col items-start gap-2 sm:items-end">
          <div className="flex items-center gap-4">
            <StatusStamp status={status} />
            <InkLink onClick={onLeave} className="font-mono text-[11px] tracking-wide text-pencil uppercase">
              {isDemo ? '← voltar à capa' : 'sair'}
            </InkLink>
          </div>
          {isDemo ? (
            <p className="max-w-[19rem] font-hand text-[1.2rem] leading-tight text-primary sm:-rotate-1 sm:text-right">
              modo demonstração: agenda fictícia, refeita em volta desta semana. não escreva dados pessoais: a pergunta vai
              para um modelo de linguagem.
            </p>
          ) : (
            <p className="max-w-[19rem] font-hand text-[1.2rem] leading-tight text-pencil sm:-rotate-1 sm:text-right">
              só você vê esta agenda.
              {status && !status.persistente && ' por enquanto ela é temporária: some quando o servidor reinicia.'}
            </p>
          )}
          <div className="flex items-center gap-4">
            <Button
              variant="ghost"
              disabled={!status}
              onClick={() => setFormOpen(true)}
              className={cn(STAMP_BUTTON, 'px-3 py-1.5 text-[11px]')}
            >
              + Novo compromisso
            </Button>
            {!isDemo && (
              <>
                <InkLink
                  disabled={!status}
                  onClick={() => fileRef.current?.click()}
                  className="font-mono text-[11px] tracking-wide text-pencil uppercase"
                >
                  importar .ics
                </InkLink>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".ics,text/calendar"
                  hidden
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    event.target.value = '' // lets the same file be chosen again
                    if (file) void importFile(file)
                  }}
                />
              </>
            )}
          </div>
        </div>
      </header>

      {notice && (
        <p role="status" className="-mt-2 font-hand text-[1.3rem] leading-tight text-primary">
          {notice}{' '}
          <button type="button" onClick={() => setNotice(null)} className="font-mono text-[11px] text-pencil" aria-label="Fechar aviso">
            ×
          </button>
        </p>
      )}

      {loadError && (
        <p role="alert" className="flex flex-wrap items-center gap-3 font-hand text-[1.35rem] text-primary">
          {loadError}
          <Button variant="outline" size="sm" onClick={load} className="font-sans">
            Tentar de novo
          </Button>
        </p>
      )}
      {status && !status.llm && (
        <p className="font-hand text-[1.35rem] text-primary">
          o modelo de linguagem não está configurado: as respostas mostram só os compromissos mais próximos da pergunta.
        </p>
      )}

      <main className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden rounded-[10px] bg-card shadow-[0_1px_0_var(--border),0_24px_48px_-24px_rgb(0_0_0/0.45)] ring-1 ring-border lg:grid-cols-[1.15fr_1fr]">
        <div className="flex min-h-0 flex-col lg:shadow-[inset_-22px_0_26px_-26px_rgb(0_0_0/0.4)]">
          <Chat
            messages={messages}
            pending={pending}
            disabled={!status}
            selectedId={selectedId}
            ideas={isDemo ? DEMO_IDEAS : emptyOwn ? [] : OWN_IDEAS}
            emptyNote={
              emptyOwn ? (
                <p className="font-hand text-[1.4rem] leading-[var(--line)] text-primary">
                  sua agenda ainda está vazia. importe o .ics do Google Agenda (configurações → importar e exportar) ou
                  anote um compromisso.
                </p>
              ) : null
            }
            onSend={send}
            onSelect={(id) => {
              setSelectedId(id)
              setTab('detalhes')
            }}
            onShowEvent={showEvent}
          />
        </div>

        <Tabs
          value={tab}
          onValueChange={(value) => setTab(value as Tab)}
          className="min-h-[32rem] gap-0 border-t border-border lg:min-h-0 lg:border-t-0 lg:border-l lg:shadow-[inset_22px_0_26px_-26px_rgb(0_0_0/0.35)]"
        >
          <TabsList variant="line" className="h-auto w-full justify-start gap-6 rounded-none border-b border-border bg-card px-6 pt-3 pb-0">
            <TabsTrigger value="detalhes" className="flex-none px-0 pb-2.5 font-heading text-[1.05rem]">
              Como foi respondido
            </TabsTrigger>
            <TabsTrigger value="agenda" className="flex-none px-0 pb-2.5 font-heading text-[1.05rem]">
              Agenda
              {status && <span className="font-mono text-[11px] text-pencil">{agenda.length}</span>}
            </TabsTrigger>
          </TabsList>
          <TabsContent value="detalhes" className="paper-plain min-h-0 flex-1">
            <DetailsPanel answer={selectedAnswer} onShowEvent={showEvent} />
          </TabsContent>
          <TabsContent value="agenda" className="min-h-0 flex-1">
            <AgendaPanel
              events={agenda}
              loaded={status !== null}
              citedIds={citedIds}
              focusedEventId={focusedEventId}
              onDelete={remove}
            />
          </TabsContent>
        </Tabs>
      </main>

      <Footer />
      <EventForm open={formOpen} space={space} onOpenChange={setFormOpen} onSaved={onSaved} />
    </div>
  )
}

export function Footer() {
  return (
    <footer className="flex flex-wrap justify-between gap-x-6 gap-y-1 font-mono text-[11px] tracking-wide text-pencil">
      <span>busca híbrida (embeddings locais + BM25) · gpt-oss-120b no Groq · citações conferidas em código</span>
      <span>
        <a className="underline-offset-2 hover:underline" href="https://github.com/ViniGHub/agenda-ai">código no GitHub</a>
        {' · '}animações adaptadas de{' '}
        <a className="underline-offset-2 hover:underline" href="https://skiper-ui.com" target="_blank" rel="noreferrer">
          Skiper UI
        </a>
      </span>
    </footer>
  )
}

function firstName(user: Me | null): string {
  return (user?.nome ?? '').split(' ')[0] || 'você'
}

function StatusStamp({ status }: { status: Status | null }) {
  if (!status) return <span className="stamp px-2 py-1 text-[11px] text-pencil">abrindo…</span>
  const source = { exemplo: 'agenda fictícia', arquivo: 'agenda do arquivo', privada: 'agenda privada' }[status.origem]
  return (
    <span
      className="stamp -rotate-2 px-2 py-1 text-[10px] whitespace-nowrap text-primary sm:text-[11px]"
      title={status.modelo ?? 'sem modelo de linguagem'}
    >
      {source} · {status.eventos} itens · {status.llm ? 'llm ativo' : 'só busca'}
    </span>
  )
}

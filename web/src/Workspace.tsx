import { CalendarDays, ListChecks, MessageSquareText } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { InkLink } from '@/components/ui/skiper-ui/ink-link'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn, STAMP_BUTTON } from '@/lib/utils'
import {
  addDraft,
  ApiError,
  askQuestion,
  deleteEvent,
  disconnectGoogle,
  getAgenda,
  getGoogle,
  getStatus,
  importIcs,
  sendSummaryNow,
  setDailyMail,
  syncGoogle,
  transcribeAudio,
} from './api'
import { AgendaCalendar } from './components/AgendaCalendar'
import { AgendaPanel } from './components/AgendaPanel'
import { GoogleBar } from './components/GoogleBar'
import { WarmupChecklist } from './components/WarmupChecklist'
import { Chat, type ChatMessage, type NoteHandlers } from './components/Chat'
import { DetailsPanel } from './components/DetailsPanel'
import { EventForm } from './components/EventForm'
import { canRecord, type Recording } from '@/lib/recorder'
import type { Warmup } from '@/lib/warmup'
import type { AgendaEvent, AskMode, Draft, GoogleStatus, Me, Space, Status } from './types'

type Tab = 'detalhes' | 'agenda'

const DEMO_IDEAS = [
  'O que tenho amanhã?',
  'Marca almoço com a Ana sexta ao meio-dia',
  'Quando é o dentista?',
  'Quantas aulas de inglês tenho esta semana?',
  'O que preciso levar no veterinário?',
  'O que ficou de ponto a melhorar no meu feedback?',
  'Tenho algo com a padaria semana que vem?',
  'Tenho show marcado?',
]

const OWN_IDEAS = ['O que tenho amanhã?', 'Como está a minha semana?', 'Qual o meu próximo compromisso?']

// What came back from Google's consent page (the server sends /?google=...#minha).
const GOOGLE_RETURN: Record<string, string> = {
  conectado: 'google agenda conectado. trazendo os seus compromissos…',
  cancelado: 'a conexão com o google foi cancelada.',
  falhou: 'não consegui conectar o google agora. tente de novo.',
  'outra-conta': 'conecte com a mesma conta google que você usou para entrar.',
  'sem-permissao': 'o google não liberou a agenda nem o e-mail. na tela do google, deixe as opções marcadas.',
  indisponivel: 'a conexão com o google não está configurada neste servidor.',
  limite: 'muitas tentativas seguidas. tente de novo em alguns minutos.',
}
// How the agenda page shows: a month calendar or the day-by-day list. The last one chosen on this
// device comes back; the first time it is the calendar.
type AgendaView = 'calendario' | 'lista'
const VIEW_KEY = 'agenda-ia:visao'

function readAgendaView(): AgendaView {
  try {
    return localStorage.getItem(VIEW_KEY) === 'lista' ? 'lista' : 'calendario'
  } catch {
    return 'calendario'
  }
}

const SYNC_EVERY_MS = 5 * 60 * 1000 // opening the agenda syncs with Google if the last sync is older than this

function googleReturnFromUrl(): string | null {
  const code = new URLSearchParams(window.location.search).get('google')
  if (!code) return null
  window.history.replaceState(null, '', window.location.pathname + window.location.hash) // read once
  return GOOGLE_RETURN[code] ?? GOOGLE_RETURN.falhou
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

interface Props {
  space: Space
  user: Me | null
  warmup: Warmup // the server's start-up: the agenda loads once it is ready
  onLeave: () => void // back to the cover (demo) or sign out (private)
  onSessionLost: () => void
}

/** The open notebook: conversation on the left page, how each answer was built on the right. */
export function Workspace({ space, user, warmup, onLeave, onSessionLost }: Props) {
  const [status, setStatus] = useState<Status | null>(null)
  const [agenda, setAgenda] = useState<AgendaEvent[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [pending, setPending] = useState(false)
  const [pendingText, setPendingText] = useState('consultando a agenda')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  // The right-hand page opens on the agenda. A new answer does not take the page over: it only
  // marks the "Como foi respondido" tab (detailsNew counts the answers not seen there yet).
  const [tab, setTabState] = useState<Tab>('agenda')
  const [detailsNew, setDetailsNew] = useState(0)
  const tabRef = useRef<Tab>('agenda') // the tab right now, for answers that arrive after an await
  // On a phone the notebook shows one page at a time, picked in the bottom bar.
  const [pane, setPaneState] = useState<'conversa' | Tab>('conversa')
  const paneRef = useRef<'conversa' | Tab>('conversa')
  const [focusedEventId, setFocusedEventId] = useState<number | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [formSeed, setFormSeed] = useState<{ draft: Draft; messageId: number } | null>(null) // form opened from a chat note
  const [formEditing, setFormEditing] = useState<{ event: AgendaEvent; messageId: number | null } | null>(null)
  const [formDate, setFormDate] = useState<string | null>(null) // a day picked on the calendar
  const [agendaView, setAgendaViewState] = useState<AgendaView>(readAgendaView)
  const [notice, setNotice] = useState<string | null>(googleReturnFromUrl)
  const [google, setGoogle] = useState<GoogleStatus | null>(null)
  const [googleBusy, setGoogleBusy] = useState<string | null>(null) // what the Google bar is doing now
  const nextId = useRef(1)
  const fileRef = useRef<HTMLInputElement>(null)

  function setPane(next: 'conversa' | Tab) {
    paneRef.current = next
    setPaneState(next)
  }

  /** Opens a right-hand tab; on a phone that also brings its page to the front. */
  function setTab(next: Tab) {
    tabRef.current = next
    setTabState(next)
    setPane(next)
    if (next === 'detalhes') setDetailsNew(0)
  }

  /** Is "Como foi respondido" on screen right now? (the right page on a computer, the open page on a phone) */
  const detailsOnScreen = () =>
    window.matchMedia('(min-width: 1024px)').matches ? tabRef.current === 'detalhes' : paneRef.current === 'detalhes'

  function handleError(error: unknown): string {
    if (error instanceof ApiError && error.status === 401) onSessionLost()
    // 409 from a write can mean the Google access was revoked: show the bar's "connect" again.
    if (error instanceof ApiError && error.status === 409 && space === 'minha') void getGoogle().then(setGoogle, () => undefined)
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

  // Waits for the server's start-up, so a cold start shows its steps instead of a request hanging.
  useEffect(() => {
    if (warmup.ready) load()
  }, [space, warmup.ready])

  // The private agenda follows Google Calendar: on opening, sync if the last sync is not recent.
  useEffect(() => {
    if (space !== 'minha' || !warmup.ready) return
    getGoogle()
      .then((g) => {
        setGoogle(g)
        const last = g.sincronizado_em ? new Date(g.sincronizado_em).getTime() : 0
        if (g.conectado && g.agenda && Date.now() - last > SYNC_EVERY_MS) void syncNow(true)
      })
      .catch(() => setGoogle(null)) // the bar just stays hidden
  }, [space, warmup.ready])

  /** quiet: an automatic sync only speaks up when something changed. */
  async function syncNow(quiet = false) {
    setGoogleBusy('sincronizando…')
    try {
      const r = await syncGoogle()
      const changes = [
        r.novos && plural(r.novos, 'novo'),
        r.atualizados && plural(r.atualizados, 'alterado'),
        r.apagados && plural(r.apagados, 'apagado'),
      ].filter(Boolean)
      if (changes.length > 0 || !quiet) setNotice(`google agenda: ${changes.length ? changes.join(', ') : 'nada novo'}.`)
      if (changes.length > 0) load()
      setGoogle(await getGoogle())
    } catch (error) {
      if (!quiet || !(error instanceof ApiError && error.status === 429)) setNotice(handleError(error))
    } finally {
      setGoogleBusy(null)
    }
  }

  async function toggleDailyMail(on: boolean) {
    setGoogleBusy('salvando…')
    try {
      setGoogle(await setDailyMail(on))
      setNotice(on ? 'e-mail das 8h ligado.' : 'e-mail das 8h desligado.')
    } catch (error) {
      setNotice(handleError(error))
    } finally {
      setGoogleBusy(null)
    }
  }

  async function sendMailNow() {
    setGoogleBusy('enviando…')
    try {
      const r = await sendSummaryNow()
      setNotice(`e-mail enviado para ${r.enviado_para} com ${r.compromissos === 0 ? 'o dia livre' : plural(r.compromissos, 'compromisso')} de hoje.`)
    } catch (error) {
      setNotice(handleError(error))
    } finally {
      setGoogleBusy(null)
    }
  }

  async function disconnect() {
    setGoogleBusy('desconectando…')
    try {
      setGoogle(await disconnectGoogle())
      setNotice('google desconectado. o que veio de lá saiu desta agenda e continua no seu google agenda.')
      load()
    } catch (error) {
      setNotice(handleError(error))
    } finally {
      setGoogleBusy(null)
    }
  }

  /** echo: false when the same message is sent again with another mode (no repeated line on the page). */
  async function send(question: string, mode: AskMode = 'auto', echo = true) {
    const text = question.trim()
    if (!text || pending) return
    setPending(true)
    try {
      await ask(text, mode, echo)
    } finally {
      setPending(false)
    }
  }

  /** A voice message: transcribed on the server, then asked like a typed one. */
  async function sendAudio(recording: Recording) {
    if ('error' in recording) {
      setMessages((m) => [...m, { id: nextId.current++, role: 'error', text: recording.error }])
      return
    }
    if (pending) return
    setPending(true)
    setPendingText('ouvindo o áudio')
    try {
      const { texto } = await transcribeAudio(space, recording.blob)
      await ask(texto, 'auto', true, 'audio')
    } catch (error) {
      setMessages((m) => [...m, { id: nextId.current++, role: 'error', text: handleError(error) }])
    } finally {
      setPending(false)
    }
  }

  async function ask(text: string, mode: AskMode, echo: boolean, via?: 'audio') {
    if (echo) setMessages((m) => [...m, { id: nextId.current++, role: 'user', text, via }])
    setPendingText('consultando a agenda')
    try {
      const answer = await askQuestion(space, text, mode)
      const id = nextId.current++
      setMessages((m) => [...m, { id, role: 'assistant', answer, mode }])
      setSelectedId(id)
      if (!detailsOnScreen()) setDetailsNew((n) => n + 1)
      if (answer.acao?.tipo === 'anotado') load() // already in the agenda: refresh the count and the list
    } catch (error) {
      setMessages((m) => [...m, { id: nextId.current++, role: 'error', text: handleError(error) }])
    }
  }

  function showEvent(eventId: number) {
    setFocusedEventId(eventId)
    setTab('agenda')
  }

  function onSaved(event: AgendaEvent, edited: boolean) {
    setNotice(`"${event.titulo}" ${edited ? 'atualizado' : 'anotado'}: ${event.quando}.`)
    const messageId = edited ? formEditing?.messageId : formSeed?.messageId
    if (messageId != null) markNote(messageId, 'anotado', event) // the chat note shows the new version
    load()
    showEvent(event.id)
  }

  function openForm(seed: { draft: Draft; messageId: number } | null) {
    setFormSeed(seed)
    setFormEditing(null)
    setFormDate(null)
    setFormOpen(true)
  }

  /** "Novo compromisso" already on a day: a day without a note was tapped on the calendar. */
  function openFormOn(date: string) {
    setFormSeed(null)
    setFormEditing(null)
    setFormDate(date)
    setFormOpen(true)
  }

  function openEdit(event: AgendaEvent, messageId: number | null = null) {
    setFormSeed(null)
    setFormEditing({ event, messageId })
    setFormDate(null)
    setFormOpen(true)
  }

  function setAgendaView(view: AgendaView) {
    setAgendaViewState(view)
    try {
      localStorage.setItem(VIEW_KEY, view)
    } catch {
      // private browsing: the choice lasts until the page closes
    }
  }

  // ----- the sticky note under a chat reply ("marca dentista quinta às 15h") -----

  function markNote(messageId: number, note: NonNullable<Extract<ChatMessage, { role: 'assistant' }>['note']>, saved?: AgendaEvent | null) {
    setMessages((all) =>
      all.map((m) => (m.id === messageId && m.role === 'assistant' ? { ...m, note, ...(saved !== undefined && { saved }) } : m)),
    )
  }

  function noteOf(messageId: number) {
    const message = messages.find((m) => m.id === messageId)
    return message?.role === 'assistant' && message.answer.acao ? { message, action: message.answer.acao } : null
  }

  const notes: NoteHandlers = {
    async confirm(messageId) {
      const found = noteOf(messageId)
      if (!found) return
      try {
        const saved = await addDraft(space, found.action.rascunho)
        markNote(messageId, 'anotado', saved)
        load()
      } catch (error) {
        throw new Error(handleError(error))
      }
    },
    async undo(messageId) {
      const found = noteOf(messageId)
      const saved = found && (found.message.saved ?? found.action.evento)
      if (!saved) return
      try {
        await deleteEvent(space, saved.id)
        markNote(messageId, 'desfeito', null)
        load()
      } catch (error) {
        throw new Error(handleError(error))
      }
    },
    edit(messageId) {
      const found = noteOf(messageId)
      if (found) openForm({ draft: found.action.rascunho, messageId })
    },
    editSaved(messageId) {
      const found = noteOf(messageId)
      const saved = found && (found.message.saved ?? found.action.evento)
      if (!saved) return
      // The agenda has the current version (it may have been edited already); fall back to the note's copy.
      openEdit(agenda.find((e) => e.id === saved.id) ?? saved, messageId)
    },
    dismiss(messageId) {
      markNote(messageId, 'cancelado')
    },
    askInstead(messageId) {
      const text = messageBefore(messageId)
      if (!text) return
      markNote(messageId, 'pergunta')
      void send(text, 'pergunta', false)
    },
    writeDown(messageId) {
      const text = messageBefore(messageId)
      if (!text) return
      setMessages((all) => all.map((m) => (m.id === messageId && m.role === 'assistant' ? { ...m, offerUsed: true } : m)))
      void send(text, 'anotar', false)
    },
  }

  /** The person's message right before a reply. */
  function messageBefore(messageId: number): string | null {
    const index = messages.findIndex((m) => m.id === messageId)
    const message = messages.slice(0, index).reverse().find((m) => m.role === 'user')
    return message?.role === 'user' ? message.text : null
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
    // A phone gets the app layout: the whole screen, pages that scroll inside, a bar at the bottom.
    <div className="mx-auto flex h-dvh max-w-[1280px] flex-col gap-3 px-3 pt-3 sm:px-6 lg:gap-5 lg:py-6">
      <header className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
        <div>
          <h1 className="font-heading text-[2rem] leading-none font-semibold tracking-tight [font-variation-settings:'SOFT'_100,'WONK'_1] sm:text-5xl">
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
          <p className="mt-2 max-w-xl text-muted-foreground max-lg:hidden">
            Pergunte, ou peça para anotar. Cada resposta diz de qual compromisso saiu.
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
            <p className="max-w-[19rem] font-hand text-[1.05rem] leading-tight text-primary sm:-rotate-1 sm:text-right sm:text-[1.2rem]">
              modo demonstração: agenda fictícia, refeita em volta desta semana. não escreva nem fale dados pessoais: a
              mensagem vai para um modelo de linguagem.
            </p>
          ) : (
            <p className="max-w-[19rem] font-hand text-[1.2rem] leading-tight text-pencil max-lg:hidden sm:-rotate-1 sm:text-right">
              só você vê esta agenda.
              {status && !status.persistente && ' por enquanto ela é temporária: some quando o servidor reinicia.'}
            </p>
          )}
          <div className="flex items-center gap-4">
            <Button
              variant="ghost"
              disabled={!status}
              onClick={() => openForm(null)}
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
        <div className={cn('flex min-h-0 flex-col lg:shadow-[inset_-22px_0_26px_-26px_rgb(0_0_0/0.4)]', pane !== 'conversa' && 'max-lg:hidden')}>
          <Chat
            messages={messages}
            pending={pending}
            disabled={!status}
            selectedId={selectedId}
            detailsOpen={tab === 'detalhes'}
            ideas={!warmup.ready ? [] : isDemo ? DEMO_IDEAS : emptyOwn ? [] : OWN_IDEAS}
            emptyNote={
              !warmup.ready ? (
                <WarmupChecklist
                  warmup={warmup}
                  title={isDemo ? 'abrindo a agenda fictícia' : 'abrindo a sua agenda'}
                  className="mb-[calc(var(--line)/2)]"
                />
              ) : emptyOwn ? (
                <p className="font-hand text-[1.4rem] leading-[var(--line)] text-primary">
                  {google?.disponivel && !google.conectado
                    ? 'sua agenda ainda está vazia. conecte o google agenda (na página da agenda), importe um .ics ou anote um compromisso.'
                    : 'sua agenda ainda está vazia. importe o .ics do Google Agenda (configurações → importar e exportar) ou anote um compromisso.'}
                </p>
              ) : null
            }
            onSend={send}
            onSelect={(id) => {
              setSelectedId(id)
              setTab('detalhes')
            }}
            onShowEvent={showEvent}
            notes={notes}
            canNote={status?.llm ?? false}
            canRecord={(status?.audio ?? false) && canRecord()}
            pendingText={pendingText}
            onAudio={(recording) => void sendAudio(recording)}
          />
        </div>

        <Tabs
          value={tab}
          onValueChange={(value) => setTab(value as Tab)}
          className={cn(
            'min-h-0 gap-0 lg:border-l lg:shadow-[inset_22px_0_26px_-26px_rgb(0_0_0/0.35)]',
            pane === 'conversa' && 'max-lg:hidden',
          )}
        >
          <TabsList variant="line" className="h-auto w-full justify-start gap-6 rounded-none border-b border-border bg-card px-6 pt-3 pb-0 max-lg:hidden">
            <TabsTrigger value="agenda" className="flex-none px-0 pb-2.5 font-heading text-[1.05rem]">
              Agenda
              {status && <span className="font-mono text-[11px] text-pencil">{agenda.length}</span>}
            </TabsTrigger>
            <TabsTrigger value="detalhes" className="flex-none px-0 pb-2.5 font-heading text-[1.05rem]">
              {/* key: each new answer remounts the label, so the highlighter swipes in again */}
              <span key={detailsNew} className={cn(detailsNew > 0 && 'marker marker-soft')}>Como foi respondido</span>
              {detailsNew > 0 && (
                <>
                  <span className="size-1.5 rounded-full bg-primary" aria-hidden />
                  <span className="sr-only">(resposta nova)</span>
                </>
              )}
            </TabsTrigger>
          </TabsList>
          <TabsContent value="detalhes" className="paper-plain min-h-0 flex-1">
            <DetailsPanel answer={selectedAnswer} onShowEvent={showEvent} />
          </TabsContent>
          <TabsContent value="agenda" className="flex min-h-0 flex-1 flex-col">
            {!isDemo && (
              <GoogleBar
                status={google}
                busy={googleBusy}
                onSync={() => void syncNow()}
                onDailyMail={(on) => void toggleDailyMail(on)}
                onSendNow={() => void sendMailNow()}
                onDisconnect={() => void disconnect()}
              />
            )}
            <div
              role="group"
              aria-label="Como ver a agenda"
              className="flex items-center justify-end gap-3 border-b border-border px-6 py-1.5 font-mono text-[10.5px] tracking-wide text-pencil uppercase"
            >
              <span>ver:</span>
              {(
                [
                  ['calendario', 'calendário'],
                  ['lista', 'lista'],
                ] as const
              ).map(([view, label]) => (
                <button
                  key={view}
                  type="button"
                  aria-pressed={agendaView === view}
                  onClick={() => setAgendaView(view)}
                  className={cn(
                    'uppercase',
                    agendaView === view ? 'text-primary underline decoration-2 underline-offset-4' : 'hover:text-primary',
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="min-h-0 flex-1">
              {agendaView === 'calendario' ? (
                <AgendaCalendar
                  events={agenda}
                  loaded={status !== null}
                  citedIds={citedIds}
                  focusedEventId={focusedEventId}
                  onEdit={(event) => openEdit(event)}
                  onDelete={remove}
                  onNew={openFormOn}
                />
              ) : (
                <AgendaPanel
                  events={agenda}
                  loaded={status !== null}
                  citedIds={citedIds}
                  focusedEventId={focusedEventId}
                  onEdit={(event) => openEdit(event)}
                  onDelete={remove}
                />
              )}
            </div>
          </TabsContent>
        </Tabs>
      </main>

      <div className="max-lg:hidden">
        <Footer />
      </div>

      {/* Phone: the three pages of the notebook, one at a time. Padded for the iPhone's home bar. */}
      <nav aria-label="Páginas" className="-mx-3 grid grid-cols-3 border-t border-border bg-card pb-[env(safe-area-inset-bottom)] sm:-mx-6 lg:hidden">
        {(
          [
            ['conversa', 'Conversa', MessageSquareText],
            ['agenda', 'Agenda', CalendarDays],
            ['detalhes', 'Como foi', ListChecks],
          ] as const
        ).map(([key, label, Icon]) => (
          <button
            key={key}
            type="button"
            aria-current={pane === key ? 'page' : undefined}
            onClick={() => (key === 'conversa' ? setPane('conversa') : setTab(key))}
            className={cn(
              'relative flex flex-col items-center gap-0.5 pt-2 pb-1.5 font-mono text-[10px] tracking-wide uppercase',
              pane === key ? 'text-primary' : 'text-pencil',
            )}
          >
            <Icon className="size-5" aria-hidden />
            <span className={cn(key === 'detalhes' && detailsNew > 0 && 'marker marker-soft text-foreground')}>{label}</span>
            {key === 'agenda' && status && (
              <span className="absolute top-1.5 left-[calc(50%+0.9rem)] font-mono text-[9px] text-pencil">{agenda.length}</span>
            )}
            {key === 'detalhes' && detailsNew > 0 && (
              <>
                <span className="absolute top-2 left-[calc(50%+0.8rem)] size-1.5 rounded-full bg-primary" aria-hidden />
                <span className="sr-only">(resposta nova)</span>
              </>
            )}
          </button>
        ))}
      </nav>

      <EventForm open={formOpen} space={space} llm={status?.llm ?? false} seed={formSeed?.draft ?? null}
        editing={formEditing?.event ?? null} initialDate={formDate}
        onOpenChange={setFormOpen} onSaved={onSaved} />
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

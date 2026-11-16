import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { askQuestion, getAgenda, getStatus } from './api'
import { AgendaPanel } from './components/AgendaPanel'
import { Chat, type ChatMessage } from './components/Chat'
import { DetailsPanel } from './components/DetailsPanel'
import type { AgendaEvent, Status } from './types'

type Tab = 'detalhes' | 'agenda'

export default function App() {
  const [status, setStatus] = useState<Status | null>(null)
  const [agenda, setAgenda] = useState<AgendaEvent[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [pending, setPending] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null) // answer shown on the right page
  const [tab, setTab] = useState<Tab>('detalhes')
  const [focusedEventId, setFocusedEventId] = useState<number | null>(null)
  const nextId = useRef(1)

  function load() {
    setLoadError(null)
    Promise.all([getStatus(), getAgenda()])
      .then(([s, a]) => {
        setStatus(s)
        setAgenda(a)
      })
      .catch((error: Error) => setLoadError(error.message))
  }

  useEffect(load, [])

  async function send(question: string) {
    const text = question.trim()
    if (!text || pending) return
    setMessages((m) => [...m, { id: nextId.current++, role: 'user', text }])
    setPending(true)
    try {
      const answer = await askQuestion(text)
      const id = nextId.current++
      setMessages((m) => [...m, { id, role: 'assistant', answer }])
      setSelectedId(id)
      setTab('detalhes')
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setMessages((m) => [...m, { id: nextId.current++, role: 'error', text: message }])
    } finally {
      setPending(false)
    }
  }

  function showEvent(eventId: number) {
    setFocusedEventId(eventId)
    setTab('agenda')
  }

  const selected = messages.find((m) => m.id === selectedId)
  const selectedAnswer = selected?.role === 'assistant' ? selected.answer : null
  const citedIds = new Set(selectedAnswer?.fontes.map((f) => f.id) ?? [])

  return (
    <div className="mx-auto flex min-h-dvh max-w-[1280px] flex-col gap-5 px-4 py-6 sm:px-6 lg:h-dvh">
      <header className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
        <div>
          <h1 className="font-heading text-[2.6rem] leading-none font-semibold tracking-tight [font-variation-settings:'SOFT'_100,'WONK'_1] sm:text-5xl">
            Agenda <em className="font-medium text-primary">ia</em>
          </h1>
          <p className="mt-2 max-w-xl text-muted-foreground">
            Pergunte em português. Cada resposta diz de qual compromisso saiu.
          </p>
        </div>
        <div className="flex flex-col items-start gap-2 sm:items-end">
          <StatusStamp status={status} />
          {status?.origem === 'exemplo' && (
            <p className="max-w-[19rem] font-hand text-[1.2rem] leading-tight text-primary sm:-rotate-1 sm:text-right">
              agenda fictícia, refeita em volta desta semana. não escreva dados pessoais: a pergunta vai para um modelo de linguagem.
            </p>
          )}
        </div>
      </header>

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

      {/* The notebook, open: conversation on the left page, its explanation on the right. */}
      <main className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden rounded-[10px] bg-card shadow-[0_1px_0_var(--border),0_24px_48px_-24px_rgb(0_0_0/0.45)] ring-1 ring-border lg:grid-cols-[1.15fr_1fr]">
        <div className="flex min-h-0 flex-col lg:shadow-[inset_-22px_0_26px_-26px_rgb(0_0_0/0.4)]">
          <Chat
            messages={messages}
            pending={pending}
            disabled={!status}
            selectedId={selectedId}
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
              {agenda.length > 0 && <span className="font-mono text-[11px] text-pencil">{agenda.length}</span>}
            </TabsTrigger>
          </TabsList>
          <TabsContent value="detalhes" className="paper-plain min-h-0 flex-1">
            <DetailsPanel answer={selectedAnswer} onShowEvent={showEvent} />
          </TabsContent>
          <TabsContent value="agenda" className="min-h-0 flex-1">
            <AgendaPanel events={agenda} citedIds={citedIds} focusedEventId={focusedEventId} />
          </TabsContent>
        </Tabs>
      </main>

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
    </div>
  )
}

function StatusStamp({ status }: { status: Status | null }) {
  if (!status) return <span className="stamp px-2 py-1 text-[11px] text-pencil">conectando…</span>
  const source = status.origem === 'exemplo' ? 'agenda fictícia' : 'agenda do arquivo'
  return (
    <span
      className="stamp -rotate-2 px-2 py-1 text-[10px] whitespace-nowrap text-primary sm:text-[11px]"
      title={status.modelo ?? 'sem modelo de linguagem'}
    >
      {source} · {status.eventos} itens · {status.llm ? 'llm ativo' : 'só busca'}
    </span>
  )
}

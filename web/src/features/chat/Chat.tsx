import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { MicIcon } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { InkLink } from '@/components/ui/skiper-ui/ink-link'
import { SmoothInput } from '@/components/ui/skiper-ui/smooth-input'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { clock, MAX_SECONDS, useRecorder, type Recording } from './recorder'
import { cn, STAMP_BUTTON } from '@/lib/utils'
import type { AgendaEvent, Answer, AskMode } from '@/api/types'
import { AnswerText } from './AnswerText'
import { NoteCard, type NoteState } from './NoteCard'

export type ChatMessage =
  | { id: number; role: 'user'; text: string; via?: 'audio' }
  // note/saved: where a "marca dentista quinta às 15h" reply stands after the person acted on it
  // mode: how the message was sent; offerUsed: the "write it down" link under a "not found" reply was clicked
  | { id: number; role: 'assistant'; answer: Answer; mode: AskMode; note?: NoteState; saved?: AgendaEvent | null; offerUsed?: boolean }
  | { id: number; role: 'error'; text: string }

/** What the sticky note under a reply can do; each takes the reply's message id. */
export interface NoteHandlers {
  confirm: (messageId: number) => Promise<void>
  undo: (messageId: number) => Promise<void>
  edit: (messageId: number) => void
  editSaved: (messageId: number) => void
  dismiss: (messageId: number) => void
  askInstead: (messageId: number) => void
  writeDown: (messageId: number) => void // a "not found" reply to a statement: read it as a new appointment
}

const MAX_CHARS = 300

interface Props {
  messages: ChatMessage[]
  pending: boolean
  disabled: boolean
  selectedId: number | null
  detailsOpen: boolean // the "Como foi respondido" tab is the one showing
  ideas: string[]
  canNote: boolean // a language model is configured, so messages can be read as appointments
  canRecord: boolean // voice messages: the server transcribes and this browser can record
  pendingText: string
  onAudio: (recording: Recording) => void
  emptyNote?: ReactNode
  onSend: (question: string) => void
  onSelect: (messageId: number) => void
  onShowEvent: (eventId: number) => void
  notes: NoteHandlers
}

/** The left page of the notebook: questions in red handwriting, answers typed underneath. */
export function Chat({
  messages, pending, disabled, selectedId, detailsOpen, ideas, canNote, canRecord, pendingText, emptyNote,
  onSend, onSelect, onShowEvent, notes, onAudio,
}: Props) {
  const [draft, setDraft] = useState('')
  const voice = useRecorder(onAudio)
  const listRef = useRef<HTMLDivElement>(null)
  const reduceMotion = useReducedMotion()

  useEffect(() => {
    // Follow the newest entry by scrolling the page itself, never the window.
    const list = listRef.current
    if (list) list.scrollTo({ top: list.scrollHeight, behavior: reduceMotion ? 'auto' : 'smooth' })
  }, [messages, pending, reduceMotion])

  function submit(event?: FormEvent) {
    event?.preventDefault()
    if (!draft.trim() || pending || disabled) return
    onSend(draft)
    setDraft('')
  }

  let questionNumber = 0
  let lastUserText = ''

  return (
    <section aria-label="Conversa" className="flex min-h-0 flex-1 flex-col">
      <div ref={listRef} className="paper flex-1 overflow-y-auto py-[var(--line)]" aria-live="polite">
        {messages.length === 0 && (
          <Line>
            <h2 className="font-heading text-[1.6rem] leading-[var(--line)] font-medium tracking-tight [font-variation-settings:'SOFT'_80]">
              O que você quer saber da agenda?
            </h2>
            {emptyNote}
            {ideas.length > 0 && <p className="leading-[var(--line)] text-muted-foreground">Algumas ideias para começar:</p>}
            <ul>
              {ideas.map((idea) => (
                <li key={idea} className="leading-[var(--line)]">
                  <InkLink
                    disabled={pending || disabled}
                    onClick={() => onSend(idea)}
                    className="font-hand text-[1.45rem] leading-[var(--line)] text-primary"
                  >
                    → {idea}
                  </InkLink>
                </li>
              ))}
            </ul>
          </Line>
        )}

        <AnimatePresence initial={false}>
          {messages.map((message) => {
            const enter = reduceMotion
              ? {}
              : { initial: { opacity: 0, y: 6 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.25 } }

            if (message.role === 'user') {
              questionNumber += 1
              lastUserText = message.text
              return (
                <motion.div key={message.id} {...enter}>
                  <Line margin={<span className="font-mono text-[11px] text-pencil">P.{String(questionNumber).padStart(2, '0')}</span>}>
                    <p className="font-hand text-[1.65rem] leading-[var(--line)] text-primary">
                      {message.text}
                      {message.via === 'audio' && (
                        <span className="ml-2 inline-flex items-center gap-1 align-middle font-mono text-[10px] tracking-wide text-pencil uppercase">
                          <MicIcon className="size-3" aria-hidden /> por áudio
                        </span>
                      )}
                    </p>
                  </Line>
                </motion.div>
              )
            }

            if (message.role === 'error') {
              return (
                <motion.div key={message.id} {...enter}>
                  <Line margin={<span className="font-hand text-2xl text-primary">!</span>}>
                    <p role="alert" className="font-hand text-[1.4rem] leading-[var(--line)] text-primary">
                      {message.text}
                    </p>
                  </Line>
                </motion.div>
              )
            }

            const { answer } = message
            const selected = message.id === selectedId
            // "dentista quinta às 15h na clínica Sorriso" taken as a question and not found: offer to write it down.
            const offerNote =
              canNote && message.mode === 'auto' && !answer.acao && !answer.encontrou && answer.detalhes.tipo !== 'anotar' &&
              !lastUserText.includes('?')
            return (
              <motion.div key={message.id} {...enter}>
                <Line
                  margin={
                    selected ? (
                      <span className="font-hand text-[1.7rem] leading-none text-primary" title="resposta detalhada ao lado">
                        ✓
                      </span>
                    ) : null
                  }
                >
                  <div className="pb-[var(--line)]">
                    <AnswerText text={answer.resposta} />
                    {offerNote && !message.offerUsed && (
                      <p className="leading-[var(--line)]">
                        <InkLink disabled={pending} onClick={() => notes.writeDown(message.id)}
                          className="font-hand text-[1.35rem] text-primary">
                          → não era pergunta? anotar como compromisso
                        </InkLink>
                      </p>
                    )}
                    {answer.acao && (
                      <NoteCard
                        action={answer.acao}
                        state={message.note ?? (answer.acao.tipo === 'anotado' ? 'anotado' : 'aberto')}
                        saved={message.saved ?? answer.acao.evento}
                        onConfirm={() => notes.confirm(message.id)}
                        onUndo={() => notes.undo(message.id)}
                        onEdit={() => notes.edit(message.id)}
                        onEditSaved={() => notes.editSaved(message.id)}
                        onDismiss={() => notes.dismiss(message.id)}
                        onAskInstead={() => notes.askInstead(message.id)}
                        onShowEvent={onShowEvent}
                      />
                    )}
                    {answer.fontes.length > 0 && (
                      <p className="leading-[var(--line)]">
                        <span className="mr-2 font-mono text-[11px] tracking-wide text-pencil uppercase">fontes</span>
                        {answer.fontes.map((source, index) => (
                          <span key={source.id}>
                            {index > 0 && <span className="text-pencil">, </span>}
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <InkLink onClick={() => onShowEvent(source.id)} className="text-[15px]">
                                  <mark className="marker bg-transparent text-inherit">{source.titulo}</mark>
                                </InkLink>
                              </TooltipTrigger>
                              <TooltipContent className="font-mono text-xs">{source.quando}</TooltipContent>
                            </Tooltip>
                          </span>
                        ))}
                      </p>
                    )}
                    <p className="font-mono text-[11px] leading-[var(--line)] text-pencil">
                      {answer.detalhes.segundos.toFixed(1).replace('.', ',')} s ·{' '}
                      {answer.detalhes.tokens.toLocaleString('pt-BR')} tokens ·{' '}
                      <InkLink onClick={() => onSelect(message.id)} className={cn(selected && detailsOpen && 'text-primary')}>
                        {selected && detailsOpen ? 'detalhado ao lado' : 'ver como foi respondido'}
                      </InkLink>
                    </p>
                  </div>
                </Line>
              </motion.div>
            )
          })}
        </AnimatePresence>

        {pending && (
          <Line>
            <p className="font-hand text-[1.4rem] leading-[var(--line)] text-pencil">
              {pendingText}<span className="inline-block animate-pulse">…</span>
            </p>
          </Line>
        )}
      </div>

      <form onSubmit={submit} className="flex items-center gap-3 border-t border-border bg-card py-2 pr-4">
        <label htmlFor="pergunta" className="w-[var(--margin)] shrink-0 text-center font-mono text-[11px] text-pencil">
          P.
        </label>
        {voice.recording ? (
          <>
            <div className="flex min-w-0 flex-1 items-center gap-3" role="status" aria-live="polite">
              <span className="size-2.5 shrink-0 animate-pulse rounded-full bg-primary" aria-hidden />
              <span className="hidden font-hand text-[1.5rem] leading-none text-primary min-[400px]:inline">gravando</span>
              <span className="font-mono text-[12px] whitespace-nowrap text-pencil tabular-nums">
                {clock(voice.seconds)}
                <span className="hidden sm:inline"> / {clock(MAX_SECONDS)}</span>
              </span>
              {/* how loud the microphone hears you: if it stays flat, you are not being heard */}
              <span className="hidden h-1.5 w-16 overflow-hidden rounded-full bg-border sm:block" aria-hidden>
                <span className="block h-full rounded-full bg-primary transition-[width] duration-100"
                  style={{ width: `${Math.min(1, voice.level * 10) * 100}%` }} />
              </span>
              <InkLink onClick={voice.cancel} className="font-mono text-[11px] tracking-wide text-pencil uppercase">
                cancelar
              </InkLink>
            </div>
            <Button type="button" variant="ghost" onClick={voice.stop} className={cn(STAMP_BUTTON, '-rotate-2 px-3 py-1.5 text-[11px]')}>
              Enviar áudio
            </Button>
          </>
        ) : (
          <>
            <SmoothInput
              id="pergunta"
              value={draft}
              maxLength={MAX_CHARS}
              disabled={disabled}
              autoComplete="off"
              placeholder={disabled ? 'abrindo a agenda…' : 'pergunte, ou peça para anotar…'}
              onChange={(event) => setDraft(event.target.value)}
              wrapperClassName="flex-1 min-w-0"
              className="font-hand text-[1.6rem] text-primary placeholder:text-pencil/70"
            />
            <span className="hidden font-mono text-[11px] text-pencil tabular-nums sm:inline">
              {draft.length}/{MAX_CHARS}
            </span>
            {canRecord && (
              <Button type="button" variant="ghost" size="icon" aria-label="Gravar uma mensagem de voz"
                title="Gravar uma mensagem de voz" disabled={pending || disabled} onClick={() => void voice.start()}
                className="shrink-0 rounded-full text-primary hover:bg-primary/10 max-lg:size-11 max-lg:[&_svg]:size-5">
                <MicIcon />
              </Button>
            )}
            <Button
              type="submit"
              variant="ghost"
              disabled={!draft.trim() || pending || disabled}
              className={cn(STAMP_BUTTON, '-rotate-2 px-3 py-1.5 text-[11px]')}
            >
              Enviar
            </Button>
          </>
        )}
      </form>
    </section>
  )
}

/** One entry on the ruled page: something in the margin, the content after the red line. */
function Line({ margin, children }: { margin?: ReactNode; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[var(--margin)_1fr]">
      <div className="flex h-[var(--line)] items-center justify-center">{margin}</div>
      <div className="min-w-0 pr-4 pl-3 sm:pr-6 sm:pl-4">{children}</div>
    </div>
  )
}

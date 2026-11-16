import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { InkLink } from '@/components/ui/skiper-ui/ink-link'
import { SmoothInput } from '@/components/ui/skiper-ui/smooth-input'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn, STAMP_BUTTON } from '@/lib/utils'
import type { Answer } from '../types'
import { AnswerText } from './AnswerText'

export type ChatMessage =
  | { id: number; role: 'user'; text: string }
  | { id: number; role: 'assistant'; answer: Answer }
  | { id: number; role: 'error'; text: string }

const MAX_CHARS = 300

interface Props {
  messages: ChatMessage[]
  pending: boolean
  disabled: boolean
  selectedId: number | null
  ideas: string[]
  emptyNote?: ReactNode
  onSend: (question: string) => void
  onSelect: (messageId: number) => void
  onShowEvent: (eventId: number) => void
}

/** The left page of the notebook: questions in red handwriting, answers typed underneath. */
export function Chat({ messages, pending, disabled, selectedId, ideas, emptyNote, onSend, onSelect, onShowEvent }: Props) {
  const [draft, setDraft] = useState('')
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

  return (
    <section aria-label="Conversa" className="flex min-h-[34rem] flex-1 flex-col lg:min-h-0">
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
              return (
                <motion.div key={message.id} {...enter}>
                  <Line margin={<span className="font-mono text-[11px] text-pencil">P.{String(questionNumber).padStart(2, '0')}</span>}>
                    <p className="font-hand text-[1.65rem] leading-[var(--line)] text-primary">{message.text}</p>
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
                      <InkLink onClick={() => onSelect(message.id)} className={cn(selected && 'text-primary')}>
                        {selected ? 'detalhado ao lado' : 'ver como foi respondido'}
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
              consultando a agenda<span className="inline-block animate-pulse">…</span>
            </p>
          </Line>
        )}
      </div>

      <form onSubmit={submit} className="flex items-center gap-3 border-t border-border bg-card py-2 pr-4">
        <label htmlFor="pergunta" className="w-[var(--margin)] shrink-0 text-center font-mono text-[11px] text-pencil">
          P.
        </label>
        <SmoothInput
          id="pergunta"
          value={draft}
          maxLength={MAX_CHARS}
          disabled={disabled}
          autoComplete="off"
          placeholder={disabled ? 'abrindo a agenda…' : 'escreva sua pergunta…'}
          onChange={(event) => setDraft(event.target.value)}
          wrapperClassName="flex-1 min-w-0"
          className="font-hand text-[1.6rem] text-primary placeholder:text-pencil/70"
        />
        <span className="hidden font-mono text-[11px] text-pencil tabular-nums sm:inline">
          {draft.length}/{MAX_CHARS}
        </span>
        <Button
          type="submit"
          variant="ghost"
          disabled={!draft.trim() || pending || disabled}
          className={cn(STAMP_BUTTON, '-rotate-2 px-3 py-1.5 text-[11px]')}
        >
          Perguntar
        </Button>
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

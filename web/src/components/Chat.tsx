import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import type { Answer } from '../types'
import { AnswerText } from './AnswerText'

export type ChatMessage =
  | { id: number; role: 'user'; text: string }
  | { id: number; role: 'assistant'; answer: Answer }
  | { id: number; role: 'error'; text: string }

const MAX_CHARS = 300

const EXAMPLES = [
  'O que tenho amanhã?',
  'Quando é o dentista?',
  'Quantas aulas de inglês tenho esta semana?',
  'O que preciso levar no veterinário?',
  'O que ficou de ponto a melhorar no meu feedback?',
  'Tenho algo com a padaria semana que vem?',
  'Tenho show marcado?',
]

interface Props {
  messages: ChatMessage[]
  pending: boolean
  disabled: boolean
  selectedId: number | null
  onSend: (question: string) => void
  onSelect: (messageId: number) => void
  onShowEvent: (eventId: number) => void
}

export function Chat({ messages, pending, disabled, selectedId, onSend, onSelect, onShowEvent }: Props) {
  const [draft, setDraft] = useState('')
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // Follow the newest message by scrolling the list itself (scrollIntoView would also move the page).
    const list = listRef.current
    if (list) list.scrollTo({ top: list.scrollHeight, behavior: 'smooth' })
  }, [messages, pending])

  function submit(event?: FormEvent) {
    event?.preventDefault()
    if (!draft.trim() || pending || disabled) return
    onSend(draft)
    setDraft('')
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <section className="chat" aria-label="Conversa">
      <div className="messages" aria-live="polite" ref={listRef}>
        {messages.length === 0 && (
          <div className="empty">
            <p>Experimente uma destas perguntas:</p>
            <div className="examples">
              {EXAMPLES.map((example) => (
                <button key={example} disabled={pending || disabled} onClick={() => onSend(example)}>
                  {example}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((message) => {
          if (message.role === 'user') {
            return (
              <div key={message.id} className="bubble bubble-user">
                {message.text}
              </div>
            )
          }
          if (message.role === 'error') {
            return (
              <div key={message.id} className="bubble bubble-error" role="alert">
                {message.text}
              </div>
            )
          }
          const { answer } = message
          return (
            <div
              key={message.id}
              className={`bubble bubble-assistant${message.id === selectedId ? ' is-selected' : ''}`}
              onClick={() => onSelect(message.id)}
            >
              <AnswerText text={answer.resposta} />
              {answer.fontes.length > 0 && (
                <div className="sources">
                  <span className="sources-label">Fontes:</span>
                  {answer.fontes.map((source) => (
                    <button
                      key={source.id}
                      className="chip"
                      title={source.quando}
                      onClick={(event) => {
                        event.stopPropagation()
                        onShowEvent(source.id)
                      }}
                    >
                      {source.titulo}
                    </button>
                  ))}
                </div>
              )}
              <div className="meta">
                {answer.detalhes.segundos.toFixed(1)} s · {answer.detalhes.tokens.toLocaleString('pt-BR')} tokens
              </div>
            </div>
          )
        })}

        {pending && <div className="bubble bubble-assistant typing">Consultando a agenda…</div>}
      </div>

      <form className="composer" onSubmit={submit}>
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value.slice(0, MAX_CHARS))}
          onKeyDown={onKeyDown}
          placeholder={disabled ? 'Carregando a agenda…' : 'Pergunte sobre a agenda (Enter envia)'}
          rows={2}
          disabled={disabled}
          aria-label="Pergunta"
        />
        <div className="composer-side">
          <span className="counter">
            {draft.length}/{MAX_CHARS}
          </span>
          <button type="submit" disabled={!draft.trim() || pending || disabled}>
            Perguntar
          </button>
        </div>
      </form>
    </section>
  )
}

import type { ReactNode } from 'react'
import { InkLink } from '@/components/ui/skiper-ui/ink-link'
import { periodLabel, QUERY_TYPE_LABEL } from '../format'
import type { Answer } from '../types'

interface Props {
  answer: Answer | null
  onShowEvent: (eventId: number) => void
}

/** Right-hand page: how one answer was put together, in three numbered notes. */
export function DetailsPanel({ answer, onShowEvent }: Props) {
  if (!answer) {
    return (
      <div className="h-full overflow-y-auto px-6 py-[var(--line)]">
        <p className="max-w-sm font-hand text-[1.45rem] leading-[var(--line)] text-pencil">
          faça uma pergunta e eu anoto aqui, passo a passo, como a resposta foi montada.
        </p>
      </div>
    )
  }

  const { detalhes: d } = answer
  const cited = new Set(answer.fontes.map((f) => f.id))

  return (
    <div className="h-full overflow-y-auto px-6 py-[var(--line)] text-[15px] leading-[var(--line)]">
      <Step number="1" title="Como a pergunta foi entendida">
        <dl className="grid grid-cols-[6rem_1fr]">
          <Term>tipo</Term>
          <dd>{QUERY_TYPE_LABEL[d.tipo]}</dd>
          <Term>período</Term>
          <dd>{periodLabel(d)}</dd>
          <Term>termos</Term>
          <dd>{d.busca ?? '—'}</dd>
        </dl>
        {d.reserva && (
          <Note>
            ↳ o modelo não entendeu a pergunta ({d.reserva}), então fiz uma busca simples, sem filtro de datas.
          </Note>
        )}
      </Step>

      <Step number="2" title="O que a busca trouxe">
        <p className="font-mono text-[11px] tracking-wide text-pencil uppercase">
          {d.estrategia} · {d.recuperados.length} compromisso{d.recuperados.length === 1 ? '' : 's'}
        </p>
        {d.recuperados.length > 0 && (
          <ol className="list-none">
            {d.recuperados.map((event, index) => (
              <li key={event.id} className="grid grid-cols-[1.75rem_1fr]">
                <span className="font-mono text-[11px] leading-[var(--line)] text-pencil">{String(index + 1).padStart(2, '0')}</span>
                <span>
                  <InkLink onClick={() => onShowEvent(event.id)}>
                    {cited.has(event.id) ? <mark className="marker bg-transparent text-inherit">{event.titulo}</mark> : event.titulo}
                  </InkLink>
                  <span className="text-muted-foreground"> · {event.quando}</span>
                </span>
              </li>
            ))}
          </ol>
        )}
      </Step>

      <Step number="3" title="Resposta e verificação">
        <p>
          {answer.encontrou
            ? `O modelo citou ${answer.fontes.length} compromisso${answer.fontes.length === 1 ? '' : 's'} (em amarelo), e o código conferiu que todos foram mesmo recuperados.`
            : 'Nenhum compromisso sustentava uma resposta, então nada foi afirmado.'}
        </p>
        {d.guarda && <Note>↳ guarda acionada: {d.guarda}.</Note>}
        <p className="font-mono text-[11px] tracking-wide text-pencil uppercase">
          {d.chamadas} chamada{d.chamadas === 1 ? '' : 's'} ao modelo · {d.tokens.toLocaleString('pt-BR')} tokens ·{' '}
          {d.segundos.toFixed(1).replace('.', ',')} s
        </p>
      </Step>
    </div>
  )
}

function Step({ number, title, children }: { number: string; title: string; children: ReactNode }) {
  return (
    <section className="grid grid-cols-[2.25rem_1fr] pb-[var(--line)]">
      <span className="font-heading text-[2rem] leading-[var(--line)] text-primary italic [font-variation-settings:'WONK'_1]">
        {number}
      </span>
      <div className="min-w-0">
        <h3 className="font-heading text-lg leading-[var(--line)] font-medium tracking-tight">{title}</h3>
        {children}
      </div>
    </section>
  )
}

function Term({ children }: { children: ReactNode }) {
  return <dt className="font-mono text-[11px] leading-[var(--line)] tracking-wide text-pencil uppercase">{children}</dt>
}

function Note({ children }: { children: ReactNode }) {
  return <p className="font-hand text-[1.3rem] leading-[var(--line)] text-primary">{children}</p>
}

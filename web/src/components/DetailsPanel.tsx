import { periodLabel, QUERY_TYPE_LABEL } from '../format'
import type { Answer } from '../types'

interface Props {
  answer: Answer | null
  onShowEvent: (eventId: number) => void
}

export function DetailsPanel({ answer, onShowEvent }: Props) {
  if (!answer) {
    return (
      <div className="panel panel-empty">
        Faça uma pergunta para ver, passo a passo, como a resposta foi montada. Clique numa resposta antiga para ver a
        dela.
      </div>
    )
  }

  const { detalhes: d } = answer
  const cited = new Set(answer.fontes.map((f) => f.id))

  return (
    <div className="panel">
      <section className="step">
        <h3>1. Como a pergunta foi entendida</h3>
        <dl>
          <dt>Tipo</dt>
          <dd>{QUERY_TYPE_LABEL[d.tipo]}</dd>
          <dt>Período</dt>
          <dd>{periodLabel(d)}</dd>
          <dt>Termos</dt>
          <dd>{d.busca ?? '—'}</dd>
        </dl>
        {d.reserva && (
          <p className="note note-warning">
            O modelo não entendeu a pergunta ({d.reserva}); foi feita uma busca simples, sem filtro de datas.
          </p>
        )}
      </section>

      <section className="step">
        <h3>2. O que foi recuperado</h3>
        <p className="muted">
          Estratégia: {d.estrategia} · {d.recuperados.length} compromisso{d.recuperados.length === 1 ? '' : 's'}
        </p>
        {d.recuperados.length > 0 && (
          <ol className="retrieved">
            {d.recuperados.map((event) => (
              <li key={event.id} className={cited.has(event.id) ? 'is-cited' : undefined}>
                <button className="link" onClick={() => onShowEvent(event.id)}>
                  {event.titulo}
                </button>
                <span className="muted"> · {event.quando}</span>
                {cited.has(event.id) && <span className="tag">citado</span>}
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="step">
        <h3>3. Resposta e verificação</h3>
        <p>
          {answer.encontrou
            ? `O modelo citou ${answer.fontes.length} compromisso${answer.fontes.length === 1 ? '' : 's'}, e todas as citações foram conferidas.`
            : 'Nenhum compromisso sustentou uma resposta, então nada foi afirmado.'}
        </p>
        {d.guarda && <p className="note">Guarda acionada: {d.guarda}.</p>}
        <p className="muted">
          {d.chamadas} chamada{d.chamadas === 1 ? '' : 's'} ao modelo · {d.tokens.toLocaleString('pt-BR')} tokens ·{' '}
          {d.segundos.toFixed(1)} s
        </p>
      </section>
    </div>
  )
}

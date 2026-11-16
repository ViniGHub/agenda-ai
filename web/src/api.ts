// The only file that talks to the back-end.
import type { AgendaEvent, Answer, Status } from './types'

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch {
    throw new ApiError('Não consegui falar com o servidor. Ele pode estar acordando; tente de novo em alguns segundos.')
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null
    throw new ApiError(typeof body?.detail === 'string' ? body.detail : `O servidor respondeu com erro ${response.status}.`)
  }
  return (await response.json()) as T
}

export const getStatus = () => request<Status>('/api/status')

export const getAgenda = () => request<AgendaEvent[]>('/api/agenda')

export const askQuestion = (pergunta: string) =>
  request<Answer>('/api/perguntar', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pergunta }),
  })

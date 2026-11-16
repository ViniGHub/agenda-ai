// The only file that talks to the back-end.
import type { AgendaEvent, Answer, Config, Me, Space, Status } from './types'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      credentials: 'same-origin',
      // The API refuses cookie-authenticated POSTs without this header (cross-site protection).
      headers: { 'X-Requested-With': 'agenda-ia', ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
    })
  } catch {
    throw new ApiError('Não consegui falar com o servidor. Ele pode estar acordando; tente de novo em alguns segundos.', 0)
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null
    throw new ApiError(typeof body?.detail === 'string' ? body.detail : `O servidor respondeu com erro ${response.status}.`, response.status)
  }
  return (await response.json()) as T
}

const post = <T>(path: string, body: unknown = {}) => request<T>(path, { method: 'POST', body: JSON.stringify(body) })

export const getConfig = () => request<Config>('/api/config')
export const getMe = () => request<Me>('/api/me')
export const login = (credential: string) => post<Me>('/api/login', { credential })
export const logout = () => post<Me>('/api/logout')

export const getStatus = (space: Space) => request<Status>(`/api/${space}/status`)
export const getAgenda = (space: Space) => request<AgendaEvent[]>(`/api/${space}/agenda`)
export const askQuestion = (space: Space, pergunta: string) => post<Answer>(`/api/${space}/perguntar`, { pergunta })

import { fetchAPI } from './client'

export interface BreadthPoint {
  t: string
  up: number
  down: number
  flat: number
  limit_up: number
  limit_down: number
  total: number
  up_pct: number | null
}

export interface BreadthResponse {
  market: string
  trade_date: string
  current: BreadthPoint | null
  series: BreadthPoint[]
}

export interface BreadthAlertRule {
  id: number
  market: string
  name: string
  enabled: boolean
  condition_type: 'breadth_up_pct' | 'breadth_up_pct_change' | string
  operator: string
  threshold: number
  lookback_minutes: number
  market_hours_mode: 'always' | 'trading_only' | string
  cooldown_minutes: number
  max_triggers_per_day: number
  repeat_mode: 'once' | 'repeat' | string
  expire_at: string | null
  notify_channel_ids: number[]
  last_trigger_at: string | null
  trigger_count_today: number
  trigger_date: string
}

export interface BreadthAlertHit {
  id: number
  rule_id: number
  trigger_time: string | null
  trigger_bucket: string
  trigger_snapshot: Record<string, unknown>
  notify_success: boolean
  notify_error: string
}

export interface BreadthAlertCreatePayload {
  market?: string
  name?: string
  enabled?: boolean
  condition_type: string
  operator: string
  threshold: number
  lookback_minutes?: number
  market_hours_mode?: string
  cooldown_minutes?: number
  max_triggers_per_day?: number
  repeat_mode?: string
  expire_at?: string | null
  notify_channel_ids?: number[]
}

export const marketApi = {
  breadth: (params?: { market?: string; date?: string }) =>
    fetchAPI<BreadthResponse>(
      `/market/breadth${params?.date || params?.market ? `?${new URLSearchParams(
        Object.entries({
          market: params?.market || 'CN',
          date: params?.date || '',
        }).filter(([, v]) => Boolean(v)) as [string, string][],
      ).toString()}` : ''}`,
    ),
  listBreadthAlerts: () => fetchAPI<BreadthAlertRule[]>('/breadth-alerts'),
  createBreadthAlert: (body: BreadthAlertCreatePayload) =>
    fetchAPI<BreadthAlertRule>('/breadth-alerts', { method: 'POST', body: JSON.stringify(body) }),
  updateBreadthAlert: (id: number, body: Partial<BreadthAlertCreatePayload & { enabled?: boolean }>) =>
    fetchAPI<BreadthAlertRule>(`/breadth-alerts/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  toggleBreadthAlert: (id: number, enabled: boolean) =>
    fetchAPI<BreadthAlertRule>(`/breadth-alerts/${id}/toggle`, {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }),
  deleteBreadthAlert: (id: number) =>
    fetchAPI<{ ok: boolean }>(`/breadth-alerts/${id}`, { method: 'DELETE' }),
  listBreadthHits: (id: number, limit = 20) =>
    fetchAPI<BreadthAlertHit[]>(`/breadth-alerts/${id}/hits?limit=${limit}`),
  sampleOnce: () =>
    fetchAPI<{ sampled: boolean; sample: BreadthPoint; triggered: unknown }>('/breadth-alerts/sample-once', {
      method: 'POST',
      body: '{}',
    }),
}

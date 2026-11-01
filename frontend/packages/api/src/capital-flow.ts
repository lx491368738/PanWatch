import { fetchAPI } from './client'

export interface CapitalFlowIntradayPoint {
  t: string
  main_net_inflow: number
  delta: number | null
}

export interface CapitalFlowIntradayResponse {
  symbol: string
  market: string
  eligible: boolean
  trade_date: string
  in_session: boolean
  interval_sec: number
  sampled: boolean
  stagnant: boolean
  point_count: number
  latest: CapitalFlowIntradayPoint | null
  points: CapitalFlowIntradayPoint[]
  reason?: string
}

export const capitalFlowApi = {
  intraday: (symbol: string, opts?: { sample?: boolean }) =>
    fetchAPI<CapitalFlowIntradayResponse>(
      `/capital-flow/${encodeURIComponent(symbol)}/intraday${opts?.sample ? '?sample=true' : ''}`
    ),
}

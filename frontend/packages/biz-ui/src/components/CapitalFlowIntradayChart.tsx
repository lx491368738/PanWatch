import { useId, useMemo } from 'react'
import { useMarketColors } from '@/hooks/use-market-colors'
import { marketSignTextClass } from '@/lib/market-colors'
import type { CapitalFlowIntradayPoint } from '@panwatch/api'

function formatFlow(value: number | null | undefined, english = false): string {
  if (value == null || !Number.isFinite(value)) return '--'
  const n = Number(value)
  const abs = Math.abs(n)
  const sign = n > 0 ? '+' : n < 0 ? '-' : ''
  const body = (() => {
    if (english) {
      if (abs >= 1e9) return `${(abs / 1e9).toFixed(2)}B`
      if (abs >= 1e6) return `${(abs / 1e6).toFixed(2)}M`
      if (abs >= 1e3) return `${(abs / 1e3).toFixed(2)}K`
      return abs.toFixed(0)
    }
    if (abs >= 1e8) return `${(abs / 1e8).toFixed(2)}亿`
    if (abs >= 1e4) return `${(abs / 1e4).toFixed(2)}万`
    return abs.toFixed(0)
  })()
  return `${sign}${body}`
}

function DualLineChart(props: {
  cumulative: number[]
  deltas: number[]
  height?: number
}) {
  const gradId = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const { palette } = useMarketColors()
  const width = 320
  const height = props.height ?? 120
  const cum = props.cumulative
  const n = cum.length
  if (n < 2) return null

  let min = Math.min(...cum)
  let max = Math.max(...cum)
  if (max - min < 1e-9) {
    const pad = Math.abs(max) * 0.02 || 1
    max += pad
    min -= pad
  }
  const padY = 10
  const innerH = height - padY * 2
  const xAt = (i: number) => (width * i) / (n - 1)
  const yAt = (v: number) => padY + innerH - (innerH * (v - min)) / (max - min)
  const points = cum.map((v, i) => `${xAt(i).toFixed(2)},${yAt(v).toFixed(2)}`).join(' ')
  const last = cum[n - 1]
  const stroke = last >= cum[0] ? palette.up.bright : palette.down.bright

  // delta bars sit in the lower band around a zero baseline
  const barBandTop = height * 0.72
  const barBandH = height * 0.24
  const zeroY = barBandTop + barBandH / 2
  const deltas = props.deltas
  const maxAbsDelta = Math.max(1, ...deltas.map((d) => Math.abs(d || 0)))

  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" width="100%" height={height} className="block">
      <defs>
        <linearGradient id={`cf-fill-${gradId}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity={0.28} />
          <stop offset="100%" stopColor={stroke} stopOpacity={0} />
        </linearGradient>
      </defs>
      <polygon
        points={`${xAt(0).toFixed(2)},${barBandTop} ${points} ${xAt(n - 1).toFixed(2)},${barBandTop}`}
        fill={`url(#cf-fill-${gradId})`}
        stroke="none"
      />
      <polyline
        points={points}
        fill="none"
        stroke={stroke}
        strokeWidth={1.6}
        vectorEffect="non-scaling-stroke"
      />
      <line x1={0} y1={zeroY} x2={width} y2={zeroY} stroke="currentColor" strokeOpacity={0.2} strokeWidth={1} />
      {deltas.map((d, i) => {
        if (i === 0 || d == null || !Number.isFinite(d) || d === 0) return null
        const mag = (Math.abs(d) / maxAbsDelta) * (barBandH / 2)
        const x = xAt(i)
        const w = Math.max(1.2, (width / n) * 0.45)
        const color = d >= 0 ? palette.up.bright : palette.down.bright
        return (
          <rect
            key={i}
            x={x - w / 2}
            y={d >= 0 ? zeroY - mag : zeroY}
            width={w}
            height={Math.max(1, mag)}
            fill={color}
            opacity={0.55}
          />
        )
      })}
    </svg>
  )
}

export default function CapitalFlowIntradayChart(props: {
  title: string
  cumulativeLabel: string
  deltaLabel: string
  emptyLabel: string
  stagnantLabel: string
  ineligibleLabel: string
  loading?: boolean
  english?: boolean
  eligible: boolean | null
  stagnant?: boolean
  points: CapitalFlowIntradayPoint[]
  latest: CapitalFlowIntradayPoint | null
}) {
  const cum = useMemo(() => props.points.map((p) => p.main_net_inflow), [props.points])
  const deltas = useMemo(() => props.points.map((p) => Number(p.delta ?? 0)), [props.points])

  if (props.eligible === false) {
    return (
      <div className="card p-4">
        <div className="text-[12px] text-muted-foreground mb-2">{props.title}</div>
        <div className="text-[11px] text-muted-foreground py-6 text-center">{props.ineligibleLabel}</div>
      </div>
    )
  }

  return (
    <div className="card p-4">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div>
          <div className="text-[12px] text-muted-foreground">{props.title}</div>
          <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[12px]">
            <span>
              <span className="text-muted-foreground">{props.cumulativeLabel}</span>{' '}
              <span className={`font-mono ${marketSignTextClass(props.latest?.main_net_inflow ?? null)}`}>
                {formatFlow(props.latest?.main_net_inflow, props.english)}
              </span>
            </span>
            <span>
              <span className="text-muted-foreground">{props.deltaLabel}</span>{' '}
              <span className={`font-mono ${marketSignTextClass(props.latest?.delta ?? null)}`}>
                {formatFlow(props.latest?.delta, props.english)}
              </span>
            </span>
          </div>
        </div>
        {props.stagnant && (
          <div className="text-[10px] text-amber-600 dark:text-amber-400 shrink-0">{props.stagnantLabel}</div>
        )}
      </div>
      {props.loading && props.points.length === 0 ? (
        <div className="h-[120px] rounded bg-accent/30 animate-pulse" />
      ) : props.points.length >= 2 ? (
        <DualLineChart cumulative={cum} deltas={deltas} />
      ) : (
        <div className="h-[120px] text-[11px] text-muted-foreground flex items-center justify-center">
          {props.emptyLabel}
        </div>
      )}
    </div>
  )
}

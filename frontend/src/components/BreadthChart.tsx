import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { BreadthPoint } from '@panwatch/api'

interface BreadthChartProps {
  series: BreadthPoint[]
  height?: number
  className?: string
  ariaLabel?: string
  labels?: {
    up: string
    upPct: string
  }
}

const GRID_FRACS = [0.25, 0.5, 0.75]

function timeLabel(iso: string): string {
  const t = iso.slice(11, 19)
  return t.length >= 5 ? t.slice(0, 5) : iso
}

export default function BreadthChart({
  series,
  height = 220,
  className,
  ariaLabel = 'Market breadth up ratio',
  labels = { up: '上涨', upPct: '占比' },
}: BreadthChartProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(640)
  const [hoverIndex, setHoverIndex] = useState<number | null>(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => setWidth(Math.max(280, el.clientWidth))
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const points = useMemo(
    () => series.filter(p => typeof p.up_pct === 'number') as Array<BreadthPoint & { up_pct: number }>,
    [series],
  )

  const padLeft = 8
  const padRight = 44
  const padTop = 12
  const padBottom = 28
  const innerW = Math.max(10, width - padLeft - padRight)
  const innerH = Math.max(10, height - padTop - padBottom)

  const layout = useMemo(() => {
    if (points.length < 2) return null
    let min = Math.min(...points.map(p => p.up_pct))
    let max = Math.max(...points.map(p => p.up_pct))
    if (max - min < 1e-6) {
      max += 1
      min -= 1
    }
    const n = points.length
    const xAt = (i: number) => padLeft + (innerW * i) / (n - 1)
    const yAt = (v: number) => padTop + innerH - (innerH * (v - min)) / (max - min)
    return { min, max, n, xAt, yAt }
  }, [points, innerW, innerH, padLeft, padTop])

  if (!layout) {
    return (
      <div ref={ref} className={className} style={{ height }}>
        <div className="h-full flex items-center justify-center text-sm text-muted-foreground">—</div>
      </div>
    )
  }

  const { min, max, n, xAt, yAt } = layout
  const line = points.map((p, i) => `${xAt(i).toFixed(1)},${yAt(p.up_pct).toFixed(1)}`).join(' ')
  const baseline = padTop + innerH
  const area = `${xAt(0).toFixed(1)},${baseline.toFixed(1)} ${line} ${xAt(n - 1).toFixed(1)},${baseline.toFixed(1)}`
  const firstLabel = timeLabel(points[0].t)
  const lastLabel = timeLabel(points[n - 1].t)
  const midLabel = timeLabel(points[Math.floor(n / 2)].t)
  const hover = hoverIndex != null ? points[hoverIndex] : null
  const hoverX = hoverIndex != null ? xAt(hoverIndex) : 0
  const hoverY = hover ? yAt(hover.up_pct) : 0

  const onMove = (clientX: number) => {
    const el = ref.current
    if (!el || n < 2) return
    const rect = el.getBoundingClientRect()
    const x = clientX - rect.left
    const ratio = (x - padLeft) / innerW
    const idx = Math.max(0, Math.min(n - 1, Math.round(ratio * (n - 1))))
    setHoverIndex(idx)
  }

  // Keep tooltip inside chart: prefer right of cursor, flip near right edge.
  const tipWidth = 128
  const tipLeft = hover
    ? Math.max(8, Math.min(hoverX + 12, width - tipWidth - 8))
    : 0
  const tipTop = hover ? Math.max(8, Math.min(hoverY - 48, height - 64)) : 0

  return (
    <div
      ref={ref}
      className={`relative ${className || ''}`}
      onMouseLeave={() => setHoverIndex(null)}
      onMouseMove={e => onMove(e.clientX)}
      onTouchStart={e => {
        if (e.touches[0]) onMove(e.touches[0].clientX)
      }}
      onTouchMove={e => {
        if (e.touches[0]) onMove(e.touches[0].clientX)
      }}
      onTouchEnd={() => setHoverIndex(null)}
    >
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
        <defs>
          <linearGradient id="breadth-area" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.22} />
            <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
          </linearGradient>
        </defs>
        {GRID_FRACS.map(frac => {
          const y = padTop + innerH * frac
          const v = min + (max - min) * (1 - frac)
          return (
            <g key={frac}>
              <line
                x1={padLeft}
                x2={padLeft + innerW}
                y1={y}
                y2={y}
                stroke="hsl(var(--border))"
                strokeWidth={1}
                strokeDasharray="4 4"
              />
              <text
                x={padLeft + innerW + 6}
                y={y}
                dominantBaseline="middle"
                fontSize={10}
                fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                fill="hsl(var(--muted-foreground))"
              >
                {v.toFixed(0)}%
              </text>
            </g>
          )
        })}
        <polygon points={area} fill="url(#breadth-area)" />
        <polyline points={line} fill="none" stroke="hsl(var(--primary))" strokeWidth={2} />
        {hover && (
          <g pointerEvents="none">
            <line
              x1={hoverX}
              x2={hoverX}
              y1={padTop}
              y2={padTop + innerH}
              stroke="hsl(var(--muted-foreground))"
              strokeWidth={1}
              strokeDasharray="3 3"
              opacity={0.7}
            />
            <circle cx={hoverX} cy={hoverY} r={3.5} fill="hsl(var(--primary))" stroke="hsl(var(--card))" strokeWidth={1.5} />
          </g>
        )}
        <text x={padLeft} y={height - 8} fontSize={10} fill="hsl(var(--muted-foreground))">{firstLabel}</text>
        <text
          x={padLeft + innerW / 2}
          y={height - 8}
          textAnchor="middle"
          fontSize={10}
          fill="hsl(var(--muted-foreground))"
        >
          {midLabel}
        </text>
        <text
          x={padLeft + innerW}
          y={height - 8}
          textAnchor="end"
          fontSize={10}
          fill="hsl(var(--muted-foreground))"
        >
          {lastLabel}
        </text>
        {/* Invisible hit area for easier hover */}
        <rect
          x={padLeft}
          y={padTop}
          width={innerW}
          height={innerH}
          fill="transparent"
        />
      </svg>
      {hover && (
        <div
          className="pointer-events-none absolute z-10 rounded-md border bg-card/95 px-2.5 py-1.5 text-[11px] shadow-sm backdrop-blur-sm"
          style={{ left: tipLeft, top: tipTop, minWidth: tipWidth }}
        >
          <div className="text-muted-foreground tabular-nums">{timeLabel(hover.t)}</div>
          <div className="mt-0.5 font-medium tabular-nums text-foreground">
            {labels.up} {hover.up}
          </div>
          <div className="tabular-nums text-muted-foreground">
            {labels.upPct} {hover.up_pct.toFixed(1)}%
          </div>
        </div>
      )}
    </div>
  )
}

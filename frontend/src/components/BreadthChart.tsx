import { useLayoutEffect, useRef, useState } from 'react'
import type { BreadthPoint } from '@panwatch/api'

interface BreadthChartProps {
  series: BreadthPoint[]
  height?: number
  className?: string
  ariaLabel?: string
}

const GRID_FRACS = [0.25, 0.5, 0.75]

export default function BreadthChart({
  series,
  height = 220,
  className,
  ariaLabel = 'Market breadth up ratio',
}: BreadthChartProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(640)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => setWidth(Math.max(280, el.clientWidth))
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const points = series.filter(p => typeof p.up_pct === 'number') as Array<BreadthPoint & { up_pct: number }>
  if (points.length < 2) {
    return (
      <div ref={ref} className={className} style={{ height }}>
        <div className="h-full flex items-center justify-center text-sm text-muted-foreground">—</div>
      </div>
    )
  }

  const padLeft = 8
  const padRight = 44
  const padTop = 12
  const padBottom = 28
  const innerW = Math.max(10, width - padLeft - padRight)
  const innerH = Math.max(10, height - padTop - padBottom)
  let min = Math.min(...points.map(p => p.up_pct))
  let max = Math.max(...points.map(p => p.up_pct))
  if (max - min < 1e-6) {
    max += 1
    min -= 1
  }
  const n = points.length
  const xAt = (i: number) => padLeft + (innerW * i) / (n - 1)
  const yAt = (v: number) => padTop + innerH - (innerH * (v - min)) / (max - min)
  const line = points.map((p, i) => `${xAt(i).toFixed(1)},${yAt(p.up_pct).toFixed(1)}`).join(' ')
  const baseline = padTop + innerH
  const area = `${xAt(0).toFixed(1)},${baseline.toFixed(1)} ${line} ${xAt(n - 1).toFixed(1)},${baseline.toFixed(1)}`
  const firstLabel = points[0].t.slice(11, 16)
  const lastLabel = points[n - 1].t.slice(11, 16)
  const midLabel = points[Math.floor(n / 2)].t.slice(11, 16)

  return (
    <div ref={ref} className={className}>
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
      </svg>
    </div>
  )
}

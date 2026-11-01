import { useConfirm } from '@panwatch/base-ui/components/ui/confirm-dialog'
import { useEffect, useMemo, useState } from 'react'
import { Plus, RefreshCw, Trash2, TrendingUp } from 'lucide-react'
import {
  fetchAPI,
  marketApi,
  type BreadthAlertRule,
  type BreadthPoint,
  type BreadthResponse,
  type NotifyChannel,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { useTranslation } from 'react-i18next'
import BreadthChart from '@/components/BreadthChart'
import { getCurrentLocale } from '@/i18n'

interface FormState {
  name: string
  condition_type: 'breadth_up_pct' | 'breadth_up_pct_change'
  operator: string
  threshold: number
  lookback_minutes: number
  cooldown_minutes: number
  max_triggers_per_day: number
  repeat_mode: 'once' | 'repeat'
  market_hours_mode: 'always' | 'trading_only'
  notify_channel_ids: number[]
}

const DEFAULT_FORM: FormState = {
  name: '',
  condition_type: 'breadth_up_pct',
  operator: '<',
  threshold: 30,
  lookback_minutes: 1,
  cooldown_minutes: 30,
  max_triggers_per_day: 3,
  repeat_mode: 'repeat',
  market_hours_mode: 'trading_only',
  notify_channel_ids: [],
}

function fmt(iso?: string | null): string {
  if (!iso) return '--'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--'
  return d.toLocaleString(getCurrentLocale(), {
    hour12: false,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function kpi(value: number | null | undefined, suffix = '') {
  if (value === null || value === undefined) return '--'
  return `${value}${suffix}`
}

export default function MarketEmotionPage() {
  const { t } = useTranslation('configuration')
  const tr = (key: string, options?: Record<string, unknown>) =>
    (t as unknown as (k: string, o?: Record<string, unknown>) => string)(`marketEmotion.${key}`, options)
  const { toast } = useToast()
  const confirmAction = useConfirm()

  const [loading, setLoading] = useState(true)
  const [breadth, setBreadth] = useState<BreadthResponse | null>(null)
  const [rules, setRules] = useState<BreadthAlertRule[]>([])
  const [channels, setChannels] = useState<NotifyChannel[]>([])
  const [formOpen, setFormOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<FormState>(DEFAULT_FORM)
  const [saving, setSaving] = useState(false)
  const [sampling, setSampling] = useState(false)

  const current: BreadthPoint | null = breadth?.current || null
  const series = breadth?.series || []

  const conditionLabel = useMemo(
    () => ({
      breadth_up_pct: tr('conditions.upPct'),
      breadth_up_pct_change: tr('conditions.upPctChange'),
    }),
    [t],
  )

  const load = async () => {
    setLoading(true)
    try {
      const [b, r, c] = await Promise.all([
        marketApi.breadth({ market: 'CN' }),
        marketApi.listBreadthAlerts(),
        fetchAPI<NotifyChannel[]>('/channels'),
      ])
      setBreadth(b)
      setRules(r || [])
      setChannels(c || [])
    } catch (e) {
      toast(e instanceof Error ? e.message : tr('messages.loadFailed'), 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const openCreate = () => {
    setEditingId(null)
    setForm(DEFAULT_FORM)
    setFormOpen(true)
  }

  const openEdit = (rule: BreadthAlertRule) => {
    setEditingId(rule.id)
    setForm({
      name: rule.name || '',
      condition_type: (rule.condition_type as FormState['condition_type']) || 'breadth_up_pct',
      operator: rule.operator || '<',
      threshold: rule.threshold,
      lookback_minutes: rule.lookback_minutes || 1,
      cooldown_minutes: rule.cooldown_minutes,
      max_triggers_per_day: rule.max_triggers_per_day,
      repeat_mode: (rule.repeat_mode as FormState['repeat_mode']) || 'repeat',
      market_hours_mode: (rule.market_hours_mode as FormState['market_hours_mode']) || 'trading_only',
      notify_channel_ids: rule.notify_channel_ids || [],
    })
    setFormOpen(true)
  }

  const save = async () => {
    setSaving(true)
    try {
      const payload = {
        name: form.name.trim(),
        condition_type: form.condition_type,
        operator: form.operator,
        threshold: Number(form.threshold),
        lookback_minutes: Number(form.lookback_minutes),
        cooldown_minutes: Number(form.cooldown_minutes),
        max_triggers_per_day: Number(form.max_triggers_per_day),
        repeat_mode: form.repeat_mode,
        market_hours_mode: form.market_hours_mode,
        notify_channel_ids: form.notify_channel_ids,
      }
      if (editingId) {
        await marketApi.updateBreadthAlert(editingId, payload)
      } else {
        await marketApi.createBreadthAlert(payload)
      }
      toast(tr('messages.saved'), 'success')
      setFormOpen(false)
      await load()
    } catch (e) {
      toast(e instanceof Error ? e.message : tr('messages.saveFailed'), 'error')
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (rule: BreadthAlertRule) => {
    try {
      await marketApi.toggleBreadthAlert(rule.id, !rule.enabled)
      await load()
    } catch (e) {
      toast(e instanceof Error ? e.message : tr('messages.toggleFailed'), 'error')
    }
  }

  const remove = async (rule: BreadthAlertRule) => {
    const ok = await confirmAction(tr('messages.deleteConfirm', { name: rule.name || `#${rule.id}` }), {
      destructive: true,
    })
    if (!ok) return
    try {
      await marketApi.deleteBreadthAlert(rule.id)
      toast(tr('messages.deleted'), 'success')
      await load()
    } catch (e) {
      toast(e instanceof Error ? e.message : tr('messages.deleteFailed'), 'error')
    }
  }

  const sampleOnce = async () => {
    setSampling(true)
    try {
      await marketApi.sampleOnce()
      toast(tr('messages.sampled'), 'success')
      await load()
    } catch (e) {
      toast(e instanceof Error ? e.message : tr('messages.sampleFailed'), 'error')
    } finally {
      setSampling(false)
    }
  }

  const toggleChannel = (id: number) => {
    setForm(prev => ({
      ...prev,
      notify_channel_ids: prev.notify_channel_ids.includes(id)
        ? prev.notify_channel_ids.filter(x => x !== id)
        : [...prev.notify_channel_ids, id],
    }))
  }

  return (
    <div className="space-y-6 p-4 md:p-6 max-w-5xl mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-primary" />
            {tr('title')}
          </h1>
          <p className="text-sm text-muted-foreground mt-1">{tr('description')}</p>
          {breadth?.trade_date ? (
            <p className="text-xs text-muted-foreground mt-1">{tr('tradeDate', { date: breadth.trade_date })}</p>
          ) : null}
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
            {tr('refresh')}
          </Button>
          <Button variant="outline" size="sm" onClick={() => void sampleOnce()} disabled={sampling}>
            {sampling ? tr('sampling') : tr('sampleOnce')}
          </Button>
          <Button size="sm" onClick={openCreate}>
            <Plus className="w-4 h-4 mr-1" />
            {tr('create')}
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        {[
          { label: tr('kpi.upPct'), value: kpi(current?.up_pct, '%') },
          { label: tr('kpi.up'), value: kpi(current?.up) },
          { label: tr('kpi.down'), value: kpi(current?.down) },
          { label: tr('kpi.flat'), value: kpi(current?.flat) },
          { label: tr('kpi.limitUp'), value: kpi(current?.limit_up) },
          { label: tr('kpi.limitDown'), value: kpi(current?.limit_down) },
        ].map(item => (
          <div key={item.label} className="rounded-lg border bg-card p-3">
            <div className="text-xs text-muted-foreground">{item.label}</div>
            <div className="text-lg font-semibold tabular-nums mt-1">{item.value}</div>
          </div>
        ))}
      </div>

      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-medium">{tr('chartTitle')}</h2>
          <span className="text-xs text-muted-foreground">{tr('points', { count: series.length })}</span>
        </div>
        {loading ? (
          <div className="h-[220px] flex items-center justify-center text-sm text-muted-foreground">{tr('loading')}</div>
        ) : (
          <BreadthChart series={series} ariaLabel={tr('chartTitle')} />
        )}
        <p className="text-xs text-muted-foreground mt-2">{tr('footnote')}</p>
      </div>

      <div className="space-y-3">
        <h2 className="text-sm font-medium">{tr('rulesTitle')}</h2>
        {rules.length === 0 ? (
          <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
            {tr('emptyRules')}
          </div>
        ) : (
          rules.map(rule => (
            <div key={rule.id} className="rounded-lg border bg-card p-4 flex flex-wrap gap-3 justify-between">
              <div className="min-w-0 space-y-1">
                <div className="font-medium flex items-center gap-2">
                  <span>{rule.name || `#${rule.id}`}</span>
                  <span className={`text-xs px-1.5 py-0.5 rounded ${rule.enabled ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'}`}>
                    {rule.enabled ? tr('enabled') : tr('paused')}
                  </span>
                </div>
                <div className="text-sm text-muted-foreground">
                  {conditionLabel[rule.condition_type as keyof typeof conditionLabel] || rule.condition_type}
                  {' '}
                  {rule.operator}
                  {' '}
                  {rule.threshold}
                  {rule.condition_type === 'breadth_up_pct_change' ? 'pp' : '%'}
                  {rule.condition_type === 'breadth_up_pct_change' ? ` · ${rule.lookback_minutes}m` : ''}
                </div>
                <div className="text-xs text-muted-foreground">
                  {tr('cooldown', { minutes: rule.cooldown_minutes })}
                  {' · '}
                  {tr('dailyLimit', { count: rule.max_triggers_per_day })}
                  {' · '}
                  {tr('lastTrigger', { time: fmt(rule.last_trigger_at) })}
                </div>
              </div>
              <div className="flex gap-2 items-start">
                <Button variant="outline" size="sm" onClick={() => void toggle(rule)}>
                  {rule.enabled ? tr('disable') : tr('enable')}
                </Button>
                <Button variant="outline" size="sm" onClick={() => openEdit(rule)}>
                  {tr('edit')}
                </Button>
                <Button variant="outline" size="sm" onClick={() => void remove(rule)}>
                  <Trash2 className="w-4 h-4" />
                </Button>
              </div>
            </div>
          ))
        )}
      </div>

      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingId ? tr('editor.editTitle') : tr('editor.createTitle')}</DialogTitle>
            <DialogDescription>{tr('editor.description')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <label className="block text-sm space-y-1">
              <span>{tr('form.name')}</span>
              <input
                className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                value={form.name}
                onChange={e => setForm(prev => ({ ...prev, name: e.target.value }))}
                placeholder={tr('form.namePlaceholder')}
              />
            </label>
            <label className="block text-sm space-y-1">
              <span>{tr('form.conditionType')}</span>
              <select
                className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                value={form.condition_type}
                onChange={e =>
                  setForm(prev => ({
                    ...prev,
                    condition_type: e.target.value as FormState['condition_type'],
                    operator: e.target.value === 'breadth_up_pct_change' ? '<=' : '<',
                    threshold: e.target.value === 'breadth_up_pct_change' ? -5 : 30,
                    lookback_minutes: e.target.value === 'breadth_up_pct_change' ? 1 : prev.lookback_minutes,
                  }))
                }
              >
                <option value="breadth_up_pct">{tr('conditions.upPct')}</option>
                <option value="breadth_up_pct_change">{tr('conditions.upPctChange')}</option>
              </select>
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm space-y-1">
                <span>{tr('form.operator')}</span>
                <select
                  className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                  value={form.operator}
                  onChange={e => setForm(prev => ({ ...prev, operator: e.target.value }))}
                >
                  {['<', '<=', '>', '>='].map(op => (
                    <option key={op} value={op}>{op}</option>
                  ))}
                </select>
              </label>
              <label className="block text-sm space-y-1">
                <span>{tr('form.threshold')}</span>
                <input
                  type="number"
                  className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                  value={form.threshold}
                  onChange={e => setForm(prev => ({ ...prev, threshold: Number(e.target.value) }))}
                />
              </label>
            </div>
            {form.condition_type === 'breadth_up_pct_change' ? (
              <label className="block text-sm space-y-1">
                <span>{tr('form.lookback')}</span>
                <input
                  type="number"
                  min={1}
                  className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                  value={form.lookback_minutes}
                  onChange={e => setForm(prev => ({ ...prev, lookback_minutes: Number(e.target.value) }))}
                />
              </label>
            ) : null}
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm space-y-1">
                <span>{tr('form.cooldown')}</span>
                <input
                  type="number"
                  min={0}
                  className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                  value={form.cooldown_minutes}
                  onChange={e => setForm(prev => ({ ...prev, cooldown_minutes: Number(e.target.value) }))}
                />
              </label>
              <label className="block text-sm space-y-1">
                <span>{tr('form.dailyLimit')}</span>
                <input
                  type="number"
                  min={0}
                  className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                  value={form.max_triggers_per_day}
                  onChange={e => setForm(prev => ({ ...prev, max_triggers_per_day: Number(e.target.value) }))}
                />
              </label>
            </div>
            <div className="space-y-2">
              <div className="text-sm">{tr('form.channels')}</div>
              {channels.length === 0 ? (
                <p className="text-xs text-muted-foreground">{tr('form.noChannels')}</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {channels.filter(c => c.enabled).map(c => (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => toggleChannel(c.id)}
                      className={`text-xs px-2 py-1 rounded border ${
                        form.notify_channel_ids.includes(c.id)
                          ? 'bg-primary text-primary-foreground border-primary'
                          : 'bg-background'
                      }`}
                    >
                      {c.name || `#${c.id}`}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setFormOpen(false)}>{tr('form.cancel')}</Button>
              <Button onClick={() => void save()} disabled={saving}>
                {saving ? tr('form.saving') : tr('form.submit')}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

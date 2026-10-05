import { Fragment, useEffect, useRef, useState, type FormEvent } from 'react';

type Predicate = {
  id?: string; type: 'threshold' | 'open_interest_change'; metric?: string;
  operator: 'gt' | 'gte' | 'lt' | 'lte'; threshold: string;
  window_seconds?: number; mode?: 'percent' | 'absolute';
};
type AlertRow = {
  id: string; alert_id: string; name: string; version: number;
  market: { network: string; dex: string; coin: string };
  predicates: Predicate[]; combinator: 'all' | 'any';
  persistence_seconds: number; cooldown_seconds: number; quality_policy: string;
  status?: string; monitoring?: string; condition?: string; quality?: string; evaluated_at?: string;
};
type AlertPage = { items: AlertRow[]; has_more: boolean };
type EvidenceItem = { id?: string; check?: string; state?: string; status?: string; reason_code?: string; evidence: Record<string, string | number | null> };
type AlertEvent = {
  evaluated_at: string; status: string; condition: string; quality: string;
  definition: AlertRow;
  evidence: { true_since: string | null; predicates: EvidenceItem[]; checks: EvidenceItem[] } | null;
};
const markets = ['BTC', 'ETH', 'SP500', 'XYZ100', 'BRENTOIL'];
const operators = { gt: '>', gte: '≥', lt: '<', lte: '≤' };
const labels: Record<string, string> = {
  mark_price: 'Mark price (USD)', oracle_price: 'Oracle price (USD)', mid_price: 'Mid price (USD)',
  bid_price: 'Bid price (USD)', ask_price: 'Ask price (USD)', open_interest: 'Open interest (base units)',
  funding_rate: 'Funding (fraction/hour)', bid_depth: 'Bid depth (USD)', ask_depth: 'Ask depth (USD)',
  confirmed: 'Confirmed', warn: 'Warning', blocked: 'Blocked', candidate: 'Candidate',
  data_unknown: 'Data unknown', true: 'Met', false: 'Not met', unknown: 'Unknown',
  valid: 'Valid', warned: 'Warning', pass: 'Passed', block: 'Blocked',
  monitoring: 'Monitoring', warming_up: 'Warming up', stale: 'Stale / interrupted',
  evaluator_unavailable: 'Evaluator unavailable', inactive: 'Not monitoring',
  OI_UNCONFIRMED: 'Waiting for an OI baseline', GAP: 'Feed continuity interrupted',
  WIDE_SPREAD: 'Spread exceeds the limit', THIN_DEPTH: 'Insufficient book depth',
  ORACLE_MODE_UNKNOWN: 'Oracle mode is unverified', OUT_OF_ORDER: 'Out-of-order observation',
};
const label = (value: string) => labels[value] ?? value.replaceAll('_', ' ').toLowerCase();
const duration = (seconds: number) => seconds === 0 ? 'None' : seconds % 3600 === 0 ? `${seconds / 3600}h` : seconds % 60 === 0 ? `${seconds / 60}m` : `${seconds}s`;
function condition(row: Pick<AlertRow, 'predicates' | 'combinator'>) {
  return row.predicates.map(p => {
    const metric = p.type === 'open_interest_change' ? `OI change (${duration(p.window_seconds!)})` : label(p.metric!);
    return `${metric} ${operators[p.operator]} ${p.threshold}${p.mode === 'percent' ? '%' : ''}`;
  }).join(row.combinator === 'all' ? ' AND ' : ' OR ');
}

async function writeAlert(url: string, method: string, body: unknown) {
  const response = await fetch(url, {
    method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    signal: AbortSignal.timeout(30_000),
  });
  if (!response.ok) throw new Error('Unable to save. Check your alerts before retrying.');
  return response.json();
}

function NewAlert({ symbol, onCreated, onCancel }: { symbol: string; onCreated: () => void; onCancel: () => void }) {
  const requestId = useRef(crypto.randomUUID());
  const [metric, setMetric] = useState('mark_price');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const predicate = {
      type: metric === 'oi_change' ? 'open_interest_change' : 'threshold',
      ...(metric === 'oi_change' ? { window_seconds: Number(fields.get('window')) * 60, mode: 'percent' } : { metric }),
      operator: fields.get('operator'), threshold: fields.get('threshold'),
    };
    setSaving(true); setError('');
    try {
      await writeAlert('/api/alerts', 'POST', { request_id: requestId.current, spec: {
        name: fields.get('name'), market: fields.get('market'), predicates: [predicate],
        persistence_seconds: Number(fields.get('persistence')), cooldown_seconds: Number(fields.get('cooldown')),
        quality_policy: fields.get('quality_policy'),
        quality: { max_spread_bps: fields.get('spread') || null },
      } });
      onCreated();
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Unable to save. Check your alerts before retrying.'); }
    finally { setSaving(false); }
  }
  return <form className="alert-form" onSubmit={submit} aria-label="Create alert">
    <h3>New alert</h3>
    <p>One condition. Notifications appear in web history. A level already met can trigger immediately.</p>
    <fieldset disabled={saving}>
      <div className="alert-fields">
        <label>Name<input name="name" required maxLength={200} placeholder="e.g. BTC above my target" autoFocus /></label>
        <label>Market<select aria-label="Market" name="market" defaultValue={symbol}>{markets.map(value => <option key={value}>{value}</option>)}</select></label>
        <label>Metric<select aria-label="Metric" value={metric} onChange={event => setMetric(event.target.value)}>
          {['mark_price', 'oracle_price', 'mid_price', 'bid_price', 'ask_price', 'open_interest', 'funding_rate', 'bid_depth', 'ask_depth'].map(value => <option key={value} value={value}>{label(value)}</option>)}
          <option value="oi_change">OI change (%)</option>
        </select></label>
        <label>Comparison<select aria-label="Comparison" name="operator" defaultValue="gt">{Object.entries(operators).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
        <label>Threshold<input name="threshold" type="number" step="any" required /></label>
        {metric === 'oi_change' && <label>Window (minutes)<input name="window" type="number" min={1} max={1440} defaultValue={15} required /></label>}
      </div>
      {metric === 'funding_rate' && <p>0.0001 means 0.01% per hour.</p>}
      {metric === 'oi_change' && <p>5 means a 5% change. Monitoring warms up until a baseline is available.</p>}
      <details><summary>Timing and quality</summary><div className="alert-fields">
        <label>Hold for (seconds)<input name="persistence" type="number" min={0} max={86400} defaultValue={0} required /></label>
        <label>Cooldown (seconds)<input name="cooldown" type="number" min={0} max={604800} defaultValue={300} required /></label>
        <label>Maximum spread (bps, optional)<input name="spread" type="number" min={0} step="any" /></label>
        <label>Quality policy<select aria-label="Quality policy" name="quality_policy" defaultValue="block"><option value="block">Block poor-quality signals</option><option value="warn">Allow with warnings</option></select></label>
      </div></details>
      {error && <p className="alert-failure" role="alert">{error}</p>}
      <div className="alert-actions"><button className="alert-button alert-primary" type="submit">{saving ? 'Creating…' : 'Create alert'}</button><button className="alert-button" type="button" onClick={onCancel}>Cancel</button></div>
    </fieldset>
  </form>;
}

function EventDetails({ id, onBack }: { id: string; onBack: () => void }) {
  const [event, setEvent] = useState<AlertEvent | null>(null);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const back = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    back.current?.focus();
    const controller = new AbortController();
    setEvent(null); setFailed(false);
    void fetch(`/api/alerts/events/${id}`, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12_000)]) })
      .then(response => { if (!response.ok) throw new Error(); return response.json(); })
      .then(data => { if (!controller.signal.aborted) setEvent(data); })
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [id, retry]);
  function values(items: Record<string, string | number | null>) {
    return <dl className="alert-evidence-values">{Object.entries(items).map(([key, value]) => <Fragment key={key}>
      <dt>{label(key)}</dt><dd>{value === null ? 'Unknown' : key.endsWith('_at') ? <time dateTime={String(value)} title={String(value)}>{new Date(value).toLocaleString()}</time> : key === 'metric' ? label(String(value)) : key === 'operator' ? operators[value as keyof typeof operators] : String(value)}</dd>
    </Fragment>)}</dl>;
  }
  return <section className="alert-details" aria-label="Alert evidence">
    <button ref={back} className="alert-button" onClick={onBack}>Back to history</button>
    {failed ? <p role="alert">Unable to load evidence. <button className="alert-button" onClick={() => setRetry(value => value + 1)}>Retry</button></p> : !event ? <p role="status">Loading evidence…</p> : <>
      <h3>{event.definition.name} · v{event.definition.version}</h3>
      <p>{condition(event.definition)}</p>
      <p><strong>{label(event.status)}</strong> · Condition: {label(event.condition)} · Quality: {label(event.quality)}</p>
      <p>Evaluated: <time dateTime={event.evaluated_at} title={event.evaluated_at}>{new Date(event.evaluated_at).toLocaleString()}</time> (local)</p>
      {event.evidence ? <>
        {event.evidence.true_since && <p>Condition met since: <time dateTime={event.evidence.true_since} title={event.evidence.true_since}>{new Date(event.evidence.true_since).toLocaleString()}</time> (local)</p>}
        <h4>Conditions</h4>
        {event.evidence.predicates.map(item => <div className="alert-evidence-item" key={item.id}>
          <strong>{label(item.state!)}{item.reason_code ? ` · ${label(item.reason_code)}` : ''}</strong>{values(item.evidence)}
        </div>)}
        <h4>Quality checks</h4>
        {event.evidence.checks.length ? event.evidence.checks.map((item, index) => <div className="alert-evidence-item" key={index}>
          <strong>{label(item.status!)} · {label(item.reason_code!)}</strong>{values(item.evidence)}
        </div>) : <p>No additional quality checks were configured.</p>}
      </> : <p>No recorded evidence is available for this event.</p>}
    </>}
  </section>;
}

export default function Alerts({ symbol }: { symbol: string }) {
  const [view, setView] = useState<'active' | 'paused' | 'history'>('active');
  const [currentMarket, setCurrentMarket] = useState(false);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<AlertPage | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [creating, setCreating] = useState(false);
  const [eventId, setEventId] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const filter = currentMarket ? symbol : '';
  const pageSize = 25;
  useEffect(() => { setOffset(0); setEventId(null); }, [view, filter]);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setData(null); setError(false);
    async function refresh() {
      try {
        const query = new URLSearchParams({ view: view === 'history' ? 'history' : 'rules', status: view === 'paused' ? 'paused' : 'active', limit: String(pageSize), offset: String(offset) });
        if (filter) query.set('symbol', filter);
        const response = await fetch(`/api/alerts?${query}`, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12_000)]) });
        if (!response.ok) throw new Error();
        const next: AlertPage = await response.json();
        if (!controller.signal.aborted) { setData(next); setError(false); }
      } catch { if (!controller.signal.aborted) setError(true); }
      finally { if (!controller.signal.aborted) timer = setTimeout(refresh, 5_000); }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [view, filter, offset, retry]);

  async function setStatus(row: AlertRow) {
    setPending(row.alert_id); setMessage('');
    try {
      await writeAlert(`/api/alerts/${row.alert_id}`, 'PATCH', { status: view === 'active' ? 'paused' : 'active' });
      setMessage(`${row.name} ${view === 'active' ? 'paused' : 'resumed'}.`);
      setOffset(0); setRetry(value => value + 1);
    } catch (failure) { setMessage(failure instanceof Error ? failure.message : 'Unable to update the alert.'); }
    finally { setPending(null); }
  }
  return <section className="alerts-pane" aria-label="Alerts">
    <div className="alerts-heading">
      <div className="alerts-views" role="group" aria-label="Alert view">
        {(['active', 'paused', 'history'] as const).map(value => <button key={value} aria-pressed={view === value} onClick={() => { setData(null); setOffset(0); setEventId(null); setView(value); setCreating(false); setMessage(''); setRetry(count => count + 1); }}>{value === 'active' ? 'Active' : value === 'paused' ? 'Paused' : 'History'}</button>)}
      </div>
      <div className="alert-actions"><label className="alerts-filter"><input type="checkbox" checked={currentMarket} onChange={event => setCurrentMarket(event.target.checked)} />{symbol} only</label>
        <button className="alert-button" aria-expanded={creating} onClick={() => { setCreating(value => !value); setEventId(null); setMessage(''); }}>New alert</button>
      </div>
    </div>
    {message && <p className="alerts-feedback" role="status">{message}</p>}
    {error && <div className="alerts-error" role="status">{data ? 'Updates interrupted. Showing last loaded alerts.' : 'Unable to load alerts.'}<button onClick={() => setRetry(value => value + 1)}>Retry</button></div>}
    <div className="alerts-scroll" tabIndex={0} role="region" aria-label="Alert workspace">
      {creating ? <NewAlert symbol={symbol} onCancel={() => setCreating(false)} onCreated={() => { setData(null); setCreating(false); setView('active'); setCurrentMarket(false); setOffset(0); setRetry(value => value + 1); setMessage('Alert created. Check its monitoring status below.'); }} /> : eventId ? <EventDetails id={eventId} onBack={() => setEventId(null)} /> : <>
        <table className="alerts-table">
          <caption className="sr-only">{view === 'history' ? 'Alert history' : `${view} alert rules`}{filter ? ` for ${filter}` : ' across all markets'}</caption>
          <thead><tr><th scope="col">Market</th><th scope="col">Alert / Condition</th>
            {view === 'history' ? <><th scope="col">Status</th><th scope="col">Condition</th><th scope="col">Quality</th><th scope="col">Time (local)</th></> : <><th scope="col">Monitoring</th><th scope="col">Hold / Cooldown</th><th scope="col">Quality policy</th></>}
            <th scope="col">Action</th>
          </tr></thead>
          <tbody>{data?.items.map(row => <tr key={row.id}>
            <td><span className="alert-market">{row.market.dex ? `${row.market.dex}:` : ''}{row.market.coin}</span><small>{row.market.network}</small></td>
            <td className="alert-description"><span>{row.name} <small className="alert-version">v{row.version}</small></span><small>{condition(row)}</small></td>
            {view === 'history' ? <><td className={`alert-status alert-${row.status}`}>{label(row.status!)}</td><td>{label(row.condition!)}</td><td>{label(row.quality!)}</td><td><time dateTime={row.evaluated_at} title={row.evaluated_at}>{new Date(row.evaluated_at!).toLocaleString()}</time></td></> : <><td className={`alert-status alert-${row.monitoring}`}>{label(row.monitoring!)}</td><td>{duration(row.persistence_seconds)} / {duration(row.cooldown_seconds)}</td><td>{row.quality_policy === 'block' ? 'Block' : 'Warn'}</td></>}
            <td>{view === 'history' ? <button className="alert-button" onClick={() => setEventId(row.id)} aria-label={`View evidence for ${row.name}`}>Evidence</button> : <button className="alert-button" disabled={pending !== null} onClick={() => void setStatus(row)} aria-label={`${view === 'active' ? 'Pause' : 'Resume'} ${row.name}`}>{pending === row.alert_id ? 'Saving…' : view === 'active' ? 'Pause' : 'Resume'}</button>}</td>
          </tr>)}</tbody>
        </table>
        {!data && !error && <p className="alerts-empty" role="status">Loading alerts…</p>}
        {data?.items.length === 0 && <div className="alerts-empty" role="status"><p>No {view === 'history' ? 'alert events' : `${view} alerts`}{filter ? ` for ${filter}` : ''}.</p><p>{view === 'active' ? 'Create an alert here or ask the assistant.' : view === 'paused' ? 'Paused rules can be resumed here.' : 'Triggered and blocked events will appear here with their evidence.'}</p></div>}
      </>}
    </div>
    {!creating && !eventId && (offset > 0 || data?.has_more) && <div className="alerts-pagination"><button className="alert-button" disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - pageSize))}>Previous</button><span>Page {offset / pageSize + 1}</span><button className="alert-button" disabled={!data?.has_more} onClick={() => setOffset(value => value + pageSize)}>Next</button></div>}
  </section>;
}

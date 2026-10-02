import { useEffect, useState } from 'react';

type Predicate = {
  type: 'threshold' | 'open_interest_change'; metric?: string;
  operator: 'gt' | 'gte' | 'lt' | 'lte'; threshold: string;
  window_seconds?: number; mode?: 'percent' | 'absolute';
};
type AlertRow = {
  id: string; name: string; version: number;
  market: { network: string; dex: string; coin: string };
  predicates: Predicate[]; combinator: 'all' | 'any';
  persistence_seconds: number; cooldown_seconds: number; quality_policy: string;
  status?: string; condition?: string; quality?: string; evaluated_at?: string;
};
type AlertPage = { items: AlertRow[]; has_more: boolean };
const operators = { gt: '>', gte: '≥', lt: '<', lte: '≤' };
const labels: Record<string, string> = {
  mark_price: 'Mark price', oracle_price: 'Oracle price', mid_price: 'Mid price',
  bid_price: 'Bid price', ask_price: 'Ask price', open_interest: 'Open interest',
  funding_rate: 'Funding rate', bid_depth: 'Bid depth', ask_depth: 'Ask depth',
  confirmed: 'Confirmed', warn: 'Warning', blocked: 'Blocked', candidate: 'Candidate',
  data_unknown: 'Data unknown', true: 'Met', false: 'Not met', unknown: 'Unknown',
  valid: 'Valid', warned: 'Warning',
};
const duration = (seconds: number) => seconds === 0 ? 'None' : seconds % 3600 === 0 ? `${seconds / 3600}h` : seconds % 60 === 0 ? `${seconds / 60}m` : `${seconds}s`;
function condition(row: AlertRow) {
  return row.predicates.map(p => {
    const metric = p.type === 'open_interest_change' ? `OI change (${duration(p.window_seconds!)})` : labels[p.metric!] ?? p.metric;
    return `${metric} ${operators[p.operator]} ${p.threshold}${p.mode === 'percent' ? '%' : ''}`;
  }).join(row.combinator === 'all' ? ' AND ' : ' OR ');
}

export default function Alerts({ symbol }: { symbol: string }) {
  const [view, setView] = useState<'rules' | 'history'>('rules');
  const [currentMarket, setCurrentMarket] = useState(false);
  const [data, setData] = useState<AlertPage | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const filter = currentMarket ? symbol : '';

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setData(null);
    setError(false);
    async function refresh() {
      try {
        const query = new URLSearchParams({ view });
        if (filter) query.set('symbol', filter);
        const response = await fetch(`/api/alerts?${query}`, {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12_000)]),
        });
        if (!response.ok) throw new Error('Alerts unavailable');
        const next: AlertPage = await response.json();
        if (!controller.signal.aborted) { setData(next); setError(false); }
      } catch {
        if (!controller.signal.aborted) setError(true);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 5_000);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [view, filter, retry]);

  return (
    <section className="alerts-pane" aria-label="Alerts">
      <div className="alerts-heading">
        <div className="alerts-views" role="group" aria-label="Alert view">
          <button type="button" aria-pressed={view === 'rules'} onClick={() => setView('rules')}>
            Active alerts {view === 'rules' && data && <span>({data.items.length}{data.has_more ? '+' : ''})</span>}
          </button>
          <button type="button" aria-pressed={view === 'history'} onClick={() => setView('history')}>Alert history</button>
        </div>
        <label className="alerts-filter"><input type="checkbox" checked={currentMarket} onChange={e => setCurrentMarket(e.target.checked)} />{symbol} only</label>
      </div>
      {error && <div className="alerts-error" role="status">
        {data ? 'Updates interrupted. Showing last loaded alerts.' : 'Unable to load alerts.'}
        <button type="button" onClick={() => setRetry(value => value + 1)}>Retry</button>
      </div>}
      <div className="alerts-scroll" tabIndex={0} role="region" aria-label={view === 'rules' ? 'Active alert rules' : 'Alert history'}>
        <table className="alerts-table">
          <caption className="sr-only">{view === 'rules' ? 'Active alert rules' : 'Recent alert events'}{filter ? ` for ${filter}` : ' across all markets'}</caption>
          <thead><tr>
            <th scope="col">Market</th><th scope="col">Alert / Condition</th>
            {view === 'rules' ? <><th scope="col">Persistence</th><th scope="col">Cooldown</th><th scope="col">Quality policy</th></> :
              <><th scope="col">Status</th><th scope="col">Condition</th><th scope="col">Quality</th><th scope="col">Time (local)</th></>}
          </tr></thead>
          <tbody>
            {data?.items.map(row => <tr key={row.id}>
              <td><span className="alert-market">{row.market.dex ? `${row.market.dex}:` : ''}{row.market.coin}</span><small>{row.market.network}</small></td>
              <td className="alert-description"><span>{row.name} <small className="alert-version">v{row.version}</small></span><small>{condition(row)}</small></td>
              {view === 'rules' ? <><td>{duration(row.persistence_seconds)}</td><td>{duration(row.cooldown_seconds)}</td><td>{row.quality_policy === 'block' ? 'Block' : 'Warn'}</td></> :
                <><td className={`alert-status alert-${row.status}`}>{labels[row.status!] ?? row.status}</td><td>{labels[row.condition!] ?? row.condition}</td><td>{labels[row.quality!] ?? row.quality}</td><td><time dateTime={row.evaluated_at} title={row.evaluated_at}>{new Date(row.evaluated_at!).toLocaleString()}</time></td></>}
            </tr>)}
          </tbody>
        </table>
        {!data && !error && <p className="alerts-empty" role="status">Loading alerts…</p>}
        {data?.items.length === 0 && <div className="alerts-empty" role="status">
          <p>{view === 'rules' ? 'No active alerts' : 'No alert events yet'}{filter ? ` for ${filter}` : ''}.</p>
          <p>{view === 'rules' ? 'Active rules will appear here once configured.' : 'Triggered alerts and quality checks will appear here.'}</p>
        </div>}
        {data?.has_more && <p className="alerts-limit">Showing the latest 100 {view === 'rules' ? 'active rules' : 'events'}.</p>}
      </div>
    </section>
  );
}

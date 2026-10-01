import { useEffect, useRef, useState } from 'react';
import OrderBook from './OrderBook';
import Chat from './Chat';
import {
  CandlestickSeries, ColorType, CrosshairMode, LineStyle, createChart,
  type CandlestickData, type UTCTimestamp,
} from 'lightweight-charts';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './components/ui/select';

const markets = ['BTC', 'ETH', 'SP500', 'XYZ100', 'BRENTOIL'] as const;
type Symbol = typeof markets[number];
const intervals = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '8h', '12h', '1d', '3d', '1w', '1M'] as const;
type Interval = typeof intervals[number];

type Candle = { time: number; open: string; high: string; low: string; close: string };
type Status = 'connecting' | 'live' | 'reconnecting';
type Message =
  | { type: 'snapshot'; status: Status; candles: Candle[] }
  | { type: 'candle'; candle: Candle }
  | { type: 'status'; status: Status };

const formatPrice = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });
const formatChange = new Intl.NumberFormat('en-US', { style: 'percent', signDisplay: 'exceptZero', minimumFractionDigits: 2, maximumFractionDigits: 2 });
const toBar = (candle: Candle): CandlestickData<UTCTimestamp> => ({
  time: Math.floor(candle.time / 1000) as UTCTimestamp,
  open: Number(candle.open), high: Number(candle.high),
  low: Number(candle.low), close: Number(candle.close),
});

export default function App() {
  const [symbol, setSymbol] = useState<Symbol>('BTC');
  const [interval, setInterval] = useState<Interval>('5m');
  const container = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<Status>('connecting');
  const [latest, setLatest] = useState<Candle | null>(null);
  const [changes, setChanges] = useState<Partial<Record<Symbol, number | null>>>({});

  function selectMarket(value: Symbol) {
    if (value === symbol) return;
    setSymbol(value);
    setLatest(null);
    setStatus('connecting');
  }

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const response = await fetch('/api/markets', { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12_000)]) });
        if (!response.ok) throw new Error('Market data unavailable');
        const data = await response.json();
        if (!controller.signal.aborted) setChanges(data);
      } catch {
        if (!controller.signal.aborted) setChanges({});
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 15_000);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, []);

  useEffect(() => {
    document.title = `${symbol} · ${interval} · Hype Radar`;
    const chart = createChart(container.current!, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#181713' },
        textColor: '#928f86', fontFamily: 'Arial, sans-serif', fontSize: 14,
      },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      rightPriceScale: {
        borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.06 },
      },
      timeScale: { borderVisible: false, rightOffset: 8, timeVisible: interval.endsWith('m') || interval.endsWith('h'), secondsVisible: false },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#5c5951', labelBackgroundColor: '#34312b' },
        horzLine: { color: '#5c5951', labelBackgroundColor: '#34312b' },
      },
      localization: { locale: 'en-US', priceFormatter: (price: number) => formatPrice.format(price) },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#30a378', downColor: '#c82346', borderVisible: false,
      wickUpColor: '#30a378', wickDownColor: '#c82346',
      priceLineStyle: LineStyle.Dotted,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });
    let socket: WebSocket;
    let retry: ReturnType<typeof setTimeout>;
    let stopped = false;
    let fitted = false;

    function connect() {
      socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/candles?symbol=${symbol}&interval=${interval}`);
      socket.onmessage = (event) => {
        const message: Message = JSON.parse(event.data);
        if (message.type === 'snapshot') {
          series.setData(message.candles.map(toBar));
          setLatest(message.candles.at(-1) ?? null);
          setStatus(message.status);
          if (message.candles.length && !fitted) {
            chart.timeScale().setVisibleLogicalRange({
              from: Math.max(0, message.candles.length - 125),
              to: message.candles.length + 8,
            });
            fitted = true;
          }
        } else if (message.type === 'candle') {
          series.update(toBar(message.candle));
          setLatest(message.candle);
        } else {
          setStatus(message.status);
        }
      };
      socket.onclose = () => {
        if (stopped) return;
        setStatus('reconnecting');
        retry = setTimeout(connect, 1500);
      };
      socket.onerror = () => socket.close();
    }

    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      socket.close();
      chart.remove();
    };
  }, [symbol, interval]);

  const rising = latest ? Number(latest.close) >= Number(latest.open) : true;

  return (
    <main>
      <header className="site-header"><h1>Hype Radar</h1></header>
      <section className="market-strip" aria-label="Market performance over 24 hours">
        <span className="market-period" title="24-hour change in perpetual mark price">24h</span>
        <div className="market-tickers">
          {markets.map((market) => {
            const change = changes[market];
            const available = typeof change === 'number' && Number.isFinite(change);
            const formatted = available ? formatChange.format(change / 100) : '—';
            return (
              <button key={market} type="button" className="market-ticker" aria-pressed={market === symbol}
                aria-label={`${market}, 24-hour change ${available ? formatted : 'unavailable'}`}
                onClick={() => selectMarket(market)}>
                <span>{market}</span>
                <span className={available ? change > 0 ? 'gain' : change < 0 ? 'loss' : '' : ''}>{formatted}</span>
              </button>
            );
          })}
        </div>
      </section>
      <div className="market-workspace">
        <section className="chart-pane" aria-label="Price chart">
          <div className="chart-header">
            <div className="chart-controls">
              <Select value={symbol} onValueChange={(value) => selectMarket(value as Symbol)}>
                <SelectTrigger aria-label="Select market"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {markets.map((market) => (
                    <SelectItem key={market} value={market}>
                      <span className="market-label">
                        <img src={`/icons/${market}.${market === 'BTC' || market === 'ETH' ? 'svg' : 'png'}`} width={24} height={24} alt="" />
                        {market}
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span> / USD · </span>
              <Select value={interval} onValueChange={(value) => {
                setInterval(value as Interval);
                setLatest(null);
                setStatus('connecting');
              }}>
                <SelectTrigger aria-label="Select time interval"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {intervals.map((value) => (
                    <SelectItem key={value} value={value}>{value}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span> · Hyperliquid</span>
            </div>
            <span className={`price ${rising ? 'up' : 'down'}`}>
              {latest ? formatPrice.format(Number(latest.close)) : '—'}
            </span>
            <span className={`status ${status}`} role="status">
              <i aria-hidden="true" />
              {status === 'live' ? 'Live' : status === 'connecting' ? 'Connecting' : 'Reconnecting'}
            </span>
          </div>
          <div className="chart" ref={container} role="img" aria-label={`Live ${symbol} perpetual ${interval} candlestick chart`} />
          {!latest && <p className="loading">Loading {symbol} {interval} candles…</p>}
        </section>
        <OrderBook key={symbol} symbol={symbol} />
        <Chat />
      </div>
      <footer>
        <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">
          Charts by TradingView
        </a>
        <span>TradingView Lightweight Charts™ · Copyright © 2025 TradingView, Inc.</span>
      </footer>
    </main>
  );
}

import { useEffect, useRef, useState } from 'react';
import {
  CandlestickSeries, ColorType, CrosshairMode, LineStyle, createChart,
  type CandlestickData, type UTCTimestamp,
} from 'lightweight-charts';

type Candle = { time: number; open: string; high: string; low: string; close: string };
type Status = 'connecting' | 'live' | 'reconnecting';
type Message =
  | { type: 'snapshot'; status: Status; candles: Candle[] }
  | { type: 'candle'; candle: Candle }
  | { type: 'status'; status: Status };

const formatPrice = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });
const toBar = (candle: Candle): CandlestickData<UTCTimestamp> => ({
  time: Math.floor(candle.time / 1000) as UTCTimestamp,
  open: Number(candle.open), high: Number(candle.high),
  low: Number(candle.low), close: Number(candle.close),
});

export default function App() {
  const container = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<Status>('connecting');
  const [latest, setLatest] = useState<Candle | null>(null);

  useEffect(() => {
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
      timeScale: { borderVisible: false, rightOffset: 8 },
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
      priceFormat: { type: 'price', precision: 1, minMove: 0.1 },
    });
    let socket: WebSocket;
    let retry: ReturnType<typeof setTimeout>;
    let stopped = false;
    let fitted = false;

    function connect() {
      socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/btc`);
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
  }, []);

  const rising = latest ? Number(latest.close) >= Number(latest.open) : true;

  return (
    <main>
      <header>
        <h1>BTC/USD <span>· 1D · Hyperliquid</span></h1>
        <span className={`price ${rising ? 'up' : 'down'}`}>
          {latest ? formatPrice.format(Number(latest.close)) : '—'}
        </span>
        <span className={`status ${status}`} role="status">
          <i aria-hidden="true" />
          {status === 'live' ? 'Live' : status === 'connecting' ? 'Connecting' : 'Reconnecting'}
        </span>
      </header>
      <div className="chart" ref={container} role="img" aria-label="Live Bitcoin perpetual daily candlestick chart" />
      {!latest && <p className="loading">Loading BTC candles…</p>}
      <footer>
        <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">
          Charts by TradingView
        </a>
        <span>TradingView Lightweight Charts™ · Copyright © 2025 TradingView, Inc.</span>
      </footer>
    </main>
  );
}

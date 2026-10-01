import { useEffect, useRef, useState } from 'react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './components/ui/select';

type Level = { px: string; sz: string };
type Book = { time: number; levels: [Level[], Level[]] };
type Status = 'connecting' | 'live' | 'reconnecting';
type Message =
  | { type: 'book'; status: Status; book: Book | null }
  | { type: 'status'; status: Status };
const priceFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 6 });
const baseFormat = new Intl.NumberFormat('en-US', { minimumFractionDigits: 5, maximumFractionDigits: 8 });
const usdFormat = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function OrderBook({ symbol }: { symbol: string }) {
  const [precision, setPrecision] = useState('5');
  const [unit, setUnit] = useState('base');
  const [book, setBook] = useState<Book | null>(null);
  const [status, setStatus] = useState<Status>('connecting');
  const [referencePrice, setReferencePrice] = useState<number | null>(null);
  const [rowCount, setRowCount] = useState(11);
  const depth = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => {
      setRowCount(Math.max(1, Math.min(20, Math.floor((entry.contentRect.height - 28) / 48))));
    });
    observer.observe(depth.current!);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let socket: WebSocket;
    let retry: ReturnType<typeof setTimeout>;
    let stopped = false;
    function connect() {
      socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/book?symbol=${symbol}&precision=${precision}`);
      socket.onmessage = (event) => {
        const message: Message = JSON.parse(event.data);
        setStatus(message.status);
        if (message.type === 'book') {
          setBook(message.book);
          const best = message.book?.levels[0][0] ?? message.book?.levels[1][0];
          if (best) setReferencePrice(Number(best.px));
        } else if (message.status !== 'live') {
          setBook(null);
        }
      };
      socket.onclose = () => {
        if (stopped) return;
        setBook(null);
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
      socket.onclose = null;
      socket.onerror = null;
      socket.close();
    };
  }, [symbol, precision]);

  const unitLabel = unit === 'base' ? symbol : 'USD';
  const sizeFormat = unit === 'base' ? baseFormat : usdFormat;
  const sides = (book?.levels ?? [[], []]).map((levels) => {
    let total = 0;
    return levels.slice(0, rowCount).map((level) => {
      const price = Number(level.px);
      const size = Number(level.sz) * (unit === 'base' ? 1 : price);
      total += size;
      return { price, size, total };
    });
  });
  const [bids, asks] = sides;
  const maxDepth = Math.max(bids.at(-1)?.total ?? 0, asks.at(-1)?.total ?? 0);
  const spread = bids.length && asks.length ? asks[0].price - bids[0].price : null;
  const mid = bids.length && asks.length ? (asks[0].price + bids[0].price) / 2 : null;
  const statusLabel = status === 'live' ? 'Live' : status === 'connecting' ? 'Connecting' : 'Reconnecting';

  function rows(side: typeof bids, name: 'ask' | 'bid') {
    return side.map((level) => (
      <div className={`book-row ${name}`} role="row" key={level.price}>
        <span className="depth-bar" aria-hidden="true" style={{ width: `${level.total / maxDepth * 100}%` }} />
        <span role="cell" className="book-price">{priceFormat.format(level.price)}</span>
        <span role="cell">{sizeFormat.format(level.size)}</span>
        <span role="cell">{sizeFormat.format(level.total)}</span>
      </div>
    ));
  }

  return (
    <section className="order-book" aria-label={`${symbol} order book`}>
      <div className="book-heading">
        <h2>Order Book</h2>
        <span className={`status ${status}`} role="status"><i aria-hidden="true" />{statusLabel}</span>
      </div>
      <div className="book-controls">
        <Select value={precision} onValueChange={(value) => {
          setPrecision(value);
          setBook(null);
          setStatus('connecting');
        }}>
          <SelectTrigger aria-label="Price grouping"><SelectValue /></SelectTrigger>
          <SelectContent>
            {['5', '4', '3', '2'].map((value) => (
              <SelectItem key={value} value={value}>
                {referencePrice === null ? `${value} significant digits` : priceFormat.format(10 ** (Math.floor(Math.log10(referencePrice)) - Number(value) + 1))}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={unit} onValueChange={setUnit}>
          <SelectTrigger aria-label="Order book size unit"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="base">{symbol}</SelectItem>
            <SelectItem value="usd">USD</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="book-table" role="table" aria-label={`${symbol} market depth`}>
        <div className="book-columns book-row" role="row">
          <span role="columnheader">Price</span>
          <span role="columnheader">Size ({unitLabel})</span>
          <span role="columnheader">Total ({unitLabel})</span>
        </div>
        <div className="book-depth" ref={depth}>
          <div className="book-side asks" role="rowgroup" aria-label="Asks, sell orders">
            {rows([...asks].reverse(), 'ask')}
          </div>
          <div className="book-spread book-row" role="row">
            <span role="cell">Spread</span>
            <span role="cell">{spread === null ? '—' : priceFormat.format(spread)}</span>
            <span role="cell">{spread === null || mid === null ? '—' : `${(spread / mid * 100).toFixed(3)}%`}</span>
          </div>
          <div className="book-side bids" role="rowgroup" aria-label="Bids, buy orders">
            {rows(bids, 'bid')}
          </div>
          {!book && <p className="book-message">{status === 'reconnecting' ? 'Reconnecting to order book…' : `Loading ${symbol} order book…`}</p>}
          {book && (!bids.length || !asks.length) && <p className="book-empty">{!bids.length && !asks.length ? 'No resting orders' : !bids.length ? 'No bids' : 'No asks'}</p>}
        </div>
      </div>
    </section>
  );
}

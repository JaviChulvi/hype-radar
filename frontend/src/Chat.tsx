import { useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, MessageSquare, Plus, RotateCcw, Square } from 'lucide-react';

type Message = { id: string; role: 'user' | 'assistant'; content: string; stopped?: boolean };
type Phase = 'idle' | 'thinking' | 'streaming';

export default function Chat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState('');
  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState<string | null>(null);
  const [awayFromBottom, setAwayFromBottom] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const viewport = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const activeRequest = useRef<AbortController | null>(null);
  const follow = useRef(true);
  const failedPrompt = useRef('');
  const busy = phase !== 'idle';

  useEffect(() => () => activeRequest.current?.abort(), []);

  useEffect(() => {
    const element = viewport.current;
    if (element && follow.current) element.scrollTop = element.scrollHeight;
  }, [messages, phase, error]);

  useEffect(() => {
    const element = input.current;
    if (element) {
      element.style.height = 'auto';
      element.style.height = `${Math.min(element.scrollHeight, 144)}px`;
    }
  }, [draft]);

  async function send(prompt = draft.trim(), retry = false) {
    if (!prompt || activeRequest.current) return;
    const controller = new AbortController();
    activeRequest.current = controller;
    const id = crypto.randomUUID();
    let content = '';
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 30_000);
    follow.current = true;
    setAwayFromBottom(false);
    setError(null);
    setPhase('thinking');
    setAnnouncement('Thinking');
    if (!retry) {
      setDraft('');
      setMessages(previous => [...previous, { id: crypto.randomUUID(), role: 'user', content: prompt }]);
    }
    input.current?.focus();
    try {
      const response = await fetch('/api/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: prompt }), signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error('Reply unavailable');
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      while (true) {
        const { value, done } = await reader.read();
        content += decoder.decode(value, { stream: !done });
        if (content) {
          setPhase('streaming');
          setMessages(previous => {
            const reply = { id, role: 'assistant' as const, content };
            return previous.some(message => message.id === id)
              ? previous.map(message => message.id === id ? reply : message)
              : [...previous, reply];
          });
        }
        if (done) break;
      }
      if (!content.trim()) throw new Error('Empty reply');
      setAnnouncement(`Hype Radar: ${content}`);
    } catch {
      if (!controller.signal.aborted || timedOut) {
        // Replace a failed partial reply on retry, without duplicating the user's turn.
        setMessages(previous => previous.filter(message => message.id !== id));
        failedPrompt.current = prompt;
        setError(timedOut ? 'The reply took too long. Try again.' : 'Couldn’t get a reply. Please try again.');
        setAnnouncement('Reply failed');
      } else {
        setMessages(previous => [...previous.filter(message => message.id !== id), {
          id, role: 'assistant', content: content || 'Response stopped.', stopped: true,
        }]);
        setAnnouncement('Response stopped');
      }
    } finally {
      clearTimeout(timeout);
      activeRequest.current = null;
      setPhase('idle');
    }
  }

  function scrollToLatest() {
    follow.current = true;
    setAwayFromBottom(false);
    viewport.current?.scrollTo({ top: viewport.current.scrollHeight,
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  }

  return (
    <aside className="chat-pane" aria-label="Hype Radar chat">
      <div className="chat-heading">
        <h2><MessageSquare size={16} aria-hidden="true" /> Assistant <span className="chat-demo">Demo</span></h2>
        <button type="button" className="chat-icon-button" aria-label="New chat" title="New chat"
          disabled={busy || messages.length === 0} onClick={() => {
            setMessages([]); setError(null); setAnnouncement('New chat');
            follow.current = true; setAwayFromBottom(false); input.current?.focus();
          }}><Plus size={18} aria-hidden="true" /></button>
      </div>
      <div className="chat-conversation" ref={viewport} role="region" aria-label="Conversation" tabIndex={0}
        onScroll={() => {
          const element = viewport.current!;
          follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 48;
          setAwayFromBottom(!follow.current);
        }}>
        {messages.length === 0 ? (
          <div className="chat-welcome">
            <MessageSquare size={28} strokeWidth={1.5} aria-hidden="true" />
            <h3>What’s on your radar?</h3>
            <p>A space to talk markets.<br />Start a conversation to try the demo.</p>
            <div className="chat-suggestions">
              {['What should I watch in BTC?', 'Help me think through an alert'].map(prompt => (
                <button type="button" key={prompt} onClick={() => void send(prompt)}>
                  {prompt}<ArrowUp size={14} aria-hidden="true" />
                </button>
              ))}
            </div>
          </div>
        ) : messages.map(message => (
          <div key={message.id} className={`chat-message chat-message-${message.role}`}>
            <span className={message.role === 'user' ? 'sr-only' : 'chat-author'}>
              {message.role === 'user' ? 'You' : 'Hype Radar'}
            </span>
            <p className={busy && message.id === messages.at(-1)?.id && message.role === 'assistant' ? 'chat-streaming' : ''}>{message.content}</p>
            {message.stopped && message.content !== 'Response stopped.' && <span className="chat-stopped">Response stopped</span>}
          </div>
        ))}
        {phase === 'thinking' && <div className="chat-thinking" aria-hidden="true">
          <span className="chat-thinking-dots"><i /><i /><i /></span><span>Thinking</span>
        </div>}
        {error && <div className="chat-error" role="alert"><p>{error}</p>
          <button type="button" onClick={() => void send(failedPrompt.current, true)}><RotateCcw size={14} aria-hidden="true" /> Retry</button>
        </div>}
      </div>
      <div className="chat-compose-area">
        {awayFromBottom && <button type="button" className="chat-jump" onClick={scrollToLatest}>
          <ArrowDown size={14} aria-hidden="true" /> Latest message
        </button>}
        <form className="chat-composer" onSubmit={event => { event.preventDefault(); void send(); }}>
          <label htmlFor="chat-message" className="sr-only">Message Hype Radar</label>
          <textarea id="chat-message" ref={input} value={draft} rows={1} maxLength={4000}
            placeholder="Message Hype Radar…" onChange={event => setDraft(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault(); void send();
              }
            }} />
          <div className="chat-composer-actions">
            <span>Demo responses</span>
            {busy ? <button type="button" className="chat-send" aria-label="Stop response" title="Stop response"
              onClick={() => activeRequest.current?.abort()}><Square size={13} fill="currentColor" aria-hidden="true" /></button>
              : <button type="submit" className="chat-send" disabled={!draft.trim()} aria-label="Send message" title="Send message">
                <ArrowUp size={18} aria-hidden="true" />
              </button>}
          </div>
        </form>
        <p className="chat-disclaimer">Demo only · No AI connected</p>
      </div>
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{announcement}</div>
    </aside>
  );
}

import { useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, MessageSquare, Plus, RotateCcw, Square } from 'lucide-react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

type Message = { id: string; role: 'user' | 'assistant'; content: string; stopped?: boolean };
const toolLabels: Record<string, string> = {
  list_markets: 'Checking supported markets', get_market_snapshot: 'Reading current market data',
  get_candles: 'Studying price history', get_market_microstructure: 'Checking liquidity and recent trades',
  get_metric_history: 'Reading historical observations', preview_alert: 'Preparing alert conditions',
  create_alert: 'Activating your alert', list_alerts: 'Checking your alerts',
  set_alert_status: 'Updating your alert', get_alert_event: 'Reading alert evidence',
};
type Phase = 'idle' | 'thinking' | 'streaming';
type RetryState = { prompt: string; message: string; responseId?: string; isError: boolean; requestId: string };

export default function Chat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState('');
  const [phase, setPhase] = useState<Phase>('idle');
  const [toolStatus, setToolStatus] = useState('');
  const [retryState, setRetryState] = useState<RetryState | null>(null);
  const [recording, setRecording] = useState(false);
  const [requestingMicrophone, setRequestingMicrophone] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [awayFromBottom, setAwayFromBottom] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const viewport = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const activeRequest = useRef<AbortController | null>(null);
  const transcriptionRequest = useRef<AbortController | null>(null);
  const mediaRecorder = useRef<MediaRecorder | null>(null);
  const microphone = useRef<MediaStream | null>(null);
  const audioChunks = useRef<Blob[]>([]);
  const recordingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const follow = useRef(true);
  const replyBusy = phase !== 'idle';
  const busy = replyBusy || recording || requestingMicrophone || transcribing;

  useEffect(() => () => {
    activeRequest.current?.abort();
    transcriptionRequest.current?.abort();
    if (recordingTimer.current) clearTimeout(recordingTimer.current);
    if (mediaRecorder.current && mediaRecorder.current.state !== 'inactive') {
      mediaRecorder.current!.onstop = null;
      mediaRecorder.current!.stop();
    }
    microphone.current?.getTracks().forEach(track => track.stop());
  }, []);

  useEffect(() => {
    const element = viewport.current;
    if (element && follow.current) element.scrollTop = element.scrollHeight;
  }, [messages, phase, retryState, voiceError]);

  useEffect(() => {
    const element = input.current;
    if (element) {
      element.style.height = 'auto';
      element.style.height = `${Math.min(element.scrollHeight, 144)}px`;
    }
  }, [draft]);

  async function send(prompt = draft.trim(), retry = false, requestId: string = crypto.randomUUID()) {
    if (!prompt || activeRequest.current || recording || requestingMicrophone || transcribing) return;
    const controller = new AbortController();
    activeRequest.current = controller;
    const id = crypto.randomUUID();
    let content = '';
    let completed = false;
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 90_000);
    const history = messages
      .filter(message => !message.stopped && message.content.trim())
      .map(({ role, content }) => ({ role, content: content.slice(-4000) }));
    if (retry && history.at(-1)?.role === 'user' && history.at(-1)?.content === prompt) history.pop();
    const boundedHistory: typeof history = [];
    let contextLength = prompt.length;
    for (let index = history.length - 1; index >= 0 && boundedHistory.length < 20; index -= 1) {
      if (contextLength + history[index].content.length > 20_000) break;
      boundedHistory.unshift(history[index]);
      contextLength += history[index].content.length;
    }
    follow.current = true;
    setAwayFromBottom(false);
    setRetryState(null);
    setPhase('thinking');
    setToolStatus('');
    setAnnouncement('Thinking');
    if (!retry) {
      setDraft('');
      setMessages(previous => [...previous, { id: crypto.randomUUID(), role: 'user', content: prompt }]);
    }
    input.current?.focus();
    try {
      const response = await fetch('/api/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: prompt, history: boundedHistory, request_id: requestId }), signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error('Reply unavailable');
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      function updateReply() {
        setPhase('streaming');
        setMessages(previous => {
          const reply = { id, role: 'assistant' as const, content };
          return previous.some(message => message.id === id)
            ? previous.map(message => message.id === id ? reply : message)
            : [...previous, reply];
        });
      }
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const frames = buffer.split('\n\n');
        buffer = frames.pop()!;
        for (const frame of frames) {
          const data = frame.split('\n').filter(line => line.startsWith('data:'))
            .map(line => line.slice(5).trim()).join('\n');
          if (!data) continue;
          const event = JSON.parse(data);
          if (event.type === 'text') { content += event.text; updateReply(); }
          else if (event.type === 'tool_start') setToolStatus(toolLabels[event.name] ?? 'Working');
          else if (event.type === 'tool_end') setToolStatus('');
          else if (event.type === 'error') throw new Error(event.message);
          else if (event.type === 'done') completed = true;
        }
        if (done) break;
      }
      if (!completed || !content.trim()) throw new Error('Incomplete reply');
      setAnnouncement(`Hype Radar: ${content}`);
    } catch {
      if (!controller.signal.aborted || timedOut) {
        // Keep the partial reply visible; stopping the stream does not undo a committed alert.
        setMessages(previous => previous.map(message => message.id === id ? { ...message, stopped: true } : message));
        setRetryState({
          prompt, requestId,
          message: timedOut ? 'The reply took too long. Check alerts before retrying.' : 'Couldn’t finish the reply. Check alerts before retrying.',
          isError: true,
        });
        setAnnouncement('Reply failed');
      } else {
        setMessages(previous => [...previous.filter(message => message.id !== id), {
          id, role: 'assistant', content: content || 'Response stopped.', stopped: true,
        }]);
        setRetryState({ prompt, requestId, message: 'Response stopped.', responseId: id, isError: false });
        setAnnouncement('Response stopped');
      }
    } finally {
      clearTimeout(timeout);
      activeRequest.current = null;
      setPhase('idle');
      setToolStatus('');
    }
  }

  async function transcribeAudio(audio: Blob) {
    const controller = new AbortController();
    transcriptionRequest.current = controller;
    setTranscribing(true);
    setVoiceError(null);
    setAnnouncement('Transcribing audio');
    try {
      const response = await fetch('/api/chat/transcribe', {
        method: 'POST',
        headers: { 'Content-Type': audio.type || 'audio/webm' },
        body: audio,
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(60_000)]),
      });
      if (!response.ok) throw new Error('Transcription unavailable');
      const result: { text?: string } = await response.json();
      const text = result.text?.trim();
      if (!text) throw new Error('Empty transcription');
      setDraft(current => current.trim() ? `${current.trim()} ${text}` : text);
      setAnnouncement(`Transcribed: ${text}`);
      input.current?.focus();
    } catch {
      if (!controller.signal.aborted) {
        setVoiceError('Couldn’t transcribe the recording. Please try again.');
        setAnnouncement('Transcription failed');
      }
    } finally {
      transcriptionRequest.current = null;
      setTranscribing(false);
    }
  }

  async function startRecording() {
    setVoiceError(null);
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setVoiceError('Audio recording is not supported by this browser.');
      return;
    }
    setRequestingMicrophone(true);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      microphone.current = stream;
      const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];
      const mimeType = candidates.find(candidate => MediaRecorder.isTypeSupported(candidate));
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      mediaRecorder.current = recorder;
      audioChunks.current = [];
      recorder.ondataavailable = event => {
        if (event.data.size) audioChunks.current.push(event.data);
      };
      recorder.onerror = () => {
        setVoiceError('The recording failed. Please try again.');
        setAnnouncement('Recording failed');
      };
      recorder.onstop = () => {
        if (recordingTimer.current) clearTimeout(recordingTimer.current);
        recordingTimer.current = null;
        setRecording(false);
        mediaRecorder.current = null;
        stream.getTracks().forEach(track => track.stop());
        microphone.current = null;
        const audio = new Blob(audioChunks.current, { type: recorder.mimeType || 'audio/webm' });
        audioChunks.current = [];
        if (audio.size) void transcribeAudio(audio);
      };
      recorder.start();
      setRecording(true);
      setAnnouncement('Recording audio');
      recordingTimer.current = setTimeout(() => {
        if (recorder.state !== 'inactive') recorder.stop();
      }, 60_000);
    } catch {
      microphone.current?.getTracks().forEach(track => track.stop());
      microphone.current = null;
      setVoiceError('Microphone permission is required for voice input.');
      setAnnouncement('Microphone unavailable');
    } finally {
      setRequestingMicrophone(false);
    }
  }

  function toggleRecording() {
    if (recording) {
      if (mediaRecorder.current?.state !== 'inactive') mediaRecorder.current?.stop();
    } else {
      void startRecording();
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
        <h2><MessageSquare size={16} aria-hidden="true" /> Assistant</h2>
        <button type="button" className="chat-icon-button" aria-label="New chat" title="New chat"
          disabled={busy || messages.length === 0} onClick={() => {
            setMessages([]); setRetryState(null); setVoiceError(null); setAnnouncement('New chat');
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
            <p>Discuss markets and turn ideas into precise alert conditions.</p>
            <div className="chat-suggestions">
              {[
                'Compare BTC and ETH: 24h moves, funding and liquidity.',
                'Assess a $100k BTC buy using the order book and recent trades.',
                'Create an ETH alert: price above $4,000 AND OI up at least 5% in 15 minutes.',
              ].map(prompt => (
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
            {message.role === 'assistant' ? (
              <div className={`chat-markdown${replyBusy && message.id === messages.at(-1)?.id ? ' chat-streaming' : ''}`}>
                <Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
                  a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
                  table: ({ children }) => <div className="chat-table-scroll" role="region" aria-label="Response table" tabIndex={0}><table>{children}</table></div>,
                }}>{message.content}</Markdown>
              </div>
            ) : <p>{message.content}</p>}
            {message.stopped && message.content !== 'Response stopped.' && <span className="chat-stopped">Response stopped</span>}
          </div>
        ))}
        {(phase === 'thinking' || toolStatus) && <div className="chat-thinking" aria-hidden="true">
          <span className="chat-thinking-dots"><i /><i /><i /></span><span>{toolStatus || 'Thinking'}</span>
        </div>}
        {retryState && <div className={retryState.isError ? 'chat-error' : 'chat-retry'} role={retryState.isError ? 'alert' : 'status'}>
          <p>{retryState.message}</p>
          <button type="button" onClick={() => {
            if (retryState.responseId) {
              setMessages(previous => previous.filter(message => message.id !== retryState.responseId));
            }
            void send(retryState.prompt, true, retryState.requestId);
          }}><RotateCcw size={14} aria-hidden="true" /> Retry</button>
        </div>}
        {voiceError && <div className="chat-error" role="alert"><p>{voiceError}</p></div>}
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
          <div className="chat-action-buttons">
              <button type="button" className={`chat-mic${recording ? ' recording' : ''}`}
                disabled={replyBusy || requestingMicrophone || transcribing} aria-pressed={recording}
                aria-label={recording ? 'Stop recording' : 'Start voice input'}
                title={recording ? 'Stop recording' : 'Start voice input'} onClick={toggleRecording}>
                {recording ? <Square size={13} fill="currentColor" aria-hidden="true" /> :
                  <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor"
                    strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v3" />
                  </svg>}
              </button>
              {replyBusy ? <button type="button" className="chat-send" aria-label="Stop response" title="Stop response"
                onClick={() => activeRequest.current?.abort()}><Square size={13} fill="currentColor" aria-hidden="true" /></button>
                : <button type="submit" className="chat-send"
                  disabled={!draft.trim() || recording || requestingMicrophone || transcribing}
                  aria-label="Send message" title="Send message"><ArrowUp size={18} aria-hidden="true" /></button>}
          </div>
        </form>
        {(requestingMicrophone || recording || transcribing) && <p className="chat-voice-status">
          {requestingMicrophone ? 'Opening microphone…' : recording ? 'Recording…' : 'Transcribing…'}
        </p>}
        <p className="chat-disclaimer">AI can make mistakes · Not financial advice</p>
      </div>
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{announcement}</div>
    </aside>
  );
}

import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Bot, Send, FileText, Loader2, Sparkles } from 'lucide-react';
import { api } from '../api/client';
import { useToast } from '../ui/ToastContext';
import { initials } from '../utils/format';
import { useAuth } from '../auth/AuthContext';

interface Msg {
  role: 'user' | 'ai';
  text: string;
  sources?: any[];
  provider?: string;
  model?: string;
  error?: string;
}

const SUGGESTIONS = [
  'What are the current risks?',
  'What are the pending action items?',
  'What blockers were identified?',
  'What are the project deliverables?',
  'Who is responsible for API integration?',
  'What deadlines are approaching?',
];

export function AssistantPanel({ projectId }: { projectId: number }) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [hasContext, setHasContext] = useState<boolean | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const { user } = useAuth();
  const toast = useToast();

  useEffect(() => {
    api
      .listDocuments({ project_id: projectId })
      .then((d) => setHasContext(d.some((x) => x.status === 'Processed')))
      .catch(() => setHasContext(false));
  }, [projectId]);

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight);
  }, [messages, busy]);

  const ask = async (q: string) => {
    const question = q.trim();
    if (!question || busy) return;
    setInput('');
    setMessages((m) => [...m, { role: 'user', text: question }]);
    setBusy(true);
    try {
      const res = await api.askAssistant(projectId, question);
      setMessages((m) => [
        ...m,
        {
          role: 'ai',
          text: res.answer || 'No response.',
          sources: res.sources || [],
          provider: res.provider,
          model: res.model,
          error: res.error,
        },
      ]);
    } catch (err: any) {
      setMessages((m) => [...m, { role: 'ai', text: err?.message || 'Assistant request failed.', error: 'request' }]);
      toast.error(err?.message || 'Request failed.');
    } finally {
      setBusy(false);
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      ask(input);
    }
  };

  return (
    <div className="panel">
      <div className="panel-head">
        <h3><Bot size={16} /> Project Assistant <span className="ph-sub">· RAG-grounded · project-scoped</span></h3>
        {hasContext === false && <Badge tone="warn" plain>No processed documents yet — answers will be limited</Badge>}
        {hasContext === true && <Badge tone="ok" plain>Knowledge base ready</Badge>}
      </div>

      <div className="panel-body chat-scroll" ref={scrollRef} style={{ maxHeight: 520, overflowY: 'auto', gap: 18 }}>
        {messages.length === 0 && (
          <div className="col" style={{ alignItems: 'center', padding: '22px 0' }}>
            {requiresBadge()}
            <div className="tiny dim" style={{ textAlign: 'center', maxWidth: 420, lineHeight: 1.6 }}>
              Ask anything about this project. Answers are generated only from the uploaded project documents
              and never from unrelated projects. Sources are shown for every important answer.
            </div>
            <div className="chip-row mt-1" style={{ justifyContent: 'center', maxWidth: 460 }}>
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chip" onClick={() => ask(s)}>{s}</button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role === 'user' ? 'msg-user' : 'msg-ai'}`}>
            <div className="msg-avatar">{m.role === 'user' ? initials(user?.name) : <Bot size={15} />}</div>
            <div className="col" style={{ gap: 6 }}>
              <div className="msg-bubble">{m.text}</div>
              {m.sources && m.sources.length > 0 && (
                <div className="sources">
                  <div className="eyebrow" style={{ fontSize: 10 }}>Sources</div>
                  {m.sources.map((s, j) => (
                    <button key={j} className="source-chip" onClick={() => {}} title={s.label}>
                      <FileText size={12} /> {s.label}
                    </button>
                  ))}
                </div>
              )}
              {m.error && <div className="tiny" style={{ color: 'var(--amber)' }}>Provider note: {m.error.slice(0, 200)}</div>}
              {m.provider && (
                <div className="tiny dim">
                  via {m.provider} · {m.model} · {m.provider && m.role === 'ai' && 'grounded in retrieved documents'}
                </div>
              )}
            </div>
          </div>
        ))}

        {busy && (
          <div className="msg msg-ai">
            <div className="msg-avatar"><Bot size={15} /></div>
            <div className="msg-bubble row gap-sm" style={{ color: 'var(--text-2)' }}>
              <Loader2 size={15} className="spin" />
              <Sparkles size={14} style={{ color: 'var(--violet-2)' }} /> Retrieving relevant documents and thinking…
            </div>
          </div>
        )}
      </div>

      <div className="chat-input-bar">
        <textarea
          className="input"
          placeholder="Ask the project assistant…  (Enter to send, Shift+Enter for new line)"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKey}
          disabled={busy}
        />
        <button className="btn btn-primary" onClick={() => ask(input)} disabled={busy || !input.trim()} title="Send">
          <Send size={16} />
        </button>
      </div>
    </div>
  );
}

function requiresBadge() {
  return (
    <div className="row gap-sm" style={{ alignItems: 'center' }}>
      <Bot size={20} style={{ color: 'var(--violet-2)' }} />
      <b style={{ fontSize: 15 }}>Project Intelligence Assistant</b>
    </div>
  );
}

function Badge({ tone, plain, children }: { tone?: string; plain?: boolean; children: React.ReactNode }) {
  return <span className={`badge badge-${tone || 'neutral'} ${plain ? 'badge-plain' : ''}`}>{children}</span>;
}
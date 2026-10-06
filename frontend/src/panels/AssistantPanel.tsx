import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import {
  Bot, Send, FileText, Loader2, Sparkles, MessagesSquare, Plus, Trash2, ShieldCheck, ShieldAlert,
} from 'lucide-react';
import {
  api,
  type AssistantAnswer,
  type AssistantConversation,
  type AssistantMessage,
} from '../api/client';
import { useToast } from '../ui/ToastContext';
import { initials } from '../utils/format';
import { useAuth } from '../auth/AuthContext';
import { Badge } from '../components/Badge';

interface Msg {
  role: 'user' | 'ai';
  text: string;
  sources?: any[];
  provider?: string;
  model?: string;
  error?: string;
  grounded?: boolean;
  insufficient?: boolean;
  usedStructured?: boolean;
  usedHistory?: boolean;
}

const SUGGESTIONS = [
  'What are the current risks?',
  'What are the pending action items?',
  'What blockers were identified?',
  'What are the project deliverables?',
  'How healthy is this project and why?',
  'What deadlines are approaching?',
];

export function AssistantPanel({ projectId }: { projectId: number }) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [conversations, setConversations] = useState<AssistantConversation[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
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

  const loadConversations = useCallback(async () => {
    try {
      const res = await api.listConversations(projectId);
      setConversations(res.conversations || []);
    } catch {
      // Conversation history is a convenience; the chat still works without it.
      setConversations([]);
    }
  }, [projectId]);

  useEffect(() => { loadConversations(); }, [loadConversations]);

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
      const res: AssistantAnswer = await api.askProjectAssistant(projectId, {
        question,
        conversation_id: activeId,
      });
      if (res.conversation_id) setActiveId(res.conversation_id);
      setMessages((m) => [
        ...m,
        {
          role: 'ai',
          text: res.answer || 'No response.',
          sources: res.sources || [],
          provider: res.provider,
          model: res.model,
          error: res.error || res.retrieval_error,
          grounded: res.grounded,
          insufficient: res.insufficient_evidence,
          usedStructured: res.used_structured_data,
          usedHistory: res.used_conversation_context,
        },
      ]);
      loadConversations();
    } catch (err: any) {
      setMessages((m) => [
        ...m,
        { role: 'ai', text: err?.message || 'Assistant request failed.', error: 'request' },
      ]);
      toast.error(err?.message || 'Request failed.');
    } finally {
      setBusy(false);
    }
  };

  const newConversation = async () => {
    setMessages([]);
    setActiveId(null);
    try {
      const conv = await api.createConversation(projectId, 'New conversation');
      setActiveId(conv.id);
      loadConversations();
    } catch {
      setActiveId(null);
    }
  };

  const openConversation = async (id: number) => {
    try {
      const conv = await api.getConversation(projectId, id);
      setActiveId(conv.id);
      setMessages(
        (conv.messages || []).map((m: AssistantMessage) => ({
          role: m.role === 'user' ? ('user' as const) : ('ai' as const),
          text: m.content,
          sources: m.sources || [],
          provider: m.provider,
          model: m.model,
          grounded: m.grounded,
          insufficient: m.insufficient_evidence,
          usedStructured: m.used_structured_data,
          usedHistory: m.used_conversation_context,
        })),
      );
    } catch (err: any) {
      toast.error(err?.message || 'Could not open the conversation.');
    }
  };

  const removeConversation = async (id: number) => {
    try {
      await api.deleteConversation(projectId, id);
      if (activeId === id) {
        setMessages([]);
        setActiveId(null);
      }
      loadConversations();
    } catch (err: any) {
      toast.error(err?.message || 'Could not delete the conversation.');
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      ask(input);
    }
  };

  return (
    <div className="col" style={{ gap: 16 }}>
      {conversations.length > 0 && (
        <div className="panel">
          <div className="panel-head">
            <h3><MessagesSquare size={16} /> Conversations <span className="ph-sub">· stored for this project</span></h3>
            <button className="btn btn-secondary btn-sm" onClick={newConversation}>
              <Plus size={14} /> New chat
            </button>
          </div>
          <div className="panel-body">
            <div className="chip-row">
              {conversations.map((c) => (
                <span key={c.id} className={`chip ${activeId === c.id ? 'active' : ''}`}>
                  <button
                    onClick={() => openConversation(c.id)}
                    style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', padding: 0 }}
                  >
                    {c.title || `Chat ${c.id}`} · {c.message_count}
                  </button>
                  <button
                    onClick={() => removeConversation(c.id)}
                    title="Delete conversation"
                    style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', padding: 0, marginLeft: 4 }}
                  >
                    <Trash2 size={11} />
                  </button>
                </span>
              ))}
            </div>
          </div>
        </div>
      )}

      <div className="panel">
        <div className="panel-head">
          <h3><Bot size={16} /> Project Assistant <span className="ph-sub">· project-scoped · conversation memory</span></h3>
          {hasContext === false && <Badge tone="warn" plain>No processed documents yet</Badge>}
          {hasContext === true && <Badge tone="ok" plain>Knowledge base ready</Badge>}
          {activeId === null && conversations.length > 0 && (
            <button className="btn btn-ghost btn-sm" onClick={newConversation}><Plus size={13} /> New chat</button>
          )}
        </div>

        <div
          className="panel-body chat-scroll"
          ref={scrollRef}
          style={{ maxHeight: 520, overflowY: 'auto', gap: 18 }}
        >
          {messages.length === 0 && (
            <div className="col" style={{ alignItems: 'center', padding: '22px 0' }}>
              <div className="row gap-sm" style={{ alignItems: 'center' }}>
                <Bot size={20} style={{ color: 'var(--violet-2)' }} />
                <b style={{ fontSize: 15 }}>Conversational Project Assistant</b>
              </div>
              <div className="tiny dim" style={{ textAlign: 'center', maxWidth: 440, lineHeight: 1.6 }}>
                Answers are grounded in this project’s uploaded documents, risks and tasks, and the assistant
                remembers the conversation. If the evidence is not there, it says so instead of guessing.
              </div>
              <div className="chip-row mt-1" style={{ justifyContent: 'center', maxWidth: 480 }}>
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

                {m.role === 'ai' && (
                  <div className="row wrap gap-sm">
                    {m.insufficient ? (
                      <Badge tone="warn" plain><ShieldAlert size={10} /> insufficient evidence</Badge>
                    ) : m.grounded ? (
                      <Badge tone="ok" plain><ShieldCheck size={10} /> grounded</Badge>
                    ) : null}
                    {m.usedStructured && <Badge tone="violet" plain>project data</Badge>}
                    {m.usedHistory && <Badge tone="cyan" plain>conversation context</Badge>}
                  </div>
                )}

                {m.sources && m.sources.length > 0 && (
                  <div className="sources">
                    <div className="eyebrow" style={{ fontSize: 10 }}>Sources</div>
                    {m.sources.map((s, j) => (
                      <span key={j} className="source-chip" title={s.label}>
                        <FileText size={12} /> {s.label}
                      </span>
                    ))}
                  </div>
                )}

                {m.error && <div className="tiny" style={{ color: 'var(--amber)' }}>Note: {String(m.error).slice(0, 200)}</div>}
                {m.provider && (
                  <div className="tiny dim">via {m.provider} · {m.model}</div>
                )}
              </div>
            </div>
          ))}

          {busy && (
            <div className="msg msg-ai">
              <div className="msg-avatar"><Bot size={15} /></div>
              <div className="msg-bubble row gap-sm" style={{ color: 'var(--text-2)' }}>
                <Loader2 size={15} className="spin" />
                <Sparkles size={14} style={{ color: 'var(--violet-2)' }} /> Searching this project’s documents and data…
              </div>
            </div>
          )}
        </div>

        <div className="chat-input-bar">
          <textarea
            className="input"
            placeholder="Ask about this project…  (Enter to send, Shift+Enter for new line)"
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
    </div>
  );
}

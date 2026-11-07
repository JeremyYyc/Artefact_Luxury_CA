import { useEffect, useState, type FormEvent } from 'react';

type Message = { text: string; at: string | null };
type HistoryMessage = Message & { role: 'customer' | 'advisor' };
type Input = {
  scenario: 'PO';
  client_id: string;
  product_id: string;
  color: string;
  history: HistoryMessage[];
  current_message: Message;
};
type Preset = {
  id: string;
  title: string;
  input: Input;
  client_name: string;
  salutation: string;
  product_name: string;
  price: { amount: number; currency: 'CNY' };
  strategy_labels: string[];
};
type Slot = 1 | 2 | 3;
type Reply = { slot: Slot; strategy: string; text: string };
type GenerateResponse = { request_id: string; options: Reply[] };
type Result = GenerateResponse & { input: Input };

const slots = [1, 2, 3] as const;
const priceFormat = new Intl.NumberFormat('zh-CN');

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body = await response.json().catch(() => null);
  if (!response.ok || body === null) {
    throw new Error(typeof body?.detail === 'string' ? body.detail : '服务暂时不可用，请重试。');
  }
  return body;
}

function errorText(error: unknown) {
  return error instanceof Error && !(error instanceof TypeError)
    ? error.message
    : '暂时无法连接服务，请重试。';
}

export default function App() {
  const [preset, setPreset] = useState<Preset>();
  const [input, setInput] = useState<Input>();
  const [loadError, setLoadError] = useState('');
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState('');
  const [result, setResult] = useState<Result>();
  const [editing, setEditing] = useState<Slot>();
  const [copied, setCopied] = useState<Slot>();
  const [copyError, setCopyError] = useState('');

  useEffect(() => {
    const abort = new AbortController();
    fetchJson<Preset[]>('/api/presets', { signal: abort.signal })
      .then((presets) => {
        const example = presets.find(({ id }) => id === 'PO-02');
        if (!example) throw new Error('暂时无法载入 PO-02 示例。');
        setPreset(example);
        setInput(example.input);
      })
      .catch((error: unknown) => {
        if (!abort.signal.aborted) setLoadError(errorText(error));
      });
    return () => abort.abort();
  }, [loadAttempt]);

  const usable = Boolean(result && result.input === input && !generating && !generateError);

  function changeInput(next: Input) {
    setInput(next);
    setEditing(undefined);
    setCopied(undefined);
    setCopyError('');
    setGenerateError('');
  }

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!input || generating) return;

    setGenerating(true);
    setGenerateError('');
    setEditing(undefined);
    setCopied(undefined);
    setCopyError('');
    setResult(undefined);
    try {
      const body = await fetchJson<GenerateResponse>('/api/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...input, history: input.history.filter(({ text }) => text.trim()) }),
      });
      setResult({ ...body, input });
    } catch (error) {
      setGenerateError(errorText(error));
    } finally {
      setGenerating(false);
    }
  }

  function editReply(slot: Slot, text: string) {
    setCopied(undefined);
    setCopyError('');
    setResult((previous) => previous && {
      ...previous,
      options: previous.options.map((reply) => reply.slot === slot ? { ...reply, text } : reply),
    });
  }

  async function copyReply(reply: Reply) {
    if (!usable || !reply.text.trim()) return;
    try {
      await navigator.clipboard.writeText(reply.text);
      setCopied(reply.slot);
      setCopyError('');
    } catch {
      setCopied(undefined);
      setCopyError('无法访问剪贴板，可手动选中正文复制。');
    }
  }

  return (
    <div className="layout">
      <aside className="sidebar" aria-label="工作台导航">
        <div className="wordmark">LIUXI <span>留隙</span></div>
        <p className="eyebrow">CLIENT ADVISOR</p>
        <a className="nav-link" href="#workspace" aria-current="page">✧ <span>回复工作台</span></a>
        <div className="sidebar-foot">精品店顾问<br /><span>留隙上海精品店</span></div>
      </aside>

      <main className="workspace" id="workspace">
        <div className="topline"><span>顾问工作台 <span aria-hidden="true">/</span> 主功能</span><span className="tag">虚构品牌 · 示例数据</span></div>
        <header className="hero">
          <p className="eyebrow">AI REPLY STUDIO</p>
          <h1>AI 回复工作台</h1>
          <p>核对顾客对话，生成三条可选的中文微信回复。</p>
        </header>

        <section className="panel" aria-labelledby="scenario-title">
          <h2 id="scenario-title"><span className="section-number">01</span> 测试场景</h2>
          {loadError && (
            <div className="notice" role="alert">
              {loadError} <button type="button" onClick={() => { setLoadError(''); setLoadAttempt(loadAttempt + 1); }}>重新载入</button>
            </div>
          )}
          {!preset && !loadError && <p role="status">正在载入 PO-02 示例…</p>}
          {preset && input && (
            <form onSubmit={generate} aria-busy={generating}>
              <fieldset disabled={generating}>
                <div className="case-title">
                  <span className="tag">{preset.id} · 价格异议</span>
                  <button type="button" className="text-button" onClick={() => changeInput(structuredClone(preset.input))}>恢复示例</button>
                </div>
                <h3>{preset.title}</h3>
                <div className="facts" aria-label="生成依据">
                  <span>{preset.client_name} · {preset.salutation}</span>
                  <span>{input.color} · {preset.product_name}</span>
                  <span>目录价 ¥{priceFormat.format(preset.price.amount)}</span>
                  <span>优惠待核实</span>
                </div>
                <details>
                  <summary>查看或修改历史对话</summary>
                  {input.history.map((message, index) => (
                    <label className="field" key={index}>
                      历史 · {message.role === 'customer' ? '顾客' : '顾问'} {message.at}
                      <textarea rows={2} maxLength={2000} value={message.text} onChange={(event) => {
                        const text = event.target.value;
                        changeInput({ ...input, history: input.history.map((entry, position) => position === index ? { ...entry, text } : entry) });
                      }} />
                    </label>
                  ))}
                </details>
                <label className="field">
                  本轮待回复
                  <textarea rows={2} required maxLength={2000} value={input.current_message.text} onChange={(event) => changeInput({
                    ...input, current_message: { ...input.current_message, text: event.target.value },
                  })} />
                </label>
                <div className="submit-row">
                  <button className="primary" type="submit" disabled={!input.current_message.text.trim() || generating}>
                    {generating ? '正在生成与校验…' : generateError ? '重新生成' : '生成 3 条回复'}
                  </button>
                </div>
              </fieldset>
            </form>
          )}
          {generateError && <p className="notice" role="alert">{generateError}</p>}
        </section>

        <section className="results" aria-labelledby="results-title" aria-busy={generating}>
          <div className="results-heading">
            <h2 id="results-title"><span className="section-number">02</span> 候选回复</h2>
            {result && <span className="tag" role="status">{usable ? '3 条已通过校验（生成稿）' : '待重新生成'}</span>}
          </div>
          <p className="hint">比较表达重点，复制到微信前可先修改。</p>
          <div className="cards">
            {slots.map((slot) => {
              const reply = result?.options.find((option) => option.slot === slot);
              const isEditing = usable && editing === slot;
              return (
                <article className="reply-card" key={slot} aria-labelledby={`reply-title-${slot}`}>
                  <div className="card-heading">
                    <span className="slot-letter" aria-hidden="true">{'ABC'[slot - 1]}</span>
                    <h3 id={`reply-title-${slot}`}>{preset?.strategy_labels[slot - 1] ?? `回复 ${slot}`}</h3>
                  </div>
                  {isEditing && reply ? (
                    <textarea aria-label={`编辑第 ${slot} 条回复正文`} rows={4} value={reply.text} onChange={(event) => editReply(slot, event.target.value)} />
                  ) : (
                    <p className={reply && usable ? 'reply-text' : 'reply-text muted'}>{reply?.text ?? (generating ? '正在生成和校验…' : '等待生成')}</p>
                  )}
                  <div className="card-actions">
                    <button type="button" disabled={!usable} aria-label={isEditing ? `完成第 ${slot} 条回复的编辑` : `编辑第 ${slot} 条回复`} onClick={() => setEditing(isEditing ? undefined : slot)}>{isEditing ? '完成' : '编辑'}</button>
                    <button type="button" disabled={!usable || !reply?.text.trim()} aria-label={`复制第 ${slot} 条回复`} onClick={() => reply && void copyReply(reply)}>{copied === slot ? '已复制' : '复制'}</button>
                  </div>
                </article>
              );
            })}
          </div>
          {usable && copyError && <p className="notice" role="alert">{copyError}</p>}
          {usable && copied && <p className="copy-status" role="status">第 {copied} 条正文已复制。</p>}
        </section>
        <footer>发送由顾问在微信中完成。</footer>
      </main>
    </div>
  );
}

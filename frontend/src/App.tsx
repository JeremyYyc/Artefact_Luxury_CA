import { useEffect, useState, type FormEvent } from 'react';
import { libraryPages, LibraryPages, type LibraryPage } from './LibraryPages';
import { FreeInputFields, GenerationBasis } from './WorkspaceDetails';
import { priceFormat, scenarioLabels, type GenerateResponse, type Input, type Library, type Preset, type Reply, type Slot } from './types';

type Result = GenerateResponse & { input: Input };
type Page = 'workspace' | LibraryPage;

const slots = [1, 2, 3] as const;
const pages = {
  workspace: { title: 'AI 回复工作台', eyebrow: 'AI REPLY STUDIO', intro: '核对顾客对话，生成三条可选的中文微信回复。' },
  ...libraryPages,
};

function currentPage(): Page {
  const name = window.location.hash.slice(1);
  return Object.hasOwn(pages, name) ? name as Page : 'workspace';
}

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
  const [presets, setPresets] = useState<Preset[]>([]);
  const [library, setLibrary] = useState<Library>();
  const [page, setPage] = useState(currentPage);
  const [presetId, setPresetId] = useState('');
  const [free, setFree] = useState(false);
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
    function navigate() {
      setPage(currentPage());
      window.scrollTo(0, 0);
    }
    window.addEventListener('hashchange', navigate);
    return () => window.removeEventListener('hashchange', navigate);
  }, []);

  useEffect(() => {
    const abort = new AbortController();
    const options = { signal: abort.signal };
    Promise.all([fetchJson<Preset[]>('/api/presets', options), fetchJson<Library>('/api/library', options)])
      .then(([items, catalog]) => {
        const example = items.find(({ id }) => id === 'PO-02') ?? items[0];
        if (!example) throw new Error('暂时无法载入场景预设。');
        setPresets(items);
        setLibrary(catalog);
        setPresetId(example.id);
        setInput(example.input);
      })
      .catch((error: unknown) => {
        if (!abort.signal.aborted) setLoadError(errorText(error));
      });
    return () => abort.abort();
  }, [loadAttempt]);

  const preset = presets.find(({ id }) => id === presetId);
  const message = input?.current_message;
  const outreach = input?.outreach;
  const task = message?.text ?? input?.advisor_goal ?? '';
  const canGenerate = Boolean(task.trim() && (!outreach || outreach.occasion.trim()));
  const usable = Boolean(result && result.input === input && !generating && !generateError);
  const strategyLabels = free ? (result && library?.strategies[result.scenario]) : preset?.strategy_labels;

  function changeInput(next: Input) {
    setInput(next);
    setEditing(undefined);
    setCopied(undefined);
    setCopyError('');
    setGenerateError('');
  }

  function selectPreset(id: string) {
    const selected = presets.find((item) => item.id === id);
    if (!selected) return;
    setPresetId(selected.id);
    setFree(false);
    setResult(undefined);
    changeInput(selected.input);
  }

  function startFreeInput() {
    setFree(true);
    setResult(undefined);
    changeInput({
      scenario: null, client_id: null, product_id: null, product_ids: [], color: null, event_id: null,
      advisor_goal: null, outreach: null, history: [], current_message: { text: '', at: null },
    });
  }

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!input || !canGenerate || generating) return;

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
        body: JSON.stringify({
          ...input,
          client_background: input.client_background?.trim() || null,
          history: input.history.filter(({ text }) => text.trim()),
        }),
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
        <nav className="side-nav">
          <a className="nav-link" href="#workspace" aria-current={page === 'workspace' ? 'page' : undefined}>✧ <span>回复工作台</span></a>
          {(Object.keys(libraryPages) as LibraryPage[]).map((name) => (
            <a className="nav-link" href={`#${name}`} key={name} aria-current={page === name ? 'page' : undefined}>{libraryPages[name].title}</a>
          ))}
        </nav>
        <div className="sidebar-foot">精品店顾问<br /><span>留隙上海精品店</span></div>
      </aside>

      <main className="workspace">
        <div className="topline"><span>顾问工作台 <span aria-hidden="true">/</span> {page === 'workspace' ? '主功能' : pages[page].title}</span><span className="tag">虚构品牌 · 示例数据</span></div>
        <header className="hero">
          <p className="eyebrow">{pages[page].eyebrow}</p>
          <h1>{pages[page].title}</h1>
          <p>{pages[page].intro}</p>
        </header>
        {loadError && (
          <div className="notice" role="alert">
            {loadError} <button type="button" onClick={() => { setLoadError(''); setLoadAttempt(loadAttempt + 1); }}>重新载入</button>
          </div>
        )}
        {!library && !loadError && <p role="status">正在载入工作台资料…</p>}
        {page === 'workspace' ? <>
        <section className="panel" aria-labelledby="scenario-title">
          <h2 id="scenario-title"><span className="section-number">01</span> 测试场景</h2>
          {preset && input && library && (
            <form onSubmit={generate} aria-busy={generating}>
              <fieldset disabled={generating}>
                {free ? <>
                  <div className="case-title"><span className="tag">自由输入 · 自动识别</span><button type="button" className="text-button" onClick={() => selectPreset(presetId)}>返回场景预设</button></div>
                  <FreeInputFields input={input} library={library} changeInput={changeInput} />
                </> : <>
                  <div className="case-title">
                  <label className="field preset-picker">
                    场景预设
                    <select value={presetId} onChange={(event) => selectPreset(event.target.value)}>
                      {presets.map((item) => (
                        <option value={item.id} key={item.id}>
                          {scenarioLabels[item.input.scenario]} · {item.id}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="case-actions">
                    <button type="button" className="text-button" onClick={() => changeInput(structuredClone(preset.input))}>恢复示例</button>
                    <button type="button" onClick={startFreeInput}>自由输入</button>
                  </div>
                </div>
                <h3>{preset.title}</h3>
                <div className="facts" aria-label="已选资料">
                  <span>{preset.client_name} · {preset.salutation}</span>
                  {preset.product_names.map((name, index) => (
                    <span key={name}>{index === 0 && input.color ? `${input.color} · ${name}` : name}</span>
                  ))}
                  {preset.event_name && <span>{preset.event_name}</span>}
                  {preset.price && <span>目录价 ¥{priceFormat.format(preset.price.amount)}</span>}
                  {input.scenario === 'PO' && <span>优惠待核实</span>}
                </div>
                </>}
                {(free || input.history.length > 0) && <details key={free ? 'free-history' : presetId}>
                  <summary>查看或修改历史对话</summary>
                  {input.history.map((message, index) => (
                    <div className="history-entry" key={index}>
                      {free && <div className="history-actions">
                        <select aria-label={`第 ${index + 1} 条历史消息角色`} value={message.role} onChange={(event) => {
                          const role = event.target.value === 'advisor' ? 'advisor' : 'customer';
                          changeInput({ ...input, history: input.history.map((entry, position) => position === index ? { ...entry, role } : entry) });
                        }}><option value="customer">顾客</option><option value="advisor">顾问</option></select>
                        <button type="button" onClick={() => changeInput({ ...input, history: input.history.filter((_, position) => position !== index) })}>移除</button>
                      </div>}
                      <label className="field">
                      历史 · {message.role === 'customer' ? '顾客' : '顾问'} {message.at}
                      <textarea rows={2} maxLength={2000} value={message.text} onChange={(event) => {
                        const text = event.target.value;
                        changeInput({ ...input, history: input.history.map((entry, position) => position === index ? { ...entry, text } : entry) });
                      }} />
                      </label>
                    </div>
                  ))}
                  {free && <button type="button" disabled={input.history.length >= 12} onClick={() => changeInput({
                    ...input, history: [...input.history, { role: 'customer', text: '', at: null }],
                  })}>添加历史消息</button>}
                </details>}
                {(outreach || (free && !message)) && (
                  <div className="outreach-fields">
                    <label className="field">
                      {free ? '问候类型（选填）' : '问候类型'}
                      <input required={!free} maxLength={60} value={outreach?.occasion ?? ''} onChange={(event) => changeInput({
                        ...input, outreach: free && !event.target.value.trim() ? null : { occasion: event.target.value, send_on: outreach?.send_on ?? null },
                      })} />
                    </label>
                    <label className="field">
                      计划发送日期
                      <input type="date" disabled={!outreach} value={outreach?.send_on ?? ''} onChange={(event) => outreach && changeInput({
                        ...input, outreach: { ...outreach, send_on: event.target.value || null },
                      })} />
                    </label>
                  </div>
                )}
                <label className="field">
                  {message ? '本轮待回复' : '顾问任务'}
                  <textarea rows={2} required maxLength={2000} value={task} onChange={(event) => {
                    const text = event.target.value;
                    changeInput(message
                      ? { ...input, current_message: { ...message, text } }
                      : { ...input, advisor_goal: text });
                  }} />
                </label>
                <GenerationBasis input={input} library={library} result={usable ? result : undefined} />
                <div className="submit-row">
                  <button className="primary" type="submit" disabled={!canGenerate || generating}>
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
            {usable && result?.routing.mode === 'automatic' && <span className="tag">识别为 {scenarioLabels[result.scenario]}</span>}
            {result && <span className="tag" role="status">{usable ? '3 条已生成' : '待重新生成'}</span>}
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
                    <h3 id={`reply-title-${slot}`}>{strategyLabels?.[slot - 1] ?? `回复 ${slot}`}</h3>
                  </div>
                  {usable && reply?.review_status === 'regenerated' && <p className="hint">重生成稿 · 复制前请核对</p>}
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
        </> : library && <LibraryPages page={page} library={library} />}
      </main>
    </div>
  );
}

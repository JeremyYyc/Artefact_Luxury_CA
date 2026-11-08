import { EventDetails, ProductDetails, formatShanghaiTime } from './LibraryPages';
import { scenarioLabels, type GenerateResponse, type Input, type Library } from './types';

function selectedProducts(input: Input, library: Library) {
  return [input.product_id, ...input.product_ids]
    .map((id) => library.products.find(({ product_id }) => product_id === id))
    .filter((product) => product !== undefined);
}

export function FreeInputFields({ input, library, changeInput }: { input: Input; library: Library; changeInput: (input: Input) => void }) {
  const products = selectedProducts(input, library);
  const primaryEvent = library.events.find(({ event_id }) => event_id === input.event_id);
  const invitee = input.client_id;
  return (
    <>
      <div className="free-grid">
        <label className="field">
          任务方式
          <select value={input.current_message ? 'reply' : 'outreach'} onChange={(event) => {
            const text = input.current_message?.text ?? input.advisor_goal ?? '';
            changeInput(event.target.value === 'reply'
              ? { ...input, current_message: { text, at: null }, advisor_goal: null, outreach: null, confirmed_invitation: null }
              : { ...input, current_message: null, advisor_goal: text });
          }}>
            <option value="reply">回复顾客</option>
            <option value="outreach">主动触达 / 描述任务</option>
          </select>
        </label>
        <label className="field">
          关联客户（选填）
          <select value={input.client_id ?? ''} onChange={(event) => changeInput({ ...input, client_id: event.target.value || null, confirmed_invitation: null })}>
            <option value="">未选择客户</option>
            {library.clients.map(({ client_id, full_name }) => <option value={client_id} key={client_id}>{full_name}</option>)}
          </select>
        </label>
        <label className="field">
          关联商品（选填）
          <select value="" disabled={products.length >= 3} onChange={(event) => {
            if (event.target.value) changeInput({ ...input, product_ids: [...input.product_ids, event.target.value] });
          }}>
            <option value="">添加目录商品</option>
            {library.products.filter(({ product_id }) => !input.product_ids.includes(product_id)).map(({ product_id, name }) => (
              <option value={product_id} key={product_id}>{name}</option>
            ))}
          </select>
        </label>
        <label className="field">
          关联活动（选填）
          <select value={input.event_id ?? ''} onChange={(event) => {
            const eventId = event.target.value || null;
            changeInput({ ...input, event_id: eventId, related_event_ids: eventId
              ? input.related_event_ids?.filter((id) => id !== eventId) : [], confirmed_invitation: null });
          }}>
            <option value="">未选择活动</option>
            {library.events.map(({ event_id, name }) => <option value={event_id} key={event_id}>{name}</option>)}
          </select>
        </label>
        {primaryEvent && <label className="field">
          补充活动参考（选填）
          <select value={input.related_event_ids?.[0] ?? ''} onChange={(event) => changeInput({
            ...input, related_event_ids: event.target.value ? [event.target.value] : [],
          })}>
            <option value="">未选择参考活动</option>
            {library.events.filter(({ event_id }) => event_id !== primaryEvent.event_id).map(({ event_id, name }) => (
              <option value={event_id} key={event_id}>{name}</option>
            ))}
          </select>
        </label>}
      </div>
      {primaryEvent?.rsvp.access === 'invite_only' && invitee && !input.current_message && (
        <label className="selection-row">
          <input type="checkbox" checked={Boolean(input.confirmed_invitation)} onChange={(event) => changeInput({
            ...input, confirmed_invitation: event.target.checked ? { event_id: primaryEvent.event_id, client_id: invitee } : null,
          })} />
          已核实该客户本人可受邀
        </label>
      )}
      {products.length > 0 && (
        <div className="selection-row">
          {products.map(({ product_id, name }, index) => (
            <button key={product_id} type="button" className="tag" aria-label={`移除 ${name}`} onClick={() => changeInput({
              ...input, product_ids: input.product_ids.filter((id) => id !== product_id), color: index === 0 ? null : input.color,
            })}>{name} ×</button>
          ))}
          <label className="field color-field">
            首件商品颜色（选填）
            <select value={input.color ?? ''} onChange={(event) => changeInput({ ...input, color: event.target.value || null })}>
              <option value="">未指定</option>
              {products[0].color_options.map(({ color }) => <option value={color} key={color}>{color}</option>)}
            </select>
          </label>
        </div>
      )}
      <label className="field">
        客户背景（选填）
        <textarea rows={2} maxLength={2000} value={input.client_background ?? ''} placeholder="例如客户的使用习惯、偏好或沟通背景" onChange={(event) => changeInput({ ...input, client_background: event.target.value })} />
      </label>
    </>
  );
}

export function GenerationBasis({ input, library, result }: { input: Input; library: Library; result?: GenerateResponse }) {
  const basis = result?.basis;
  const client = library.clients.find(({ client_id }) => client_id === input.client_id);
  const salutation = basis ? basis.client?.salutation : client && `${client.full_name} · ${client.salutation}`;
  const message = basis ? basis.current_message : input.current_message;
  const goal = basis ? basis.advisor_goal : input.advisor_goal;
  const history = basis?.history ?? input.history.filter(({ text }) => text.trim());
  const background = basis ? basis.client_background : input.client_background;
  const outreach = basis ? basis.outreach : input.outreach;
  const invitation = basis
    ? basis.verified_facts?.find(({ topic }) => topic === '私享场拟邀约对象')?.value.scope
    : input.confirmed_invitation && '仅限本人，席位仍需确认';
  const events = [input.event_id, ...(input.related_event_ids ?? [])]
    .map((id) => library.events.find(({ event_id }) => event_id === id))
    .filter((event) => event !== undefined);
  return (
    <details className="basis">
      <summary>查看生成依据</summary>
      {result ? (
        <p className="hint">{result.routing.mode === 'automatic' ? '自动识别' : '已选场景'} · {scenarioLabels[result.scenario]}{result.routing.reason ? `。${result.routing.reason}` : ''}</p>
      ) : <p className="hint">生成前显示当前表单和已选资料，生成后显示本轮实际采用的资料。</p>}
      <dl className="records">
        {salutation && <div><dt>客户称呼</dt><dd>{salutation}</dd></div>}
        {basis && <div><dt>资料核对时间（上海）</dt><dd>{formatShanghaiTime(basis.effective_at)}</dd></div>}
        {background?.trim() && <div><dt>顾问补充的客户背景</dt><dd>{background}</dd></div>}
        {message?.text.trim() && <div><dt>本轮顾客消息</dt><dd>{message.text}</dd></div>}
        {goal?.trim() && <div><dt>顾问任务</dt><dd>{goal}</dd></div>}
        {outreach && <div><dt>问候安排</dt><dd>{outreach.occasion}{outreach.send_on ? ` · ${outreach.send_on}` : ''}</dd></div>}
        {invitation && <div><dt>私享活动资格</dt><dd>顾问已核实 · {invitation}</dd></div>}
      </dl>
      {history.length > 0 && <>
        <h3>历史对话</h3>
        <ul className="basis-history">{history.map(({ role, at, text }, index) => <li key={index}><strong>{role === 'customer' ? '顾客' : '顾问'}</strong> {at && <span>{at} · </span>}{text}</li>)}</ul>
      </>}
      {(basis?.products ?? selectedProducts(input, library)).map((product) => (
        <section className="basis-item" key={product.product_id}>
          <h3>{product.name}</h3>
          <ProductDetails product={product} />
        </section>
      ))}
      {(basis?.events ?? events).map((item) => <EventDetails event={item} key={item.event_id} />)}
      {basis?.to_confirm.length ? <>
        <h3>待核实事项</h3>
        <ul className="basis-history">{basis.to_confirm.map(({ topic, note }) => <li key={topic}><strong>{topic}</strong> · {note}</li>)}</ul>
      </> : null}
    </details>
  );
}

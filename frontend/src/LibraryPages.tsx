import { useState } from 'react';
import { priceFormat, type EventFacts, type Library, type ProductFacts } from './types';

const eventTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});
const clockTime = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const eventStates = { open: '可申请预约', closed: '报名已截止', started: '进行中', ended: '已结束', unknown: '状态待核实' };

export function formatShanghaiTime(value: string) {
  return eventTime.format(new Date(value));
}

export const libraryPages = {
  clients: { title: '客户资料', eyebrow: 'CLIENT PROFILES', intro: '查阅客户称呼、联系时间、购买和活动记录。' },
  products: { title: '商品资料', eyebrow: 'PRODUCT CATALOG', intro: '核对规格、价格、库存快照和同款护理说明。' },
  brand: { title: '品牌资料', eyebrow: 'BRAND GUIDE', intro: '查看品牌定位、微信话术准则和门店活动。' },
};

export type LibraryPage = keyof typeof libraryPages;

export function ProductDetails({ product }: { product: ProductFacts }) {
  const dimensions = product.size_and_capacity?.external_dimensions_cm;
  const inventory = product.inventory_snapshot;
  return (
    <>
      <dl className="detail-grid">
        {product.selected_color && <div><dt>选定颜色</dt><dd>{product.selected_color}</dd></div>}
        {inventory && <div><dt>{inventory.as_of_date} · {inventory.location}库存</dt><dd>
          {inventory.colors.map(({ color, stock_quantity }) => `${color} ${stock_quantity} 件`).join('、')}。现货需核实。
        </dd></div>}
        {product.price && <div><dt>目录价</dt><dd>¥{priceFormat.format(product.price.amount)}</dd></div>}
        {dimensions && <div><dt>外部尺寸</dt><dd>{dimensions.width} × {dimensions.height} × {dimensions.depth} cm</dd></div>}
        {product.weight && <div><dt>重量</dt><dd>{product.weight.grams} g（{product.weight.measurement_basis}）</dd></div>}
        {product.materials_and_craft && <div><dt>外部材质</dt><dd>{product.materials_and_craft.exterior}</dd></div>}
        {product.materials_and_craft?.lining && <div><dt>内衬材质</dt><dd>{product.materials_and_craft.lining}</dd></div>}
        {product.materials_and_craft?.craft_details.length ? <div><dt>工艺</dt><dd>{product.materials_and_craft.craft_details.join('、')}</dd></div> : null}
        {product.size_and_capacity?.typical_contents && <div><dt>典型随身物</dt><dd>{product.size_and_capacity.typical_contents.join('、')}</dd></div>}
        {product.use_cases_and_features && <div><dt>款式特点</dt><dd>{product.use_cases_and_features.features.join('、')}</dd></div>}
      </dl>
      {product.care_manual && (
        <details>
          <summary>护理手册 v{product.care_manual.version}</summary>
          <dl className="records">
            {product.care_manual.care_topics.map(({ topic, steps }) => <div key={topic}><dt>{topic}</dt><dd>{steps.join('')}</dd></div>)}
            <div><dt>应避免</dt><dd>{product.care_manual.avoid.join('、')}</dd></div>
            <div><dt>何时联系顾问</dt><dd>{product.care_manual.contact_advisor_when.join('、')}</dd></div>
          </dl>
        </details>
      )}
    </>
  );
}

export function EventDetails({ event }: { event: EventFacts }) {
  const { rsvp, venue } = event;
  return (
    <div className="event-detail">
      <span className="tag">{eventStates[event.invite_state]}</span>
      <h3>{event.name}</h3>
      <p>{formatShanghaiTime(event.starts_at)} 至 {clockTime.format(new Date(event.ends_at))} · {venue.name} · {venue.area}</p>
      <p>{rsvp.access === 'invite_only' ? '仅限受邀顾客' : '需要预约'}
        {rsvp.max_guests_per_invitee === 0 && ' · 不可携伴'}
        {!!rsvp.max_guests_per_invitee && ` · 可携伴 ${rsvp.max_guests_per_invitee} 人`}
      </p>
      {rsvp.deadline_at && <p>报名截止 {formatShanghaiTime(rsvp.deadline_at)} · {rsvp.channel}</p>}
      {event.public_milestone && <p>{event.public_milestone.type === 'product_launch' ? '商品上市' : '陈列正式开放'} · {event.public_milestone.date}</p>}
    </div>
  );
}

function Clients({ library }: { library: Library }) {
  const [selectedId, setSelectedId] = useState(library.clients[0].client_id);
  const client = library.clients.find((item) => item.client_id === selectedId) ?? library.clients[0];
  return (
    <div className="library-grid">
      <section className="panel catalog-picker" aria-label="客户列表">
        <h2>客户列表 <span className="tag">{library.clients.length} 位</span></h2>
        {library.clients.map((item) => (
          <button key={item.client_id} type="button" aria-pressed={item === client} onClick={() => setSelectedId(item.client_id)}>
            <strong>{item.full_name}</strong><small>{library.segment_labels[item.segment]}</small>
          </button>
        ))}
      </section>
      <section className="panel library-content" aria-label="客户详情">
        <span className="tag">{library.segment_labels[client.segment]}</span>
        <h2>{client.full_name}</h2>
        <dl className="detail-grid">
          <div><dt>称呼</dt><dd>{client.salutation}</dd></div>
          <div><dt>首次联系</dt><dd>{client.first_contact_date}</dd></div>
          <div><dt>最近联系</dt><dd>{client.last_contact_date}</dd></div>
        </dl>
        <h3>购买记录</h3>
        {client.recent_purchases.length ? <ul className="record-list">{client.recent_purchases.map((item) => (
          <li key={`${item.product_name}-${item.purchased_on}`}><strong>{item.product_name}</strong><span>{item.purchased_on} · 成交价 ¥{priceFormat.format(item.transaction_price.amount)}{item.catalog_status === 'archived' ? ' · 已归档' : ''}</span></li>
        ))}</ul> : <p className="hint">暂无购买记录</p>}
        <h3>活动记录</h3>
        {client.event_participation.length ? <ul className="record-list">{client.event_participation.map((item) => (
          <li key={`${item.event_name}-${item.attended_on}`}><strong>{item.event_name}</strong><span>{item.attended_on} · {item.status === 'attended' ? '已参加' : item.status}</span></li>
        ))}</ul> : <p className="hint">暂无活动记录</p>}
      </section>
    </div>
  );
}

function Products({ library }: { library: Library }) {
  const [selectedId, setSelectedId] = useState(library.products[0].product_id);
  const product = library.products.find((item) => item.product_id === selectedId) ?? library.products[0];
  return (
    <div className="library-grid">
      <section className="panel catalog-picker" aria-label="商品目录">
        <h2>商品目录 <span className="tag">{library.products.length} 件</span></h2>
        {library.products.map((item) => (
          <button key={item.product_id} type="button" aria-pressed={item === product} onClick={() => setSelectedId(item.product_id)}>
            <strong>{item.name}</strong><small>¥{priceFormat.format(item.price.amount)}</small>
          </button>
        ))}
      </section>
      <section className="panel library-content" aria-label="商品详情" key={product.product_id}>
        <span className="tag">{product.collection}</span>
        <h2>{product.name}</h2>
        <p>{product.positioning}</p>
        <ProductDetails product={product} />
        <h3>{product.inventory.location}库存快照</h3>
        <p className="hint">{product.inventory.as_of_date} · 现货请另行核实</p>
        <div className="facts">{product.color_options.map(({ color, stock_quantity }) => <span key={color}>{color} · {stock_quantity} 件</span>)}</div>
      </section>
    </div>
  );
}

const guidelines = [
  ['礼貌、简短', '用合适的称呼，直接回应顾客本轮的问题。'],
  ['用细节说话', '从顾客的实际使用习惯出发，核对容量、背法和材质。'],
  ['给顾客留余地', '把试背、核实或到店作为可选下一步，避免催促。'],
  ['事实以资料为准', '折扣、库存、席位和售后承诺需要核实。'],
];

function Brand({ library }: { library: Library }) {
  const { brand, events } = library;
  return (
    <div className="brand-grid">
      <section className="panel library-content">
        <span className="tag">{brand.origin_city}</span>
        <h2>{brand.name_zh} {brand.name_en}</h2>
        <p>{brand.positioning}</p>
        <h3>微信话术准则</h3>
        <ol className="guideline-list">{guidelines.map(([title, text]) => <li key={title}><strong>{title}</strong><span>{text}</span></li>)}</ol>
      </section>
      <section className="panel library-content">
        <h2>门店活动</h2>
        {[...events].reverse().map((event) => <EventDetails key={event.event_id} event={event} />)}
      </section>
    </div>
  );
}

export function LibraryPages({ page, library }: { page: LibraryPage; library: Library }) {
  if (page === 'clients') return <Clients library={library} />;
  if (page === 'products') return <Products library={library} />;
  return <Brand library={library} />;
}

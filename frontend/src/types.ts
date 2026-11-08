export const scenarioLabels = {
  BG: '生日与节日祝福',
  PR: '商品推荐',
  PC: '购后护理',
  RE: '老客唤醒',
  EV: '活动邀约',
  PO: '价格异议',
};

export type Scenario = keyof typeof scenarioLabels;
export type Message = { text: string; at: string | null };
export type HistoryMessage = Message & { role: 'customer' | 'advisor' };
export type Outreach = { occasion: string; send_on: string | null };
export type Input = {
  scenario: Scenario | null;
  client_id: string | null;
  client_background?: string | null;
  product_id: string | null;
  product_ids: string[];
  color: string | null;
  event_id: string | null;
  related_event_ids?: string[];
  confirmed_invitation?: { event_id: string; client_id: string } | null;
  advisor_goal: string | null;
  outreach: Outreach | null;
  history: HistoryMessage[];
  current_message: Message | null;
};

type Price = { amount: number; currency: 'CNY' };
export type Preset = {
  id: string;
  title: string;
  input: Input & { scenario: Scenario };
  client_name: string;
  salutation: string;
  product_names: string[];
  price: Price | null;
  event_name: string | null;
  strategy_labels: string[];
};

export type ProductFacts = {
  product_id: string;
  name: string;
  selected_color?: string;
  available_colors?: string[];
  inventory_snapshot?: { as_of_date: string; location: string; colors: { color: string; stock_quantity: number }[] };
  price?: Price;
  weight?: { grams: number; measurement_basis: string };
  size_and_capacity?: {
    external_dimensions_cm: { width: number; height: number; depth: number };
    measurement_basis: string;
    typical_contents?: string[];
  };
  materials_and_craft?: { exterior: string; lining?: string; craft_details: string[] };
  use_cases_and_features?: { use_cases: string[]; features: string[] };
  care_manual?: { version: string; care_topics: { topic: string; steps: string[] }[]; avoid: string[]; contact_advisor_when: string[] };
};

export type CatalogProduct = ProductFacts & {
  collection: string;
  positioning: string;
  price: Price;
  color_options: { color: string; stock_quantity: number }[];
  inventory: { location: string; as_of_date: string };
};

export type EventFacts = {
  event_id: string;
  name: string;
  starts_at: string;
  ends_at: string;
  venue: { name: string; area: string };
  invite_state: 'open' | 'closed' | 'started' | 'ended' | 'unknown';
  highlights: string[];
  public_milestone?: { type: string; date: string };
  rsvp: { access?: string; deadline_at?: string; max_guests_per_invitee?: number; channel?: string };
};

export type CatalogClient = {
  client_id: string;
  full_name: string;
  salutation: string;
  segment: string;
  first_contact_date: string;
  last_contact_date: string;
  recent_purchases: { product_name: string; catalog_status: string; purchased_on: string; transaction_price: Price }[];
  event_participation: { event_name: string; attended_on: string; status: string }[];
};

export type Library = {
  clients: CatalogClient[];
  segment_labels: Record<string, string>;
  products: CatalogProduct[];
  events: EventFacts[];
  brand: { name_zh: string; name_en: string; origin_city: string; positioning: string };
  strategies: Record<Scenario, string[]>;
};

export type Slot = 1 | 2 | 3;
export type Reply = { slot: Slot; strategy: string; text: string; review_status?: 'passed' | 'regenerated' | 'local_only' };
export type GenerateResponse = {
  request_id: string;
  scenario: Scenario;
  options: Reply[];
  routing: { mode: 'selected' | 'automatic'; reason: string | null };
  basis: {
    effective_at: string;
    advisor_goal: string | null;
    current_message: Message | null;
    history: HistoryMessage[];
    client: { client_id: string; salutation: string } | null;
    client_background: string | null;
    outreach: Outreach | null;
    products: ProductFacts[];
    events: EventFacts[];
    verified_facts?: { topic: string; source: string; value: { scope: string } }[];
    to_confirm: { topic: string; owner: string; note: string }[];
  };
};

export const priceFormat = new Intl.NumberFormat('zh-CN');

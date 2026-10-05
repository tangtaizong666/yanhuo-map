export interface User {
  id: number;
  username: string;
  display_name: string;
  is_merchant: boolean;
  is_staff: boolean;
  merchant_application_status?:
    "draft" | "submitted" | "needs_changes" | "approved" | "rejected" | null;
}
export interface Area {
  id: number;
  name: string;
  subtitle: string;
  latitude: number;
  longitude: number;
}
export interface Config {
  services_simulation_enabled?: boolean;
  demo_mode: boolean;
  brand: string;
  amap_key: string;
  amap_proxy: string;
  areas: Area[];
  user: User | null;
  stale_minutes?: number;
  public_base_url?: string;
}
export interface Product {
  id: number;
  name: string;
  description: string;
  image: string;
  price_cents: number;
  availability: "available" | "sold_out" | "paused" | "unavailable";
  max_order_quantity: number;
  sale_paused?: boolean;
  is_active?: boolean;
  category?: string;
  taste_options?: { name: string; choices: string[] }[];
}
export interface MerchantProduct extends Omit<
  Product,
  "availability" | "max_order_quantity"
> {
  stock: number;
  stock_version: number;
}
export interface Portion {
  options: Record<string, string>;
  note: string;
}
export interface Review {
  id: number;
  rating: number;
  content: string;
  created_at: string;
  display_name?: string;
  user_name?: string;
  username?: string;
  merchant_reply?: string;
  replied_at?: string | null;
}
export interface Stall {
  delivery?: DeliverySettings;
  wechat_payment?: PaymentReadiness;
  id: number;
  name: string;
  description: string;
  category: string;
  image: string;
  address: string;
  arrival_note?: string;
  arrival_image?: string;
  payment_qr_image?: string;
  accepting_orders?: boolean;
  usual_hours?: string;
  stop_orders_at?: string | null;
  receiving_status?: "unknown" | "recent" | "stale";
  receiving_valid_for_seconds?: number;
  business_session_id?: number | null;
  order_unavailable_reason?: string;
  session_status?: "open" | "paused" | "closed";
  is_visible?: boolean;
  latitude: number;
  longitude: number;
  area_id: number;
  area_name: string;
  status: "open" | "paused" | "closed" | "stale";
  last_confirmed_at: string | null;
  closes_at: string;
  prep_minutes: number;
  transaction_enabled: boolean;
  can_order: boolean;
  qualification_note: string;
  merchant_name: string;
  contact_phone: string;
  rating: number;
  review_count: number;
  order_count: number;
  distance_m: number | null;
  is_followed: boolean;
  products: Product[];
  reviews: Review[];
}
export type StallSummary = Pick<
  Stall,
  | "id"
  | "name"
  | "description"
  | "category"
  | "image"
  | "address"
  | "latitude"
  | "longitude"
  | "area_id"
  | "area_name"
  | "status"
  | "last_confirmed_at"
  | "prep_minutes"
  | "transaction_enabled"
  | "can_order"
  | "rating"
  | "review_count"
  | "distance_m"
  | "is_followed"
  | "products"
  | "accepting_orders"
  | "order_unavailable_reason"
  | "usual_hours"
  | "receiving_status"
  | "receiving_valid_for_seconds"
>;
export type StallMap = Omit<
  StallSummary,
  | "description"
  | "rating"
  | "review_count"
  | "distance_m"
  | "products"
  | "usual_hours"
>;
export interface MerchantStall extends Omit<Stall, "products"> {
  location_draft_address?: string;
  products: MerchantProduct[];
  public_phone_enabled: boolean;
  prep_capacity?: number | null;
  prep_active_orders: number;
  receiving_seen_at?: string | null;
  receiving_age_seconds?: number | null;
  activation: {
    is_visible: boolean;
    has_location: boolean;
    verified: boolean;
    has_sellable_products: boolean;
    blockers: string[];
    steps: {
      key: string;
      label: string;
      status: "done" | "pending" | "optional";
      owner: "merchant" | "operator";
      reason: string;
    }[];
  };
}
export interface DiscoveryPage<T> {
  results: T[];
  next: string | null;
}
export interface DiscoveredMeal {
  product: Product;
  stall: StallMap;
}
export interface MerchantApplication {
  id: number;
  status: "draft" | "submitted" | "needs_changes" | "approved" | "rejected";
  source: "self" | "assisted";
  business_name: string;
  stall_name: string;
  contact_phone: string;
  area_id: number | null;
  category: string;
  address_note: string;
  description: string;
  review_note: string;
  approved_stall_id: number | null;
  updated_at: string;
}
export interface CartItem {
  product: Product;
  quantity: number;
  portions?: Portion[];
  // Local draft identity only; never sent to the order API.
  portionKeys?: string[];
}
export type OrderStatus =
  | "pending_payment"
  | "pending"
  | "preparing"
  | "ready"
  | "delivering"
  | "arrived"
  | "completed"
  | "cancelled"
  | "rejected";
export interface DeliveryPoint {
  id: number;
  name: string;
  address: string;
  latitude: number;
  longitude: number;
  area_id: number;
}
export interface DeliverySettings {
  mode?: "simulation" | "live";
  enabled: boolean;
  approved: boolean;
  available: boolean;
  reason: string;
  fee_cents: number;
  min_order_cents: number;
  eta_min_minutes: number;
  eta_max_minutes: number;
  starts_at: string;
  ends_at: string;
  capacity?: number;
  points: DeliveryPoint[];
  point_ids: number[];
  available_points?: DeliveryPoint[];
}
export interface PaymentReadiness {
  mode?: "simulation" | "live";
  available: boolean;
  reason: string;
  channels: ("native" | "h5" | "simulation")[];
}
export interface WechatPayment {
  next_query_at?: string | null;
  mode?: "simulation" | "live";
  id: string;
  status: "creating" | "pending" | "paid" | "closed" | "reconcile" | "review";
  channel: "native" | "h5" | "simulation";
  code_url: string;
  h5_url: string;
  expires_at: string;
  error_message: string;
}
export interface PaymentRefund {
  next_query_at?: string | null;
  resolved_at?: string | null;
  mode?: "simulation" | "live";
  id: string;
  status:
    "creating" | "processing" | "success" | "closed" | "abnormal" | "reconcile";
  reason: string;
  amount_cents: number;
  created_at: string;
  completed_at: string | null;
  error_message: string;
}
export interface Order {
  payment_query_after_seconds?: number;
  allowed_actions?: string[];
  financial_hold_reason?: string;
  refunds?: PaymentRefund[];
  mode?: "simulation" | "live";
  fulfillment_type?: "pickup" | "delivery";
  items_total_cents?: number;
  delivery_fee_cents?: number;
  delivery_point_id?: number | null;
  delivery_point_name?: string;
  delivery_point_address?: string;
  delivery_point_latitude?: number | null;
  delivery_point_longitude?: number | null;
  recipient_name?: string;
  delivery_eta_min_at?: string | null;
  delivery_eta_max_at?: string | null;
  dispatched_at?: string | null;
  arrived_at?: string | null;
  delivery_issue?: string;
  id: string;
  number: string;
  stall_id: number;
  stall_name: string;
  stall_image: string;
  status: OrderStatus;
  payment_status: "unpaid" | "paid" | "refunding" | "refunded";
  payment_method?: "offline" | "wechat";
  payment_review_required?: boolean;
  wechat_payment?: PaymentReadiness;
  payment?: WechatPayment | null;
  payment_can_close?: boolean;
  refund?: PaymentRefund | null;
  total_cents: number;
  created_at: string;
  accepted_at: string | null;
  estimated_ready_at?: string | null;
  prep_updated_at?: string | null;
  prep_delay_reason?: string;
  ready_at: string | null;
  completed_at: string | null;
  expires_at: string | null;
  pickup_code: string;
  pickup_address: string;
  pickup_latitude: number;
  pickup_longitude: number;
  current_address: string;
  location_changed: boolean;
  note: string;
  contact_phone: string;
  cancel_requested: boolean;
  merchant_contact_phone?: string;
  stall_payment_qr_image?: string;
  cancel_reason: string;
  review: Review | null;
  items: {
    product_id: number;
    name: string;
    image: string;
    unit_price_cents: number;
    quantity: number;
    portions?: Portion[];
  }[];
}

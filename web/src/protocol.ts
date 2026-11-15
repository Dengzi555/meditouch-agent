export type Safety = "safe" | "unsafe" | "unknown";
export type EmergencyFacts = {
  call_status: 'unknown' | 'not_called' | 'called';
  consciousness: 'unknown' | 'responsive' | 'unresponsive';
  breathing: 'unknown' | 'normal' | 'abnormal' | 'absent_or_gasping';
  onset: string | null; last_known_well: string | null; location: string | null;
  age: string | null; history: string | null; medications: string | null; actions_taken: string | null;
};
export type EmergencyCard = { id: string; type: 'emergency_support'; title: string;
  severity: 'emergency' | 'prompt_care'; original_query: string; symptoms: string[];
  facts: EmergencyFacts; actions: string[]; handover: string; revision: number;
  updated_at: string; guidance_version: string; sources: { title: string; url: string }[] };
export type EmergencyUpdater = (card: EmergencyCard, changes: Partial<EmergencyFacts>) => Promise<EmergencyCard>;
export type HealthCard = { id: string; type: 'health_answer'; title: string;
  basis: 'public' | 'local' | 'mixed' | 'model_knowledge' | 'unavailable'; disclaimer: string;
  sections: { title: string; items: { text: string; highlights: string[]; source_ids: string[] }[] }[];
  sources: { id: string; title: string; publisher: string; url: string; quote: string; attribution_logo?: string }[];
  follow_up: { question: string; options: string[] } | null };
export type Card =
  | HealthCard
  | EmergencyCard
  | { id: string; type: "intercept_warning"; title: string; items: { name: string; reason: string; inventory_details?: string | null }[] }
  | { id: string; type: "missing_information"; title: string; message: string; reason: string }
  | { id: string; type: "staff_script"; title: string; text: string }
  | { id: string; type: "notice"; title: string; message: string }
  | { id: string; type: "drug_candidate"; title: string; safety_status: Safety;
      name_match?: string; matched_terms?: string[];
      topic_only?: boolean;
      availability: "in_stock" | "out_of_stock"; sale_authorized: false;
      drug: { inventory_id: number; name: string; shelf_location: string; stock: number;
              is_rx: boolean; brand?: string | null; specification?: string | null;
              dosage_form?: string | null; ingredients?: string | null; contraindications?: string | null } };

export type WireEvent = { schema_version: string; run_id: string; seq: number;
  event: "progress" | "cards.snapshot" | "done" | "error"; payload: Record<string, unknown> };

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
export function safety(value: unknown): value is Safety {
  return value === "safe" || value === "unsafe" || value === "unknown";
}
export function card(value: unknown): value is Card {
  if (!object(value) || typeof value.id !== "string" || typeof value.title !== "string") return false;
  switch (value.type) {
    case 'emergency_support': return ['emergency', 'prompt_care'].includes(String(value.severity))
      && typeof value.original_query === 'string' && typeof value.handover === 'string'
      && typeof value.updated_at === 'string' && typeof value.guidance_version === 'string'
      && Number.isSafeInteger(value.revision) && Number(value.revision) >= 0
      && Array.isArray(value.symptoms) && value.symptoms.every(v => typeof v === 'string')
      && Array.isArray(value.actions) && value.actions.length > 0 && value.actions.every(v => typeof v === 'string')
      && Array.isArray(value.sources) && value.sources.every(s => object(s) && typeof s.title === 'string'
        && typeof s.url === 'string' && /^https:\/\//i.test(s.url))
      && object(value.facts) && ['unknown', 'not_called', 'called'].includes(String(value.facts.call_status))
      && ['unknown', 'responsive', 'unresponsive'].includes(String(value.facts.consciousness))
      && ['unknown', 'normal', 'abnormal', 'absent_or_gasping'].includes(String(value.facts.breathing))
      && ['onset', 'last_known_well', 'location', 'age', 'history', 'medications', 'actions_taken']
        .every(key => value.facts && (value.facts as Record<string, unknown>)[key] === null
          || typeof (value.facts as Record<string, unknown>)[key] === 'string');
    case 'health_answer': return ['public', 'local', 'mixed', 'model_knowledge', 'unavailable'].includes(String(value.basis))
      && typeof value.disclaimer === 'string' && Array.isArray(value.sources) && value.sources.every(source =>
        object(source) && ['id', 'title', 'publisher', 'url', 'quote'].every(key => typeof source[key] === 'string')
        && (source.url === '' || typeof source.url === 'string' && /^https:\/\//i.test(source.url)))
      && Array.isArray(value.sections) && value.sections.length > 0 && value.sections.every(section =>
        object(section) && typeof section.title === 'string' && Array.isArray(section.items) && section.items.every(point =>
          object(point) && typeof point.text === 'string' && Array.isArray(point.highlights)
          && point.highlights.every(text => typeof text === 'string' && (point.text as string).includes(text))
          && Array.isArray(point.source_ids) && point.source_ids.every(id => typeof id === 'string'
            && (value.sources as Record<string, unknown>[]).some(source => source.id === id))))
      && (value.follow_up === null || object(value.follow_up) && typeof value.follow_up.question === 'string'
        && Array.isArray(value.follow_up.options) && value.follow_up.options.length >= 2
        && value.follow_up.options.every(option => typeof option === 'string' && option.length > 0));
    case "intercept_warning": return Array.isArray(value.items) && value.items.every(
      item => object(item) && typeof item.name === "string" && typeof item.reason === "string");
    case "missing_information": return typeof value.message === "string" && typeof value.reason === "string";
    case "staff_script": return typeof value.text === "string";
    case "notice": return typeof value.message === "string";
    case "drug_candidate": return object(value.drug) && safety(value.safety_status)
      && value.sale_authorized === false && (value.availability === "in_stock" || value.availability === "out_of_stock")
      && typeof value.drug.name === "string" && typeof value.drug.shelf_location === "string"
      && typeof value.drug.inventory_id === "number" && typeof value.drug.is_rx === "boolean"
      && (value.drug.specification == null || typeof value.drug.specification === "string")
      && Number.isInteger(value.drug.stock) && Number(value.drug.stock) >= 0;
    default: return false;
  }
}

export function parseFrame(frame: string): WireEvent | null {
  const lines = frame.split("\n");
  const data = lines.filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
  if (!data) return null; // SSE heartbeat comments.
  const value: unknown = JSON.parse(data);
  if (!object(value) || value.schema_version !== "pharmacy.ui.v1" || typeof value.run_id !== "string"
      || !Number.isSafeInteger(value.seq) || Number(value.seq) < 1 || !object(value.payload)
      || !["progress", "cards.snapshot", "done", "error"].includes(String(value.event))) {
    throw new Error("不支持的事件格式，请刷新后重试");
  }
  const declared = lines.find(line => line.startsWith("event:"))?.slice(6).trim();
  if (declared && declared !== value.event) throw new Error("事件类型不一致");
  return value as WireEvent;
}


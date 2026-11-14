export type Safety = "safe" | "unsafe" | "unknown";
export type Card =
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


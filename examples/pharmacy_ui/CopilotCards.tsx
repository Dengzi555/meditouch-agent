"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./CopilotCards.module.css";

type Safety = "safe" | "unsafe" | "unknown";
type Card =
  | { id: string; type: "intercept_warning"; title: string; items: { name: string; reason: string }[] }
  | { id: string; type: "missing_information"; title: string; message: string; reason: string }
  | { id: string; type: "staff_script"; title: string; text: string }
  | { id: string; type: "notice"; title: string; message: string }
  | { id: string; type: "drug_candidate"; title: string; safety_status: Safety;
      availability: "in_stock" | "out_of_stock"; sale_authorized: false;
      drug: { inventory_id: number; name: string; shelf_location: string; stock: number;
              is_rx: boolean; specification?: string | null } };

type WireEvent = { schema_version: string; run_id: string; seq: number;
  event: "progress" | "cards.snapshot" | "done" | "error"; payload: Record<string, unknown> };

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
function safety(value: unknown): value is Safety {
  return value === "safe" || value === "unsafe" || value === "unknown";
}
function card(value: unknown): value is Card {
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

function parseFrame(frame: string): WireEvent | null {
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

function CardView({ item, overall }: { item: Card; overall: Safety }) {
  if (item.type === "intercept_warning") return <section className={`${styles.card} ${styles.red}`} role="alert">
    <h2>{item.title}</h2><ul>{item.items.map((drug, i) => <li key={`${drug.name}-${i}`}>
      <strong>{drug.name}</strong><span>{drug.reason}</span></li>)}</ul></section>;
  if (item.type === "missing_information") return <section className={`${styles.card} ${styles.yellow}`}>
    <h2>{item.title}</h2><strong>{item.message}</strong><p>{item.reason}</p></section>;
  if (item.type === "staff_script") return <section className={`${styles.card} ${styles.gray}`}>
    <h2>{item.title}</h2><blockquote>{item.text}</blockquote></section>;
  if (item.type === "notice") return <section className={`${styles.card} ${styles.gray}`}>
    <h2>{item.title}</h2><p>{item.message}</p></section>;
  // Defense in depth: never turn unknown, unsafe or zero-stock items green.
  if (overall === "unsafe" || item.safety_status === "unsafe") return null;
  const available = item.drug.stock > 0 && item.availability === "in_stock";
  const green = overall === "safe" && item.safety_status === "safe" && available;
  return <section className={`${styles.card} ${green ? styles.green : available ? styles.yellow : styles.gray}`}>
    <div className={styles.cardTop}><h2>{item.drug.name}</h2>
      <span>{!available ? "缺货" : green ? "核查通过候选" : "待核对候选"}</span></div>
    <dl><div><dt>货架</dt><dd>{item.drug.shelf_location}</dd></div>
      <div><dt>库存</dt><dd>{item.drug.stock}</dd></div>
      <div><dt>规格</dt><dd>{item.drug.specification ?? "未录入"}</dd></div></dl>
    {item.drug.is_rx && <p className={styles.rx}>处方药需药师审核</p>}
    <small>仍需药师确认 · 不自动放行售药</small></section>;
}

export default function CopilotCards({ endpoint = "http://127.0.0.1:8766/events" }: { endpoint?: string }) {
  const [cards, setCards] = useState<Card[]>([]);
  const [overall, setOverall] = useState<Safety>("unknown");
  const [stages, setStages] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const epoch = useRef(0);
  useEffect(() => () => { epoch.current++; controller.current?.abort(); }, []);

  async function start() {
    controller.current?.abort();
    const current = ++epoch.current;
    const abort = new AbortController();
    controller.current = abort;
    setCards([]); setStages([]); setOverall("unknown"); setError(""); setBusy(true);
    let lastSeq = 0, runId = "", done = false;
    try {
      const response = await fetch(endpoint, { method: "POST", signal: abort.signal,
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ demo: true }) });
      if (!response.ok || !response.body || !response.headers.get("content-type")?.includes("text/event-stream")) {
        throw new Error("无法建立 SSE 连接");
      }
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let pending = "";
      try {
        while (!done) {
          const chunk = await reader.read();
          if (chunk.done) break;
          if (current !== epoch.current) return;
          pending = (pending + decoder.decode(chunk.value, { stream: true })).replace(/\r\n/g, "\n");
          if (pending.length > 2_000_000) throw new Error("事件过大");
          let index: number;
          while (!done && (index = pending.indexOf("\n\n")) >= 0) {
            const message = parseFrame(pending.slice(0, index)); pending = pending.slice(index + 2);
            if (!message) continue;
            if (runId && runId !== message.run_id) throw new Error("混入其他任务事件");
            runId = message.run_id;
            if (message.seq <= lastSeq) continue; // Ignore duplicate/stale events.
            lastSeq = message.seq;
            if (message.event === "progress" && typeof message.payload.label === "string") {
              const label = message.payload.label;
              setStages(previous => [...previous, label]);
            } else if (message.event === "cards.snapshot") {
              const snapshot = message.payload;
              if (!safety(snapshot.safety_status) || !Array.isArray(snapshot.cards) || !snapshot.cards.every(card)) {
                throw new Error("卡片格式异常，已停止展示");
              }
              if (new Set(snapshot.cards.map(item => item.id)).size !== snapshot.cards.length) throw new Error("卡片编号重复");
              setOverall(snapshot.safety_status); setCards(snapshot.cards); // Replace, never append old recommendations.
            } else if (message.event === "error") {
              throw new Error(typeof message.payload.message === "string" ? message.payload.message : "处理失败");
            } else if (message.event === "done") {
              if (message.payload.status !== "completed") throw new Error("任务未正常完成");
              done = true;
            }
          }
        }
        if (!done) throw new Error("连接中断，结果未完成，请重新核对");
      } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
    } catch (cause) {
      if (current !== epoch.current) return;
      setCards([]); setOverall("unknown");
      setError(abort.signal.aborted ? "已停止，结果不可作为拿药依据" : cause instanceof Error ? cause.message : "连接失败");
    } finally { if (current === epoch.current) setBusy(false); }
  }

  return <main className={styles.shell}>
    <header><span className={styles.kicker}>门店工作助手 / 协议演示</span><h1>接待与拿药核对</h1>
      <p>当前为合成数据演示，不查询真实库存。颜色表示核查状态，不代表售药批准。</p></header>
    <div className={styles.actions}><button type="button" onClick={start} disabled={busy}>运行卡片演示</button>
      {busy && <button type="button" onClick={() => controller.current?.abort()}>停止</button>}</div>
    <ol className={styles.steps} aria-label="执行记录">{stages.map((stage, i) => <li key={i}>{stage}</li>)}</ol>
    {error && <p className={styles.error} role="alert">{error}</p>}
    <div className={styles.cards} aria-busy={busy}>{cards.map(item => <CardView key={item.id} item={item} overall={overall} />)}</div>
  </main>;
}

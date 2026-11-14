import type { Card, Safety } from "./protocol";
import styles from "./CopilotCards.module.css";

export function CardView({ item, overall }: { item: Card; overall: Safety }) {
  if (item.type === "intercept_warning") return <section data-card-type={item.type} className={`${styles.card} ${styles.red}`} role="alert">
    <h2>{item.title}</h2><ul>{item.items.map((drug, i) => <li key={`${drug.name}-${i}`}>
      <strong>{drug.name}</strong><span>{drug.reason}</span>
      {drug.inventory_details && <small>{drug.inventory_details}</small>}</li>)}</ul></section>;
  if (item.type === "missing_information") return <aside data-card-type={item.type} className={styles.compactWarning}>
    <strong>{item.id === 'missing' ? '请人工核对病史' : item.title}</strong><span>{item.reason || item.message}</span></aside>;
  if (item.type === "staff_script") return <section data-card-type={item.type} className={`${styles.card} ${styles.gray}`}>
    <h2>{item.title}</h2><blockquote>{item.text}</blockquote></section>;
  if (item.type === "notice" && item.id === 'local-not-found') return <aside data-card-type={item.type} className={styles.compactNotice}>
    <strong>{item.title}</strong><span>{item.message}</span></aside>;
  if (item.type === "notice") return <section data-card-type={item.type} className={`${styles.card} ${styles.gray}`}>
    <h2>{item.title}</h2><p>{item.message}</p></section>;
  // Defense in depth: never turn unknown, unsafe or zero-stock items green.
  if (overall === "unsafe" || item.safety_status === "unsafe") return null;
  const available = item.drug.stock > 0 && item.availability === "in_stock";
  const green = overall === "safe" && item.safety_status === "safe" && available;
  return <section data-card-type={item.type} className={`${styles.card} ${green ? styles.green : available ? styles.yellow : styles.gray}`}>
    <div className={styles.cardTop}><h2>{item.drug.name}</h2>
      <span>{!available ? "本店缺货" : green ? "本店有货 · 核查通过候选" : "本店有货 · 待核对"}</span></div>
    <dl><div><dt>货架</dt><dd>{item.drug.shelf_location}</dd></div>
      <div><dt>库存</dt><dd>{item.drug.stock}</dd></div>
      <div><dt>品牌</dt><dd>{item.drug.brand || "未录入"}</dd></div>
      <div><dt>规格</dt><dd>{item.drug.specification || "未录入"}</dd></div>
      <div><dt>剂型</dt><dd>{item.drug.dosage_form || "未录入"}</dd></div>
      <div><dt>处方属性</dt><dd>{item.drug.is_rx ? "处方药标记" : "非处方标记（录入值）"}</dd></div></dl>
    <details className={styles.drugDetails}><summary>药品资料与匹配说明</summary>
      <p>主要成分：{item.drug.ingredients || '未录入'}</p>
      <p>禁忌原文：{item.drug.contraindications || '未录入，不能据此认定没有禁忌'}</p>
      {item.name_match === 'brand_exact' && <p>品牌匹配，请结合通用名与规格核对具体商品。</p>}
      {item.topic_only ? <p>按咨询主题字面匹配到的本店商品，未核实适应症，不能据此判定适合使用。</p>
        : item.name_match === 'fuzzy' && <p>模糊匹配，请核对完整通用名、成分和规格，不能视为同一药品。</p>}
    </details>
    {item.drug.is_rx && <p className={styles.rx}>处方药需药师审核</p>}
    <small>{item.topic_only ? '仅目录匹配 · ' : ''}适用性待药师确认</small></section>;
}


export default function CopilotCards({ cards, overall, loading }: {
  cards: Card[]; overall: Safety; loading: boolean;
}) {
  const detailsIds = new Set(['mock-data', 'topic-context', 'knowledge-source']);
  const metadata = cards.filter(item => item.type === 'notice' && detailsIds.has(item.id));
  const visible = cards.filter(item => !(item.type === 'notice' && detailsIds.has(item.id)));
  const priority = (item: Card) => item.type === 'intercept_warning' ? 0
    : item.type === 'drug_candidate' ? 1 : item.type === 'missing_information' ? 2
    : item.type === 'staff_script' ? 4 : 3;
  const ordered = [...visible].sort((left, right) => priority(left) - priority(right));
  return <div className={styles.cards} aria-busy={loading} aria-label="核查结果">
    {loading && cards.length === 0 && <div role="status" className={styles.skeleton}>
      正在查询本店库存并核对禁忌…<span /><span />
    </div>}
    {metadata.some(item => item.id === 'mock-data') && <span className={styles.demoBadge}>演示数据</span>}
    {ordered.map(item => <CardView key={item.id} item={item} overall={overall} />)}
    {metadata.length > 0 && <details className={styles.explanation}>
      <summary>查看来源与说明</summary>
      {metadata.map(item => item.type === 'notice' && <div key={item.id}>
        <strong>{item.title}</strong><p>{item.message}</p>
      </div>)}
    </details>}
  </div>;
}

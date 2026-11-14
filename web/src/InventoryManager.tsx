import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import styles from './InventoryManager.module.css';

type Drug = { id: number; name: string; brand: string | null; shelf_location: string; stock: number;
  is_rx: boolean; unit: string; version: number; specification: string | null; dosage_form: string | null;
  ingredients: string | null; contraindications: string | null; incomplete: boolean };
type Movement = { id: number; inventory_id: number; drug_name: string; kind: string; delta: number;
  before_stock: number; after_stock: number; unit: string; reason: string; operator: string; created_at: string };
const kinds: Record<string, string> = { sale: '销售出库', restock: '补货入库', return: '退货入库', adjustment: '盘点调整' };
const units = ['盒', '瓶', '支', '袋', '板', '件'];
async function data(response: Response) {
  const result = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof result?.detail === 'string' ? result.detail : '操作未完成，请核对输入后重试');
  return result;
}

export default function InventoryManager({ storeId, onImport }: { storeId: string; onImport: () => void }) {
  const [tab, setTab] = useState<'stock' | 'ledger'>('stock');
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [kindFilter, setKindFilter] = useState('all');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [items, setItems] = useState<Drug[]>([]);
  const [movements, setMovements] = useState<Movement[]>([]);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [editor, setEditor] = useState<Drug | null>(null);
  const [transaction, setTransaction] = useState<Drug | null>(null);
  const [kind, setKind] = useState('sale');
  const [quantity, setQuantity] = useState('1');
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const lock = useRef(false);
  const operation = useRef({ signature: '', id: '' });
  const panel = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => { setSearch(query.trim()); setOffset(0); }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    if (!storeId) return;
    const abort = new AbortController();
    setLoading(true); setError(''); setItems([]); setMovements([]);
    const params = new URLSearchParams({ store_id: storeId, offset: String(offset), limit: '30' });
    if (tab === 'stock') { params.set('q', search); params.set('status', filter); }
    else { params.set('kind', kindFilter); if (selectedId) params.set('inventory_id', String(selectedId)); }
    fetch(`/api/pharmacy/inventory${tab === 'ledger' ? '/movements' : ''}?${params}`, { signal: abort.signal })
      .then(data).then(result => {
        if (abort.signal.aborted) return;
        if (tab === 'stock') setItems(result.items); else setMovements(result.items);
        setTotal(result.total);
      }).catch(cause => { if (!abort.signal.aborted) setError(cause.message); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [storeId, tab, search, filter, kindFilter, selectedId, offset, refresh]);
  function close() {
    if (lock.current) return;
    setEditor(null); setTransaction(null); setFormError(''); opener.current?.focus();
  }
  useEffect(() => {
    if (!editor && !transaction) return;
    panel.current?.querySelector<HTMLElement>('input,button')?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); close(); }
      if (event.key === 'Tab') {
        const all = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled)') ?? []);
        const first = all[0], last = all[all.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  }, [Boolean(editor), Boolean(transaction)]);
  function open(drug: Drug, mode: 'edit' | 'move') {
    opener.current = document.activeElement as HTMLElement;
    setFormError(''); setSuccess('');
    if (mode === 'edit') setEditor({ ...drug });
    else { setTransaction(drug); setKind(drug.stock ? 'sale' : 'restock'); setQuantity('1'); setReason(''); }
  }
  async function edit(event: FormEvent) {
    event.preventDefault(); if (!editor || lock.current) return;
    lock.current = true; setSaving(true); setFormError('');
    try {
      const { id, stock: _stock, version, incomplete: _incomplete, ...fields } = editor;
      // Never send ORM metadata or stock through the edit endpoint.
      const body = { store_id: storeId, expected_version: version, name: fields.name, brand: fields.brand,
        shelf_location: fields.shelf_location, is_rx: fields.is_rx, unit: fields.unit,
        specification: fields.specification, dosage_form: fields.dosage_form,
        ingredients: fields.ingredients, contraindications: fields.contraindications };
      await data(await fetch(`/api/pharmacy/inventory/${id}/edit`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));
      if (mounted.current) { setEditor(null); setRefresh(value => value + 1); setSuccess('药品资料已保存'); opener.current?.focus(); }
    } catch (cause) { if (mounted.current) setFormError(cause instanceof Error ? cause.message : '保存失败'); }
    finally { lock.current = false; if (mounted.current) setSaving(false); }
  }
  const number = Number(quantity);
  const after = !transaction ? 0 : kind === 'adjustment' ? number : transaction.stock + (kind === 'sale' ? -number : number);
  const valid = quantity !== '' && Number.isSafeInteger(number) && number >= (kind === 'adjustment' ? 0 : 1)
    && after >= 0 && after <= 2147483647;
  async function move(event: FormEvent) {
    event.preventDefault(); if (!transaction || !valid || lock.current) return;
    lock.current = true; setSaving(true); setFormError('');
    const body = { store_id: storeId, expected_version: transaction.version, expected_stock: transaction.stock,
      kind, quantity: number, reason: reason.trim() };
    const signature = JSON.stringify([transaction.id, body]);
    if (operation.current.signature !== signature) operation.current = { signature, id: crypto.randomUUID() };
    try {
      const result = await data(await fetch(`/api/pharmacy/inventory/${transaction.id}/movements`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, request_id: operation.current.id }) }));
      if (mounted.current) {
        setTransaction(null); setRefresh(value => value + 1);
        setSuccess(`${kinds[kind]}已登记，登记后库存 ${result.movement.after_stock} ${result.movement.unit}`);
        operation.current = { signature: '', id: '' }; opener.current?.focus();
      }
    } catch (cause) { if (mounted.current) setFormError(cause instanceof Error ? cause.message : '登记失败'); }
    finally { lock.current = false; if (mounted.current) setSaving(false); }
  }
  function changeTab(next: 'stock' | 'ledger') { setTab(next); setOffset(0); setError(''); setSuccess(''); }
  return <div className={styles.page}>
    <div className={styles.heading}><div><p className={styles.eyebrow}>门店工作台 / INVENTORY</p>
      <h2>每一盒，都有去向。</h2><p>查货、补资料、登记出入库，在一个地方完成。</p></div>
      <button className={styles.primary} onClick={onImport}>＋ 新增药品</button></div>
    <div className={styles.tabs} role="tablist" aria-label="库存管理视图">
      <button role="tab" aria-selected={tab === 'stock'} onClick={() => changeTab('stock')}>药品库存</button>
      <button role="tab" aria-selected={tab === 'ledger'} onClick={() => { setSelectedId(null); changeTab('ledger'); }}>出入库记录</button>
    </div>
    <div className={styles.toolbar}>
      {tab === 'stock' ? <><input aria-label="搜索库存" placeholder="搜索药名、品牌或货架" value={query} maxLength={200} onChange={event => setQuery(event.target.value)} />
        <select aria-label="库存筛选" value={filter} onChange={event => { setFilter(event.target.value); setOffset(0); }}>
          <option value="all">全部药品</option><option value="out">缺货</option><option value="incomplete">资料待完善</option></select></>
        : <><select aria-label="记录类型" value={kindFilter} onChange={event => { setKindFilter(event.target.value); setOffset(0); }}>
          <option value="all">全部类型</option>{Object.entries(kinds).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select>
          {selectedId && <button onClick={() => { setSelectedId(null); setOffset(0); }}>取消单品筛选 ×</button>}
          <span className={styles.hint}>原有库存作为期初余额；本页启用后记录每次变动。</span></>}
      <button className={styles.reload} onClick={() => setRefresh(value => value + 1)} disabled={loading}>刷新</button>
    </div>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {success && <p role="status" className={styles.success}>{success}</p>}
    <div className={styles.tableWrap} aria-busy={loading}>
      {loading ? <div className={styles.empty} role="status">正在读取本店库存…</div> : tab === 'stock' ? <table>
        <caption className={styles.srOnly}>本店药品库存</caption>
        <thead><tr><th>药品 / 品牌</th><th>规格</th><th>货架</th><th>当前库存</th><th>处方属性</th><th>操作</th></tr></thead>
        <tbody>{items.map(drug => <tr key={drug.id}>
          <td><strong>{drug.name}</strong><small>{drug.brand || '品牌未录入'}{drug.incomplete && <span className={styles.pending}>资料待完善</span>}</small></td>
          <td>{drug.specification || '未录入'}</td><td>{drug.shelf_location}</td>
          <td><b className={drug.stock === 0 ? styles.zero : styles.stock}>{drug.stock}</b> <span>{drug.unit || '单位待确认'}</span></td>
          <td><span className={styles.badge}>{drug.is_rx ? '处方' : '非处方'} · 录入值</span></td>
          <td><div className={styles.rowActions}><button onClick={() => open(drug, 'edit')}>编辑资料</button>
            <button onClick={() => open(drug, 'move')}>出入库</button>
            <button onClick={() => { setSelectedId(drug.id); changeTab('ledger'); }}>记录</button></div></td>
        </tr>)}</tbody>
      </table> : <table><caption className={styles.srOnly}>库存变动记录</caption>
        <thead><tr><th>时间 / 编号</th><th>药品</th><th>类型</th><th>数量变化</th><th>库存前 → 后</th><th>原因 / 操作人</th></tr></thead>
        <tbody>{movements.map(row => <tr key={row.id}>
          <td>{new Date(row.created_at.endsWith('Z') || /[+-]\d\d:\d\d$/.test(row.created_at) ? row.created_at : `${row.created_at}Z`).toLocaleString('zh-CN')}<small>#{row.id}</small></td>
          <td><strong>{row.drug_name}</strong></td><td>{kinds[row.kind] || row.kind}</td>
          <td className={row.delta < 0 ? styles.zero : styles.positive}>{row.delta > 0 ? '+' : ''}{row.delta} {row.unit}</td>
          <td>{row.before_stock} → <b>{row.after_stock}</b></td><td>{row.reason}<small>{row.operator === 'local-demo' ? '本地店员（演示身份）' : row.operator}</small></td>
        </tr>)}</tbody></table>}
      {!loading && !error && !(tab === 'stock' ? items.length : movements.length) && <div className={styles.empty}>
        {tab === 'stock' ? '没有匹配的药品，试试其他关键词或新增入库。' : '暂无出入库记录。'}</div>}
    </div>
    <div className={styles.pagination}><span>共 {loading ? '…' : total} {tab === 'stock' ? '种药品' : '条记录'}</span>
      <div><button disabled={loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 30))}>上一页</button>
        <span>{Math.floor(offset / 30) + 1}</span><button disabled={loading || offset + 30 >= total} onClick={() => setOffset(value => value + 30)}>下一页</button></div></div>
    {(editor || transaction) && <div className={styles.overlay} onClick={event => { if (event.target === event.currentTarget) close(); }}>
      <div className={styles.panel} role="dialog" aria-modal="true" aria-labelledby="inventory-dialog-title" ref={panel}>
        <div className={styles.panelTop}><h3 id="inventory-dialog-title">{editor ? '编辑药品资料' : '登记出入库'}</h3>
          <button aria-label="关闭库存操作" onClick={close} disabled={saving}>×</button></div>
        {editor ? <form onSubmit={edit}><fieldset disabled={saving} className={styles.fields}>
          <label>药名<input required maxLength={200} value={editor.name} onChange={e => setEditor({ ...editor, name: e.target.value })} /></label>
          <label>品牌<input maxLength={100} placeholder="选填" value={editor.brand || ''} onChange={e => setEditor({ ...editor, brand: e.target.value })} /></label>
          <label>货架号<input required maxLength={100} value={editor.shelf_location} onChange={e => setEditor({ ...editor, shelf_location: e.target.value })} /></label>
          <label>库存计数单位<select required value={editor.unit} onChange={e => setEditor({ ...editor, unit: e.target.value })}>
            <option value="">请选择</option>{units.map(unit => <option key={unit}>{unit}</option>)}</select></label>
          <label>规格<input maxLength={200} placeholder="选填，如 0.3g×20粒" value={editor.specification || ''} onChange={e => setEditor({ ...editor, specification: e.target.value })} /></label>
          <label>剂型<input maxLength={100} placeholder="选填" value={editor.dosage_form || ''} onChange={e => setEditor({ ...editor, dosage_form: e.target.value })} /></label>
          <label className={styles.full}>处方属性<select value={String(editor.is_rx)} onChange={e => setEditor({ ...editor, is_rx: e.target.value === 'true' })}>
            <option value="false">非处方药（需核对药盒标识）</option><option value="true">处方药</option></select></label>
          <label className={styles.full}>主要成分<textarea maxLength={2000} placeholder="选填，请按说明书录入" value={editor.ingredients || ''} onChange={e => setEditor({ ...editor, ingredients: e.target.value })} /></label>
          <label className={styles.full}>禁忌原文<textarea maxLength={4000} placeholder="未录入代表未知，不代表无禁忌" value={editor.contraindications || ''} onChange={e => setEditor({ ...editor, contraindications: e.target.value })} /></label>
        </fieldset><p className={styles.hint}>当前数量 {editor.stock}；单位仅标记计数方式，不自动换算数量。改库存请使用出入库登记。</p>
          {formError && <p role="alert" className={styles.error}>{formError}</p>}
          <div className={styles.formActions}><button type="button" onClick={close} disabled={saving}>取消</button><button className={styles.primary} disabled={saving}>{saving ? '保存中…' : '保存资料'}</button></div></form>
          : transaction && <form onSubmit={move}>
            <div className={styles.drugSummary}><strong>{transaction.name}</strong><span>{transaction.specification || '规格未录入'} · {transaction.shelf_location}</span></div>
            {!transaction.unit && <p role="alert" className={styles.error}>请先编辑该药品资料，确认库存计数单位。</p>}
            <fieldset disabled={saving || !transaction.unit} className={styles.fields}>
              <label className={styles.full}>登记类型<select value={kind} onChange={e => setKind(e.target.value)}>{Object.entries(kinds).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
              <label className={styles.full}>{kind === 'adjustment' ? '盘点实有数量' : '登记数量'}（{transaction.unit || '单位待确认'}）
                <input type="number" required min={kind === 'adjustment' ? 0 : 1} max={2147483647} step={1} value={quantity} onChange={e => setQuantity(e.target.value)} /></label>
              <label className={styles.full}>登记原因<textarea required maxLength={500} placeholder={kind === 'adjustment' ? '说明盘盈、盘亏或纠错原因' : '如：柜台售出、供应商补货或顾客退货'} value={reason} onChange={e => setReason(e.target.value)} /></label>
            </fieldset>
            <div className={styles.preview}><span>当前库存 <b>{transaction.stock}</b></span><span>→</span><span>登记后 <b>{valid ? after : '—'}</b> {transaction.unit}</span></div>
            <p className={styles.hint}>仅记录库存变化，不代表用药审核通过。退货登记增加库存，须由店员确认可重新入库。</p>
            {formError && <p role="alert" className={styles.error}>{formError}</p>}
            <div className={styles.formActions}><button type="button" onClick={close} disabled={saving}>取消</button><button className={styles.primary} disabled={saving || !valid || !transaction.unit || !reason.trim()}>{saving ? '登记中…' : '确认登记'}</button></div>
          </form>}
      </div>
    </div>}
  </div>;
}

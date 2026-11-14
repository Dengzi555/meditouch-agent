import { useEffect, useRef, useState } from 'react';
import type { DragEvent, FormEvent } from 'react';
import styles from './InventoryImport.module.css';
import { parseOcrResponse } from './inventoryOcr';

type Form = { name: string; brand: string; is_rx: '' | 'true' | 'false'; shelf_location: string;
  stock: string; ingredients: string; contraindications: string; dosage_form: string; specification: string };
const blank = (): Form => ({ name: '', brand: '', is_rx: '', shelf_location: '', stock: '',
  ingredients: '', contraindications: '', dosage_form: '', specification: '' });
const MAX_BYTES = 5 * 1024 * 1024;

async function responseData(response: Response) {
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : '请求未完成，请核对必填字段后重试');
  return data;
}

export default function InventoryImport({ storeId, visionReady }: { storeId: string; visionReady: boolean }) {
  const [drafts, setDrafts] = useState<Form[]>([blank()]);
  const [active, setActive] = useState(0);
  const form = drafts[active] ?? blank();
  const [batchNote, setBatchNote] = useState('');
  function setForm(value: Form | ((current: Form) => Form)) {
    setDrafts(current => current.map((item, index) => index === active
      ? typeof value === 'function' ? value(item) : value : item));
  }
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState('');
  const [recognizing, setRecognizing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [ocrError, setOcrError] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [prefilled, setPrefilled] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);
  const ocrAbort = useRef<AbortController | null>(null);
  const run = useRef(0);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => {
    mounted.current = false; run.current++; ocrAbort.current?.abort();
  }; }, []);
  useEffect(() => {
    if (!file) { setPreview(''); return; }
    const url = URL.createObjectURL(file); setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  function update(key: keyof Form, value: string) {
    setForm(current => ({ ...current, [key]: value })); setError(''); setSuccess('');
  }
  async function recognize(image: File) {
    ocrAbort.current?.abort();
    const current = ++run.current;
    setPrefilled(false); setOcrError('');
    if (!storeId) { setOcrError('请先配置门店'); return; }
    if (!visionReady) { setOcrError('视觉模型尚未配置，可先手动填写后确认入库'); return; }
    const abort = new AbortController(); ocrAbort.current = abort; setRecognizing(true);
    try {
      const body = new FormData(); body.append('image', image); body.append('store_id', storeId);
      const data = await responseData(await fetch('/api/pharmacy/ocr_prefill', {
        method: 'POST', body, signal: abort.signal,
      }));
      if (!mounted.current || current !== run.current) return;
      const batch = parseOcrResponse(data);
      setDrafts(current => batch.items.length ? batch.items.map(item => ({...blank(),
        name: item.name ?? '', brand: item.brand ?? '', ingredients: item.ingredients ?? '',
        contraindications: item.contraindications ?? '', dosage_form: item.dosage_form ?? '',
        specification: item.specification ?? '',
        is_rx: item.is_rx === true ? 'true' : item.is_rx === false ? 'false' : '',
        shelf_location: batch.items.length === 1 && current.length === 1 ? current[0].shelf_location : '',
        stock: batch.items.length === 1 && current.length === 1 ? current[0].stock : '',
      })) : [blank()]);
      setActive(0);
      setBatchNote(batch.items.length ? `识别到 ${batch.items.length} 条药品，请逐条核对。`
        + (batch.duplicates ? ` 已合并 ${batch.duplicates} 条完全重复识别，不累加库存。` : '')
        + (batch.truncated ? ' 图片药品较多，本次仅返回前12条，请分批补拍。' : '')
        : '未识别到清晰药品，可换图或手动填写。');
      setPrefilled(batch.items.length > 0);
    } catch (cause) {
      if (mounted.current && current === run.current && !abort.signal.aborted) {
        setOcrError(cause instanceof Error ? cause.message : '识别失败，可手动填写');
      }
    } finally { if (mounted.current && current === run.current) setRecognizing(false); }
  }

  function choose(image?: File) {
    if (!image || saving) return;
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(image.type) || image.size > MAX_BYTES) {
      setOcrError('请选择5MB以内的 JPEG、PNG 或 WebP 图片'); return;
    }
    // A new box starts a new form; old stock/shelf data must not silently carry over.
    setDrafts([blank()]); setActive(0); setBatchNote(''); setError(''); setSuccess(''); setFile(image);
    void recognize(image);
  }
  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault(); choose(event.dataTransfer.files[0]);
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving || recognizing || !storeId) return;
    const stock = Number(form.stock);
    if (!form.stock.trim() || !Number.isSafeInteger(stock) || stock < 0 || !['true', 'false'].includes(form.is_rx)) {
      setError('请手动填写非负整数库存，并确认处方属性'); return;
    }
    setSaving(true); setError(''); setSuccess('');
    try {
      const optional = (value: string) => value.trim() || null;
      const data = await responseData(await fetch('/api/pharmacy/confirm_import', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ store_id: storeId, name: form.name.trim(), brand: optional(form.brand),
          shelf_location: form.shelf_location.trim(), stock, is_rx: form.is_rx === 'true',
          ingredients: optional(form.ingredients), contraindications: optional(form.contraindications),
          dosage_form: optional(form.dosage_form), specification: optional(form.specification) }),
      }));
      if (!mounted.current) return;
      if (data?.status !== 'imported') throw new Error('尚未确认入库成功，请核对记录');
      const remaining = drafts.filter((_, index) => index !== active);
      setDrafts(remaining.length ? remaining : [blank()]); setActive(Math.min(active, Math.max(0, remaining.length - 1)));
      setOcrError('');
      if (!remaining.length) {
        setFile(null); setPrefilled(false); setBatchNote('');
        if (picker.current) picker.current.value = '';
        if (camera.current) camera.current.value = '';
      }
      setSuccess(`入库成功：${data.name}（库存编号 ${data.inventory_id}）`
        + (remaining.length ? `，剩余 ${remaining.length} 条待确认` : '，本批次已完成'));
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : '入库失败，请核对后重试');
    } finally { if (mounted.current) setSaving(false); }
  }

  const readonly = recognizing || saving;
  return <section className={styles.layout} aria-label="拍照入库">
    <div className={styles.uploadPanel}>
      <span className={styles.step}>01 / 药盒图片</span>
      <h2>拖入照片，或直接拍摄</h2>
      <div className={styles.dropzone} onDragOver={event => event.preventDefault()} onDrop={drop}
        role="button" tabIndex={0} aria-label="拖拽或选择药盒图片" aria-disabled={saving}
        onClick={() => { if (!saving) picker.current?.click(); }}
        onKeyDown={event => { if (!saving && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault(); picker.current?.click();
        } }}>
        {preview ? <img src={preview} alt="待识别的药盒照片" /> : <div><span className={styles.uploadIcon}>＋</span>
          <strong>可拍摄一盒或多种药盒</strong><small>JPEG / PNG / WebP · 最大5MB · 每图最多12种</small></div>}
      </div>
      <input ref={picker} type="file" accept="image/jpeg,image/png,image/webp" hidden data-testid="image-picker"
        onChange={event => choose(event.target.files?.[0])} disabled={saving} />
      <input ref={camera} type="file" accept="image/jpeg,image/png,image/webp" capture="environment" hidden
        onChange={event => choose(event.target.files?.[0])} disabled={saving} />
      <div className={styles.uploadActions}><button type="button" onClick={() => picker.current?.click()} disabled={saving}>选择图片</button>
        <button type="button" onClick={() => camera.current?.click()} disabled={saving}>拍照</button>
        {file && <button type="button" onClick={() => void recognize(file)} disabled={readonly || !visionReady}>重新识别</button>}</div>
      <p className={styles.caption}>图片发送至配置的视觉模型，仅提取可见文字，不保存为库存附件。识别结果需人工核对。</p>
      {!visionReady && <p className={styles.caption}>视觉模型未配置；仍可手动填写右侧表单入库。</p>}
      {recognizing && <p role="status" className={styles.progress}>正在识别，尚未入库…</p>}
      {ocrError && <p role="alert" className={styles.error}>{ocrError}</p>}
      {prefilled && <p className={styles.prefilled}>已预填。请核对药名与处方标记，并手动填写货架和库存。</p>}
      {batchNote && <p className={styles.caption}>{batchNote} 不同品牌、规格请分别保留；漏识别可手动添加。</p>}
      <div className={styles.draftList} aria-label="待入库药品列表">
        {drafts.map((item, index) => <button type="button" key={index} aria-pressed={active === index}
          disabled={recognizing || saving} onClick={() => {setActive(index); setError('');}}>
          <strong>{index + 1}. {item.name || '待补充药名'}</strong>
          <small>{item.brand || '品牌未录入'} · {item.specification || '规格未录入'}</small>
        </button>)}
      </div>
      <div className={styles.uploadActions}>
        <button type="button" disabled={readonly || drafts.length >= 12} onClick={() => {
          setDrafts(current => [...current, blank()]); setActive(drafts.length);
        }}>手动添加药品</button>
        {(prefilled || drafts.length > 1) && <button type="button" disabled={readonly} onClick={() => {
          const remaining = drafts.filter((_, index) => index !== active);
          setDrafts(remaining.length ? remaining : [blank()]); setActive(Math.max(0, active - 1));
          setError('');
          if (!remaining.length) { setFile(null); setPrefilled(false); setBatchNote(''); }
        }}>移除当前条目</button>}
      </div>
    </div>
    <form className={styles.formPanel} onSubmit={submit}>
      <span className={styles.step}>02 / 人工确认 · 第 {active + 1} / {drafts.length} 条</span><h2>核对后再入库</h2>
      <div className={styles.fields}>
        <label>药名 *<input value={form.name} onChange={e => update('name', e.target.value)} required maxLength={200} disabled={readonly} placeholder="通用名 / 商品名" /></label>
        <label>品牌<input value={form.brand} onChange={e => update('brand', e.target.value)} maxLength={100} disabled={readonly} placeholder="选填" /></label>
        <label>货架号 *<input value={form.shelf_location} onChange={e => update('shelf_location', e.target.value)} required maxLength={100} disabled={saving} placeholder="员工填写，如3排2号" /></label>
        <label>库存数量 *<input type="number" min={0} step={1} max={Number.MAX_SAFE_INTEGER} value={form.stock}
          onChange={e => update('stock', e.target.value)} required disabled={saving} placeholder="员工填写，最小销售包装数" /></label>
        <label>处方属性 *<select value={form.is_rx} onChange={e => update('is_rx', e.target.value)} required disabled={readonly}>
          <option value="">待人工确认</option><option value="false">非处方药（请核对标识）</option><option value="true">处方药</option></select></label>
        <label>剂型<input value={form.dosage_form} onChange={e => update('dosage_form', e.target.value)} maxLength={100} disabled={readonly} placeholder="选填" /></label>
        <label className={styles.wide}>规格<input value={form.specification} onChange={e => update('specification', e.target.value)} maxLength={200} disabled={readonly} placeholder="选填，按实物填写" /></label>
        <label className={styles.wide}>主要成分<textarea value={form.ingredients} onChange={e => update('ingredients', e.target.value)} rows={3} maxLength={2000} disabled={readonly} placeholder="选填，按可见原文核对" /></label>
        <label className={styles.wide}>禁忌文字<textarea value={form.contraindications} onChange={e => update('contraindications', e.target.value)} rows={3} maxLength={4000} disabled={readonly} placeholder="选填，未收录不代表没有禁忌" /></label>
      </div>
      <p className={styles.caption}>识别成功不会写库。只有点击下方按钮，才提交右侧最终表单并写入当前门店库存。</p>
      {error && <p role="alert" className={styles.error}>{error}</p>}
      {success && <p role="status" className={styles.success}>{success}</p>}
      <button className={styles.confirm} type="submit" disabled={readonly || !storeId}>{saving ? '正在入库…' : '确认入库'}</button>
    </form>
  </section>;
}

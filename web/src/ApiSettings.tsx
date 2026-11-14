import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import styles from './ApiSettings.module.css';

type Field = { key: string; label: string; group: string; kind: 'url' | 'text' | 'secret' | 'boolean';
  configured: boolean; value: string };
type Snapshot = { fields: Field[]; note: string };

export default function ApiSettings({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    dialog.current?.showModal();
    const controller = new AbortController();
    fetch('/api/settings', { signal: controller.signal, cache: 'no-store' }).then(async response => {
      if (!response.ok) throw new Error(response.status === 403 ? '请通过 localhost 或 127.0.0.1 在本机配置 API。' : '无法读取 API 配置');
      setSnapshot(await response.json());
    }).catch(cause => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setMessage('');
    try {
      const response = await fetch('/api/settings', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ values }) });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '保存失败');
      setSnapshot(data); setValues({}); onSaved();
      setMessage('已保存到本地 .env，下一次请求生效。');
    } catch (cause) { setError(cause instanceof Error ? cause.message : '保存失败，请检查本地服务'); }
    finally { setBusy(false); }
  }
  function field(item: Field) {
    const value = values[item.key] ?? item.value;
    return <div className={styles.field} key={item.key}>
      <label htmlFor={item.key}>{item.label}
        {item.kind === 'secret' && <span>{values[item.key] === '' ? '保存后清除' : item.configured ? '已配置' : '未配置'}</span>}</label>
      <div className={styles.inputRow}>
        {item.kind === 'boolean' ? <select id={item.key} value={value} disabled={busy}
          onChange={event => setValues(current => ({ ...current, [item.key]: event.target.value }))}>
          <option value="0">关闭</option><option value="1">开启</option>
        </select> : <input id={item.key} type={item.kind === 'secret' ? 'password' : item.kind === 'url' ? 'url' : 'text'}
          autoComplete={item.kind === 'secret' ? 'new-password' : 'off'} spellCheck={false} value={value} disabled={busy}
          placeholder={item.kind === 'secret' ? item.configured ? '已配置，留空保留原密钥' : '输入 API Key' : '选填'}
          onChange={event => { const text = event.target.value;
            setValues(current => { const next = { ...current };
              if (item.kind === 'secret' && !text) delete next[item.key]; else next[item.key] = text;
              return next; }); }} />}
        {item.kind === 'secret' && item.configured && <button type="button" className={styles.clear} disabled={busy}
          onClick={() => setValues(current => { const next = { ...current };
            if (next[item.key] === '') delete next[item.key]; else next[item.key] = '';
            return next; })}>
          {values[item.key] === '' ? '撤销清除' : '清除'}</button>}
      </div>
    </div>;
  }
  return <dialog ref={dialog} className={styles.dialog} aria-labelledby="settings-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }} onClose={onClose}>
    <form onSubmit={save}>
      <header className={styles.header}><div><h2 id="settings-title">API 配置</h2><p>统一管理本机使用的模型与外部接口</p></div>
        <button type="button" className={styles.close} aria-label="关闭 API 配置" onClick={onClose} disabled={busy}>×</button></header>
      <div className={styles.body}>
        {!snapshot && !error && <p role="status">正在读取配置…</p>}
        {snapshot && <>
          <p className={styles.note}>密钥不回显。保存后以明文写入服务端本地 .env，不写入浏览器存储，也不会提交到 Git。</p>
          {['文本模型', '视觉识别'].map(group => <fieldset key={group} disabled={busy}>
            <legend>{group}</legend><p className={styles.hint}>{group === '文本模型' ? '识别顾客需求与整理店员话术' : '独立配置支持图片输入的模型，用于药盒识别'}</p>
            {snapshot.fields.filter(item => item.group === group).map(field)}
          </fieldset>)}
          <details className={styles.advanced}><summary>公开知识与搜索 · 可选</summary>
            <p className={styles.hint}>{snapshot.note}</p>
            {snapshot.fields.filter(item => item.group === '公开知识与搜索').map(field)}
          </details>
        </>}
        {error && <p role="alert" className={styles.error}>{error}</p>}
        {message && <p role="status" className={styles.success}>{message}</p>}
      </div>
      <footer className={styles.footer}><button type="button" disabled={busy} onClick={onClose}>关闭</button>
        <button type="submit" className={styles.save} disabled={busy || !snapshot}>{busy ? '正在保存…' : '保存配置'}</button></footer>
    </form>
  </dialog>;
}

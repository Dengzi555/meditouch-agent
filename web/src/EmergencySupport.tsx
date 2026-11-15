import { useEffect, useRef, useState } from 'react';
import type { EmergencyCard, EmergencyFacts, EmergencyUpdater } from './protocol';
import styles from './EmergencySupport.module.css';

export default function EmergencySupport({ item, onUpdate, loading }: {
  item: EmergencyCard; onUpdate?: EmergencyUpdater; loading: boolean;
}) {
  const [draft, setDraft] = useState(item.facts);
  const [busy, setBusy] = useState(false);
  const saving = useRef(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  useEffect(() => { setDraft(item.facts); setCopied(false); }, [item]);
  const dirty = Object.keys(draft).some(key => draft[key as keyof EmergencyFacts] !== item.facts[key as keyof EmergencyFacts]);
  async function save() {
    if (!onUpdate || saving.current) return;
    saving.current = true; setBusy(true); setError('');
    try { await onUpdate(item, draft); }
    catch (err) { setError(err instanceof Error ? err.message : '保存失败，记录尚未更新'); }
    finally { saving.current = false; setBusy(false); }
  }
  async function copy() {
    try { await navigator.clipboard.writeText(item.handover); setCopied(true); }
    catch { setError('复制失败，可选中下方交接记录手动复制'); }
  }
  const editable = Boolean(onUpdate) && !loading && !busy;
  return <section className={styles.card} data-card-type="emergency_support">
    <div className={styles.top}><h2>{item.title}</h2><a href="tel:120">拨打120</a></div>
    <ol className={styles.actions}>{item.actions.map((action, i) => <li key={i}>{action}</li>)}</ol>
    <h3>现场情况</h3>
    <p>救助优先。由店员确认后记录，不清楚的保留“未确认”。</p>
    <fieldset disabled={!editable} className={styles.fields}>
      <label>是否已呼救<select aria-label="是否已呼救" value={draft.call_status} onChange={e => setDraft({ ...draft, call_status: e.target.value as EmergencyFacts['call_status'] })}>
        <option value="unknown">未确认</option><option value="not_called">尚未拨打120</option><option value="called">已拨打120</option></select></label>
      <label>意识反应<select aria-label="意识反应" value={draft.consciousness} onChange={e => setDraft({ ...draft, consciousness: e.target.value as EmergencyFacts['consciousness'] })}>
        <option value="unknown">未确认</option><option value="responsive">呼唤有反应</option><option value="unresponsive">呼唤无反应</option></select></label>
      <label>呼吸<select aria-label="呼吸" value={draft.breathing} onChange={e => setDraft({ ...draft, breathing: e.target.value as EmergencyFacts['breathing'] })}>
        <option value="unknown">未确认</option><option value="normal">正常呼吸</option><option value="abnormal">呼吸不正常</option><option value="absent_or_gasping">无呼吸或仅喘息</option></select></label>
      {(['onset', 'last_known_well'] as const).map(key => <label key={key}>
        {key === 'onset' ? '最早出现症状时间' : '最后一次正常时间'}<input value={draft[key] ?? ''}
          maxLength={150} placeholder="不确定可留空，如：约10分钟前"
          onChange={e => setDraft({ ...draft, [key]: e.target.value })} /></label>)}
    </fieldset>
    <details><summary>补充地点、病史与已采取措施</summary><fieldset disabled={!editable} className={styles.fields}>
      {([['location', '地点', 200], ['age', '年龄', 50], ['history', '已知病史/过敏', 400],
        ['medications', '已用药名、用量和时间', 400], ['actions_taken', '已实际采取的措施', 500]] as const).map(([key, label, max]) =>
        <label key={key}>{label}<input value={draft[key] ?? ''} maxLength={max} placeholder="未知可留空"
          onChange={e => setDraft({ ...draft, [key]: e.target.value })} /></label>)}
    </fieldset></details>
    {onUpdate ? <button type="button" disabled={!editable || !dirty} onClick={() => void save()}>{busy ? '正在保存…' : '更新记录'}</button>
      : <p>历史记录只读。最新一轮应急记录可更新。</p>}
    {dirty && <span role="status"> 有未保存的修改</span>}
    {error && <p role="alert">{error}</p>}
    <div className={styles.top}><h3>交接记录</h3><button type="button" disabled={dirty || busy} onClick={() => void copy()}>{copied ? '已复制' : '复制交接记录'}</button></div>
    <pre className={styles.report}>{item.handover}</pre>
    <details><summary>急救指引来源</summary><ul>{item.sources.map(source => <li key={source.url}>
      <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a></li>)}</ul></details>
  </section>;
}

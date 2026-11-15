import type { HealthCard } from './protocol';
import styles from './CopilotCards.module.css';

function emphasized(text: string, highlights: string[]) {
  const words = [...new Set(highlights.filter(word => word && text.includes(word)))].sort((a, b) => b.length - a.length);
  if (!words.length) return text;
  const escaped = words.map(word => word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  return text.split(new RegExp(`(${escaped.join('|')})`, 'g')).map((part, index) =>
    words.includes(part) ? <strong key={index}>{part}</strong> : part);
}

export default function HealthAnswer({ item, onFollowUp, loading }: {
  item: HealthCard; onFollowUp?: (text: string) => void; loading: boolean;
}) {
  return <section data-card-type="health_answer" className={styles.health}>
    {item.disclaimer && <p className={styles.knowledgeBasis}>{item.disclaimer}</p>}
    {item.sections.map((section, index) => <div key={index} className={styles.healthSection}>
      <h2>{section.title}</h2><ul>{section.items.map((point, number) => <li key={number}>
        {emphasized(point.text, point.highlights)}{' '}
        {point.source_ids.map(id => { const source = item.sources.find(source => source.id === id);
          return source?.url ? <a className={styles.citation} key={id} href={source.url} target="_blank" rel="noopener noreferrer"
            aria-label={`查看来源：${source.title}`} title={source.title}>↗</a> : null; })}
      </li>)}</ul>
    </div>)}
    {item.sources.length > 0 && <details className={styles.healthSources}><summary>参考资料 · {item.sources.length} 条</summary>
      {item.sources.map(source => <div key={source.id}>
        {source.url ? <a href={source.url} target="_blank" rel="noopener noreferrer">{source.title} ↗</a> : <strong>{source.title}</strong>}
        <p>{source.publisher}</p>
        {source.attribution_logo === 'https://assets.nhs.uk/nhsuk-cms/images/nhs-attribution.width-510.png'
          && <img src={source.attribution_logo} alt="Content provided by the NHS website" width="160" />}
        <blockquote>{source.quote}</blockquote>
      </div>)}
    </details>}
    {item.follow_up && onFollowUp && <div className={styles.followUp}>
      <p>{item.follow_up.question}</p><div>{item.follow_up.options.map(option =>
        <button type="button" key={option} disabled={loading} onClick={() => onFollowUp(`补充信息：${item.follow_up!.question} ${option}`)}>{option}</button>)}</div>
    </div>}
  </section>;
}

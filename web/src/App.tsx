import { useEffect, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import CopilotCards from './CopilotCards';
import InventoryImport from './InventoryImport';
import InventoryManager from './InventoryManager';
import ApiSettings from './ApiSettings';
import { useCopilotStream } from './useCopilotStream';
import { chatUrl, createChat, listChats, loadChat } from './chatApi';
import type { Chat, Turn } from './chatApi';
import { card as validCard } from './protocol';
import type { EmergencyCard, EmergencyFacts } from './protocol';
import styles from './ChatLayout.module.css';
import brand from '../brand.png';

const TAGS = ['孕妇', '哺乳期', '儿童', '高血压'];

export default function App() {
  const stream = useCopilotStream();
  const [question, setQuestion] = useState('');
  const [storeId, setStoreId] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  const [configError, setConfigError] = useState('');
  const [ready, setReady] = useState(false);
  const [mock, setMock] = useState(false);
  const [view, setView] = useState<'query' | 'import' | 'inventory'>('query');
  const [editInventoryId, setEditInventoryId] = useState<number | null>(null);
  const [visionReady, setVisionReady] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [configVersion, setConfigVersion] = useState(0);
  const accountArea = useRef<HTMLDivElement>(null);
  const [collapsed, setCollapsed] = useState(() => window.innerWidth < 760);
  const [chats, setChats] = useState<Chat[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState('');
  const [sending, setSending] = useState(false);
  const selectionEpoch = useRef(0);
  const submissionLock = useRef(false);
  const pending = useRef<{chat: string; turn: string} | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const active = chats.find(chat => chat.id === activeId);

  useEffect(() => {
    const abort = new AbortController();
    setConfigError('');
    fetch('/api/pharmacy/config', { signal: abort.signal }).then(async response => {
      if (!response.ok) throw new Error('无法读取门店配置');
      const config = await response.json();
      setStoreId(typeof config.store_id === 'string' ? config.store_id : '');
      setReady(config.inventory_ready === true);
      setVisionReady(config.vision_configured === true);
      setMock(config.inventory_data_mode === 'mock');
      if (!config.store_id) setHistoryLoading(false);
      if (!config.inventory_ready) setConfigError('尚未配置库存数据库，请核对门店配置。');
    }).catch(error => { if (!abort.signal.aborted) { setConfigError(error.message); setHistoryLoading(false); } });
    return () => abort.abort();
  }, [configVersion]);
  useEffect(() => {
    if (!storeId) return;
    const abort = new AbortController();
    const epoch = ++selectionEpoch.current;
    setHistoryLoading(true); setHistoryError('');
    void (async () => {
      try {
        const sessions = await listChats(storeId, abort.signal);
        if (abort.signal.aborted || epoch !== selectionEpoch.current) return;
        setChats(sessions);
        const id = new URL(window.location.href).searchParams.get('chat');
        if (id) {
          const saved = await loadChat(id, storeId, abort.signal);
          if (abort.signal.aborted || epoch !== selectionEpoch.current) return;
          setChats(previous => previous.some(chat => chat.id === id)
            ? previous.map(chat => chat.id === id ? saved : chat) : [saved, ...previous]);
          setActiveId(id); setTags(saved.customer?.customer_tags ?? []);
        }
      } catch (error) {
        if (!abort.signal.aborted && epoch === selectionEpoch.current) {
          setHistoryError(error instanceof Error ? error.message : '无法加载聊天记录');
        }
      } finally {
        if (!abort.signal.aborted && epoch === selectionEpoch.current) setHistoryLoading(false);
      }
    })();
    return () => abort.abort();
  }, [storeId]);
  useEffect(() => {
    if (!accountOpen) return;
    const outside = (event: PointerEvent) => {
      if (!accountArea.current?.contains(event.target as Node)) setAccountOpen(false);
    };
    const escape = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') setAccountOpen(false); };
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [accountOpen]);
  useEffect(() => {
    const target = pending.current;
    if (!target) return;
    setChats(previous => previous.map(chat => chat.id !== target.chat ? chat : {
      ...chat, turns: chat.turns.map(turn => turn.id !== target.turn ? turn : {
        ...turn, cards: stream.cards, overall: stream.overall, stages: stream.stages,
        error: stream.error, completed: stream.completed, loading: stream.loading,
      }),
    }));
  }, [stream.cards, stream.overall, stream.stages, stream.error, stream.completed, stream.loading]);
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }); }, [chats, view]);

  function stopPending() {
    const target = pending.current;
    pending.current = null;
    if (!stream.loading) return;
    if (target) setChats(previous => previous.map(chat => chat.id !== target.chat ? chat : {
      ...chat, turns: chat.turns.map(turn => turn.id !== target.turn ? turn : {
        ...turn, loading: false, completed: false, cards: [], error: '已停止，未完成的结果不可作为拿药依据',
      }),
    }));
    stream.cancel();
  }
  function newChat() {
    selectionEpoch.current++; stopPending(); setActiveId(null); setQuestion(''); setTags([]); setView('query');
    setHistoryLoading(false); setHistoryError(''); chatUrl(null);
  }
  async function openChat(id: string) {
    stopPending();
    const epoch = ++selectionEpoch.current;
    setView('query'); setQuestion(''); setHistoryLoading(true); setHistoryError('');
    try {
      const saved = await loadChat(id, storeId);
      if (epoch !== selectionEpoch.current) return;
      setChats(previous => previous.some(chat => chat.id === id)
        ? previous.map(chat => chat.id === id ? saved : chat) : [saved, ...previous]);
      setActiveId(id); setTags(saved.customer?.customer_tags ?? []); chatUrl(id);
    } catch (error) {
      if (epoch === selectionEpoch.current) setHistoryError(error instanceof Error ? error.message : '无法加载会话');
    } finally { if (epoch === selectionEpoch.current) setHistoryLoading(false); }
  }
  async function submit(event?: FormEvent, suggestion?: string) {
    event?.preventDefault();
    const raw = (suggestion ?? question).trim();
    if (!raw || !storeId || stream.loading || historyLoading || submissionLock.current) return;
    submissionLock.current = true; setSending(true); setHistoryError('');
    const epoch = selectionEpoch.current;
    let chatId = activeId;
    try {
      if (!chatId) {
        const created = await createChat(storeId);
        if (epoch !== selectionEpoch.current) return;
        chatId = created.id;
        setChats(previous => [created, ...previous]);
      }
      if (epoch !== selectionEpoch.current) return;
      const turn: Turn = { id: crypto.randomUUID(), question: raw, cards: [], overall: 'unknown',
        stages: [], error: '', completed: false, loading: true, animate: true };
      pending.current = { chat: chatId, turn: turn.id };
      setChats(previous => previous.map(chat => chat.id === chatId
        ? { ...chat, title: chat.turns.length ? chat.title : raw.slice(0, 80), turns: [...chat.turns, turn] } : chat));
      setActiveId(chatId); setQuestion('');
      chatUrl(chatId);
      void stream.start({ question: raw, session_id: chatId, turn_id: turn.id, workspace: { store_id: storeId, task: 'drug_lookup',
        customer: { customer_tags: tags } } });
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : '会话创建失败，本次未发送');
    } finally { submissionLock.current = false; setSending(false); }
  }
  function enter(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault(); submit();
    }
  }
  function resumeTurn(turn: Turn) {
    if (!activeId || stream.loading || submissionLock.current) return;
    pending.current = { chat: activeId, turn: turn.id };
    setChats(previous => previous.map(chat => chat.id !== activeId ? chat : {
      ...chat, turns: chat.turns.map(item => item.id !== turn.id ? item : {
        ...item, cards: [], stages: [], error: '', loading: true, completed: false, animate: true,
      }),
    }));
    void stream.start({ question: turn.question, session_id: activeId, turn_id: turn.id, resume: true,
      workspace: { store_id: storeId, task: 'drug_lookup', customer: { customer_tags: tags } } });
  }

  async function updateEmergency(chatId: string, turnId: string, item: EmergencyCard, changes: Partial<EmergencyFacts>): Promise<EmergencyCard> {
    const response = await fetch(`/api/chats/${encodeURIComponent(chatId)}/turns/${encodeURIComponent(turnId)}/emergency`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ store_id: storeId, expected_revision: item.revision,
        changes: Object.fromEntries(Object.entries(changes).filter(([, value]) => value !== null)) }),
    });
    const value: unknown = await response.json();
    if (!response.ok) throw new Error(response.status === 409 ? '记录已更新，请重新打开会话后再修改' : '保存失败，现场记录尚未更新');
    if (!validCard(value) || value.type !== 'emergency_support') throw new Error('记录格式异常，请重新打开会话');
    setChats(previous => previous.map(chat => chat.id !== chatId ? chat : {
      ...chat, turns: chat.turns.map(turn => turn.id !== turnId ? turn : {
        ...turn, cards: turn.cards.map(c => c.id === item.id ? value : c),
      }),
    }));
    return value;
  }

  return <div className={`${styles.app} ${collapsed ? styles.collapsed : ''}`}>
    <aside className={styles.sidebar}>
      <div className={styles.brandRow}>
        {!collapsed && <img src={brand} alt="医点通 MediTouch" />}
        <button className={styles.collapse} onClick={() => setCollapsed(value => !value)}
          aria-label={collapsed ? '展开侧栏' : '收起侧栏'} aria-expanded={!collapsed} title={collapsed ? '展开侧栏' : '收起侧栏'}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16M5.5 8h1M5.5 12h1"/></svg>
        </button>
      </div>
      <button className={styles.newChat} onClick={newChat} aria-label="新咨询"><span>＋</span>{!collapsed && ' 新咨询'}</button>
      {!collapsed && <>
        <div className={styles.historyLabel}>聊天记录</div>
        <nav className={styles.history} aria-label="聊天记录">
          {chats.map(chat => <button key={chat.id} title={chat.title}
            aria-current={activeId === chat.id && view === 'query' ? 'page' : undefined}
            onClick={() => void openChat(chat.id)}>
            {chat.title}
          </button>)}
          {!chats.length && <p className={styles.emptyHistory}>暂无聊天记录</p>}
        </nav>
      </>}
      <div className={styles.sidebarBottom}>
        <button className={styles.importButton} aria-label="库存管理" aria-pressed={view === 'inventory'}
          onClick={() => { stopPending(); setView('inventory'); }}><span aria-hidden>▦</span>{!collapsed && ' 库存管理'}</button>
        <button className={styles.importButton} aria-label="拍照入库" aria-pressed={view === 'import'}
          onClick={() => { stopPending(); setView('import'); }}><svg width="18" height="18" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M14.5 4h-5L7.5 7H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3.5z" />
            <circle cx="12" cy="13.5" r="4" />
          </svg>{!collapsed && ' 拍照入库'}</button>
        <div ref={accountArea} className={styles.accountArea}>
        {accountOpen && <div className={styles.accountMenu}>
          <button type="button" onClick={() => { stopPending(); setAccountOpen(false); setSettingsOpen(true); }}>⚙ <span>API 配置</span></button>
        </div>}
        <button type="button" className={styles.account} aria-label="本地门店设置" aria-expanded={accountOpen}
          onClick={() => setAccountOpen(value => !value)}><span className={styles.avatar} aria-hidden>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="8" r="4"/><path d="M4 22v-3a8 8 0 0 1 16 0v3z"/></svg>
        </span>{!collapsed && <><span>本地门店</span><span className={styles.chevron} aria-hidden>{accountOpen ? '⌄' : '⌃'}</span></>}</button>
        </div>
      </div>
    </aside>
    <main className={styles.main}>
      <header className={styles.topbar}><h1>{view === 'inventory' ? '库存管理' : view === 'import' ? '拍照入库' : active?.title || '新对话'}</h1>
        <span className={styles.connection}>{ready ? mock ? '演示库存' : '库存已连接' : '等待配置'}</span></header>
      {view === 'inventory' ? <div className={styles.importPage}>
        <InventoryManager storeId={storeId} onImport={() => setView('import')}
          editInventoryId={editInventoryId} onEditOpened={() => setEditInventoryId(null)} />
      </div> : view === 'import' ? <div className={styles.importPage}>
        {configError && <p role="alert" className={styles.error}>{configError}</p>}
        {mock && <p className={styles.mock}>演示库存 · 记录仅供测试，不能作为真实售药依据</p>}
        <InventoryImport storeId={storeId} visionReady={visionReady} onEditExisting={id => {
          setEditInventoryId(id); setView('inventory');
        }} />
      </div> : <>
        <div className={styles.transcript}>
          {!active?.turns.length && <div className={styles.welcome}>
            <img src={brand} alt="医点通" />
            <div><h2>我是<span>医点通</span><br />你的药店接待助手</h2><p>健康问答、查库存、找货架、核对用药注意事项</p></div>
          </div>}
          <div className={styles.messages}>
            {active?.turns.map(turn => <section className={styles.turn} key={turn.id} aria-label="一轮对话">
              <div className={styles.question}>{turn.question}</div>
              <div className={styles.answer}>
                {!turn.loading && turn.stages.length > 0 && <details className={styles.process}>
                  <summary>{turn.completed ? '本轮核对完成' : '执行记录'}</summary>
                  <ol>{turn.stages.map((stage, index) => <li key={index}>{stage}</li>)}</ol>
                </details>}
                {turn.error && <p className={styles.error} role="alert">{turn.error}</p>}
                {turn.error && !turn.loading && !turn.completed && active.turns.at(-1)?.id === turn.id
                  && turn.status !== 'processing' && <button type="button" className={styles.resume}
                    disabled={stream.loading} onClick={() => resumeTurn(turn)}>继续处理</button>}
                <CopilotCards cards={turn.cards} overall={turn.overall} loading={turn.loading} animate={turn.animate} stages={turn.stages}
                  onEmergencyUpdate={turn.completed && active.turns.at(-1)?.id === turn.id
                    ? (item, changes) => updateEmergency(active.id, turn.id, item, changes) : undefined}
                  onFollowUp={text => void submit(undefined, text)} />
              </div>
            </section>)}
            <div ref={end} />
          </div>
        </div>
        <div className={styles.composerDock}>
          {historyLoading && <p role="status">正在加载聊天记录…</p>}
          {historyError && <p role="alert" className={styles.error}>{historyError}</p>}
          {configError && <p role="alert" className={styles.error}>{configError}</p>}
          <form className={styles.composer} onSubmit={submit}>
            <label htmlFor="question" className={styles.srOnly}>顾客想找什么药？</label>
            <textarea id="question" value={question} onChange={event => setQuestion(event.target.value)} onKeyDown={enter}
              rows={3} maxLength={20000} placeholder="输入药名，或描述顾客的症状与用药问题" />
            <div className={styles.composerFooter}>
              <details className={styles.context}><summary>已知情况 <span>选填</span></summary>
                <div className={styles.tags}>{TAGS.map(tag => <label key={tag}>
                  <input type="checkbox" checked={tags.includes(tag)} onChange={() => setTags(current =>
                    current.includes(tag) ? current.filter(value => value !== tag) : [...current, tag])} />{tag}</label>)}</div>
              </details>
              {stream.loading ? <button type="button" className={styles.send} onClick={stopPending}>停止 ■</button>
                : <button type="submit" className={styles.send} disabled={!question.trim() || !storeId || historyLoading || sending}>{sending ? '正在保存…' : '发送 ↑'}</button>}
            </div>
          </form>
          <p className={styles.disclaimer}>商品信息与健康资料供参考{mock ? ' · 当前为演示数据' : ''}</p>
        </div>
      </>}
    </main>
    {settingsOpen && <ApiSettings onClose={() => setSettingsOpen(false)} onSaved={() => setConfigVersion(value => value + 1)} />}
  </div>;
}

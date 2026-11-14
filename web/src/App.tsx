import { useEffect, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import CopilotCards from './CopilotCards';
import InventoryImport from './InventoryImport';
import InventoryManager from './InventoryManager';
import ApiSettings from './ApiSettings';
import { useCopilotStream } from './useCopilotStream';
import { chatUrl, createChat, listChats, loadChat } from './chatApi';
import type { Chat, Turn } from './chatApi';
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
    fetch('/api/pharmacy/config', { signal: abort.signal }).then(async response => {
      if (!response.ok) throw new Error('无法读取门店配置');
      const config = await response.json();
      setStoreId(typeof config.store_id === 'string' ? config.store_id : '');
      setReady(config.inventory_ready === true);
      setVisionReady(config.vision_configured === true);
      setMock(config.inventory_data_mode === 'mock');
      if (!config.inventory_ready) setConfigError('尚未配置库存数据库，请核对门店配置。');
    }).catch(error => { if (!abort.signal.aborted) setConfigError(error.message); });
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
  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const raw = question.trim();
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
        stages: [], error: '', completed: false, loading: true };
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
          onClick={() => { stopPending(); setView('import'); }}><span aria-hidden>▧</span>{!collapsed && ' 拍照入库'}</button>
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
        <InventoryManager storeId={storeId} onImport={() => setView('import')} />
      </div> : view === 'import' ? <div className={styles.importPage}>
        {configError && <p role="alert" className={styles.error}>{configError}</p>}
        {mock && <p className={styles.mock}>演示库存 · 记录仅供测试，不能作为真实售药依据</p>}
        <InventoryImport storeId={storeId} visionReady={visionReady} />
      </div> : <>
        <div className={styles.transcript}>
          {!active?.turns.length && <div className={styles.welcome}>
            <img src={brand} alt="医点通" />
            <div><h2>我是<span>医点通</span><br />你的药店接待助手</h2><p>查库存、找货架、核对用药注意事项</p></div>
          </div>}
          <div className={styles.messages}>
            {active?.turns.map(turn => <section className={styles.turn} key={turn.id} aria-label="一轮对话">
              <div className={styles.question}>{turn.question}</div>
              <div className={styles.answer}>
                {turn.stages.length > 0 && <details className={styles.process} open={turn.loading}>
                  <summary>{turn.loading ? '正在处理' : turn.completed ? '本轮核对完成' : '执行记录'}</summary>
                  <ol>{turn.stages.map((stage, index) => <li key={index}>{stage}</li>)}</ol>
                </details>}
                {turn.error && <p className={styles.error} role="alert">{turn.error}</p>}
                <CopilotCards cards={turn.cards} overall={turn.overall} loading={turn.loading} />
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
          <p className={styles.disclaimer}>核查结果供店员参考，最终由门店药师确认{mock ? ' · 当前为演示数据' : ''}</p>
        </div>
      </>}
    </main>
    {settingsOpen && <ApiSettings onClose={() => setSettingsOpen(false)} onSaved={() => setConfigVersion(value => value + 1)} />}
  </div>;
}

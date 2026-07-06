import React, { useState, useEffect, useRef } from 'react';
import {
  Activity,
  CheckCircle2,
  XCircle,
  Image as ImageIcon,
  Zap,
  Box,
  Play,
  Square,
  AlertCircle
} from 'lucide-react';

console.log("App.jsx: File Loaded");

// Helper to ensure price has $ and commas
const formatDisplayPrice = (val) => {
  if (!val) return '(無價格)';
  if (val === '無' || val === '未偵測到') return val;
  if (val.toString().includes('$')) return val;
  const num = parseInt(val.toString().replace(/,/g, ''), 10);
  if (!isNaN(num)) {
      return `$${num.toLocaleString()}`;
  }
  return val;
};

// [v18.73] 超嚴格型號驗證（檢查標題）
const getResultThumbSrc = (res) => {
  if (!res) return null;
  if (res.source_path) return `/api/image/${encodeURIComponent(res.source_path)}`;
  if (res.file_name) return `/api/image/${encodeURIComponent(res.file_name)}`;
  if (res.thumb_b64) return `data:image/jpeg;base64,${res.thumb_b64}`;
  return null;
};

const getResultImageSrc = (res) => {
  if (!res) return null;
  if (res.source_path) return `/api/image/${encodeURIComponent(res.source_path)}`;
  if (res.file_name) return `/api/image/${encodeURIComponent(res.file_name)}`;
  return null;
};

const ResultThumbnail = ({ res, onClick }) => {
  const [failed, setFailed] = useState(false);
  const src = failed ? null : getResultThumbSrc(res);
  return (
    <button
      type="button"
      onClick={onClick}
      title="檢視照片"
      style={{
        width: '56px',
        height: '56px',
        flex: '0 0 56px',
        padding: 0,
        border: '1px solid #333',
        borderRadius: '4px',
        background: '#0a0a0a',
        overflow: 'hidden',
        cursor: 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center'
      }}
    >
      {src ? (
        <img
          src={src}
          onError={() => setFailed(true)}
          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
          alt=""
        />
      ) : (
        <ImageIcon size={22} color="#666" />
      )}
    </button>
  );
};

const UI_VERSION = "v19.14 (LLM Log Restored)";
console.log(`[Dashboard-Init] Version: ${UI_VERSION} | Timestamp: ${new Date().toLocaleTimeString()}`);

const App = () => {
  console.log("App: Component Initialize");

  // Default State to prevent crash/white screen
  const defaultState = {
      stats: { success: 0, failed: 0, total: 0, processed: 0, is_running: false },
      lm_logs: ["系統初始化完成，等待連線..."],
      current_file: null,
      stream_file: null,
      latest_result_file: null,
      sys: { cpu: 0, mem: 0 },
      recent_results: [],
      dynamic_examples_list: [],
      stream_buffer: "" // Real-time thinking buffer
  };

  const [data, setData] = useState(defaultState);
  const [currentImage, setCurrentImage] = useState(null);
  const [currentThumb, setCurrentThumb] = useState(null);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);
  const [error, setError] = useState(null);
  const [saveStatus, setSaveStatus] = useState('');
  const [availableDirs, setAvailableDirs] = useState([]);
  const [targetDir, setTargetDir] = useState(localStorage.getItem('samsung_ocr_target_dir') || '商化照片-202512');
  const [controlMsg, setControlMsg] = useState('');
  const [isConnected, setIsConnected] = useState(true); // [v18.67] 追蹤伺服器連線狀態
  const [showConfirmModal, setShowConfirmModal] = useState(false);
  const [confirmModalConfig, setConfirmModalConfig] = useState({ title: '', message: '', onConfirm: null });
  const [editingFile, setEditingFile] = useState(null);

  // [v16.9] Image Inspection Modal State
  const [inspectImage, setInspectImage] = useState(null);
  const [modalPosition, setModalPosition] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const dragStartRef = useRef({ x: 0, y: 0 });
  const [correctionData, setCorrectionData] = useState({
      view_type: '單機',
      screen_status: '',
      quality_issue: '',
      model: '',
      price: ''
  });
  const [selectedImage, setSelectedImage] = useState(null);
  const [autoScroll, setAutoScroll] = useState(true);
  // [v19.8] Track files queued for rerun so UI gives immediate feedback.
  const [rerunQueue, setRerunQueue] = useState({});
  const [showReviewPanel, setShowReviewPanel] = useState(false);
  const [reviewYear, setReviewYear] = useState('2026');
  const [reviewReason, setReviewReason] = useState('');
  const [reviewQueue, setReviewQueue] = useState({ items: [], total: 0, returned: 0, summary: {} });
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewDrafts, setReviewDrafts] = useState({});
  const [reviewMsg, setReviewMsg] = useState('');

  // Refs for auto-scroll
  const logsContainerRef = useRef(null);
  const logsEndRef = useRef(null);
  const streamBufferRef = useRef(null);
  const messagesEndRef = useRef(null);
  const lastProcessedRef = useRef(null);
  const currentImageFileRef = useRef(null);

  const fetchData = async () => {
    try {
      const response = await fetch('/api/status');
      if (!response.ok) throw new Error('API Error');
      const apiResult = await response.json();
      const activeFile = apiResult.current_file && apiResult.current_file !== 'None'
        ? apiResult.current_file
        : null;
      const isStreamSynced = activeFile && apiResult.stream_file === activeFile;
      const syncedResult = {
        ...apiResult,
        stream_buffer: isStreamSynced ? (apiResult.stream_buffer || "") : ""
      };

      setData(prev => ({...prev, ...syncedResult}));

      setError(null);
      setIsConnected(true); // [v18.67] 連線成功
    } catch (err) {
      setError(err.message);
      setIsConnected(false); // [v18.67] 連線失敗
    }
  };

  // [v19.8 UX] Display queue lets backend run ahead while UI plays completed
  // results at typewriter speed. -1 means "live" (show current_file + stream_buffer).
  const [displayQueueIndex, setDisplayQueueIndex] = useState(-1);
  const [displayedBuffer, setDisplayedBuffer] = useState("");
  const isAdvancingRef = useRef(false);
  const playedQueueKeysRef = useRef(new Set());
  const [presentedQueueCutoff, setPresentedQueueCutoff] = useState(-1);
  const [displayTargetKey, setDisplayTargetKey] = useState("");
  const [typewriterReady, setTypewriterReady] = useState(false);

  const getQueueKey = (item) => `${item?.completed_at || ''}|${item?.file_name || ''}`;
  const getQueueDisplayText = (item) => {
    if (!item) return "";
    if (item.stream_buffer && item.stream_buffer.trim()) return item.stream_buffer;
    const result = item.result || {};
    return `這張已完成辨識：${result.view_type || '單機'}，${result.model || '無型號'}，${result.price || '無價格'}。`;
  };

  // When new completed results arrive and we are in live mode, start draining queue.
  useEffect(() => {
    const queue = data.display_queue || [];
    if (displayQueueIndex === -1 && queue.length > 0) {
      const nextIndex = queue.findIndex((item) => !playedQueueKeysRef.current.has(getQueueKey(item)));
      if (nextIndex !== -1) {
        setDisplayQueueIndex(nextIndex);
      }
      setDisplayedBuffer("");
    } else if (displayQueueIndex >= 0 && displayQueueIndex >= queue.length) {
      // Queue overflow / caught up to live - switch back to live mode.
      setDisplayQueueIndex(-1);
      setDisplayedBuffer("");
    }
  }, [data.display_queue, displayQueueIndex]);

  // Determine the current typewriter target (queued result or live stream).
  const getDisplayTarget = () => {
    const queue = data.display_queue || [];
    if (displayQueueIndex >= 0 && displayQueueIndex < queue.length) {
      const item = queue[displayQueueIndex];
      return { target: getQueueDisplayText(item), isQueue: true, key: getQueueKey(item) };
    }
    return { target: data.stream_buffer || "", isQueue: false, key: `live|${data.stream_file || data.current_file || ""}` };
  };

  // [v19.12 UX] Stage the illusion deliberately:
  // 1) show the photo immediately,
  // 2) give the viewer a tiny visual lead-in,
  // 3) then start the self-talk,
  // 4) reveal the thumbnail/result only after the self-talk completes.
  useEffect(() => {
    const { key } = getDisplayTarget();
    if (!key || key === displayTargetKey) return;
    setDisplayTargetKey(key);
    setDisplayedBuffer("");
    setTypewriterReady(false);
    const leadIn = setTimeout(() => setTypewriterReady(true), 140);
    return () => clearTimeout(leadIn);
  }, [data.current_file, data.stream_file, data.display_queue, displayQueueIndex]);

  // [v19.12 UX] Linear Typewriter Effect (Readable Speed)
  useEffect(() => {
    const { target } = getDisplayTarget();
    if (!typewriterReady) return;
    if (!target) {
        setDisplayedBuffer("");
        return;
    }

    // If target reset/shrank, reset display immediately
    if (target.length < displayedBuffer.length) {
         setDisplayedBuffer(target);
         return;
    }

    const timer = setInterval(() => {
        setDisplayedBuffer((prev) => {
            const { target: t } = getDisplayTarget();
            if (prev.length < t.length) {
                return t.slice(0, prev.length + 1);
            }
            return prev;
        });
    }, 18); // v19.12: readable but still brisk for supervisor viewing.

    return () => clearInterval(timer);
  }, [data.display_queue, data.stream_buffer, displayQueueIndex, typewriterReady, displayTargetKey]);

  // [v19.8 UX] Advance to next queued item when current one finishes typing.
  useEffect(() => {
    const queue = data.display_queue || [];
    const { target, isQueue } = getDisplayTarget();
    if (!isQueue || !target || displayedBuffer.length < target.length || isAdvancingRef.current) return;

    isAdvancingRef.current = true;
    const timer = setTimeout(() => {
      const currentItem = queue[displayQueueIndex];
      if (currentItem) {
        playedQueueKeysRef.current.add(getQueueKey(currentItem));
        setPresentedQueueCutoff((prev) => Math.max(prev, displayQueueIndex));
      }

      const nextIndex = queue.findIndex((item, idx) => (
        idx > displayQueueIndex && !playedQueueKeysRef.current.has(getQueueKey(item))
      ));

      if (nextIndex !== -1) {
        setDisplayQueueIndex(nextIndex);
        setDisplayedBuffer("");
      } else {
        // Drained queue - return to live mode.
        setDisplayQueueIndex(-1);
        setDisplayedBuffer("");
      }
      isAdvancingRef.current = false;
    }, 120); // Must stay below the 500ms polling interval or reveal gets canceled.

    return () => { clearTimeout(timer); isAdvancingRef.current = false; };
  }, [displayedBuffer, displayQueueIndex, data.display_queue, data.stream_buffer]);

  // [v19.8 UX] Choose displayed image: queued completed result, or live current_file.
  useEffect(() => {
    const queue = data.display_queue || [];
    if (displayQueueIndex >= 0 && displayQueueIndex < queue.length) {
      const item = queue[displayQueueIndex];
      setImageLoaded(false);
      setImageFailed(false);
      setCurrentThumb(item.thumb_b64 || null);
      setCurrentImage(getResultImageSrc(item));
    } else {
      const activeFile = data.current_file && data.current_file !== 'None' ? data.current_file : null;
      if (activeFile) {
        setImageLoaded(false);
        setImageFailed(false);
        setCurrentImage(`/api/image/${encodeURIComponent(activeFile)}`);
        setCurrentThumb(data.current_thumb || null);
      } else {
        setCurrentImage(null);
        setCurrentThumb(null);
        setImageFailed(false);
      }
    }
  }, [displayQueueIndex, data.display_queue, data.current_file, data.current_thumb]);



  // Poll API with Dynamic Interval
  useEffect(() => {
    console.log("App: useEffect (Polling) Start");
    let intervalId;
    const poll = async () => { await fetchData(); };
    poll();
    // [v19.8] Faster polling (500ms) for smoother photo/self-talk sync.
    const intervalMs = 500;
    // if (data?.stats?.is_running) console.log(`⏱️ 同步頻率證據: ${intervalMs}ms`);
    intervalId = setInterval(poll, intervalMs);
    return () => clearInterval(intervalId);
  }, []); // Remove dependency on is_running to avoid re-bind loops

  useEffect(() => {
    console.log("App: useEffect (Initial Styles) Start");
    document.body.style.margin = '0';
    document.body.style.overflow = 'hidden';
    document.body.style.background = '#080808';
  }, []);

  const fetchDirs = async () => {
    try {
      console.log("App: Fetching Dirs...");
      const res = await fetch('/api/list_dirs');
      const dirs = await res.json();
      setAvailableDirs(dirs);
      if (dirs.length > 0 && !targetDir) setTargetDir(dirs[0]);
    } catch (e) {
      console.error("Failed to fetch dirs", e);
    }
  };

  useEffect(() => {
    console.log("App: useEffect (Fetch Dirs) Start");
    fetchDirs();
  }, []);

  // [v16.27 Persistence] Save targetDir to localStorage
  // [v19.7 Fix] Also sync with backend immediately!
  useEffect(() => {
    if (targetDir) {
        localStorage.setItem('samsung_ocr_target_dir', targetDir);

        // Call Backend to Switch Dir
        fetch('/api/set_work_dir', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ dir: targetDir })
        }).then(res => res.json())
          .then(data => {
              console.log("[Backend] Work Dir Switched:", targetDir, data);
              // Force fetch data to update stats immediately
              fetchData();
          })
          .catch(err => console.error("Failed to sync work dir:", err));
    }
  }, [targetDir]);

  const handleSave = async (fileName) => {
      try {
          const res = await fetch('/api/update_record', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                  filename: fileName,
                  updates: correctionData
              })
          });
          if (res.ok) {
              setSaveStatus(`✅ 已儲存 ${fileName}`);
              setEditingFile(null);
              fetchData();
              setTimeout(() => setSaveStatus(''), 2000);
          } else {
              setSaveStatus('❌ 儲存失敗');
          }
      } catch (e) {
          setSaveStatus('❌ 發生錯誤');
      }
  };

  const handleStart = async (restart = false, reprocessLast = 0) => {
      console.log("handleStart called", { restart, reprocessLast });
      try {
          const res = await fetch('/api/start_batch', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                  dir: targetDir,
                  restart: restart,
                  reprocess_last_n: reprocessLast
              })
          });
          const json = await res.json();

          if (json.status === 'needs_confirmation') {
              setConfirmModalConfig({
                  title: "📌 批次處理確認",
                  message: json.message,
                  onConfirm: () => {
                      setShowConfirmModal(false);
                      handleStart(false, 5);
                  }
              });
              setShowConfirmModal(true);
              return;
          }

          if (res.ok) {
             setControlMsg(`✅ ${json.message}`);
             setTimeout(() => setControlMsg(''), 3000);
          } else {
             setControlMsg(`❌ ${json.error || '不明錯誤'}`);
          }
      } catch (e) {
          setControlMsg(`❌ 啟動失敗: ${e}`);
      }
  };

  const handleStop = async () => {
      try {
          const res = await fetch('/api/stop', { method: 'POST' });
          const json = await res.json();
          if (res.ok) {
             setControlMsg(`🛑 ${json.message}`);
             setTimeout(() => setControlMsg(''), 3000);
          }
      } catch (e) {
          // [v18.67] 即使連線失敗，也顯示已發送停止請求
          setControlMsg(`⚠️ 已發送停止請求 (伺服器可能已停止或斷線)`);
          setTimeout(() => setControlMsg(''), 5000);
      }
  };

  const fetchReviewQueue = async (year = reviewYear, reason = reviewReason) => {
      setReviewLoading(true);
      setReviewMsg('');
      try {
          const params = new URLSearchParams({ year, limit: '300' });
          if (reason) params.set('reason', reason);
          const res = await fetch(`/api/review_queue?${params.toString()}`);
          const json = await res.json();
          if (!res.ok) throw new Error(json.error || '待審清單讀取失敗');
          setReviewQueue(json);
      } catch (e) {
          setReviewMsg(`讀取失敗：${e.message || e}`);
      } finally {
          setReviewLoading(false);
      }
  };

  const openReviewPanel = () => {
      setShowReviewPanel(true);
      fetchReviewQueue();
  };

  const getReviewDraft = (item) => reviewDrafts[item.file_name] || {};

  const updateReviewDraft = (item, patch) => {
      setReviewDrafts(prev => ({
          ...prev,
          [item.file_name]: {
              ...(prev[item.file_name] || {}),
              ...patch
          }
      }));
  };

  const saveReviewCorrection = async (item, action = 'manual_correction') => {
      const draft = getReviewDraft(item);
      const payload = {
          file_name: item.file_name,
          source_path: item.source_path,
          period: item.period,
          year: item.year,
          reasons: item.reasons,
          view_type: draft.view_type ?? item.view_type ?? '',
          model: draft.model ?? item.model ?? '',
          price: draft.price ?? item.price ?? '',
          price_symbol: draft.price_symbol ?? '',
          note: draft.note ?? '',
          learn_rule: Boolean(draft.learn_rule),
          rule_hint: draft.rule_hint ?? '',
          match_text: draft.match_text ?? item.file_name,
          action
      };
      try {
          const res = await fetch('/api/review_correction', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
          });
          const json = await res.json();
          if (!res.ok) throw new Error(json.error || '儲存失敗');
          setReviewMsg(action === 'needs_rerun' ? '已標記重跑需求' : '人工校正已記錄');
          setTimeout(() => setReviewMsg(''), 2500);
      } catch (e) {
          setReviewMsg(`儲存失敗：${e.message || e}`);
      }
  };

  const stats = data.stats || defaultState.stats;
  const isRunning = Boolean(data.is_running || stats.is_running);
  const displayQueue = data.display_queue || [];
  const displayedQueueItem = displayQueueIndex >= 0 && displayQueueIndex < displayQueue.length
    ? displayQueue[displayQueueIndex]
    : null;
  const displayedQueueText = displayedQueueItem ? getQueueDisplayText(displayedQueueItem) : "";
  const currentQueueTextDone = Boolean(displayedQueueText && displayedBuffer.length >= displayedQueueText.length);
  const effectivePresentedQueueCutoff = Math.max(
    presentedQueueCutoff,
    displayQueueIndex > 0 ? displayQueueIndex - 1 : -1,
    currentQueueTextDone ? displayQueueIndex : -1
  );
  const queueCutoff = Math.min(
    displayQueue.length - 1,
    effectivePresentedQueueCutoff
  );
  const activeQueueKey = displayQueueIndex >= 0
    ? getQueueKey(displayQueue[displayQueueIndex])
    : null;
  const queuedPanelItems = queueCutoff >= 0
    ? displayQueue
        .slice(0, queueCutoff + 1)
        .reverse()
        .map((item) => {
          const recent = (data.recent_results || []).find(r => r.file_name === item.file_name) || {};
          const itemResult = item.result || {};
          const itemKey = getQueueKey(item);
          return {
            ...recent,
            ...itemResult,
            file_name: item.file_name || recent.file_name,
            source_path: item.source_path || recent.source_path,
            thumb_b64: item.thumb_b64 || recent.thumb_b64,
            stream_buffer: item.stream_buffer || recent.stream_buffer,
            price_status: itemResult.price_status || recent.price_status,
            price_symbol: itemResult.price_symbol || recent.price_symbol,
            official_price: itemResult.official_price || recent.official_price,
            price_diff_percent: itemResult.price_diff_percent || recent.price_diff_percent,
            _queueKey: itemKey,
            _isCurrent: itemKey === activeQueueKey,
          };
        })
    : [];
  const rightPanelItems = displayQueue.length > 0
    ? queuedPanelItems
    : (data.recent_results || []).map((res, i) => ({ ...res, _queueKey: null, _isCurrent: i === 0 }));
  const displayedFileName = displayedQueueItem?.file_name || data.stream_file || data.current_file || "-";
  const reviewReasonCounts = reviewQueue.summary?.reason_counts || {};
  const reviewYearCounts = reviewQueue.summary?.year_counts || {};
  console.log("App: Ready to render", { stats, dataExists: !!data });

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100vh',
      width: '100vw',
      background: '#080808',
      color: '#e0e0e0',
      fontFamily: 'system-ui, -apple-system, sans-serif'
    }}>
      {/* 1. Header */}
      <header style={{ height: '50px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 16px', borderBottom: '1px solid #333', background: '#111' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Activity color="#00f5ff" size={24} />
              <h1 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 'bold', color: '#ffffff', background: 'none', WebkitBackgroundClip: 'initial', WebkitTextFillColor: 'initial' }}>
                  三星電腦螢幕-通路陳列-照片分析
              </h1>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
               <span style={{ fontSize: '0.75rem', color: '#ffffff', fontWeight: 'bold', border: '1px solid #333', padding: '2px 6px', borderRadius: '4px', background: '#222' }}>
                 {UI_VERSION}
               </span>
               <div style={{ height: '3px', width: '200px', background: '#222', borderRadius: '10px', overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${(stats.processed / (stats.total || 1)) * 100}%`, background: '#22c55e', transition: 'width 0.3s ease' }} />
               </div>
               <span style={{ fontSize: '0.7rem', color: '#888' }}>{stats.processed}/{stats.total || 0}</span>
               <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                  <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: isRunning ? '#22c55e' : '#ff4b2b', boxShadow: isRunning ? '0 0 10px #22c55e' : 'none' }}></div>
                  <span style={{ fontSize: '0.7rem', color: '#888' }}>{isRunning ? '正在執行' : '待機中'}</span>
               </div>
          </div>
      </header>

      {error && (
          <div style={{ background: '#b71c1c', color: '#fff', fontSize: '0.7rem', textAlign: 'center', padding: '2px' }}>
              ⚠️ 與後端伺服器失去連線... ({error})
          </div>
      )}

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', gap: '8px' }}>
            <div style={{ padding: '8px 16px', background: '#111', borderRadius: '6px', border: '1px solid #333', display: 'flex', gap: '8px', alignItems: 'center', flexShrink: 0 }}>
               <span style={{fontSize:'0.75rem', color:'#aaa'}}>📁 來源根目錄:</span>
                {isRunning && data.image_dir ? (
                 <>
                   <span style={{ color: '#00f5ff', fontSize: '0.75rem', fontFamily: 'JetBrains Mono', maxWidth: '260px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={data.source_root || 'D:\\00_商化\\00_未整理商化照片'}>
                     {data.source_root || 'D:\\00_商化\\00_未整理商化照片'}
                   </span>
                   <span style={{fontSize:'0.75rem', color:'#aaa'}}>目前資料夾:</span>
                   <span style={{ color: '#fbbf24', fontSize: '0.75rem', fontFamily: 'JetBrains Mono', maxWidth: '260px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={data.image_dir}>
                     {data.current_relative_dir || data.image_dir}
                   </span>
                 </>
               ) : (
               <select
                   value={targetDir}
                   onChange={(e)=>setTargetDir(e.target.value)}
                   style={{
                       background: '#111', border: '1px solid #444', color: '#00f5ff',
                       padding: '3px 6px', fontSize: '0.75rem', borderRadius: '3px', width: '180px'
                   }}
               >
                   {availableDirs.map(d => <option key={d} value={d}>{d}</option>)}
                   {targetDir && !availableDirs.includes(targetDir) && <option value={targetDir}>{targetDir}</option>}
               </select>
               )}

                <button onClick={() => handleStart(false)} disabled={isRunning}
                    style={{ background: isRunning ? '#333' : '#22c55e', color: '#fff', border:'1px solid #333', padding:'4px 10px', borderRadius:'4px', cursor: isRunning?'not-allowed':'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px' }}>
                    <Play size={12} /> 續跑
                </button>
               <button onClick={handleStop}
                   style={{ background: '#ef4444', color: '#fff', border:'1px solid #333', padding:'4px 10px', borderRadius:'4px', cursor: 'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px' }}>
                   <Square size={12} /> 停止
               </button>
               <button onClick={() => window.open(`/failed_records.html?v=${Date.now()}`, '_blank')}
                   style={{ background: '#6366f1', color: '#fff', border:'1px solid #333', padding:'4px 10px', borderRadius:'4px', cursor: 'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px' }}>
                   <AlertCircle size={12} /> 失敗記錄 ({stats.failed})
               </button>
               <button onClick={() => window.open(`/success_records.html?v=${Date.now()}`, '_blank')}
                   style={{ background: '#10b981', color: '#fff', border:'1px solid #333', padding:'4px 10px', borderRadius:'4px', cursor: 'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px' }}>
                   <CheckCircle2 size={12} /> 成功記錄 ({stats.success})
               </button>
               <button onClick={openReviewPanel}
                   style={{ background: '#f59e0b', color: '#111', border:'1px solid #333', padding:'4px 10px', borderRadius:'4px', cursor: 'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px' }}>
                   <AlertCircle size={12} /> 待人工校正
               </button>
               {controlMsg && <span style={{fontSize:'0.7rem', marginLeft:'5px'}}>{controlMsg}</span>}
           </div>

           <div style={{ flex: 1, display: 'flex', gap: '8px', overflow: 'hidden' }}>
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: '#111', borderRadius: '6px', border: '1px solid #333', overflow: 'hidden' }}>
                  <div style={{ flex: '0 0 50%', position: 'relative', borderBottom: '1px solid #333', display: 'flex', flexDirection: 'column' }}>
                      <div style={{ padding: '4px 8px', background: '#111', display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', fontWeight: 'bold', borderBottom: '1px solid #333' }}>
                          <span style={{display:'flex', alignItems:'center', gap:'4px', color: '#888'}}>
                            <ImageIcon size={12}/>
                            辨識預覽
                          </span>
                          <span style={{color: '#00f5ff', fontFamily: 'JetBrains Mono'}}>
                            {displayedFileName}
                          </span>
                      </div>
                      <div style={{ flex: 1, position: 'relative', overflow: 'hidden', display: 'flex', justifyContent: 'center', alignItems: 'center', background: '#000' }}>
                          {(currentImage || currentThumb) ? (
                              <>
                                  {!imageLoaded && !imageFailed && (
                                      <div style={{ color: '#666', display:'flex', flexDirection:'column', alignItems:'center', gap:'6px' }}>
                                          <ImageIcon size={28} />
                                          <span style={{fontSize:'0.75rem'}}>照片載入中</span>
                                      </div>
                                  )}
                                  {currentImage && !imageFailed && <img src={currentImage} onLoad={() => setImageLoaded(true)} onError={() => { setImageLoaded(false); setImageFailed(true); }} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain', zIndex: 20 }} alt="P" />}
                                  {imageFailed && (
                                      <div style={{ color: '#666', display:'flex', flexDirection:'column', alignItems:'center', gap:'6px' }}>
                                          <ImageIcon size={28} />
                                          <span style={{fontSize:'0.75rem'}}>照片切換中</span>
                                      </div>
                                  )}
                              </>
                          ) : (
                              <div style={{ color: '#333', display:'flex', flexDirection:'column', alignItems:'center' }}><Box size={24} /><span style={{fontSize:'0.7rem'}}>無訊號</span></div>
                          )}
                          {isRunning && (
                              <div style={{ position: 'absolute', bottom: 0, left: 0, height: '4px', width: '100%', zIndex: 30, background: '#111', overflow: 'hidden' }}>
                                  <div style={{ width: '30%', height: '100%', background: 'linear-gradient(90deg, transparent, #ff0000, transparent)', animation: 'scan 1.5s ease-in-out infinite alternate', boxShadow: '0 0 10px #ff0000', borderRadius: '50%' }}></div>
                              </div>
                          )}
                      </div>
                  </div>

                  <div className="log-wall" style={{ flex: 1, padding: '12px', overflow: 'hidden', display: 'flex', flexDirection: 'column', gap: '8px', background: '#0a0a0f', fontFamily: 'JetBrains Mono, monospace', fontSize: '0.85rem', position: 'relative' }}>
                      {/* 1. Top Pane: Active Stream Only */}
                       <div ref={streamBufferRef} style={{ flex: '0 0 150px', borderBottom: '1px solid #333', overflowY: 'auto', paddingBottom: '8px', marginBottom: '8px' }}>
                            {displayedBuffer ? (
                               <div style={{ wordBreak: 'break-all', whiteSpace: 'pre-wrap', color: '#ffffff', fontSize: '1.05rem', fontFamily: 'JetBrains Mono', lineHeight: '1.6', fontWeight: 'bold' }}>
                                   {displayedBuffer}
                                   <span className="typing-dots"></span>
                               </div>
                            ) : (
                                <div style={{ color: '#444', fontSize: '0.8rem', fontStyle: 'italic' }}>...</div>
                            )}
                       </div>

                      {/* 2. Bottom Pane: System Logs / History */}
                      <div style={{ flex: 1, overflowY: 'auto' }}>
                          {data.lm_logs?.length > 0 ? (
                               [...data.lm_logs]
                                .filter(line => !line.includes('JSON Error') && !line.includes('初始化 Local LLM') && !line.includes('正在分析圖片') && !line.includes('已略過') && !line.includes('現在硬碟上的成功數應已減少') && !line.includes('個紀錄檔中移除'))
                               .reverse()
                               .map((line, i) => {
                                  const isThink = line.includes('[THINK]');
                                  let content = line;
                                  let icon = null;

                                  const trimmed = line.trimStart();
                                  const iconMatch = trimmed.match(/^([^\s\w]{1,2})\s/);
                                  if (iconMatch) {
                                      icon = iconMatch[1];
                                      content = trimmed.substring(iconMatch[0].length);
                                  }

                                  let style = {
                                      display: 'flex',
                                      alignItems: 'flex-start',
                                      wordBreak: 'break-all',
                                      whiteSpace: 'pre-wrap',
                                      marginBottom: '4px',
                                      fontSize: '0.8rem',
                                      color: '#aaaaaa',
                                      paddingLeft: '4px',
                                      borderLeft: '2px solid transparent'
                                  };

                                  if (isThink) {
                                      content = line.replace('[THINK]', '').trim();
                                      icon = null;
                                      style.paddingLeft = '28px';
                                      style.color = '#ffffff';
                                      style.fontWeight = 'bold';
                                  } else if (line.includes('判斷是單機') || line.includes('判斷是遠景')) {
                                      style.color = '#ffd700';
                                      style.fontWeight = 'bold';
                                  } else if (line.includes('▶️') || line.includes('✅')) {
                                      style.color = '#33ff33';
                                  } else if (line.includes('❌')) {
                                      style.borderLeft = '2px solid #ef4444';
                                  }

                                  return (
                                    <div key={i} style={style}>
                                        {icon && <span style={{ width: '24px', flexShrink: 0, display: 'inline-block' }}>{icon}</span>}
                                        <span style={{ flex: 1 }}>{content}</span>
                                    </div>
                                  );
                              })
                          ) : (
                               <div style={{textAlign:'center', marginTop:'10%', color:'#222', fontSize:'0.7rem'}}>無歷史紀錄</div>
                          )}
                          <div ref={logsEndRef} />
                      </div>
                  </div>
              </div>

              <div style={{ width: '280px', display: 'flex', flexDirection: 'column', background: '#111', borderRadius: '6px', border: '1px solid #333', overflow: 'hidden' }}>
                  <div style={{ padding: '8px', borderBottom: '1px solid #333', display:'grid', gridTemplateColumns:'1fr 1fr', gap:'4px' }}>
                      {[
                        {l:'成功', v:stats.success, c:'#22c55e'}, {l:'失敗', v:stats.failed, c:'#ef4444'},
                        {l:'處理器', v:`${data.resources?.cpu??0}%`, c:'#00f5ff'}, {l:'記憶體', v:`${data.resources?.ram??0}%`, c:'#a855f7'},
                        {l:'最後耗時', v:data.metrics?.last_duration||'-', c:'#00f5ff'}, {l:'平均耗時', v:data.metrics?.avg_duration||'-', c:'#a855f7'}
                      ].map((item, i)=>(
                        <div key={i} style={{ background:'#0a0a0f', border:'1px solid #333', padding:'8px', borderRadius:'4px', textAlign:'center' }}>
                            <div style={{ color:'#888', fontSize:'0.6rem' }}>{item.l}</div>
                            <div style={{ color:item.c, fontSize:'1.1rem', fontWeight:'bold' }}>{item.v}</div>
                        </div>
                      ))}
                  </div>

                  <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>

                      {data.unknown_models && data.unknown_models.length > 0 && (
                        <div style={{ padding: '8px', borderBottom: '1px solid #333', background: '#1c1917' }}>
                            <div style={{ fontSize: '0.75rem', fontWeight: 'bold', color: '#f59e0b', marginBottom: '4px', display:'flex', alignItems:'center', gap:'4px' }}>
                                <AlertCircle size={12} /> 未建檔型號 ({data.unknown_models.length})
                            </div>
                            <div style={{ height: '50px', overflowY: 'auto', background: '#000', border: '1px solid #444', borderRadius: '4px', padding: '4px', fontSize: '0.7rem', color: '#fff', fontFamily: 'JetBrains Mono' }}>
                                {data.unknown_models.map((m, i) => (
                                    <div key={i} style={{ marginBottom: '2px', whiteSpace: 'nowrap' }}>{m.replace(' (未建檔)', '')}</div>
                                ))}
                            </div>
                        </div>
                      )}

                      <div style={{ padding: '8px', borderBottom: '1px solid #333', fontSize: '0.8rem', fontWeight: 'bold', display:'flex', alignItems:'center', gap:'4px', color: '#888' }}>
                          <Zap size={12} color="#f59e0b"/> 辨識紀錄
                      </div>
                      <div style={{ flex: 1, overflowY: 'auto', padding: '8px' }}>
                          {/* [v19.10] Right panel follows the presentation queue, not the faster backend. */}
                          {rightPanelItems.map((res, i) => (
                             <div key={`${res.file_name}-${i}`} style={{ background: res._isCurrent ? '#1e293b' : '#161616', border: res._isCurrent ? '1px solid #00f5ff' : '1px solid #222', borderRadius: '4px', padding: '6px', marginBottom:'6px', transition: 'background 0.2s' }} onMouseEnter={(e)=>e.currentTarget.style.background='#222'} onMouseLeave={(e)=>e.currentTarget.style.background=res._isCurrent ? '#1e293b' : '#161616'}>
                                 <div style={{ display: 'flex', gap: '6px' }}>
                                     <ResultThumbnail res={res} onClick={() => { setInspectImage(res); }} />
                                     <div style={{ flex: 1, minWidth: 0 }}>
                                         <div style={{ color: '#fff', fontSize: '0.7rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{res.file_name}</div>
                                         {res.view_type !== '遠景' && (
                                            <div style={{ display: 'grid', gridTemplateColumns: '80px 1fr', alignItems: 'center', marginTop: '2px', width: '100%', columnGap: '8px' }}>
                                                <div style={{ fontSize: '0.7rem', color: res.category?.startsWith('不合格') ? '#ef4444' : '#22c55e', fontWeight: 'bold', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                    {res.model || (res.category?.startsWith('不合格') ? res.category.replace('不合格-', '') : '(無型號)')}
                                                </div>
                                                <div style={{ fontSize: '0.7rem', color: '#f59e0b', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: '4px' }}>
                                                    {formatDisplayPrice(res.price)}
                                                    {res.price_symbol && res.price_status && res.price_status !== 'not_compared' && (
                                                        <span
                                                            title={res.official_price ? `官方價: $${res.official_price.toLocaleString()} (${res.price_diff_percent > 0 ? '+' : ''}${res.price_diff_percent}%)` : '官網/PChome 查無價格'}
                                                            style={{
                                                                fontSize: '0.65rem',
                                                                fontWeight: '900',
                                                                color: res.price_status === 'match' ? '#22c55e' :
                                                                       res.price_status === 'high' ? '#ef4444' :
                                                                       res.price_status === 'low' ? '#3b82f6' : '#ff0000'
                                                            }}
                                                        >
                                                            {res.price_symbol === '-' ? '？' : res.price_symbol}
                                                        </span>
                                                    )}
                                                </div>
                                            </div>
                                         )}
                                          <div style={{ display: 'flex', gap: '4px', marginTop: '2px', flexWrap: 'wrap', alignItems: 'center' }}>
                                             <button
                                                 title="重新辨識"
                                                 onClick={(e) => {
                                                     e.stopPropagation();
                                                     setRerunQueue(prev => ({...prev, [res.file_name]: true}));
                                                     fetch('/api/rerun', {
                                                         method: 'POST',
                                                         headers: {'Content-Type': 'application/json'},
                                                         body: JSON.stringify({filename: res.file_name})
                                                     }).then(r=>r.json()).then(d=>{
                                                         console.log(d.message);
                                                         // Clear queue indicator after backend has had time to process
                                                         setTimeout(() => {
                                                             setRerunQueue(prev => {
                                                                 const next = {...prev};
                                                                 delete next[res.file_name];
                                                                 return next;
                                                             });
                                                         }, 8000);
                                                     }).catch(err=>{
                                                         console.error(err);
                                                         setRerunQueue(prev => {
                                                             const next = {...prev};
                                                             delete next[res.file_name];
                                                             return next;
                                                         });
                                                     });
                                                 }}
                                                 disabled={rerunQueue[res.file_name]}
                                                 style={{
                                                     background: rerunQueue[res.file_name] ? '#374151' : '#1f2937',
                                                     border: '1px solid #4b5563',
                                                     borderRadius: '3px', cursor: rerunQueue[res.file_name] ? 'not-allowed' : 'pointer',
                                                     padding: '0 4px',
                                                     color: rerunQueue[res.file_name] ? '#fbbf24' : '#e5e7eb',
                                                     fontSize: '0.6rem', display: 'flex', alignItems: 'center'
                                                 }}
                                             >
                                                 {rerunQueue[res.file_name] ? '已排隊' : '重跑'}
                                             </button>
                                            {res.view_type && <span style={{ fontSize: '0.6rem', padding: '1px 4px', borderRadius: '3px', background: res.view_type==='遠景'?'#3b82f6':'#22c55e', color: '#fff' }}>{res.view_type}</span>}
                                            {res.view_type !== '遠景' && res.screen_status && <span style={{ fontSize: '0.6rem', padding: '1px 4px', borderRadius: '3px', background: '#ec4899', color: '#fff' }}>{res.screen_status}</span>}
                                            {res.view_type !== '遠景' && res.quality_issue && res.quality_issue !== '無' && <span style={{ fontSize: '0.6rem', padding: '1px 4px', borderRadius: '3px', background: '#f97316', color: '#fff' }}>{res.quality_issue.replace('不合格-', '')}</span>}
                                             {/* [v18.67] 價格驗證符號 - 包含 ? 未知，但排除 not_compared */}
                                             {res.view_type !== '遠景' && res.price && res.price_symbol && res.price_status && res.price_status !== 'not_compared' && (
                                                <span
                                                    title={
                                                        res.price_status === 'unknown' ? '官網/PChome 查無價格；需人工確認或重跑' :
                                                        res.official_price ? `官方 $${res.official_price.toLocaleString()} (${res.price_diff_percent > 0 ? '+' : ''}${res.price_diff_percent}%)` : ''
                                                    }
                                                    style={{
                                                        fontSize: '0.7rem',
                                                        padding: '1px 4px',
                                                        borderRadius: '3px',
                                                        background: res.price_status === 'match' ? '#22c55e' :
                                                                   res.price_status === 'high' ? '#ef4444' :
                                                                   res.price_status === 'low' ? '#3b82f6' : '#dc2626',
                                                        color: '#fff',
                                                        fontWeight: '900',
                                                        cursor: 'help'
                                                    }}
                                                >
                                                    {res.price_symbol === '-' ? '？' : (res.price_symbol || '？')}
                                                </span>
                                             )}
                                         </div>
                                     </div>
                                 </div>
                             </div>
                          ))}
                      </div>
                  </div>
              </div>
           </div>
      </div>
         {showReviewPanel && (
             <div style={{
                 position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.72)',
                 zIndex: 9900, display: 'flex', justifyContent: 'flex-end'
             }}>
                 <div style={{
                     width: 'min(980px, 94vw)', height: '100%', background: '#0f0f0f',
                     borderLeft: '1px solid #333', boxShadow: '-20px 0 60px rgba(0,0,0,0.45)',
                     display: 'flex', flexDirection: 'column'
                 }}>
                     <div style={{ padding: '14px 16px', borderBottom: '1px solid #333', display: 'flex', alignItems: 'center', gap: '10px' }}>
                         <AlertCircle size={18} color="#f59e0b" />
                         <div style={{ flex: 1 }}>
                             <div style={{ color: '#fff', fontWeight: 'bold', fontSize: '1rem' }}>待人工校正</div>
                             <div style={{ color: '#888', fontSize: '0.72rem' }}>
                                 上傳前被擋住的照片；先校正或標記重跑，避免錯檔先進雲端。
                             </div>
                         </div>
                         <select
                             value={reviewYear}
                             onChange={(e) => {
                                 const next = e.target.value;
                                 setReviewYear(next);
                                 fetchReviewQueue(next, reviewReason);
                             }}
                             style={{ background: '#111', color: '#e5e7eb', border: '1px solid #444', borderRadius: '4px', padding: '5px 8px' }}
                         >
                             {['2026', '2025', '2024', '2023', '2022', '2021', '2020', '2019', '2018', '2017', '2016', '2015', ''].map(y => (
                                 <option key={y || 'all'} value={y}>{y || '全部年份'}</option>
                             ))}
                         </select>
                         <select
                             value={reviewReason}
                             onChange={(e) => {
                                 const next = e.target.value;
                                 setReviewReason(next);
                                 fetchReviewQueue(reviewYear, next);
                             }}
                             style={{ background: '#111', color: '#e5e7eb', border: '1px solid #444', borderRadius: '4px', padding: '5px 8px', maxWidth: '220px' }}
                         >
                             <option value="">全部原因</option>
                             {Object.entries(reviewReasonCounts).slice(0, 24).map(([reason, count]) => (
                                 <option key={reason} value={reason}>{reason} ({count})</option>
                             ))}
                         </select>
                         <button
                             onClick={() => fetchReviewQueue(reviewYear, reviewReason)}
                             style={{ background: '#1f2937', color: '#fff', border: '1px solid #4b5563', padding: '6px 10px', borderRadius: '4px', cursor: 'pointer' }}
                         >
                             刷新
                         </button>
                         <button
                             onClick={() => setShowReviewPanel(false)}
                             style={{ background: '#ef4444', color: '#fff', border: 'none', width: '32px', height: '32px', borderRadius: '50%', cursor: 'pointer', display:'flex', alignItems:'center', justifyContent:'center' }}
                         >
                             <XCircle size={18} />
                         </button>
                     </div>

                     <div style={{ padding: '10px 16px', display: 'flex', gap: '8px', flexWrap: 'wrap', borderBottom: '1px solid #222', background: '#121212' }}>
                         <span style={{ color: '#ddd', fontSize: '0.75rem' }}>顯示 {reviewQueue.returned || 0}/{reviewQueue.total || 0} 筆</span>
                         {Object.entries(reviewYearCounts).slice(0, 8).map(([year, count]) => (
                             <span key={year} style={{ color: year === reviewYear ? '#111' : '#aaa', background: year === reviewYear ? '#f59e0b' : '#1f2937', padding: '2px 6px', borderRadius: '3px', fontSize: '0.68rem' }}>
                                 {year}: {count}
                             </span>
                         ))}
                         {reviewMsg && <span style={{ color: reviewMsg.includes('失敗') ? '#ef4444' : '#22c55e', fontSize: '0.75rem', marginLeft: 'auto' }}>{reviewMsg}</span>}
                     </div>

                     <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}>
                         {reviewLoading ? (
                             <div style={{ color: '#888', textAlign: 'center', marginTop: '80px' }}>待審清單載入中...</div>
                         ) : reviewQueue.items?.length ? (
                             reviewQueue.items.map((item) => {
                                 const draft = getReviewDraft(item);
                                 const draftView = draft.view_type ?? item.view_type ?? '單機';
                                 const draftModel = draft.model ?? item.model ?? '';
                                 const draftPrice = draft.price ?? item.price ?? '';
                                 return (
                                     <div key={item.file_name} style={{ background: '#171717', border: '1px solid #2a2a2a', borderRadius: '6px', padding: '10px', marginBottom: '10px' }}>
                                         <div style={{ display: 'grid', gridTemplateColumns: '72px 1fr', gap: '10px' }}>
                                             <ResultThumbnail res={item} onClick={() => setInspectImage(item)} />
                                             <div style={{ minWidth: 0 }}>
                                                 <div style={{ color: '#fff', fontSize: '0.86rem', fontWeight: 'bold', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={item.file_name}>
                                                     {item.file_name}
                                                 </div>
                                                 <div style={{ color: '#f59e0b', fontSize: '0.72rem', marginTop: '3px' }}>
                                                     {item.reason_labels || item.reasons}
                                                 </div>
                                                 <div style={{ color: '#888', fontSize: '0.68rem', marginTop: '2px' }}>
                                                     {item.suggested_action}
                                                 </div>
                                             </div>
                                         </div>

                                         <div style={{ display: 'grid', gridTemplateColumns: '88px 1fr 116px 96px', gap: '8px', marginTop: '10px', alignItems: 'center' }}>
                                             <select
                                                 value={draftView}
                                                 onChange={(e) => updateReviewDraft(item, { view_type: e.target.value })}
                                                 style={{ background: '#0b0b0b', color: '#e5e7eb', border: '1px solid #444', borderRadius: '4px', padding: '6px' }}
                                             >
                                                 <option value="單機">單機</option>
                                                 <option value="遠景">遠景</option>
                                             </select>
                                             <input
                                                 value={draftModel}
                                                 onChange={(e) => updateReviewDraft(item, { model: e.target.value })}
                                                 placeholder="型號，例如 S55BG970NC"
                                                 style={{ background: '#0b0b0b', color: '#fff', border: '1px solid #444', borderRadius: '4px', padding: '7px' }}
                                             />
                                             <input
                                                 value={draftPrice}
                                                 onChange={(e) => updateReviewDraft(item, { price: e.target.value })}
                                                 placeholder="價格"
                                                 style={{ background: '#0b0b0b', color: '#f59e0b', border: '1px solid #444', borderRadius: '4px', padding: '7px' }}
                                             />
                                             <select
                                                 value={draft.price_symbol ?? ''}
                                                 onChange={(e) => updateReviewDraft(item, { price_symbol: e.target.value })}
                                                 title="2026+ 檔名需 ↑/↓/✓"
                                                 style={{ background: '#0b0b0b', color: '#e5e7eb', border: '1px solid #444', borderRadius: '4px', padding: '6px' }}
                                             >
                                                 <option value="">符號</option>
                                                 <option value="↑">↑ 高於官網</option>
                                                 <option value="↓">↓ 低於官網</option>
                                                 <option value="✓">✓ 持平</option>
                                                 <option value="？">？ 待查</option>
                                             </select>
                                         </div>

                                         <div style={{ display: 'grid', gridTemplateColumns: '1fr 92px 74px 88px 88px', gap: '8px', marginTop: '8px', alignItems: 'center' }}>
                                             <input
                                                 value={draft.note ?? ''}
                                                 onChange={(e) => updateReviewDraft(item, { note: e.target.value })}
                                                 placeholder="備註或辨識依據"
                                                 style={{ background: '#0b0b0b', color: '#d1d5db', border: '1px solid #444', borderRadius: '4px', padding: '7px' }}
                                             />
                                             <label style={{ color: '#aaa', fontSize: '0.7rem', display: 'flex', alignItems: 'center', gap: '4px' }}>
                                                 <input
                                                     type="checkbox"
                                                     checked={Boolean(draft.learn_rule)}
                                                     onChange={(e) => updateReviewDraft(item, { learn_rule: e.target.checked })}
                                                 />
                                                 學規則
                                             </label>
                                             <button
                                                 onClick={() => updateReviewDraft(item, {
                                                     view_type: '單機',
                                                     model: 'S55BG970NC',
                                                     learn_rule: true,
                                                     rule_hint: 'Odyssey Ark / Ark Mini LED / 55吋大型直立或曲面桌上機 -> S55BG970NC',
                                                     note: draft.note || 'Odyssey Ark 55吋大型直立或曲面桌上機'
                                                 })}
                                                 style={{ background: '#2563eb', color: '#fff', border: 'none', padding: '7px 8px', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold' }}
                                             >
                                                 ARK
                                             </button>
                                             <button
                                                 onClick={() => updateReviewDraft(item, { view_type: '遠景', model: '', price: '', price_symbol: '' })}
                                                 style={{ background: '#3b82f6', color: '#fff', border: 'none', padding: '7px 8px', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold' }}
                                             >
                                                 遠景
                                             </button>
                                             <button
                                                 onClick={() => saveReviewCorrection(item)}
                                                 style={{ background: '#22c55e', color: '#fff', border: 'none', padding: '7px 8px', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold' }}
                                             >
                                                 記錄
                                             </button>
                                         </div>
                                         <div style={{ marginTop: '8px', display: 'flex', justifyContent: 'flex-end' }}>
                                             <button
                                                 onClick={() => saveReviewCorrection(item, 'needs_rerun')}
                                                 style={{ background: '#374151', color: '#e5e7eb', border: '1px solid #4b5563', padding: '5px 8px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.72rem' }}
                                             >
                                                 標記重跑需求
                                             </button>
                                         </div>
                                     </div>
                                 );
                             })
                         ) : (
                             <div style={{ color: '#666', textAlign: 'center', marginTop: '80px' }}>
                                 目前沒有待校正資料
                             </div>
                         )}
                     </div>
                 </div>
             </div>
         )}

         {showConfirmModal && (
             <div style={{ position: 'fixed', top: 0, left: 0, width: '100%', height: '100%', background: 'rgba(0,0,0,0.85)', zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center', backdropFilter: 'blur(4px)' }}>
                 <div style={{ background: '#111', border: '2px solid #00f5ff', borderRadius: '12px', width: '400px', padding: '24px', boxShadow: '0 0 30px rgba(0, 245, 255, 0.2)', textAlign: 'center' }}>
                     <div style={{ fontSize: '1.4rem', color: '#00f5ff', fontWeight: 'bold', marginBottom: '16px' }}>{confirmModalConfig.title}</div>
                     <div style={{ fontSize: '1.1rem', color: '#fff', marginBottom: '24px', lineHeight: '1.6' }}>{confirmModalConfig.message}</div>
                     <div style={{ display: 'flex', gap: '12px', justifyContent: 'center' }}>
                         <button onClick={() => setShowConfirmModal(false)} style={{ background: '#333', color: '#ccc', border: '1px solid #444', padding: '10px 24px', borderRadius: '6px', cursor: 'pointer', fontSize: '1rem' }}>取消 (Skip)</button>
                         <button onClick={confirmModalConfig.onConfirm} style={{ background: '#00f5ff', color: '#000', border: 'none', padding: '10px 24px', borderRadius: '6px', cursor: 'pointer', fontSize: '1rem', fontWeight: 'bold' }}>確定 (Confirm)</button>
                     </div>
                 </div>
             </div>
         )}

         {/* Image Inspection Modal [v18.16 Metadata UI] */}
         {inspectImage && (
             <div
                style={{
                    position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh',
                    background: 'rgba(0,0,0,0.95)', zIndex: 10000,
                    display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                    cursor: isDragging ? 'grabbing' : 'default'
                }}
                onMouseMove={(e) => {
                    if (!isDragging) return;
                    setModalPosition({
                        x: e.clientX - dragStartRef.current.x,
                        y: e.clientY - dragStartRef.current.y
                    });
                }}
                onMouseUp={() => setIsDragging(false)}
             >
                  {/* Close Button */}
                  <button
                         onClick={() => { setInspectImage(null); setModalPosition({x:0,y:0}); }}
                         style={{
                             position: 'fixed', top: '20px', right: '40px',
                             background: '#ef4444', color: '#fff', border: '2px solid #fff',
                             borderRadius: '50%', width: '40px', height: '40px',
                             cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                             zIndex: 10005, boxShadow: '0 0 15px rgba(0,0,0,0.5)'
                         }}
                         title="關閉 (ESC)"
                      >
                         <XCircle size={24} />
                  </button>

                  {/* Image Container */}
                      <div
                         style={{
                             position: 'absolute',
                             transform: `translate(${modalPosition.x}px, ${modalPosition.y}px)`,
                             transition: isDragging ? 'none' : 'transform 0.1s ease-out',
                             cursor: isDragging ? 'grabbing' : 'grab'
                         }}
                         onMouseDown={(e) => {
                            setIsDragging(true);
                            dragStartRef.current = { x: e.clientX - modalPosition.x, y: e.clientY - modalPosition.y };
                         }}
                      >
                          <img
                              src={getResultImageSrc(inspectImage)}
                              style={{
                                  display: 'block',
                                  border: '4px solid #444',
                                  borderRadius: '4px',
                                  boxShadow: '0 0 100px rgba(0,0,0,0.8)',
                                  userSelect: 'none',
                                  pointerEvents: 'auto'
                              }}
                              alt="Inspection"
                              draggable={false}
                          />
                      </div>

                   {/* Metadata Footer [v18.30 Fix: Stays at Bottom] */}
                   <div style={{
                       position: 'absolute', bottom: 0, left: 0,
                       width: '100%', background: '#111', borderTop: '2px solid #333',
                       padding: '12px 24px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '20px',
                       color: '#ddd', fontFamily: 'JetBrains Mono', fontSize: '0.9rem', zIndex: 10005,
                       boxShadow: '0 -5px 25px rgba(0,0,0,0.8)'
                   }}>
                      <div style={{ display:'flex', flexDirection:'column', alignItems:'center' }}>
                           <span style={{ fontSize:'0.7rem', color:'#888' }}>檔名</span>
                           <span style={{ color:'#00f5ff', fontWeight:'bold' }}>{inspectImage.file_name}</span>
                      </div>
                      <div style={{ width:'1px', height:'30px', background:'#333' }}></div>

                      <div style={{ display:'flex', flexDirection:'column', alignItems:'center' }}>
                           <span style={{ fontSize:'0.7rem', color:'#888' }}>視角</span>
                           <span style={{ color: inspectImage.view_type==='遠景'?'#3b82f6':'#22c55e', fontWeight:'bold' }}>{inspectImage.view_type||'-'}</span>
                      </div>
                      <div style={{ width:'1px', height:'30px', background:'#333' }}></div>

                      <div style={{ display:'flex', flexDirection:'column', alignItems:'center' }}>
                           <span style={{ fontSize:'0.7rem', color:'#888' }}>型號</span>
                           <span style={{ color:'#ffffff', fontWeight:'bold', fontSize:'1.1rem' }}>{inspectImage.model || '(無)'}</span>
                      </div>
                      <div style={{ width:'1px', height:'30px', background:'#333' }}></div>

                      <div style={{ display:'flex', flexDirection:'column', alignItems:'center' }}>
                           <span style={{ fontSize:'0.7rem', color:'#888' }}>價格</span>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                <span style={{ color:'#f59e0b', fontWeight:'bold', fontSize:'1.1rem' }}>{formatDisplayPrice(inspectImage.price)}</span>
                                {inspectImage.price_symbol && inspectImage.price_status && inspectImage.price_status !== 'not_compared' && (
                                    <span
                                        title={inspectImage.official_price ? `官方價: $${inspectImage.official_price.toLocaleString()} (${inspectImage.price_diff_percent > 0 ? '+' : ''}${inspectImage.price_diff_percent}%)` : '官網/PChome 查無價格'}
                                        style={{
                                            fontSize: '1rem',
                                            fontWeight: '900',
                                            padding: '2px 6px',
                                            borderRadius: '4px',
                                            background: inspectImage.price_status === 'match' ? '#22c55e' :
                                                       inspectImage.price_status === 'high' ? '#ef4444' :
                                                       inspectImage.price_status === 'low' ? '#3b82f6' : '#dc2626',
                                            color: '#fff',
                                            cursor: 'help'
                                        }}
                                    >
                                        {inspectImage.price_symbol === '-' ? '?' : inspectImage.price_symbol}
                                    </span>
                                )}
                            </div>
                      </div>
                      <div style={{ width:'1px', height:'30px', background:'#333' }}></div>

                      <div style={{ display:'flex', flexDirection:'column', alignItems:'center' }}>
                            <span style={{ fontSize:'0.7rem', color:'#888' }}>狀態</span>
                            <span style={{ color: inspectImage.screen_status ? '#ec4899' : '#555', fontWeight:'bold' }}>{inspectImage.screen_status || '正常'}</span>
                      </div>
                      <div style={{ width:'1px', height:'30px', background:'#333' }}></div>

                      <div style={{ display:'flex', flexDirection:'column', alignItems:'center' }}>
                            <span style={{ fontSize:'0.7rem', color:'#888' }}>品質異常</span>
                            <span style={{ color: inspectImage.category?.startsWith('不合格') ? '#ef4444' : '#555', fontWeight:'bold' }}>
                                {inspectImage.category?.startsWith('不合格') ? inspectImage.category.replace('不合格-', '') : '無'}
                            </span>
                      </div>
                      <div style={{ width:'1px', height:'30px', background:'#333' }}></div>

                      <div style={{ display:'flex', flexDirection:'column', alignItems:'center', maxWidth: '150px' }}>
                            <span style={{ fontSize:'0.7rem', color:'#888' }}>稽查備註</span>
                            <span style={{ color: '#fff', fontSize: '0.75rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', width: '100%', textAlign: 'center' }} title={inspectImage.note}>
                                {inspectImage.note || '-'}
                            </span>
                      </div>
                  </div>
             </div>
         )}

      <style>{`
        @keyframes scan { from { transform: translateX(-100%); } to { transform: translateX(300%); } }
        @keyframes dot-cycle {
           0% { content: "."; }
           33% { content: ".."; }
           66% { content: "..."; }
           100% { content: ""; }
        }
        .typing-dots::after {
           content: "";
           animation: dot-cycle 1.5s infinite steps(1);
           color: #ffffff;
           margin-left: 2px;
         }
       `}</style>
     </div>
   );
 };

 export default App;

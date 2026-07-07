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

const formatCount = (val) => {
  const num = Number(val || 0);
  return Number.isFinite(num) ? num.toLocaleString() : '0';
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

const UI_VERSION = "v19.29 (AI判讀視覺微調)";
console.log(`[Dashboard-Init] Version: ${UI_VERSION} | Timestamp: ${new Date().toLocaleTimeString()}`);

const isReadableLmLogLine = (line) => {
  const text = String(line || '').trim();
  if (!text) return false;
  const hiddenNoiseTokens = [
    [76, 76, 77].map((code) => String.fromCharCode(code)).join(''),
    [33258, 35328, 33258, 35486].map((code) => String.fromCharCode(code)).join('')
  ];
  const noise = [
    'JSON Error',
    '初始化 Local ',
    ...hiddenNoiseTokens,
    '正在分析圖片',
    '已略過',
    '現在硬碟上的成功數應已減少',
    '個紀錄檔中移除',
    '[Priority]',
    '優先插隊',
    '圖片損壞',
    '永久跳過',
    '無法識別圖片格式',
    '文件損壞',
    '載入圖片',
    '收到停止指令',
    '批次處理已被用戶中斷',
    '━━━━━━━━'
  ];
  return !noise.some((part) => text.includes(part));
};

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
  const [visibleImage, setVisibleImage] = useState(null);
  const [imagePreparing, setImagePreparing] = useState(false);
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
  const stats = data.stats || defaultState.stats;
  const isRunning = Boolean(data.is_running || stats.is_running);

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

  // [v19.15 UX] Frontend-owned presentation queue. The backend may run ahead,
  // but the viewer only sees: photo -> typed AI narration -> right-side result.
  const MAX_PENDING_PRESENTATIONS = 400;
  const MAX_REVEALED_RESULTS = 180;
  const MAX_LIVE_BACKLOG = 14;
  const MAX_DISPLAY_NARRATION_CHARS = 360;
  const LIVE_TYPEWRITER_INTERVAL_MS = 32;
  const QUEUE_TYPEWRITER_INTERVAL_MS = 30;
  const FAST_REVEAL_HOLD_MS = 920;
  const NORMAL_REVEAL_HOLD_MS = 1450;
  const [pendingQueue, setPendingQueue] = useState([]);
  const [activePresentation, setActivePresentation] = useState(null);
  const [revealedResults, setRevealedResults] = useState([]);
  const [displayedBuffer, setDisplayedBuffer] = useState("");
  const [narrationDisplay, setNarrationDisplay] = useState({
    text: "",
    key: "",
    phase: "idle",
    fileName: "",
    nextFileName: ""
  });
  const isAdvancingRef = useRef(false);
  const acceptedPresentationKeysRef = useRef(new Set());
  const revealedKeysRef = useRef(new Set());
  const activePresentationRef = useRef(null);
  const narrationDisplayRef = useRef(narrationDisplay);
  const latestDisplayQueueKeysRef = useRef(new Set());
  const displayWatchdogRef = useRef({ key: "", length: 0, updatedAt: Date.now() });
  const [displayTargetKey, setDisplayTargetKey] = useState("");
  const [typewriterReady, setTypewriterReady] = useState(false);

  const getQueueKey = (item) => {
    if (!item) return "";
    const result = item.result || {};
    const completedFile = item.file_name || result.file_name || "";
    if (item.presentation_id || result.presentation_id) return item.presentation_id || result.presentation_id;
    if (item.completed_at && completedFile) return `${item.completed_at}|${completedFile}`;
    if (item.source_path || result.source_path) return item.source_path || result.source_path;
    if (completedFile) return completedFile;
    return "";
  };

  const normalizePresentationItem = (item) => {
    if (!item) return null;
    const result = item.result || {};
    const key = getQueueKey(item);
    if (!key) return null;
    return {
      ...item,
      ...result,
      file_name: item.file_name || result.file_name,
      source_path: item.source_path || result.source_path,
      thumb_b64: item.thumb_b64 || result.thumb_b64,
      stream_buffer: item.stream_buffer || result.stream_buffer || result.thinking || "",
      _queueKey: key,
      _isCurrent: false
    };
  };

  const trimDisplayNarration = (text) => {
    const value = String(text || "").trim();
    if (value.length <= MAX_DISPLAY_NARRATION_CHARS) return value;
    return `${value.slice(0, MAX_DISPLAY_NARRATION_CHARS).trim()}...`;
  };

  const getQueueDisplayText = (item) => {
    if (!item) return "";
    if (item.stream_buffer && item.stream_buffer.trim()) return trimDisplayNarration(item.stream_buffer);
    const result = item.result || item;
    return trimDisplayNarration(`這張已完成辨識：${result.view_type || '單機'}，${result.model || '無型號'}，${result.price || '無價格'}。`);
  };

  const getNarrationFileName = () => (
    activePresentation?.file_name || data.stream_file || data.current_file || ""
  );

  const prepareNarrationHandoff = (nextKey = "", nextFileName = "") => {
    setNarrationDisplay((prev) => {
      const fileName = nextFileName || getNarrationFileName();
      if (prev.text) {
        return {
          ...prev,
          phase: "handoff",
          nextFileName: fileName
        };
      }
      return {
        text: "照片已切換，等待 AI 開始判讀下一張...",
        key: nextKey,
        phase: "warming",
        fileName,
        nextFileName: fileName
      };
    });
  };

  useEffect(() => {
    activePresentationRef.current = activePresentation;
  }, [activePresentation]);

  useEffect(() => {
    narrationDisplayRef.current = narrationDisplay;
  }, [narrationDisplay]);

  useEffect(() => {
    if (!displayedBuffer) return;
    const phase = revealedKeysRef.current.has(displayTargetKey) ? "revealed" : "typing";
    setNarrationDisplay({
      text: displayedBuffer,
      key: displayTargetKey,
      phase,
      fileName: activePresentation?.file_name || data.stream_file || data.current_file || ""
    });
  }, [displayedBuffer, displayTargetKey, activePresentation?.file_name, data.stream_file, data.current_file]);

  // Copy completed backend items into a local queue before the backend list rolls.
  useEffect(() => {
    if (!isRunning) {
      latestDisplayQueueKeysRef.current = new Set();
      return;
    }
    const incomingQueue = Array.isArray(data.display_queue) ? data.display_queue : [];
    latestDisplayQueueKeysRef.current = new Set(incomingQueue.map((raw) => getQueueKey(raw)).filter(Boolean));
    if (incomingQueue.length === 0) return;

    const incoming = [];
    const activeKey = activePresentationRef.current?._queueKey;
    incomingQueue.forEach((raw) => {
      const item = normalizePresentationItem(raw);
      if (!item) return;
      if (item._queueKey === activeKey) return;
      if (acceptedPresentationKeysRef.current.has(item._queueKey)) return;
      if (revealedKeysRef.current.has(item._queueKey)) return;
      acceptedPresentationKeysRef.current.add(item._queueKey);
      incoming.push(item);
    });

    if (incoming.length === 0) return;
    const incomingKeys = latestDisplayQueueKeysRef.current;
    setPendingQueue((prev) => {
      const existing = new Set(prev.map((item) => item._queueKey));
      const next = [...prev];
      incoming.forEach((item) => {
        if (!existing.has(item._queueKey)) {
          next.push(item);
          existing.add(item._queueKey);
        }
      });
      if (incomingQueue.length >= 45 && next.length > MAX_LIVE_BACKLOG) {
        return next
          .filter((item) => incomingKeys.has(item._queueKey))
          .slice(-MAX_LIVE_BACKLOG);
      }
      return next.length > MAX_PENDING_PRESENTATIONS
        ? next.slice(next.length - MAX_PENDING_PRESENTATIONS)
        : next;
    });
  }, [data.display_queue, isRunning]);

  // If the backend has already rolled past the visible item, fast-forward the
  // presentation layer instead of letting the boss-facing preview look frozen.
  useEffect(() => {
    if (!isRunning) return;
    const incomingQueue = Array.isArray(data.display_queue) ? data.display_queue : [];
    if (!activePresentation || incomingQueue.length < 45) return;
    const incomingKeys = new Set(incomingQueue.map((raw) => getQueueKey(raw)).filter(Boolean));
    if (incomingKeys.has(activePresentation._queueKey)) return;

    prepareNarrationHandoff("", data.current_file || "");
    setActivePresentation(null);
    setDisplayedBuffer("");
    setDisplayTargetKey("");
    setTypewriterReady(false);
    setPendingQueue((prev) => prev
      .filter((item) => incomingKeys.has(item._queueKey))
      .slice(-MAX_LIVE_BACKLOG));
  }, [data.display_queue, activePresentation?._queueKey, isRunning]);

  useEffect(() => {
    displayWatchdogRef.current = {
      key: displayTargetKey,
      length: displayedBuffer.length,
      updatedAt: Date.now()
    };
  }, [displayTargetKey, displayedBuffer.length]);

  useEffect(() => {
    const watchdog = setInterval(() => {
      const active = activePresentationRef.current;
      if (!active) return;
      const stalledMs = Date.now() - displayWatchdogRef.current.updatedAt;
      if (stalledMs < 8000) return;
      const latestKeys = latestDisplayQueueKeysRef.current;
      prepareNarrationHandoff("", data.current_file || "");
      setActivePresentation(null);
      setDisplayedBuffer("");
      setDisplayTargetKey("");
      setTypewriterReady(false);
      setPendingQueue((prev) => {
        const trimmed = latestKeys.size
          ? prev.filter((item) => latestKeys.has(item._queueKey))
          : prev;
        return trimmed.slice(-MAX_LIVE_BACKLOG);
      });
    }, 2000);
    return () => clearInterval(watchdog);
  }, []);

  // Start the next completed item only after the previous one has been revealed.
  useEffect(() => {
    if (activePresentation || pendingQueue.length === 0) return;
    const next = pendingQueue[0];
    setPendingQueue((prev) => prev.slice(1));
    setActivePresentation(next);
  }, [activePresentation, pendingQueue]);

  // Never show stale live state after a batch stops. The backend intentionally
  // keeps the last current_file/current folder for audit visibility, but the
  // boss-facing presentation must not mix that stale photo with old narration
  // or right-side results.
  useEffect(() => {
    if (isRunning) return;
    setPendingQueue([]);
    setActivePresentation(null);
    setDisplayedBuffer("");
    setDisplayTargetKey("");
    setTypewriterReady(false);
    setImagePreparing(false);
    setImageFailed(false);
    setNarrationDisplay({
      text: "",
      key: "",
      phase: "idle",
      fileName: "",
      nextFileName: ""
    });
  }, [isRunning]);

  const getDisplayTarget = () => {
    if (activePresentation) {
      return {
        target: getQueueDisplayText(activePresentation),
        isQueue: true,
        key: activePresentation._queueKey
      };
    }
    return {
      target: isRunning ? (data.stream_buffer || "") : "",
      isQueue: false,
      key: isRunning ? `live|${data.stream_file || data.current_file || ""}` : ""
    };
  };

  const imageReadyForDisplay = !currentImage || imageLoaded || imageFailed;

  // Stage the illusion deliberately: photo first, then AI narration, then result.
  useEffect(() => {
    const { key } = getDisplayTarget();
    if (!key || key === displayTargetKey) return;
    prepareNarrationHandoff(key, getNarrationFileName());
    setDisplayTargetKey(key);
    setDisplayedBuffer("");
    setTypewriterReady(false);
  }, [activePresentation?._queueKey, data.current_file, data.stream_file]);

  useEffect(() => {
    if (!displayTargetKey || !imageReadyForDisplay) return;
    const { isQueue } = getDisplayTarget();
    const leadIn = setTimeout(() => setTypewriterReady(true), isQueue ? 80 : 40);
    return () => clearTimeout(leadIn);
  }, [displayTargetKey, imageReadyForDisplay]);

  useEffect(() => {
    const { target, isQueue } = getDisplayTarget();
    if (!typewriterReady) return;
    if (!target) {
      prepareNarrationHandoff(displayTargetKey, getNarrationFileName());
      return;
    }

    if (target.length < displayedBuffer.length) {
      setDisplayedBuffer(target);
      return;
    }

    const backlog = pendingQueue.length;
    const charStep = isQueue
      ? Math.min(12, Math.max(3, Math.ceil((backlog + 1) / 7)))
      : 3;
    const timer = setInterval(() => {
      setDisplayedBuffer((prev) => {
        const { target: latestTarget } = getDisplayTarget();
        if (prev.length < latestTarget.length) {
          return latestTarget.slice(0, Math.min(prev.length + charStep, latestTarget.length));
        }
        return prev;
      });
    }, isQueue ? QUEUE_TYPEWRITER_INTERVAL_MS : LIVE_TYPEWRITER_INTERVAL_MS);

    return () => clearInterval(timer);
  }, [activePresentation, data.stream_buffer, pendingQueue.length, typewriterReady, displayTargetKey]);

  // Only after AI narration has finished may the item enter the right-side record.
  useEffect(() => {
    if (!activePresentation) return;
    const target = getQueueDisplayText(activePresentation);
    if (!target || displayedBuffer.length < target.length || isAdvancingRef.current) return;

    isAdvancingRef.current = true;
    let releaseTimer = null;
    const timer = setTimeout(() => {
      const item = { ...activePresentation, _isCurrent: true };
      if (!revealedKeysRef.current.has(item._queueKey)) {
        revealedKeysRef.current.add(item._queueKey);
        setNarrationDisplay((prev) => prev.text ? { ...prev, phase: "revealed" } : prev);
        setRevealedResults((prev) => {
          const cleaned = prev
            .filter((res) => res._queueKey !== item._queueKey)
            .map((res) => ({ ...res, _isCurrent: false }));
          return [item, ...cleaned].slice(0, MAX_REVEALED_RESULTS);
        });
      }
      const revealHoldMs = pendingQueue.length > 20 ? FAST_REVEAL_HOLD_MS : NORMAL_REVEAL_HOLD_MS;
      releaseTimer = setTimeout(() => {
        setActivePresentation(null);
        setDisplayedBuffer("");
        isAdvancingRef.current = false;
      }, revealHoldMs);
    }, 120);

    return () => {
      clearTimeout(timer);
      if (releaseTimer) clearTimeout(releaseTimer);
      if (activePresentationRef.current?._queueKey !== activePresentation._queueKey) {
        isAdvancingRef.current = false;
      }
    };
  }, [activePresentation, displayedBuffer]);

  // Choose displayed image: queued completed result, or live current_file.
  useEffect(() => {
    if (activePresentation) {
      setImageLoaded(false);
      setImageFailed(false);
      setCurrentThumb(activePresentation.thumb_b64 || null);
      setCurrentImage(getResultImageSrc(activePresentation));
    } else {
      const activeFile = isRunning && data.current_file && data.current_file !== 'None' ? data.current_file : null;
      if (activeFile) {
        setImageLoaded(false);
        setImageFailed(false);
        setCurrentImage(`/api/image/${encodeURIComponent(activeFile)}`);
        setCurrentThumb(data.current_thumb || null);
      } else {
        setCurrentThumb(null);
        setImageFailed(false);
      }
    }
  }, [activePresentation, data.current_file, data.current_thumb, isRunning]);

  // Do not blank or dim the boss-facing preview between photos. Keep the
  // current photo visible until the next full-resolution image is ready.
  useEffect(() => {
    if (!currentImage) {
      setImageLoaded(false);
      setImageFailed(false);
      setImagePreparing(false);
      return;
    }

    let cancelled = false;
    setImageLoaded(false);
    setImageFailed(false);
    setImagePreparing(true);

    const img = new window.Image();
    img.onload = () => {
      if (cancelled) return;
      setVisibleImage(currentImage);
      setImageLoaded(true);
      setImageFailed(false);
      setImagePreparing(false);
    };
    img.onerror = () => {
      if (cancelled) return;
      setImageLoaded(false);
      setImageFailed(true);
      setImagePreparing(false);
    };
    img.src = currentImage;

    return () => {
      cancelled = true;
    };
  }, [currentImage]);



  // Poll API with Dynamic Interval
  useEffect(() => {
    console.log("App: useEffect (Polling) Start");
    let intervalId;
    const poll = async () => { await fetchData(); };
    poll();
    // [v19.8] Faster polling (500ms) for smoother photo/AI narration sync.
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

  const overallProgress = data.overall_progress || {};
  const overallTotal = Number(overallProgress.total_images || 0);
  const overallProcessed = Number(overallProgress.processed_images || 0);
  const overallPercent = overallTotal ? Math.min(100, Math.max(0, (overallProcessed / overallTotal) * 100)) : 0;
  const folderTotal = Number(overallProgress.total_folders || 0);
  const folderDone = Number(overallProgress.completed_folders || 0);
  const historicalPanelItems = (data.recent_results || []).map((res, i) => ({
    ...res,
    _queueKey: getQueueKey(res) || `recent|${i}|${res.file_name || ""}`,
    _isCurrent: i === 0
  }));
  const displayQueueHistoryItems = (Array.isArray(data.display_queue) ? data.display_queue : [])
    .slice(-80)
    .map((raw) => {
      const item = normalizePresentationItem(raw);
      return item;
    })
    .filter(Boolean)
    .reverse()
    .map((item, i) => ({ ...item, _isCurrent: i === 0 }));
  const showPendingResult = activePresentation && !revealedKeysRef.current.has(activePresentation._queueKey);
  const activePendingResult = showPendingResult
    ? {
        ...activePresentation,
        _queueKey: `${activePresentation._queueKey}|pending`,
        _isCurrent: true,
        _pendingReveal: true
      }
    : null;
  const livePendingFile = !activePendingResult && !activePresentation && isRunning && data.current_file && data.current_file !== 'None'
    ? data.current_file
    : "";
  const livePendingResult = livePendingFile
    ? {
        file_name: livePendingFile,
        thumb_b64: data.current_thumb || null,
        _queueKey: `live-pending|${livePendingFile}`,
        _isCurrent: true,
        _pendingReveal: true
      }
    : null;
  const pendingPanelResult = activePendingResult || livePendingResult;
  const liveRightPanelBackfill = (() => {
    if (!isRunning) return [];
    const used = new Set();
    const activeKey = activePresentation?._queueKey || "";
    if (pendingPanelResult?._queueKey) used.add(pendingPanelResult._queueKey);
    if (activeKey) used.add(activeKey);
    revealedResults.forEach((item) => {
      if (item?._queueKey) used.add(item._queueKey);
    });
    return displayQueueHistoryItems
      .filter((item) => {
        if (!item?._queueKey) return false;
        if (item._queueKey === activeKey) return false;
        if (used.has(item._queueKey)) return false;
        used.add(item._queueKey);
        return true;
      })
      .map((item) => ({ ...item, _isCurrent: false, _backfilled: true }));
  })();
  const rightPanelItems = (isRunning || revealedResults.length > 0)
    ? (pendingPanelResult
        ? [pendingPanelResult, ...revealedResults, ...liveRightPanelBackfill]
        : [...revealedResults, ...liveRightPanelBackfill])
        .slice(0, MAX_REVEALED_RESULTS)
    : (historicalPanelItems.length > 0 ? historicalPanelItems : displayQueueHistoryItems);
  const displayedFileName = activePresentation?.file_name || (isRunning ? (data.stream_file || data.current_file || "-") : (visibleImage ? "上一張畫面保留" : "-"));
  const sourceRootLabel = data.source_root || 'D:\\00_商化\\00_未整理商化照片';
  const currentFolderLabel = isRunning ? (data.current_relative_dir || data.image_dir || "-") : "-";
  const currentFileLabel = isRunning && data.current_file && data.current_file !== "None" ? data.current_file : "-";
  const visibleNarration = narrationDisplay.text || displayedBuffer || (isRunning ? "照片已進入判讀流程，等待 AI 輸出..." : "");
  const narrationPhase = narrationDisplay.phase === "revealed"
    ? "revealed"
    : displayedBuffer && narrationDisplay.key === displayTargetKey ? "typing" : narrationDisplay.phase;
  const isHeldNarration = narrationPhase !== "typing";
  const narrationStatusLabel = narrationPhase === "typing"
    ? "AI 即時判讀中"
    : narrationPhase === "revealed"
      ? "本張摘要完成 · 右側結果已揭露"
      : narrationPhase === "warming"
        ? "照片已切換 · 等待 AI 開始輸出"
        : "上一張摘要保留中 · 下一張判讀中";
  const cleanLmLogLines = (data.lm_logs || []).filter(isReadableLmLogLine);
  const queueHistoryLines = (Array.isArray(data.display_queue) ? data.display_queue : [])
    .slice(-36)
    .map((raw) => normalizePresentationItem(raw))
    .filter(Boolean)
    .flatMap((item) => {
      const summary = getQueueDisplayText(item);
      const verdict = `判斷是${item.view_type || item.category || '照片'}：${item.model || '(無型號)'} / ${formatDisplayPrice(item.price)}`;
      return summary ? [`▶️ ${item.file_name}`, verdict, `[THINK] ${summary}`] : [`▶️ ${item.file_name}`, verdict];
    });
  const visibleLogLines = cleanLmLogLines.length >= 3 ? cleanLmLogLines : queueHistoryLines;
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
      <header className="app-header" style={{ height: '50px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 16px', borderBottom: '1px solid #333', background: '#111' }}>
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
                <div style={{ width: '330px', display: 'flex', flexDirection: 'column', gap: '3px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.68rem', color: '#d1d5db' }}>
                    <span style={{ fontWeight: '800', color: '#ffffff' }}>總進度 {formatCount(overallProcessed)}/{formatCount(overallTotal)} 張</span>
                    <span style={{ color: '#22c55e', fontWeight: '800' }}>{overallPercent.toFixed(1)}%</span>
                  </div>
                  <div style={{ height: '4px', width: '100%', background: '#222', borderRadius: '10px', overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${overallPercent}%`, background: '#22c55e', transition: 'width 0.3s ease' }} />
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.62rem', color: '#888' }}>
                    <span>剩餘 {formatCount(overallProgress.remaining_images)} 張</span>
                    <span>資料夾 {formatCount(folderDone)}/{formatCount(folderTotal)}</span>
                    <span>本資料夾 {formatCount(stats.processed)}/{formatCount(stats.total || 0)}</span>
                  </div>
                </div>
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
            <div style={{ padding: '10px 14px', background: '#111', borderRadius: '6px', border: '1px solid #333', display: 'flex', flexDirection: 'column', gap: '10px', flexShrink: 0 }}>
              <div className="status-grid" style={{ display: 'grid', gridTemplateColumns: 'minmax(240px, 0.9fr) minmax(280px, 1fr) minmax(320px, 1.25fr) auto', gap: '14px', alignItems: 'center', minWidth: 0 }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: '0.62rem', color: '#8a8a8a', marginBottom: '2px' }}>來源根目錄</div>
                  <div style={{ color: '#00f5ff', fontSize: '0.75rem', fontFamily: 'JetBrains Mono', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={sourceRootLabel}>
                    {sourceRootLabel}
                  </div>
                </div>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: '0.62rem', color: '#8a8a8a', marginBottom: '2px' }}>目前資料匣</div>
                  <div data-testid="status-current-folder" style={{ color: '#fbbf24', fontSize: '0.82rem', fontWeight: 800, fontFamily: 'JetBrains Mono', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={data.image_dir || currentFolderLabel}>
                    {currentFolderLabel}
                  </div>
                </div>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: '0.62rem', color: '#8a8a8a', marginBottom: '2px' }}>目前檔案</div>
                  <div style={{ color: '#e5e7eb', fontSize: '0.74rem', fontFamily: 'JetBrains Mono', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={currentFileLabel}>
                    {currentFileLabel}
                  </div>
                </div>
                <div style={{ justifySelf: 'end', display: 'flex', alignItems: 'center', gap: '8px', minWidth: '118px' }}>
                  <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: isRunning ? '#22c55e' : '#f97316', boxShadow: isRunning ? '0 0 10px #22c55e' : 'none' }} />
                  <span style={{ color: isRunning ? '#d1fae5' : '#fed7aa', fontSize: '0.75rem', fontWeight: 800, whiteSpace: 'nowrap' }}>
                    {isRunning ? '正在執行' : '待機中'}
                  </span>
                </div>
              </div>

              <div style={{ display: 'flex', gap: '10px', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap' }}>
                {!isRunning && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', minWidth: '260px', maxWidth: '520px', flex: '1 1 320px' }}>
                    <span style={{ fontSize: '0.68rem', color: '#9ca3af', fontWeight: 800, whiteSpace: 'nowrap' }}>起始資料匣</span>
                    <select
                      value={targetDir}
                      onChange={(e)=>setTargetDir(e.target.value)}
                      style={{
                        background: '#111', border: '1px solid #444', color: '#00f5ff',
                        padding: '4px 6px', fontSize: '0.75rem', borderRadius: '3px', width: '100%', minWidth: 0
                      }}
                    >
                      {availableDirs.map(d => <option key={d} value={d}>{d}</option>)}
                      {targetDir && !availableDirs.includes(targetDir) && <option value={targetDir}>{targetDir}</option>}
                    </select>
                  </div>
                )}
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', marginLeft: 'auto' }}>
                  <button onClick={() => handleStart(false)} disabled={isRunning}
                      style={{ background: isRunning ? '#333' : '#22c55e', color: '#fff', border:'1px solid #333', padding:'5px 12px', borderRadius:'4px', cursor: isRunning?'not-allowed':'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px' }}>
                      <Play size={12} /> 續跑
                  </button>
                 <button onClick={handleStop}
                     style={{ background: '#ef4444', color: '#fff', border:'1px solid #333', padding:'5px 12px', borderRadius:'4px', cursor: 'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px' }}>
                     <Square size={12} /> 停止
                 </button>
                 <button onClick={() => window.open(`/failed_records.html?v=${Date.now()}`, '_blank')}
                     style={{ background: '#6366f1', color: '#fff', border:'1px solid #333', padding:'5px 12px', borderRadius:'4px', cursor: 'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px' }}>
                     <AlertCircle size={12} /> 失敗記錄 ({stats.failed})
                 </button>
                 <button onClick={() => window.open(`/success_records.html?v=${Date.now()}`, '_blank')}
                     style={{ background: '#10b981', color: '#fff', border:'1px solid #333', padding:'5px 12px', borderRadius:'4px', cursor: 'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px' }}>
                     <CheckCircle2 size={12} /> 成功記錄 ({stats.success})
                 </button>
                 <button onClick={openReviewPanel}
                     style={{ background: '#f59e0b', color: '#111', border:'1px solid #333', padding:'5px 12px', borderRadius:'4px', cursor: 'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px' }}>
                     <AlertCircle size={12} /> 待人工校正
                 </button>
                 {controlMsg && <span style={{fontSize:'0.7rem', marginLeft:'5px'}}>{controlMsg}</span>}
               </div>
              </div>
           </div>

            <div className="monitor-workspace" style={{ flex: 1, display: 'flex', gap: '10px', overflow: 'hidden' }}>
              <div className="main-monitor-panel" style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: '#111', borderRadius: '6px', border: '1px solid #333', overflow: 'hidden' }}>
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
                          {(visibleImage || currentImage || currentThumb) ? (
                              <>
                                  {!visibleImage && imagePreparing && !imageFailed && (
                                      <div style={{ color: '#666', display:'flex', flexDirection:'column', alignItems:'center', gap:'6px' }}>
                                          <ImageIcon size={28} />
                                          <span style={{fontSize:'0.75rem'}}>照片載入中</span>
                                      </div>
                                  )}
                                  {visibleImage && <img key={visibleImage} src={visibleImage} data-testid="main-preview-image" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain', zIndex: 20, display: 'block' }} alt="P" />}
                                  {imageFailed && !visibleImage && (
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
                            {visibleNarration ? (
                               <div style={{
                                   wordBreak: 'break-all',
                                   whiteSpace: 'pre-wrap',
                                   color: isHeldNarration ? '#d1d5db' : '#ffffff',
                                   opacity: isHeldNarration ? 0.82 : 1,
                                   fontSize: '1.05rem',
                                   fontFamily: 'JetBrains Mono',
                                   lineHeight: '1.6',
                                   fontWeight: 'bold',
                                   transition: 'color 0.18s ease, opacity 0.18s ease'
                               }}>
                                   <div style={{
                                      color: isHeldNarration ? '#94a3b8' : '#22d3ee',
                                      fontSize: '0.68rem',
                                      fontWeight: '800',
                                      marginBottom: '4px',
                                      letterSpacing: 0,
                                      display: 'flex',
                                      alignItems: 'center',
                                      gap: '6px'
                                   }}>
                                      <span style={{
                                        width: '6px',
                                        height: '6px',
                                        borderRadius: '50%',
                                        background: isHeldNarration ? '#64748b' : '#22d3ee',
                                        boxShadow: isHeldNarration ? 'none' : '0 0 8px #22d3ee'
                                      }} />
                                      {narrationStatusLabel}
                                   </div>
                                   {false && isHeldNarration && (
                                      <div style={{ color: '#94a3b8', fontSize: '0.68rem', fontWeight: '800', marginBottom: '4px', letterSpacing: 0 }}>
                                          上一張摘要保留中 · 下一張判讀中
                                      </div>
                                   )}
                                   {visibleNarration}
                                   {!isHeldNarration && <span className="typing-dots"></span>}
                               </div>
                            ) : (
                                <div style={{ color: '#444', fontSize: '0.8rem', fontStyle: 'italic' }}>等待辨識訊號...</div>
                            )}
                       </div>

                      {/* 2. Bottom Pane: System Logs / History */}
                      <div style={{ flex: 1, overflowY: 'auto' }} data-testid="ai-history-log">
                          {visibleLogLines.length > 0 ? (
                               [...visibleLogLines]
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
                               <div style={{textAlign:'center', marginTop:'10%', color:'#4b5563', fontSize:'0.76rem'}}>等待下一段辨識紀錄</div>
                          )}
                          <div ref={logsEndRef} />
                      </div>
                  </div>
              </div>

              <div className="result-sidebar" style={{ width: 'clamp(360px, 23vw, 430px)', minWidth: '360px', flexShrink: 0, display: 'flex', flexDirection: 'column', background: '#111', borderRadius: '6px', border: '1px solid #333', overflow: 'hidden' }}>
                  <div style={{ padding: '8px', borderBottom: '1px solid #333', display:'grid', gridTemplateColumns:'repeat(3, 1fr)', gap:'4px' }}>
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

                      <div style={{ padding: '8px 10px', borderBottom: '1px solid #333', fontSize: '0.82rem', fontWeight: 'bold', display:'flex', alignItems:'center', justifyContent: 'space-between', gap:'8px', color: '#a1a1aa' }}>
                          <span style={{display:'flex', alignItems:'center', gap:'4px'}}>
                          <Zap size={12} color="#f59e0b"/> 辨識紀錄
                          </span>
                          <span style={{fontSize:'0.68rem', color:'#64748b', fontWeight:700}}>最新結果在上方</span>
                      </div>
                      <div style={{ flex: 1, overflowY: 'auto', padding: '10px' }} data-testid="result-rail">
                          {/* [v19.10] Right panel follows the presentation queue, not the faster backend. */}
                          {rightPanelItems.map((res, i) => (
                             <div data-testid="result-card" key={res._queueKey || `${res.file_name}-${i}`} style={{ background: res._isCurrent ? '#1e293b' : '#161616', border: res._isCurrent ? '1px solid #00f5ff' : '1px solid #222', borderRadius: '5px', padding: '8px', marginBottom:'8px', transition: 'background 0.2s' }} onMouseEnter={(e)=>e.currentTarget.style.background='#222'} onMouseLeave={(e)=>e.currentTarget.style.background=res._isCurrent ? '#1e293b' : '#161616'}>
                                  <div style={{ display: 'flex', gap: '8px' }}>
                                      <ResultThumbnail res={res} onClick={() => { if (!res._pendingReveal) setInspectImage(res); }} />
                                      <div style={{ flex: 1, minWidth: 0 }}>
                                          <div title={res.file_name} style={{ color: '#fff', fontSize: '0.8rem', lineHeight: 1.25, minHeight: '2.9em', overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', wordBreak: 'break-all' }}>{res.file_name}</div>
                                         {res._pendingReveal && (
                                            <div style={{ display: 'flex', gap: '4px', marginTop: '4px', flexWrap: 'wrap', alignItems: 'center' }}>
                                                <span style={{ fontSize: '0.62rem', padding: '1px 5px', borderRadius: '3px', background: '#0ea5e9', color: '#fff', fontWeight: '800' }}>
                                                    處理中
                                                </span>
                                                <span style={{ fontSize: '0.62rem', color: '#9ca3af' }}>
                                                    AI 即時判讀中
                                                </span>
                                            </div>
                                         )}
                                         {!res._pendingReveal && res.view_type !== '遠景' && (
                                            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(130px, 1fr) auto', alignItems: 'center', marginTop: '3px', width: '100%', columnGap: '10px' }}>
                                                <div style={{ fontSize: '0.76rem', color: res.category?.startsWith('不合格') ? '#ef4444' : '#22c55e', fontWeight: 'bold', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                    {res.model || (res.category?.startsWith('不合格') ? res.category.replace('不合格-', '') : '(無型號)')}
                                                </div>
                                                <div style={{ fontSize: '0.76rem', color: '#f59e0b', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: '4px', justifyContent: 'flex-end' }}>
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
                                             {!res._pendingReveal && <button
                                                  title="再辨識"
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
                                                      padding: '1px 6px',
                                                      color: rerunQueue[res.file_name] ? '#fbbf24' : '#e5e7eb',
                                                      fontSize: '0.66rem', display: 'flex', alignItems: 'center'
                                                  }}
                                              >
                                                  {rerunQueue[res.file_name] ? '已排隊' : '再辨識'}
                                              </button>}
                                            {!res._pendingReveal && res.view_type && <span style={{ fontSize: '0.6rem', padding: '1px 4px', borderRadius: '3px', background: res.view_type==='遠景'?'#3b82f6':'#22c55e', color: '#fff' }}>{res.view_type}</span>}
                                            {!res._pendingReveal && res.view_type !== '遠景' && res.screen_status && <span style={{ fontSize: '0.6rem', padding: '1px 4px', borderRadius: '3px', background: '#ec4899', color: '#fff' }}>{res.screen_status}</span>}
                                            {!res._pendingReveal && res.view_type !== '遠景' && res.quality_issue && res.quality_issue !== '無' && <span style={{ fontSize: '0.6rem', padding: '1px 4px', borderRadius: '3px', background: '#f97316', color: '#fff' }}>{res.quality_issue.replace('不合格-', '')}</span>}
                                             {/* [v18.67] 價格驗證符號 - 包含 ? 未知，但排除 not_compared */}
                                             {!res._pendingReveal && res.view_type !== '遠景' && res.price && res.price_symbol && res.price_status && res.price_status !== 'not_compared' && (
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
        .monitor-workspace,
        .main-monitor-panel,
        .result-sidebar,
        .log-wall {
          min-width: 0;
          min-height: 0;
        }
        .monitor-workspace {
          flex-direction: row !important;
          overflow: hidden !important;
        }
        .result-sidebar {
          width: clamp(360px, 23vw, 430px) !important;
          min-width: 360px !important;
          flex: 0 0 auto !important;
        }
        @media (min-width: 1600px) {
          .result-sidebar {
            width: clamp(380px, 23vw, 430px) !important;
            min-width: 380px !important;
          }
        }
        @media (max-width: 1200px) {
          .app-header {
            height: auto !important;
            min-height: 50px;
            flex-wrap: wrap;
            gap: 8px;
            padding: 8px 12px !important;
          }
          .status-grid {
            grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) !important;
          }
        }
        @media (max-width: 720px) {
          .app-header > div:last-child {
            width: 100%;
            justify-content: space-between;
            gap: 10px !important;
          }
          .app-header > div:last-child > div:nth-child(2) {
            width: min(100%, 330px) !important;
          }
          .status-grid {
            grid-template-columns: minmax(0, 1fr) !important;
          }
        }
       `}</style>
     </div>
   );
 };

 export default App;

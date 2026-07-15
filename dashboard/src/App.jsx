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

const buildLivePendingNarration = ({ fileName, passIndex, reviewMode }) => {
  const safeFileName = String(fileName || "").trim();
  if (!safeFileName) return "";

  const normalizedPass = Math.max(1, Number(passIndex) || 1);
  const passLabel = ({
    1: "初次辨識",
    2: "第二輪複核",
    3: "第三輪獨立判讀",
    4: "慢模型仲裁"
  })[normalizedPass] || `第 ${normalizedPass} 輪複核`;
  const task = reviewMode === "current_year_review"
    ? "核對遠景、單機與 FollowMe 實體線索，並重新檢查型號及價格標籤"
    : "辨認照片主體與陳列情境，並檢查可見的型號、價格及 FollowMe 實體線索";

  return `正在針對 ${safeFileName} 進行${passLabel}。本輪會${task}；AI 正在整理這張照片的可見證據，判讀文字將接續顯示。`;
};

const isStructuredModelOutput = (text) => {
  const value = String(text || "").trim().replace(/^```(?:json)?\s*/i, "");
  return value.startsWith("{") || /"(?:view_type|screen_status|quality_issue|model|price)"\s*:/.test(value);
};

const humanizeStructuredModelOutput = (text, fallback) => {
  const value = String(text || "").trim();
  if (!isStructuredModelOutput(value)) return value;
  try {
    const normalized = value.replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "").trim();
    const parsed = JSON.parse(normalized);
    const narration = String(parsed?.narration || parsed?.desc || "").trim();
    if (narration) return narration;
  } catch {
    // A live JSON stream is often incomplete. Show bounded evidence below
    // until the model's narration field becomes parseable.
  }
  const field = (name) => {
    const match = value.match(new RegExp(`"${name}"\\s*:\\s*"([^"\\r\\n]*)"`, "i"));
    return match ? match[1].trim() : "";
  };
  const viewType = field("view_type") || field("category");
  const model = field("model");
  const price = field("price");
  const screenStatus = field("screen_status");
  const qualityIssue = field("quality_issue");
  const facts = [];
  if (viewType) facts.push(`目前辨識為${viewType}`);
  if (model && !/^(?:無|無型號|型號未辨識)$/i.test(model)) facts.push(`正在核對型號 ${model}`);
  if (price && !/^(?:無|無價格)$/i.test(price)) facts.push(`正在核對價格 ${formatDisplayPrice(price)}`);
  if (screenStatus) facts.push(`畫面狀態為${screenStatus}`);
  if (qualityIssue && qualityIssue !== "無") facts.push(`仍需檢查${qualityIssue}`);
  return facts.length
    ? `AI 正在逐項核對本張照片；${facts.join("，")}。完整判讀尚未完成。`
    : fallback;
};

const getLivePhotoIdentityKey = (key) => String(key || "").replace(/\|pass:\d+$/, "");

const getLoadedAssetFingerprint = async () => {
  const assets = [...document.querySelectorAll('script[src], link[rel="stylesheet"][href]')]
    .map((node) => node.src || node.href)
    .filter((url) => /\.(js|css)(\?|$)/i.test(url))
    .map((url) => new URL(url, window.location.href).pathname + (new URL(url, window.location.href).search || ''))
    .sort();
  const bytes = new TextEncoder().encode(assets.join('\n'));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, '0')).join('').slice(0, 16);
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

const UI_VERSION = "v19.45 (accuracy-first evidence contract)";
const CURRENT_GUARD_REVISION = "20260715.5";
console.log(`[Dashboard-Init] Version: ${UI_VERSION} | Timestamp: ${new Date().toLocaleTimeString()}`);

const COMPACT_STATUS_CONTRACT = "compact-v2";
const COMPACT_STATUS_POLL_MS = 2000;
const LEGACY_STATUS_POLL_MS = 5000;
const MAX_CLIENT_STATUS_PRESENTATIONS = 24;
const stripHeavyStatusFields = (value) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  const {
    thumb_b64,
    image_b64,
    raw_model_output,
    raw_response,
    raw_objects,
    evidence_images,
    ...light
  } = value;
  return light;
};
const sanitizeStatusPayload = (apiResult) => ({
  ...apiResult,
  recent_durations: (Array.isArray(apiResult?.recent_results) ? apiResult.recent_results : [])
    .slice(0, 5)
    .map((item) => Number(item?.duration))
    .filter((duration) => Number.isFinite(duration) && duration > 0),
  presentation_queue: (Array.isArray(apiResult?.presentation_queue) ? apiResult.presentation_queue : [])
    .slice(-MAX_CLIENT_STATUS_PRESENTATIONS)
    .map((item) => ({
      ...stripHeavyStatusFields(item),
      result: stripHeavyStatusFields(item?.result)
    })),
  // Running presentation state is sourced only from presentation_queue.
  // Keeping legacy recent_results retains tens of duplicate image payloads.
  recent_results: []
});

const isReadableLmLogLine = (line) => {
  const text = String(line || '').trim();
  if (!text) return false;
  if (isStructuredModelOutput(text) || isStructuredModelOutput(text.replace(/^\[THINK\]\s*/, ''))) return false;
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
  const [currentImageTarget, setCurrentImageTarget] = useState({ src: null, key: "", fileName: "" });
  const [currentThumb, setCurrentThumb] = useState(null);
  const [visibleImageTarget, setVisibleImageTarget] = useState({ src: null, key: "", fileName: "" });
  const currentImage = currentImageTarget.src;
  const currentImagePresentationKey = currentImageTarget.key;
  const visibleImage = visibleImageTarget.src;
  const visibleImagePresentationKey = visibleImageTarget.key;
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
  const [modalZoomMode, setModalZoomMode] = useState('actual');
  const [modalImageError, setModalImageError] = useState(false);
  const dragStartRef = useRef({ x: 0, y: 0 });
  const modalViewportRef = useRef(null);
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
      const apiResult = sanitizeStatusPayload(await response.json());
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
      return syncedResult;
    } catch (err) {
      setError(err.message);
      setIsConnected(false); // [v18.67] 連線失敗
      return null;
    }
  };

  useEffect(() => {
    const serverFingerprint = String(data.frontend_asset_fingerprint || '');
    // A pre-compact backend was started before the latest hot-deployed UI and
    // can report a stale cached fingerprint forever.  Reloading against that
    // value creates a 30-second refresh loop that repeatedly clears narration.
    // compact-v2 recomputes and verifies the served asset contract.
    if (data.status_contract_version !== 'compact-v2' || !serverFingerprint) return;
    let cancelled = false;
    getLoadedAssetFingerprint().then((loadedFingerprint) => {
      if (cancelled || !loadedFingerprint || loadedFingerprint === serverFingerprint) return;
      const key = 'samsung_ocr_asset_reload_at';
      const last = Number(sessionStorage.getItem(key) || 0);
      if (Date.now() - last < 30000) return;
      sessionStorage.setItem(key, String(Date.now()));
      window.location.replace(`/?ui=${encodeURIComponent(serverFingerprint)}`);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [data.frontend_asset_fingerprint, data.status_contract_version]);

  // [v19.15 UX] Frontend-owned presentation queue. The backend may run ahead,
  // but the viewer only sees: photo -> typed AI narration -> right-side result.
  const MAX_PENDING_PRESENTATIONS = 400;
  const MAX_REVEALED_RESULTS = 200;
  const MAX_LIVE_BACKLOG = 14;
  const MAX_DISPLAY_NARRATION_CHARS = 360;
  const LIVE_TYPEWRITER_INTERVAL_MS = 32;
  const QUEUE_TYPEWRITER_INTERVAL_MS = 30;
  const FAST_REVEAL_HOLD_MS = 920;
  const NORMAL_REVEAL_HOLD_MS = 1450;
  const [pendingQueue, setPendingQueue] = useState([]);
  const [activePresentation, setActivePresentation] = useState(null);
  const [revealedResults, setRevealedResults] = useState([]);
  const [resultRailBatchKey, setResultRailBatchKey] = useState("");
  const [displayedBuffer, setDisplayedBuffer] = useState("");
  const [narrationDisplay, setNarrationDisplay] = useState({
    text: "",
    key: "",
    phase: "idle",
    fileName: "",
    nextFileName: ""
  });

  useEffect(() => {
    if (!inspectImage) return;
    setModalPosition({ x: 0, y: 0 });
    setIsDragging(false);
    setModalZoomMode('actual');
    setModalImageError(false);
  }, [inspectImage?._queueKey, inspectImage?.source_path, inspectImage?.file_name]);

  useEffect(() => {
    if (!inspectImage) return;
    const closeOnEscape = (event) => {
      if (event.key !== 'Escape') return;
      setInspectImage(null);
      setModalPosition({ x: 0, y: 0 });
      setIsDragging(false);
      setModalZoomMode('actual');
      setModalImageError(false);
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [inspectImage]);

  useEffect(() => {
    if (!isDragging) return;
    const stopDragging = () => setIsDragging(false);
    window.addEventListener('mouseup', stopDragging);
    return () => window.removeEventListener('mouseup', stopDragging);
  }, [isDragging]);

  const centerModalImage = () => {
    const viewport = modalViewportRef.current;
    if (!viewport) return;
    window.requestAnimationFrame(() => {
      viewport.scrollLeft = Math.max(0, (viewport.scrollWidth - viewport.clientWidth) / 2);
      viewport.scrollTop = Math.max(0, (viewport.scrollHeight - viewport.clientHeight) / 2);
    });
  };
  const isAdvancingRef = useRef(false);
  const acceptedPresentationKeysRef = useRef(new Set());
  const revealedKeysRef = useRef(new Set());
  const presentationHydratedRef = useRef(false);
  const activePresentationRef = useRef(null);
  const narrationDisplayRef = useRef(narrationDisplay);
  const latestDisplayQueueKeysRef = useRef(new Set());
  const displayWatchdogRef = useRef({ key: "", length: 0, updatedAt: Date.now() });
  const [displayTargetKey, setDisplayTargetKey] = useState("");
  const [typewriterReady, setTypewriterReady] = useState(false);
  const [presentationInvariantError, setPresentationInvariantError] = useState("");
  const [expandedHistoryKeys, setExpandedHistoryKeys] = useState({});
  const [historyCache, setHistoryCache] = useState({});
  const [historyLoading, setHistoryLoading] = useState({});
  const [historyErrors, setHistoryErrors] = useState({});

  const getQueueKey = (item) => {
    if (!item) return "";
    return String(item.presentation_id || "");
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
      stream_buffer: item.full_ai_narration || item.narration || item.stream_buffer || result.stream_buffer || result.thinking || "",
      narration: item.full_ai_narration || item.narration || result.narration || item.stream_buffer || result.stream_buffer || result.thinking || "",
      source_item_id: item.source_item_id || result.source_item_id || "",
      pass_index: item.pass_index ?? result.pass_index,
      pass_label: item.pass_label || result.pass_label || "",
      retry_reason: item.retry_reason || result.retry_reason || "",
      previous_result_summary: item.previous_result_summary || result.previous_result_summary || "",
      // model_id is the inference engine identifier, not the detected product
      // model.  Mixing those fields made legacy rows render a fake pass header
      // such as "第 未提供 輪 · 未提供 · S27...".
      model_id: item.model_id || result.model_id || "",
      started_at: item.started_at || result.started_at || "",
      completed_at: item.completed_at || result.completed_at || "",
      decision: item.decision || result.decision || "",
      review_status: item.review_status || result.review_status || "",
      evidence_unresolved: item.evidence_unresolved ?? result.evidence_unresolved,
      accepted: item.accepted ?? result.accepted,
      auto_review_required: item.auto_review_required ?? result.auto_review_required,
      auto_verified: item.auto_verified ?? result.auto_verified,
      _queueKey: key,
      _isCurrent: false
    };
  };

  const getResultRailIdentity = (item) => String(
    item?.source_item_id || item?.source_path || item?.file_name || item?._queueKey || ""
  );

  const presentationTime = (item) => {
    const parsed = Date.parse(item?.completed_at || item?.started_at || "");
    return Number.isFinite(parsed) ? parsed : 0;
  };

  const comparePresentationsAscending = (left, right) => (
    presentationTime(left) - presentationTime(right)
    || Number(left?.presentation_sequence || 0) - Number(right?.presentation_sequence || 0)
  );

  const comparePresentationsDescending = (left, right) => (
    presentationTime(right) - presentationTime(left)
    || Number(right?.presentation_sequence || 0) - Number(left?.presentation_sequence || 0)
  );

  const mergeResultRailItems = (items) => {
    const newestByPhoto = new Map();
    [...items]
      .filter(Boolean)
      .sort(comparePresentationsDescending)
      .forEach((item) => {
        const identity = getResultRailIdentity(item);
        if (identity && !newestByPhoto.has(identity)) newestByPhoto.set(identity, item);
      });
    return [...newestByPhoto.values()]
      .slice(0, MAX_REVEALED_RESULTS)
      .map((item, index) => ({ ...item, _isCurrent: index === 0 }));
  };

  const currentResultRailBatchKey = `${String(data.current_relative_dir || data.image_dir || "")}|run:${String(data.presentation_run_id || "legacy")}`;
  const resultRailStorageKey = "samsung_ocr_result_rail_v2";

  // Preserve the current batch's completed cards across an asset refresh.
  useEffect(() => {
    if (!currentResultRailBatchKey || currentResultRailBatchKey === resultRailBatchKey) return;
    let restored = [];
    try {
      const saved = JSON.parse(sessionStorage.getItem(resultRailStorageKey) || "null");
      if (saved?.batchKey === currentResultRailBatchKey && Array.isArray(saved.items)) {
        restored = saved.items.map(normalizePresentationItem).filter(Boolean);
      }
    } catch (_) {}
    setRevealedResults(mergeResultRailItems(restored));
    setResultRailBatchKey(currentResultRailBatchKey);
  }, [currentResultRailBatchKey, resultRailBatchKey]);

  useEffect(() => {
    if (!resultRailBatchKey || resultRailBatchKey !== currentResultRailBatchKey) return;
    try {
      sessionStorage.setItem(resultRailStorageKey, JSON.stringify({
        batchKey: resultRailBatchKey,
        items: revealedResults
      }));
    } catch (_) {}
  }, [revealedResults, resultRailBatchKey, currentResultRailBatchKey]);

  useEffect(() => {
    if (!resultRailBatchKey || resultRailBatchKey !== currentResultRailBatchKey) return;
    let cancelled = false;
    fetch("/api/presentation_history?limit=200&scope=current_batch")
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => {
        if (cancelled || !Array.isArray(payload?.items)) return;
        const restored = payload.items.map(normalizePresentationItem).filter(Boolean);
        const allowed = new Set(Array.isArray(payload.source_item_ids) ? payload.source_item_ids.map(String) : []);
        const expectedRunId = String(payload.run_id || "");
        setRevealedResults((prev) => mergeResultRailItems([
          ...restored,
          ...prev.filter((item) => (
            allowed.has(String(item.source_item_id || ""))
            && String(item.run_id || "") === expectedRunId
          ))
        ]));
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [resultRailBatchKey, currentResultRailBatchKey]);

  const getPassLabel = (item) => item?.pass_label || ({
    1: "初次辨識",
    2: "第二輪複核",
    3: "第三輪獨立判讀",
    4: "慢模型仲裁"
  }[Number(item?.pass_index)] || "未提供");

  const formatMetaValue = (value) => {
    if (value === null || value === undefined || value === "") return "未提供";
    if (Array.isArray(value)) return value.length ? value.join("、") : "無";
    if (typeof value === "object") return Object.entries(value)
      .filter(([, item]) => item !== null && item !== undefined && item !== "")
      .map(([key, item]) => `${key}: ${Array.isArray(item) ? item.join("、") : String(item)}`)
      .join("；") || "無";
    return String(value);
  };
  const formatDecision = (value) => ({
    retry_scheduled: "已安排下一輪",
    accepted: "已通過自動守門",
    review_required: "需慢模型或人工校正"
  }[String(value || "")] || formatMetaValue(value));
  const isExplicitlyUnresolved = (item) => {
    if (!item) return false;
    // Cards from the stopped contaminated run remain useful as an audit
    // trail, but may not look like accepted results under the new guard.
    if ((item.pass_index || item.pass_label)
      && String(item.evidence_guard_revision || "") !== CURRENT_GUARD_REVISION) return true;
    if (item.auto_review_required === true) return true;
    const decision = String(item.decision || "").trim().toLowerCase();
    if (["retry_scheduled", "review_required", "failed"].includes(decision)) return true;
    if (decision === "accepted") return false;
    if (item.auto_verified === true) return false;
    const reviewStatus = String(item.review_status || "").trim().toLowerCase();
    if (["review_required", "待審核", "需慢模型或人工校正"].includes(reviewStatus)) return true;
    return item.evidence_unresolved === true
      || item.auto_review_required === true
      || item.accepted === false;
  };
  const hasPassMetadata = (item) => Boolean(item && (item.pass_index || item.pass_label));
  const getPassHeading = (item) => {
    if (!hasPassMetadata(item)) return "";
    const parts = [];
    if (item?.pass_index) parts.push(`第 ${formatMetaValue(item.pass_index)} 輪`);
    const label = item?.pass_label || (item?.pass_index ? getPassLabel(item) : "");
    if (label) parts.push(label);
    return parts.join(" · ");
  };
  const getNarrationFullText = (item) => String(item?.narration || item?.stream_buffer || "").trim();

  const trimDisplayNarration = (text) => {
    const value = String(text || "").trim();
    if (value.length <= MAX_DISPLAY_NARRATION_CHARS) return value;
    return `${value.slice(0, MAX_DISPLAY_NARRATION_CHARS).trim()}...`;
  };

  const getQueueDisplayText = (item) => {
    if (!item) return "";
    if (item.stream_buffer && item.stream_buffer.trim() && !isStructuredModelOutput(item.stream_buffer)) {
      return trimDisplayNarration(item.stream_buffer);
    }
    const result = item.result || item;
    return trimDisplayNarration(`這張已完成辨識：${result.view_type || '單機'}，${result.model || '無型號'}，${result.price || '無價格'}。`);
  };

  const getSyncedLiveStream = () => {
    const liveFile = String(data.stream_file || "").trim();
    const currentFile = String(data.current_file || "").trim();
    const text = String(data.stream_buffer || "").trim();
    if (!isRunning || !currentFile || currentFile === "None") return null;

    // LM Studio may not emit a new token for many seconds, or an individual
    // retry can time out after an earlier pass already produced a detailed
    // explanation.  Keep the current photo visible and reuse only detailed
    // thinking from this exact filename; never fall back to another photo.
    const logs = Array.isArray(data.lm_logs) ? data.lm_logs : [];
    let belongsToCurrentFile = false;
    const currentFileThinking = [];
    logs.forEach((rawLine) => {
      const line = String(rawLine || "");
      if (line.includes("載入圖片:")) {
        belongsToCurrentFile = line.includes(currentFile);
        return;
      }
      if (belongsToCurrentFile && line.includes("[THINK]")) {
        currentFileThinking.push(line.split("[THINK]").slice(1).join("[THINK]").trim());
      }
    });
    const detailedThinking = [...currentFileThinking]
      .reverse()
      .find((value) => (
        value.length >= 45
        && !value.startsWith("這張已完成辨識：")
        && !value.includes("AI 本輪未回傳完整判讀文字")
      ));
    const rawSameFileStream = liveFile === currentFile
      && !text.includes("AI 本輪未回傳完整判讀文字")
      ? text
      : "";
    const liveDir = String(data.current_relative_dir || data.image_dir || "").trim();
    const livePassIndex = Math.max(1, Number(data.review_progress?.current_pass || 1));
    const pendingNarration = buildLivePendingNarration({
      fileName: currentFile,
      passIndex: livePassIndex,
      reviewMode: String(data.review_progress?.mode || "")
    });
    const sameFileStream = humanizeStructuredModelOutput(rawSameFileStream, pendingNarration);
    const liveText = sameFileStream
      || detailedThinking
      || pendingNarration;
    return {
      fileName: currentFile,
      text: trimDisplayNarration(liveText),
      key: `live:${liveDir}|${currentFile}|pass:${livePassIndex}`,
      passIndex: livePassIndex,
      hasModelText: Boolean(rawSameFileStream || detailedThinking)
    };
  };

  const getLatestBackendNarration = () => {
    const queue = Array.isArray(data.presentation_queue) ? data.presentation_queue : [];
    const latest = normalizePresentationItem(queue[queue.length - 1]) || revealedResults[0] || null;
    if (!latest) return null;
    const text = getQueueDisplayText(latest);
    return text ? { fileName: latest.file_name || "", text, key: `latest:${latest._queueKey}` } : null;
  };

  const getNarrationFileName = () => {
    if (activePresentation?.file_name) return activePresentation.file_name;
    return getSyncedLiveStream()?.fileName || getLatestBackendNarration()?.fileName || "";
  };

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
      fileName: activePresentation?.file_name || ""
    });
  }, [displayedBuffer, displayTargetKey, activePresentation?.file_name, data.stream_file, data.current_file]);

  // Copy completed backend items into a local queue before the backend list rolls.
  useEffect(() => {
    if (!isRunning) {
      latestDisplayQueueKeysRef.current = new Set();
      return;
    }
    const incomingQueue = Array.isArray(data.presentation_queue) ? data.presentation_queue : [];
    latestDisplayQueueKeysRef.current = new Set(incomingQueue.map((raw) => getQueueKey(raw)).filter(Boolean));
    if (incomingQueue.length === 0) return;

    // The legacy backend exposes 200 historical events.  On initial load the
    // operator needs the newest completed item, not a minutes-long replay that
    // prevents the current model stream from ever becoming visible.
    const queueForHydration = presentationHydratedRef.current
      ? incomingQueue
      : incomingQueue.slice(-1);
    presentationHydratedRef.current = true;
    const incoming = [];
    const activeKey = activePresentationRef.current?._queueKey;
    queueForHydration.forEach((raw) => {
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
      // Never discard an unrevealed item because the backend window rolled.
      // The active snapshot and pending order are the sole presentation state.
      return next
        .sort(comparePresentationsAscending)
        .slice(0, MAX_PENDING_PRESENTATIONS);
    });
  }, [data.presentation_queue]);

  // The live LLM stream must never block completed photos from accumulating in
  // the right rail.  Hydrate the whole compact backend window, then continuously
  // upsert one newest card per physical photo. The final completion event can
  // arrive in the same response that flips is_running to false, so hydration
  // must not be gated by the running flag.
  useEffect(() => {
    const completed = (Array.isArray(data.presentation_queue) ? data.presentation_queue : [])
      .map(normalizePresentationItem)
      .filter(Boolean);
    if (completed.length === 0) return;
    setRevealedResults((prev) => mergeResultRailItems([...completed, ...prev]));
  }, [data.presentation_queue]);

  // Never let a stale async update pair narration with another snapshot.
  useEffect(() => {
    const activeKey = activePresentation?._queueKey || "";
    const narrationKey = displayTargetKey || narrationDisplay.key || "";
    const narrationIsCommitted = narrationDisplay.phase === "typing" || narrationDisplay.phase === "revealed";
    const narrationIsQueueItem = narrationKey && !narrationKey.startsWith("live:") && !narrationKey.startsWith("latest:");
    if (activeKey && narrationKey && narrationIsCommitted && narrationIsQueueItem && activeKey !== narrationKey) {
      setPresentationInvariantError(`presentation key divergence: ${activeKey} != ${narrationKey}`);
      setDisplayedBuffer("");
      setDisplayTargetKey("");
      setTypewriterReady(false);
      setActivePresentation(null);
      return;
    }
    if (!activeKey || !narrationKey || activeKey === narrationKey) {
      setPresentationInvariantError("");
    }
  }, [activePresentation?._queueKey, displayTargetKey, narrationDisplay.key]);

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
      const watched = displayWatchdogRef.current;
      if (!active || !watched.key || active._queueKey !== watched.key) {
        // A same-file live stream may temporarily own the left panel while an
        // older completed presentation waits in the local queue.  That is not
        // a stalled presentation: the photo, narration, and placeholder are
        // advancing under the live key.  Clear only the obsolete watchdog
        // warning; never hide a real queue-key divergence.
        setPresentationInvariantError((prev) => prev.startsWith("presentation stalled:") ? "" : prev);
        return;
      }
      const stalledMs = Date.now() - watched.updatedAt;
      if (stalledMs < 8000) return;
      setPresentationInvariantError(`presentation stalled: ${active._queueKey}`);
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

  const getDisplayTarget = () => {
    const live = getSyncedLiveStream();
    if (live) return { target: live.text, isQueue: false, key: live.key };
    if (activePresentation) {
      return {
        target: getQueueDisplayText(activePresentation),
        isQueue: true,
        key: activePresentation._queueKey
      };
    }
    if (!isRunning) {
      const latest = getLatestBackendNarration();
      if (latest) return { target: latest.text, isQueue: false, key: latest.key };
    }
    return { target: "", isQueue: false, key: "" };
  };

  const liveVisualSnapshot = getSyncedLiveStream();
  const activeVisualKey = liveVisualSnapshot?.key || activePresentation?._queueKey || "";
  const expectedVisualKey = activeVisualKey || liveVisualSnapshot?.key || currentImagePresentationKey;
  const isSameLivePhotoPassHandoff = Boolean(
    liveVisualSnapshot
    && visibleImage
    && visibleImage === currentImage
    && getLivePhotoIdentityKey(visibleImagePresentationKey) === getLivePhotoIdentityKey(expectedVisualKey)
  );
  const effectiveVisibleImagePresentationKey = isSameLivePhotoPassHandoff
    ? expectedVisualKey
    : visibleImagePresentationKey;
  const imageBelongsToActivePresentation = Boolean(
    expectedVisualKey
    && currentImagePresentationKey === expectedVisualKey
    && effectiveVisibleImagePresentationKey === expectedVisualKey
    && visibleImage === currentImage
  );
  const imageReadyForDisplay = liveVisualSnapshot || activePresentation
    ? Boolean(currentImagePresentationKey === activeVisualKey && (imageBelongsToActivePresentation || imageFailed))
    : (!currentImage || imageLoaded || imageFailed);

  // Stage the illusion deliberately: photo first, then AI narration, then result.
  useEffect(() => {
    const { key } = getDisplayTarget();
    if (!key || key === displayTargetKey) return;
    prepareNarrationHandoff(key, getNarrationFileName());
    setDisplayTargetKey(key);
    setDisplayedBuffer("");
    setTypewriterReady(false);
  }, [activePresentation?._queueKey, data.current_file, data.current_relative_dir, data.stream_file, data.stream_buffer, data.lm_logs]);

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
  }, [activePresentation, data.stream_buffer, data.lm_logs, pendingQueue.length, typewriterReady, displayTargetKey]);

  // Only after AI narration has finished may the item enter the right-side record.
  useEffect(() => {
    if (getSyncedLiveStream()) return;
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
        setRevealedResults((prev) => mergeResultRailItems([item, ...prev]));
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
  }, [activePresentation, displayedBuffer, data.current_file, data.stream_file, data.stream_buffer]);

  // Choose displayed image: queued completed result, or live current_file.
  useEffect(() => {
    const live = getSyncedLiveStream();
    if (live?.fileName) {
      const nextSrc = `/api/image/${encodeURIComponent(live.fileName)}`;
      setImageLoaded(false);
      setImageFailed(false);
      setCurrentThumb(null);
      setCurrentImageTarget({
        src: nextSrc,
        key: live.key,
        fileName: live.fileName
      });
      setVisibleImageTarget((prev) => {
        const samePhoto = prev.src === nextSrc
          && getLivePhotoIdentityKey(prev.key) === getLivePhotoIdentityKey(live.key);
        return samePhoto ? { ...prev, key: live.key, fileName: live.fileName } : prev;
      });
      return;
    }
    if (activePresentation) {
      setImageLoaded(false);
      setImageFailed(false);
      setCurrentThumb(activePresentation.thumb_b64 || null);
      setCurrentImageTarget({
        src: getResultImageSrc(activePresentation),
        key: activePresentation._queueKey,
        fileName: activePresentation.file_name || ""
      });
      return;
    }
    if (!isRunning && revealedResults[0]) {
      const latest = revealedResults[0];
      setImageLoaded(false);
      setImageFailed(false);
      setCurrentThumb(latest.thumb_b64 || null);
      setCurrentImageTarget({
        src: getResultImageSrc(latest),
        key: `latest:${latest._queueKey}`,
        fileName: latest.file_name || ""
      });
    }
  }, [activePresentation, data.current_file, data.current_relative_dir, data.review_progress?.current_pass, isRunning, revealedResults[0]?._queueKey]);

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
      setVisibleImageTarget({
        src: currentImage,
        key: currentImagePresentationKey,
        fileName: currentImageTarget.fileName
      });
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
  }, [currentImage, currentImagePresentationKey, currentImageTarget.fileName]);



  // Poll API with Dynamic Interval
  useEffect(() => {
    console.log("App: useEffect (Polling) Start");
    let timerId = null;
    let cancelled = false;
    let inFlight = false;
    const poll = async () => {
      if (cancelled || inFlight) return;
      inFlight = true;
      let snapshot = null;
      try {
        snapshot = await fetchData();
      } finally {
        inFlight = false;
        if (!cancelled) {
          const delay = snapshot?.status_contract_version === COMPACT_STATUS_CONTRACT
            ? COMPACT_STATUS_POLL_MS
            : LEGACY_STATUS_POLL_MS;
          timerId = window.setTimeout(poll, delay);
        }
      }
    };
    poll();
    // Recursive timeout schedules only after parsing and rendering the prior
    // response, so a multi-megabyte legacy payload can never overlap itself.
    return () => {
      cancelled = true;
      if (timerId !== null) window.clearTimeout(timerId);
    };
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
  const reviewProgress = data.review_progress || {};
  const completedPassCount = Math.max(0, Number(data.presentation_sequence || 0));
  const completedPassLabel = data.presentation_sequence_durable === true
    ? '累計判讀'
    : '本次服務判讀';
  const recentDurations = Array.isArray(data.recent_durations) ? data.recent_durations : [];
  const recentAverageDuration = recentDurations.length
    ? (recentDurations.reduce((sum, duration) => sum + duration, 0) / recentDurations.length).toFixed(2)
    : null;
  const activeDirectoryText = String(data.current_relative_dir || data.image_dir || "");
  const isReviewRun = reviewProgress.mode === 'current_year_review' || activeDirectoryText.includes('_ocr_staging');
  const reviewPeriodMatches = [...activeDirectoryText.matchAll(/20\d{4}/g)];
  const reviewPeriodLabel = reviewProgress.period || reviewPeriodMatches.at(-1)?.[0] || '本輪';
  const liveStreamSnapshot = getSyncedLiveStream();
  const showPendingResult = !liveStreamSnapshot && activePresentation && !revealedKeysRef.current.has(activePresentation._queueKey);
  const activePendingResult = showPendingResult
    ? {
        ...activePresentation,
        _queueKey: activePresentation._queueKey,
        _isCurrent: true,
        _pendingReveal: true
      }
    : null;
  const livePendingResult = liveStreamSnapshot
    ? {
        file_name: liveStreamSnapshot.fileName,
        _queueKey: liveStreamSnapshot.key,
        presentation_id: liveStreamSnapshot.key,
        presentation_sequence: '',
        pass_index: liveStreamSnapshot.passIndex,
        pass_label: getPassLabel({ pass_index: liveStreamSnapshot.passIndex }),
        model_id: data.current_model || '',
        _isCurrent: true,
        _pendingReveal: true
      }
    : null;
  const pendingPanelResult = livePendingResult || activePendingResult;
  const visiblePassPresentation = liveStreamSnapshot ? livePendingResult : activePresentation;
  const rightPanelItems = revealedResults.slice(0, MAX_REVEALED_RESULTS);
  const latestBackendNarration = getLatestBackendNarration();
  const heldNarrationSnapshot = !activePresentation && !liveStreamSnapshot && narrationDisplay.text
    ? {
        fileName: narrationDisplay.fileName || visibleImageTarget.fileName || "",
        text: narrationDisplay.text,
        key: narrationDisplay.key || visibleImagePresentationKey
      }
    : null;
  // The API snapshot is the display authority.  Animation state may lag or be
  // reset during a handoff, but it must never be able to blank a narration
  // that the backend has already supplied.
  const activeNarrationSnapshot = activePresentation
    ? {
        fileName: activePresentation.file_name || "",
        text: imageBelongsToActivePresentation || imageFailed
          ? getQueueDisplayText(activePresentation)
          : "照片載入中；為避免照片與判讀錯配，本筆 LLM 判讀將在同一張照片確認後顯示。",
        key: activePresentation._queueKey
      }
    : null;
  // Identity is stronger than freshness: while a queued photo is on screen,
  // only narration from that exact presentation may be rendered.  A newer
  // backend stream belongs to the next photo and must wait for handoff.
  const visibleNarrationSnapshot = liveStreamSnapshot
    || activeNarrationSnapshot
    || (!isRunning ? latestBackendNarration : heldNarrationSnapshot)
    || heldNarrationSnapshot;
  const displayedFileName = liveStreamSnapshot?.fileName
    || activePresentation?.file_name
    || visibleNarrationSnapshot?.fileName
    || (!isRunning ? latestBackendNarration?.fileName : "")
    || (visibleImage ? "上一張畫面保留" : "-");
  const sourceRootLabel = data.source_root || 'D:\\00_商化\\00_未整理商化照片';
  const currentFolderLabel = data.current_relative_dir || data.image_dir || overallProgress.current_folder || "-";
  // The header is part of the same visible presentation contract as the
  // photo and narration.  The backend may advance current_file before the
  // next presentation exists, so never let that early pointer rename the
  // still-visible prior photo during the handoff window.
  const currentFileLabel = displayedFileName && displayedFileName !== "-" && displayedFileName !== "上一張畫面保留"
    ? displayedFileName
    : (data.current_file && data.current_file !== "None"
      ? data.current_file
      : (data.latest_result_file || "-"));
  const narrationPhase = narrationDisplay.phase === "revealed"
    ? "revealed"
    : displayedBuffer && narrationDisplay.key === displayTargetKey ? "typing" : narrationDisplay.phase;
  const visibleNarrationKey = visibleNarrationSnapshot?.key || displayTargetKey || "";
  const narrationAnimationOwnsDisplay = Boolean(
    visibleNarrationKey
    && displayTargetKey === visibleNarrationKey
    && (typewriterReady || displayedBuffer)
  );
  const visibleNarration = !isRunning && visibleNarrationSnapshot?.text
    ? visibleNarrationSnapshot.text
    : narrationAnimationOwnsDisplay
    ? (displayedBuffer || "正在接收本張照片的 AI 判讀文字...")
    : (visibleNarrationSnapshot?.text
      || narrationDisplay.text
      || displayedBuffer
      || (isRunning ? "照片已進入判讀流程，等待 AI 輸出..." : ""));
  const isHeldNarration = !isRunning || (!visibleNarrationKey.startsWith("live:") && narrationPhase !== "typing");
  const matchingVisibleImage = effectiveVisibleImagePresentationKey === expectedVisualKey && visibleImage === currentImage
    ? visibleImage
    : null;
  const heldPresentation = !activePresentation && visibleNarrationKey && !visibleNarrationKey.startsWith("live:")
    ? revealedResults.find((item) => item._queueKey === visibleNarrationKey) || null
    : null;
  const visiblePresentation = visibleNarrationKey.startsWith("live:") ? null : (activePresentation || heldPresentation);
  const visiblePresentationId = visibleNarrationKey.startsWith("live:")
    ? visibleNarrationKey
    : (visiblePresentation?.presentation_id || "");
  const visiblePresentationSequence = visiblePresentation?.presentation_sequence ?? "";
  const narrationStatusLabel = !isRunning && visibleNarration
    ? "最新完成判讀"
    : visibleNarrationKey.startsWith("live:")
    ? "AI 即時判讀中"
    : visibleNarrationKey.startsWith("latest:")
      ? "最新完成判讀"
      : narrationPhase === "typing"
        ? "AI 判讀內容播放中"
    : narrationPhase === "revealed"
      ? "本張摘要完成 · 右側結果已揭露"
      : narrationPhase === "warming"
        ? "照片已切換 · 等待 AI 開始輸出"
        : "上一張摘要保留中 · 下一張判讀中";
  const cleanLmLogLines = (data.lm_logs || []).filter(isReadableLmLogLine);
  const queueHistoryLines = (Array.isArray(data.presentation_queue) ? data.presentation_queue : [])
    .slice(-36)
    .map((raw) => normalizePresentationItem(raw))
    .filter(Boolean)
    .flatMap((item) => {
      const summary = getQueueDisplayText(item);
      const verdict = `判斷是${item.view_type || item.category || '照片'}：${item.model || '(無型號)'} / ${formatDisplayPrice(item.price)}`;
      return summary ? [`▶️ ${item.file_name}`, verdict, `[THINK] ${summary}`] : [`▶️ ${item.file_name}`, verdict];
    });
  const visibleLogLines = cleanLmLogLines.length >= 3 ? cleanLmLogLines : queueHistoryLines;
  const historyItems = [...revealedResults, ...(Array.isArray(data.presentation_queue) ? data.presentation_queue.map(normalizePresentationItem).filter(Boolean) : [])]
    .reduce((items, item) => items.some((entry) => entry._queueKey === item._queueKey) ? items : [...items, item], []);
  const localHistoryBySourceId = historyItems.reduce((groups, item) => {
    if (!item.source_item_id) return groups;
    const key = String(item.source_item_id);
    groups[key] = groups[key] || [];
    groups[key].push(item);
    return groups;
  }, {});
  const getHistoryFor = (item) => {
    if (!item?.source_item_id) return [];
    const key = String(item.source_item_id);
    const combined = [...(historyCache[key] || []), ...(localHistoryBySourceId[key] || [])]
      .map((entry) => normalizePresentationItem(entry))
      .filter(Boolean)
      .reduce((items, entry) => items.some((existing) => existing._queueKey === entry._queueKey) ? items : [...items, entry], []);
    return combined.sort(comparePresentationsAscending);
  };
  const toggleHistory = async (item) => {
    if (!item?.source_item_id) return;
    const key = String(item.source_item_id);
    const opening = !expandedHistoryKeys[key];
    setExpandedHistoryKeys((prev) => ({ ...prev, [key]: opening }));
    if (!opening || historyCache[key] || historyLoading[key]) return;
    setHistoryLoading((prev) => ({ ...prev, [key]: true }));
    setHistoryErrors((prev) => ({ ...prev, [key]: "" }));
    try {
      const response = await fetch(`/api/presentation_history/${encodeURIComponent(key)}?limit=50`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "判讀歷程暫時無法載入");
      const items = (Array.isArray(payload.items) ? payload.items : [])
        .map((entry) => normalizePresentationItem(entry))
        .filter(Boolean);
      setHistoryCache((prev) => ({ ...prev, [key]: items }));
    } catch (historyError) {
      setHistoryErrors((prev) => ({ ...prev, [key]: historyError.message || "判讀歷程暫時無法載入" }));
    } finally {
      setHistoryLoading((prev) => ({ ...prev, [key]: false }));
    }
  };
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
                <div style={{ width: '360px', display: 'flex', flexDirection: 'column', gap: '3px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.68rem', color: '#d1d5db' }}>
                    <span style={{ fontWeight: '800', color: '#ffffff' }}>初次辨識總進度 {formatCount(overallProcessed)}/{formatCount(overallTotal)} 張</span>
                    <span style={{ color: '#22c55e', fontWeight: '800' }}>{overallPercent.toFixed(1)}%</span>
                  </div>
                  <div style={{ height: '4px', width: '100%', background: '#222', borderRadius: '10px', overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${overallPercent}%`, background: '#22c55e', transition: 'width 0.3s ease' }} />
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', whiteSpace: 'nowrap', fontSize: '0.58rem', color: '#888' }}>
                    <span>剩餘 {formatCount(overallProgress.remaining_images)} 張</span>
                    <span aria-hidden="true">·</span>
                    <span>資料匣 {formatCount(folderDone)}/{formatCount(folderTotal)}</span>
                    <span aria-hidden="true">·</span>
                    <span data-testid="review-pass-progress">
                      {isReviewRun ? `${reviewPeriodLabel} 複核` : '本資料夾'} {formatCount(stats.processed)}/{formatCount(stats.total || 0)}
                      {isReviewRun && completedPassCount ? ` · ${completedPassLabel} ${formatCount(completedPassCount)} 次` : ''}
                      {isReviewRun && reviewProgress.current_pass ? ` · 本張第 ${reviewProgress.current_pass}/3 輪` : ''}
                    </span>
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

      <div className="dashboard-body" style={{ flex: 1, minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', gap: '8px' }}>
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
                      <CheckCircle2 size={12} /> 判讀記錄 ({stats.success})
                 </button>
                 <button onClick={openReviewPanel}
                     style={{ background: '#f59e0b', color: '#111', border:'1px solid #333', padding:'5px 12px', borderRadius:'4px', cursor: 'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px' }}>
                      <AlertCircle size={12} /> 待人工校正 ({stats.review_required ?? 0})
                 </button>
                 {controlMsg && <span style={{fontSize:'0.7rem', marginLeft:'5px'}}>{controlMsg}</span>}
               </div>
              </div>
           </div>

            <div className="monitor-workspace" style={{ flex: 1, minWidth: 0, minHeight: 0, display: 'flex', gap: '10px', overflow: 'hidden' }}>
              <div className="main-monitor-panel" data-presentation-invariant={presentationInvariantError || "ok"} style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: '#111', borderRadius: '6px', border: '1px solid #333', overflow: 'hidden' }}>
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
                      <div data-testid="active-photo" data-presentation-key={expectedVisualKey} data-presentation-id={visiblePresentationId} data-presentation-sequence={visiblePresentationSequence} style={{ flex: 1, position: 'relative', overflow: 'hidden', display: 'flex', justifyContent: 'center', alignItems: 'center', background: '#000' }}>
                          {(matchingVisibleImage || currentImage || currentThumb) ? (
                              <>
                                  {!matchingVisibleImage && imagePreparing && !imageFailed && (
                                      <div style={{ color: '#666', display:'flex', flexDirection:'column', alignItems:'center', gap:'6px' }}>
                                          <ImageIcon size={28} />
                                          <span style={{fontSize:'0.75rem'}}>照片載入中</span>
                                      </div>
                                  )}
                                  {matchingVisibleImage && <img key={matchingVisibleImage} src={matchingVisibleImage} data-testid="main-preview-image" data-presentation-key={effectiveVisibleImagePresentationKey} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain', zIndex: 20, display: 'block' }} alt="P" />}
                                  {imageFailed && !matchingVisibleImage && (
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
                       <div ref={streamBufferRef} data-testid="narration-container" data-narration-source={visibleNarrationKey} data-presentation-id={visiblePresentationId} data-presentation-sequence={visiblePresentationSequence} style={{ flex: '0 0 150px', borderBottom: '1px solid #333', overflowY: 'auto', paddingBottom: '8px', marginBottom: '8px' }}>
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
                                      AI 判讀內容 · {narrationStatusLabel}
                                   </div>
                                   {false && isHeldNarration && (
                                      <div style={{ color: '#94a3b8', fontSize: '0.68rem', fontWeight: '800', marginBottom: '4px', letterSpacing: 0 }}>
                                          上一張摘要保留中 · 下一張判讀中
                                      </div>
                                   )}
                                   {hasPassMetadata(visiblePassPresentation) && (
                                     <div style={{ color: '#67e8f9', fontSize: '0.72rem', marginBottom: '5px' }}>
                                       {getPassHeading(visiblePassPresentation)}{visiblePassPresentation?.model_id ? ` · ${visiblePassPresentation.model_id}` : ''}
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
                        {l:'完成判讀', v:stats.success, c:'#22c55e'}, {l:'待複核', v:stats.review_required ?? 0, c:'#f59e0b'},
                        {l:'失敗', v:stats.failed, c:'#ef4444'}, {l:'處理器', v:`${data.resources?.cpu??0}%`, c:'#00f5ff'},
                        {l:'記憶體', v:`${data.resources?.ram??0}%`, c:'#a855f7'},
                        {l:'近期平均', v:recentAverageDuration || data.metrics?.last_duration || '-', c:'#a855f7', title:'最近 5 張實際耗時平均；不讓先前的逾時永久扭曲目前速度'}
                      ].map((item, i)=>(
                        <div key={i} title={item.title || ''} style={{ background:'#0a0a0f', border:'1px solid #333', padding:'8px', borderRadius:'4px', textAlign:'center' }}>
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
                          {pendingPanelResult && (
                            <div data-testid="active-placeholder" data-presentation-id={pendingPanelResult.presentation_id || ""} data-presentation-sequence={pendingPanelResult.presentation_sequence ?? ""} style={{ background: '#1e293b', border: '1px solid #00f5ff', borderRadius: '5px', padding: '8px', marginBottom: '8px' }}>
                              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                <ResultThumbnail res={pendingPanelResult} onClick={() => {}} />
                                <div style={{ flex: 1, minWidth: 0 }}>
                                  <div data-testid="active-placeholder-file" title={pendingPanelResult.file_name} style={{ color: '#fff', fontSize: '0.8rem', lineHeight: 1.18, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{pendingPanelResult.file_name}</div>
                                  <div style={{ display: 'flex', gap: '5px', marginTop: '5px', alignItems: 'center' }}>
                                    <span data-testid="active-placeholder-badge" style={{ fontSize: '0.62rem', padding: '1px 5px', borderRadius: '3px', background: '#0ea5e9', color: '#fff', fontWeight: '800' }}>處理中</span>
                                    <span data-testid="active-placeholder-text" style={{ fontSize: '0.62rem', color: '#cbd5e1' }}>AI 即時判讀中</span>
                                  </div>
                                  {hasPassMetadata(pendingPanelResult) && <div style={{ fontSize: '0.62rem', color: '#93c5fd', marginTop: '4px' }}>{getPassHeading(pendingPanelResult)}</div>}
                                </div>
                              </div>
                            </div>
                          )}
                          {rightPanelItems.map((res, i) => (
                             <div data-testid="result-card" data-presentation-id={res.presentation_id || ""} data-presentation-sequence={res.presentation_sequence ?? ""} data-review-state={isExplicitlyUnresolved(res) ? "pending-review" : "completed"} key={res._queueKey || res.presentation_id} style={{ background: res._isCurrent ? '#1e293b' : '#161616', border: res._isCurrent ? '1px solid #00f5ff' : '1px solid #222', borderRadius: '5px', padding: '8px', marginBottom:'8px', transition: 'background 0.2s' }} onMouseEnter={(e)=>e.currentTarget.style.background='#222'} onMouseLeave={(e)=>e.currentTarget.style.background=res._isCurrent ? '#1e293b' : '#161616'}>
                                  <div style={{ display: 'flex', gap: '8px' }}>
                                      <ResultThumbnail res={res} onClick={() => { if (!res._pendingReveal) setInspectImage(res); }} />
                                      <div style={{ flex: 1, minWidth: 0 }}>
                                         <div title={res.file_name} style={{ color: '#fff', fontSize: '0.8rem', lineHeight: 1.18, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', wordBreak: 'break-all', marginBottom: '2px' }}>{res.file_name}</div>
                                         {hasPassMetadata(res) && <div style={{ fontSize: '0.62rem', color: '#67e8f9', marginTop: '3px' }}>{getPassHeading(res)}</div>}
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
                                          {!res._pendingReveal && isExplicitlyUnresolved(res) && (
                                            <div style={{ display: 'flex', gap: '5px', marginTop: '5px', alignItems: 'center', flexWrap: 'wrap' }}>
                                              <span style={{ fontSize: '0.66rem', padding: '2px 6px', borderRadius: '3px', background: '#b45309', color: '#fff', fontWeight: '800' }}>判讀未完成／待複核</span>
                                            </div>
                                          )}
                                          {!res._pendingReveal && !isExplicitlyUnresolved(res) && res.view_type !== '遠景' && (
                                            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(118px, 136px) minmax(76px, 92px)', alignItems: 'center', marginTop: 0, width: 'fit-content', maxWidth: '100%', columnGap: '8px' }}>
                                                <div style={{ fontSize: '0.76rem', color: res.category?.startsWith('不合格') ? '#ef4444' : '#22c55e', fontWeight: 'bold', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                    {res.model || (res.category?.startsWith('不合格') ? res.category.replace('不合格-', '') : '(無型號)')}
                                                </div>
                                                <div style={{ fontSize: '0.76rem', color: '#f59e0b', fontWeight: '900', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: '4px', justifyContent: 'flex-start', minWidth: 0 }}>
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
                                            {!res._pendingReveal && !isExplicitlyUnresolved(res) && res.view_type && <span style={{ height: '22px', fontSize: '0.6rem', padding: '0 7px', borderRadius: '3px', background: res.view_type==='遠景'?'#3b82f6':'#22c55e', color: '#fff', display:'inline-flex', alignItems:'center', lineHeight:'20px' }}>{res.view_type}</span>}
                                            {!res._pendingReveal && !isExplicitlyUnresolved(res) && res.view_type !== '遠景' && res.screen_status && <span style={{ height: '22px', fontSize: '0.6rem', padding: '0 7px', borderRadius: '3px', background: '#ec4899', color: '#fff', display:'inline-flex', alignItems:'center', lineHeight:'20px' }}>{res.screen_status}</span>}
                                            {!res._pendingReveal && !isExplicitlyUnresolved(res) && res.view_type !== '遠景' && res.quality_issue && res.quality_issue !== '無' && <span style={{ height: '22px', fontSize: '0.6rem', padding: '0 7px', borderRadius: '3px', background: '#f97316', color: '#fff', display:'inline-flex', alignItems:'center', lineHeight:'20px' }}>{res.quality_issue.replace('不合格-', '')}</span>}
                                             {/* [v18.67] 價格驗證符號 - 包含 ? 未知，但排除 not_compared */}
                                             {!res._pendingReveal && !isExplicitlyUnresolved(res) && res.view_type !== '遠景' && res.price && res.price_symbol && res.price_status && res.price_status !== 'not_compared' && (
                                                <span
                                                    title={
                                                        res.price_status === 'unknown' ? '官網/PChome 查無價格；需人工確認或重跑' :
                                                        res.official_price ? `官方 $${res.official_price.toLocaleString()} (${res.price_diff_percent > 0 ? '+' : ''}${res.price_diff_percent}%)` : ''
                                                    }
                                                    style={{
                                                        fontSize: '0.7rem',
                                                        height: '22px',
                                                        padding: '0 7px',
                                                        borderRadius: '3px',
                                                        background: res.price_status === 'match' ? '#22c55e' :
                                                                   res.price_status === 'high' ? '#ef4444' :
                                                                   res.price_status === 'low' ? '#3b82f6' : '#dc2626',
                                                        color: '#fff',
                                                        fontWeight: '900',
                                                        cursor: 'help',
                                                        display: 'inline-flex',
                                                        alignItems: 'center',
                                                        lineHeight: '20px'
                                                    }}
                                                >
                                                    {res.price_symbol === '-' ? '？' : (res.price_symbol || '？')}
                                                </span>
                                             )}
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
                                                     marginLeft: 'auto',
                                                     flexShrink: 0,
                                                     background: rerunQueue[res.file_name] ? '#374151' : '#1f2937',
                                                     border: '1px solid #4b5563',
                                                     borderRadius: '3px', cursor: rerunQueue[res.file_name] ? 'not-allowed' : 'pointer',
                                                     height: '22px',
                                                     padding: '0 7px',
                                                     color: rerunQueue[res.file_name] ? '#fbbf24' : '#e5e7eb',
                                                     fontSize: '0.66rem', lineHeight: '20px', display: 'inline-flex', alignItems: 'center'
                                                 }}
                                             >
                                                 {rerunQueue[res.file_name] ? '已排隊' : '再辨識'}
                                             </button>}
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
                                                     learn_rule: false,
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
             <div data-testid="inspection-modal" data-presentation-id={inspectImage.presentation_id || ""} data-presentation-sequence={inspectImage.presentation_sequence ?? ""}
                style={{
                    position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh',
                    background: 'rgba(0,0,0,0.95)', zIndex: 10000,
                    display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                    cursor: 'default'
                }}
              >
                  {/* Close Button */}
                  <button
                         onClick={() => { setInspectImage(null); setModalPosition({x:0,y:0}); setIsDragging(false); setModalZoomMode('actual'); setModalImageError(false); }}
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

                  <div style={{
                      position: 'fixed', top: '20px', left: '28px', zIndex: 10005,
                      display: 'flex', gap: '8px', alignItems: 'center',
                      background: 'rgba(17,17,17,0.88)', border: '1px solid #333',
                      borderRadius: '6px', padding: '6px'
                  }}>
                      <button
                          onClick={() => { setModalZoomMode('actual'); setModalPosition({x:0,y:0}); setIsDragging(false); setTimeout(centerModalImage, 0); }}
                          style={{
                              height: '28px', padding: '0 10px', borderRadius: '4px',
                              border: modalZoomMode === 'actual' ? '1px solid #00f5ff' : '1px solid #444',
                              background: modalZoomMode === 'actual' ? '#0f2933' : '#1f2937',
                              color: '#fff', fontWeight: 800, cursor: 'pointer'
                          }}
                      >
                          100%
                      </button>
                      <button
                          onClick={() => { setModalZoomMode('fit'); setModalPosition({x:0,y:0}); setIsDragging(false); }}
                          style={{
                              height: '28px', padding: '0 10px', borderRadius: '4px',
                              border: modalZoomMode === 'fit' ? '1px solid #00f5ff' : '1px solid #444',
                              background: modalZoomMode === 'fit' ? '#0f2933' : '#1f2937',
                              color: '#fff', fontWeight: 800, cursor: 'pointer'
                          }}
                      >
                          適合視窗
                      </button>
                      <span style={{ color: '#94a3b8', fontSize: '0.72rem', fontWeight: 700 }}>
                          100% 可拖曳檢視
                      </span>
                  </div>

                  {/* Image Container */}
                      <div
                         ref={modalViewportRef}
                         style={{
                             width: '100vw',
                             height: 'calc(100vh - 86px)',
                             display: modalZoomMode === 'actual' ? 'block' : 'flex',
                             alignItems: modalZoomMode === 'fit' ? 'center' : 'stretch',
                             justifyContent: modalZoomMode === 'fit' ? 'center' : 'flex-start',
                             overflow: modalZoomMode === 'actual' ? 'auto' : 'hidden',
                             cursor: modalZoomMode === 'actual' ? (isDragging ? 'grabbing' : 'grab') : 'default',
                             userSelect: 'none',
                             padding: '52px 24px 12px',
                             boxSizing: 'border-box',
                             overscrollBehavior: 'contain'
                         }}
                         onMouseDown={(e) => {
                             if (modalZoomMode !== 'actual' || e.button !== 0 || !modalViewportRef.current) return;
                             e.preventDefault();
                             setIsDragging(true);
                             dragStartRef.current = {
                                 x: e.clientX,
                                 y: e.clientY,
                                 scrollLeft: modalViewportRef.current.scrollLeft,
                                 scrollTop: modalViewportRef.current.scrollTop
                             };
                         }}
                         onMouseMove={(e) => {
                             if (!isDragging || modalZoomMode !== 'actual' || !modalViewportRef.current) return;
                             e.preventDefault();
                             const start = dragStartRef.current;
                             modalViewportRef.current.scrollLeft = start.scrollLeft - (e.clientX - start.x);
                             modalViewportRef.current.scrollTop = start.scrollTop - (e.clientY - start.y);
                         }}
                         onMouseUp={() => setIsDragging(false)}
                         onMouseLeave={() => setIsDragging(false)}
                         onDragStart={(e) => e.preventDefault()}
                      >
                          <img
                              src={getResultImageSrc(inspectImage)}
                              style={{
                                  display: modalImageError ? 'none' : 'block',
                                  maxWidth: modalZoomMode === 'fit' ? 'calc(100vw - 48px)' : 'none',
                                  maxHeight: modalZoomMode === 'fit' ? 'calc(100vh - 132px)' : 'none',
                                  width: modalZoomMode === 'actual' ? 'auto' : 'auto',
                                  height: modalZoomMode === 'actual' ? 'auto' : 'auto',
                                  objectFit: 'contain',
                                  border: '4px solid #444',
                                  borderRadius: '4px',
                                  boxShadow: '0 0 100px rgba(0,0,0,0.8)',
                                  userSelect: 'none',
                                  pointerEvents: 'none',
                                  WebkitUserDrag: 'none',
                                  transform: 'none',
                                  transition: 'none'
                              }}
                              alt="放大照片"
                              className="inspection-modal-image"
                              draggable={false}
                              onLoad={centerModalImage}
                              onError={() => setModalImageError(true)}
                          />
                          {modalImageError && (
                              <div style={{
                                  height: '100%',
                                  display: 'flex',
                                  alignItems: 'center',
                                  justifyContent: 'center',
                                  color: '#f87171',
                                  fontWeight: 800
                              }}>
                                  原圖載入失敗
                              </div>
                          )}
                      </div>

                   {/* Metadata Footer [v18.30 Fix: Stays at Bottom] */}
                   <div style={{
                       position: 'absolute', bottom: 0, left: 0,
                       width: '100%', background: '#111', borderTop: '2px solid #333',
                       padding: '12px 24px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '20px',
                       color: '#ddd', fontFamily: 'JetBrains Mono', fontSize: '0.9rem', zIndex: 10005,
                       boxShadow: '0 -5px 25px rgba(0,0,0,0.8)'
                   }}>
                      <div style={{ position: 'absolute', left: '12px', bottom: '76px', right: '12px', display: 'flex', flexWrap: 'wrap', gap: '6px 14px', padding: '7px 10px', background: 'rgba(17,17,17,0.96)', border: '1px solid #333', borderRadius: '4px', fontSize: '0.68rem', color: '#cbd5e1' }}>
                        {hasPassMetadata(inspectImage) && <span>{getPassHeading(inspectImage)}</span>}
                        {inspectImage.retry_reason && <span>複核原因：{formatMetaValue(inspectImage.retry_reason)}</span>}
                        {inspectImage.model_id && <span>使用模型：{inspectImage.model_id}</span>}
                        {inspectImage.started_at && <span>開始：{inspectImage.started_at}</span>}
                        {inspectImage.completed_at && <span>完成：{inspectImage.completed_at}</span>}
                        {inspectImage.decision && <span>判讀結果：{formatDecision(inspectImage.decision)}</span>}
                        {inspectImage.previous_result_summary && <span>上一輪摘要：{formatMetaValue(inspectImage.previous_result_summary)}</span>}
                        {inspectImage.source_item_id && <button type="button" onClick={() => toggleHistory(inspectImage)} style={{ background: 'transparent', color: '#7dd3fc', border: '1px solid #155e75', borderRadius: '3px', padding: '2px 6px', fontSize: '0.68rem', cursor: 'pointer' }}>本張判讀歷程</button>}
                        {inspectImage.source_item_id && expandedHistoryKeys[String(inspectImage.source_item_id)] && <div style={{ flexBasis: '100%', maxHeight: '180px', overflowY: 'auto', borderTop: '1px solid #334155', paddingTop: '6px' }}>
                          {historyLoading[String(inspectImage.source_item_id)] && <div style={{ color: '#7dd3fc' }}>判讀歷程載入中...</div>}
                          {historyErrors[String(inspectImage.source_item_id)] && <div style={{ color: '#fca5a5' }}>{historyErrors[String(inspectImage.source_item_id)]}</div>}
                          {getHistoryFor(inspectImage).map((pass) => <div key={pass._queueKey} style={{ marginBottom: '7px' }}>
                            {hasPassMetadata(pass) && <div style={{ color: '#bae6fd', fontWeight: 800 }}>{getPassHeading(pass)}{pass.decision ? ` · ${formatDecision(pass.decision)}` : ''}</div>}
                            <div style={{ color: '#e5e7eb', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{getNarrationFullText(pass) || '未提供'}</div>
                          </div>)}
                        </div>}
                      </div>
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
        .inspection-modal-image {
          max-width: none !important;
          max-height: none !important;
        }
        .inspection-modal-image[style*="calc"] {
          max-width: calc(100vw - 48px) !important;
          max-height: calc(100vh - 132px) !important;
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

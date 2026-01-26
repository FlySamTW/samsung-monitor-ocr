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

const UI_VERSION = "v16.9 (Stream & Sync Fix)";

const App = () => {
  console.log("App: Component Initialize");
  
  // Default State to prevent crash/white screen
  const defaultState = {
      stats: { success: 0, failed: 0, total: 0, processed: 0, is_running: false },
      lm_logs: ["系統初始化完成，等待連線..."],
      current_file: null,
      sys: { cpu: 0, mem: 0 },
      recent_results: [],
      dynamic_examples_list: [],
      stream_buffer: "" // Real-time thinking buffer
  };

  const [data, setData] = useState(defaultState);
  const [currentImage, setCurrentImage] = useState(null);
  const [currentThumb, setCurrentThumb] = useState(null);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [error, setError] = useState(null);
  const [saveStatus, setSaveStatus] = useState('');
  const [availableDirs, setAvailableDirs] = useState([]);
  const [targetDir, setTargetDir] = useState('商化照片-202512');
  const [controlMsg, setControlMsg] = useState('');
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

  // Refs for auto-scroll
  const logsContainerRef = useRef(null);
  const logsEndRef = useRef(null);
  const streamBufferRef = useRef(null);
  const messagesEndRef = useRef(null);
  const lastProcessedRef = useRef(null);

  const fetchData = async () => {
    try {
      const response = await fetch('/api/status');
      if (!response.ok) throw new Error('API Error');
      const apiResult = await response.json(); 
      
      setData(prev => ({...prev, ...apiResult}));

      if (apiResult.current_file) {
        const timestamp = new Date().getTime();
        const newImageUrl = `/api/image/${encodeURIComponent(apiResult.current_file)}?t=${timestamp}`;
        if (newImageUrl !== currentImage) {
          setImageLoaded(false); 
          setCurrentImage(newImageUrl);
        }
        if (apiResult.current_thumb) {
            setCurrentThumb(apiResult.current_thumb);
        }
      }

      
      // Auto-Sync Phase 2: If recent_results exists and is different from last sync, update preview
      if (apiResult.recent_results && apiResult.recent_results.length > 0) {
          const latestFile = apiResult.recent_results[0].file_name;
          // Use a ref to track the last auto-synced file to allow manual override
          // But user requested: "Preview should be the same as the rightmost thumbnail"
          // This implies "Always Sync" or "Sync on Change".
          // Let's implement: If header file changed, sync it.
          if (latestFile !== lastProcessedRef.current) {
               lastProcessedRef.current = latestFile;
               const timestamp = new Date().getTime();
               setCurrentImage(`/api/image/${encodeURIComponent(latestFile)}?t=${timestamp}`);
               setCurrentThumb(null); 
          }
      }

      setError(null);
    } catch (err) {
      setError(err.message);
    }
  };

  // [v16.9 UX] Typewriter Effect State
  const [displayedBuffer, setDisplayedBuffer] = useState("");

  // Smooth Typewriter Effect Hook
  useEffect(() => {
    if (!data.stream_buffer) {
        setDisplayedBuffer("");
        return;
    }
    
    // If buffer reset (new image), reset display immediately
    if (data.stream_buffer.length < displayedBuffer.length) {
         setDisplayedBuffer(data.stream_buffer);
         return;
    }

    const timer = setInterval(() => {
        setDisplayedBuffer((prev) => {
            const target = data.stream_buffer || "";
            if (prev.length < target.length) {
                // [v16.10 UX] Proportional Smoothing: Speed is % of lag
                // This ensures we never stop completely but also catch up smoothly
                const lag = target.length - prev.length;
                const step = Math.max(1, Math.ceil(lag / 10)); // 10% of lag per frame
                return target.slice(0, prev.length + step);
            }
            return prev;
        });
    }, 16); // 16ms = 60fps

    return () => clearInterval(timer);
  }, [data.stream_buffer]);

  // Poll API with Dynamic Interval
  useEffect(() => {
    console.log("App: useEffect (Polling) Start");
    let intervalId;
    const poll = async () => { await fetchData(); };
    poll();
    // [v16.10 UX] Polling at 50ms to minimize input latency
    const intervalMs = (data && data.stats && data.stats.is_running) ? 50 : 2000;
    intervalId = setInterval(poll, intervalMs);
    return () => clearInterval(intervalId);
  }, [data?.stats?.is_running]);

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
          setControlMsg(`❌ 停止失敗: ${e}`);
      }
  };

  const stats = data.stats || defaultState.stats;

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
              <h1 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 'bold', background: 'linear-gradient(90deg, #fff, #888)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
                  三星電腦螢幕-通路陳列-照片分析
              </h1>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
               <span style={{ fontSize: '0.75rem', color: '#444', fontWeight: 'bold', border: '1px solid #333', padding: '2px 6px', borderRadius: '4px' }}>
                 {status?.version || UI_VERSION}
               </span>
               <div style={{ height: '3px', width: '200px', background: '#222', borderRadius: '10px', overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${(stats.processed / (stats.total || 1)) * 100}%`, background: '#22c55e', transition: 'width 0.3s ease' }} />
               </div>
               <span style={{ fontSize: '0.7rem', color: '#888' }}>{stats.processed}/{stats.total || 0}</span>
               <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                  <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: stats.is_running ? '#22c55e' : '#ff4b2b', boxShadow: stats.is_running ? '0 0 10px #22c55e' : 'none' }}></div>
                  <span style={{ fontSize: '0.7rem', color: '#888' }}>{stats.is_running ? '正在執行' : '待機中'}</span>
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
               <span style={{fontSize:'0.75rem', color:'#aaa'}}>📁 來源路徑:</span>
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
               
                <button onClick={() => handleStart(false)} disabled={stats.is_running} 
                    style={{ background: stats.is_running ? '#333' : '#22c55e', color: '#fff', border:'1px solid #333', padding:'4px 10px', borderRadius:'4px', cursor: stats.is_running?'not-allowed':'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px' }}>
                    <Play size={12} /> 繼續執行
                </button>
                <button onClick={() => handleStart(true)} disabled={stats.is_running} 
                    style={{ background: stats.is_running ? '#333' : '#f59e0b', color: '#fff', border:'1px solid #333', padding:'4px 10px', borderRadius:'4px', cursor: stats.is_running?'not-allowed':'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px' }}>
                    <Zap size={12} /> 重新啟動
                </button>
               <button onClick={handleStop} disabled={!stats.is_running} 
                   style={{ background: !stats.is_running ? '#333' : '#ef4444', color: '#fff', border:'1px solid #333', padding:'4px 10px', borderRadius:'4px', cursor: !stats.is_running?'not-allowed':'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px' }}>
                   <Square size={12} /> 停止
               </button>
               <button onClick={() => window.open('/failed_records.html', '_blank')} 
                   style={{ background: '#6366f1', color: '#fff', border:'1px solid #333', padding:'4px 10px', borderRadius:'4px', cursor: 'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px' }}>
                   <AlertCircle size={12} /> 失敗記錄 ({stats.failed})
               </button>
               <button onClick={() => window.open('/success_records.html', '_blank')} 
                   style={{ background: '#10b981', color: '#fff', border:'1px solid #333', padding:'4px 10px', borderRadius:'4px', cursor: 'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px' }}>
                   <CheckCircle2 size={12} /> 成功記錄 ({stats.success})
               </button>
               {controlMsg && <span style={{fontSize:'0.7rem', marginLeft:'5px'}}>{controlMsg}</span>}
           </div>

           <div style={{ flex: 1, display: 'flex', gap: '8px', overflow: 'hidden' }}>
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: '#111', borderRadius: '6px', border: '1px solid #333', overflow: 'hidden' }}>
                  <div style={{ flex: '0 0 50%', position: 'relative', borderBottom: '1px solid #333', display: 'flex', flexDirection: 'column' }}>
                      <div style={{ padding: '4px 8px', background: '#111', display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', fontWeight: 'bold', borderBottom: '1px solid #333' }}>
                          <span style={{display:'flex', alignItems:'center', gap:'4px', color: '#888'}}><ImageIcon size={12}/> 即時預覽</span>
                          <span style={{color: '#00f5ff', fontFamily: 'JetBrains Mono'}}>{data.current_file || '-'}</span>
                      </div>
                      <div style={{ flex: 1, position: 'relative', overflow: 'hidden', display: 'flex', justifyContent: 'center', alignItems: 'center', background: '#000' }}>
                          {(currentImage || currentThumb) ? (
                              <>
                                  {currentThumb && <img src={`data:image/jpeg;base64,${currentThumb}`} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain', filter: 'blur(4px)', opacity: imageLoaded ? 0 : 1, zIndex: 10 }} alt="T" />}
                                  {currentImage && <img src={currentImage} onLoad={() => setImageLoaded(true)} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain', zIndex: 20 }} alt="P" />}
                              </>
                          ) : (
                              <div style={{ color: '#333', display:'flex', flexDirection:'column', alignItems:'center' }}><Box size={24} /><span style={{fontSize:'0.7rem'}}>無訊號</span></div>
                          )}
                          {stats.is_running && (
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
                              <div style={{ wordBreak: 'break-all', whiteSpace: 'pre-wrap', color: '#ffffff', fontSize: '1.3rem', fontFamily: 'JetBrains Mono', lineHeight: '1.6' }}>
                                  {displayedBuffer}
                                  <span className="cursor-blink">_</span>
                              </div>
                           ) : (
                               <div style={{ color: '#444', fontSize: '0.8rem', fontStyle: 'italic' }}>...</div>
                           )}
                      </div>

                      {/* 2. Bottom Pane: System Logs / History */}
                      <div style={{ flex: 1, overflowY: 'auto' }}>
                          {data.lm_logs?.length > 0 ? (
                              [...data.lm_logs].reverse().map((line, i) => (
                                  <div key={i} style={{ 
                                      wordBreak: 'break-all', whiteSpace: 'pre-wrap', marginBottom: '4px', fontSize: '0.8rem',
                                      color: (line.includes('判斷是單機') || line.includes('判斷是遠景')) ? '#ffd700' : (line.includes('▶️') || line.includes('✅')) ? '#33ff33' : '#aaaaaa',
                                      fontWeight: line.includes('判斷是') ? 'bold' : 'normal',
                                      paddingLeft: '4px', borderLeft: (line.includes('❌') ? '2px solid #ef4444' : '2px solid transparent')
                                  }}>{line}</div>
                              ))
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
                      
                      {/* [v16.10] Unknown Models Widget (User Request) */}
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
                          {data.recent_results?.map((res, i) => (
                             <div key={i} onClick={() => { setInspectImage(`/api/image/${encodeURIComponent(res.file_name)}`); }} style={{ background: '#161616', border: '1px solid #222', borderRadius: '4px', padding: '6px', marginBottom:'6px', cursor: 'pointer', transition: 'background 0.2s' }} onMouseEnter={(e)=>e.currentTarget.style.background='#222'} onMouseLeave={(e)=>e.currentTarget.style.background='#161616'}>
                                 <div style={{ display: 'flex', gap: '6px' }}>
                                     <img src={`/api/image/${encodeURIComponent(res.file_name)}`} style={{ width: '40px', height: '40px', objectFit: 'cover', borderRadius: '3px', border: '1px solid #333' }} alt="t" />
                                     <div style={{ flex: 1, minWidth: 0 }}>
                                         <div style={{ color: '#fff', fontSize: '0.7rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{res.file_name}</div>
                                         <div style={{ fontSize: '0.7rem', color: '#22c55e', fontWeight: 'bold' }}>{res.model || '(無型號)'}</div>
                                         <div style={{ fontSize: '0.7rem', color: '#f59e0b' }}>{formatDisplayPrice(res.price)}</div>
                                         <div style={{ display: 'flex', gap: '4px', marginTop: '2px', flexWrap: 'wrap' }}>
                                            {res.view_type && <span style={{ fontSize: '0.6rem', padding: '1px 4px', borderRadius: '3px', background: res.view_type==='遠景'?'#3b82f6':'#22c55e', color: '#fff' }}>{res.view_type}</span>}
                                            {res.screen_status && <span style={{ fontSize: '0.6rem', padding: '1px 4px', borderRadius: '3px', background: '#ec4899', color: '#fff' }}>{res.screen_status}</span>}
                                            {res.quality_issue && <span style={{ fontSize: '0.6rem', padding: '1px 4px', borderRadius: '3px', background: '#ef4444', color: '#fff' }}>{res.quality_issue}</span>}
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
         {/* Custom Confirmation Modal */}
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

         {/* Image Inspection Modal */}
         {inspectImage && (
             <div 
                style={{ 
                    position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh', 
                    background: 'rgba(0,0,0,0.85)', zIndex: 10000, 
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
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
                 < div 
                    style={{ position: 'relative', transform: `translate(${modalPosition.x}px, ${modalPosition.y}px)` }}
                    onMouseDown={(e) => {
                        setIsDragging(true);
                        dragStartRef.current = { x: e.clientX - modalPosition.x, y: e.clientY - modalPosition.y };
                    }}
                 >
                     <img src={inspectImage} style={{ maxHeight: '90vh', maxWidth: '90vw', border: '2px solid #555', borderRadius: '8px', pointerEvents: 'none' }} alt="Inspection" />
                     <button 
                        onClick={() => { setInspectImage(null); setModalPosition({x:0,y:0}); }}
                        style={{
                            position: 'absolute', top: -15, right: -15, 
                            background: '#ef4444', color: '#fff', border: '2px solid #fff', 
                            borderRadius: '50%', width: '32px', height: '32px', 
                            cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                            zIndex: 10001
                        }}
                     >
                        <XCircle size={20} />
                     </button>
                 </div>
             </div>
         )}

      <style>{`
        @keyframes scan { from { transform: translateX(-100%); } to { transform: translateX(300%); } }
      `}</style>
    </div>
  );
};

export default App;

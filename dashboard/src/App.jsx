import React, { useState, useEffect, useRef } from 'react';
import { 
  Activity, 
  CheckCircle2, 
  XCircle, 
  Image as ImageIcon, 
  Zap, 
  Box,
  Brain,
  Play,
  RotateCcw,
  AlertCircle
} from 'lucide-react';

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

const UI_VERSION = "v11.0 (Failed Tracking Backend)";

const App = () => {
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
  const [loading, setLoading] = useState(true);
  const [feedback, setFeedback] = useState('');
  const [feedbackStatus, setFeedbackStatus] = useState('');
  const [selectedImage, setSelectedImage] = useState(null);
  const [editingFile, setEditingFile] = useState(null);
  const [correctionData, setCorrectionData] = useState({ category: '單機', model: '', price: '' });
  
  // Refs for auto-scroll
  const logsContainerRef = React.useRef(null);
  const logsEndRef = React.useRef(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [showFailedFiles, setShowFailedFiles] = useState(true); // [v11.0] Collapse control

  // ... (useEffect hooks)

  const fetchData = async () => {
    try {
      const response = await fetch('/api/status');
      if (!response.ok) throw new Error('API Error');
      const apiResult = await response.json(); 
      
      setData(prev => ({...prev, ...apiResult})); // Merge to keep structure

      if (apiResult.current_file) {
        const newImageUrl = `/api/image/${encodeURIComponent(apiResult.current_file)}`;
        if (newImageUrl !== currentImage) {
          setCurrentImage(newImageUrl);
          setImageLoaded(false); // Force reload state
        }
        if (apiResult.current_thumb) {
            setCurrentThumb(apiResult.current_thumb);
        }
      }

      if (apiResult.stats) {
        // Stats are updated via setData merging above
        // setProcessingStats(apiResult.stats);  <-- REMOVED: Caused ReferenceError
      }

      setError(null);
    } catch (err) {
      setError(err.message);
      // Do NOT set data to null, keep displaying stale/default data
    } finally {
      setLoading(false);
    }
  };

  // Poll API with Dynamic Interval for Streaming Effect (Typwriter)
  useEffect(() => {
    let intervalId;
    
    const poll = async () => {
        await fetchData();
    };

    // Initial load
    poll();

    // Fast polling (200ms) when running to catch "Thinking" stream
    // Slow polling (2000ms) when idle
    const intervalMs = (data && data.stats && data.stats.is_running) ? 200 : 2000;
    
    intervalId = setInterval(poll, intervalMs);
    return () => clearInterval(intervalId);
  }, [data?.stats?.is_running]); // Depend on is_running to switch speeds

  // Global Style Injection to ensure no body scroll
  useEffect(() => {
      document.body.style.margin = '0';
      document.body.style.overflow = 'hidden';
      document.body.style.background = '#080808';
  }, []);

  // Auto-scroll ONCE when container is ~70% full (v10.7 - Optimal Timing)
  const streamBufferRef = useRef(null);
  const hasScrolledRef = useRef(false);
  
  useEffect(() => {
    if (streamBufferRef.current && data?.stream_buffer && !hasScrolledRef.current && logsContainerRef.current) {
      // Check if container is getting full (scrollHeight > clientHeight means content overflows)
      const container = logsContainerRef.current;
      const isFilling = container.scrollHeight > container.clientHeight * 0.7;
      
      if (isFilling) {
        streamBufferRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
        hasScrolledRef.current = true; // Only scroll once
      }
    }
  }, [data?.stream_buffer, data?.lm_logs]);

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    if (autoScroll && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'auto' });
    }
  }, [data?.lm_logs, autoScroll]);

  const handleScroll = (e) => {
    const { scrollTop, scrollHeight, clientHeight } = e.target;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 50;
    setAutoScroll(isAtBottom);
  };

  const sendFeedback = async () => {
    if (!feedback.trim()) return;
    try {
      await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_name: data?.current_file, reason: feedback, is_correct: false })
      });
      setFeedbackStatus('✅ 已送出');
      setFeedback('');
      setTimeout(() => setFeedbackStatus(''), 2000);
    } catch (e) {
      setFeedbackStatus('❌ 失敗');
    }
  };

  const handleFeedback = async (fileName, isCorrect, correctedData) => {
    try {
      await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          file_name: fileName,
          is_correct: isCorrect,
          correct_data: isCorrect ? null : correctedData
        })
      });
      setEditingFile(null);
    } catch (e) {
      console.error('Feedback error:', e);
    }
  };

  // --- Control Panel Handlers ---
  const [targetDir, setTargetDir] = useState('商化照片-202512');
  const [controlMsg, setControlMsg] = useState('');

  const handleStart = async (restart = false) => {
      try {
          const res = await fetch('/api/start_batch', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ dir: targetDir, restart: restart })
          });
          const json = await res.json();
          if (res.ok) {
             setControlMsg(`✅ ${json.message}`);
             setTimeout(() => setControlMsg(''), 3000);
          } else {
             setControlMsg(`❌ ${json.error}`);
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
          } else {
             setControlMsg(`❌ ${json.error}`);
          }
      } catch (e) {
          setControlMsg(`❌ 停止失敗: ${e}`);
      }
  };

  // REMOVED: Full screen error blocking code.
  // The UI will now ALWAYS render, with a top banner if error exists.

  const stats = data?.stats || defaultState.stats;
  const safePercent = stats.total > 0 ? (stats.processed / stats.total) * 100 : 0;
  const progressPercent = Math.min(100, Math.max(0, safePercent));

  // Lightbox Pan State
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });

  const handleDragStart = (e) => {
    e.preventDefault();
    setIsDragging(true);
    setDragStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
  };

  const handleDragMove = (e) => {
    if (!isDragging) return;
    setPan({ x: e.clientX - dragStart.x, y: e.clientY - dragStart.y });
  };

  const handleDragEnd = () => {
    setIsDragging(false);
  };

  return (
    <div className="dashboard-root" style={{ 
        display: 'flex', 
        flexDirection: 'column', 
        height: '100vh', 
        width: '100vw',
        padding: '8px',
        boxSizing: 'border-box',
        background: '#0a0a0f', 
        color: '#e0e0e0', 
        fontFamily: 'Inter, system-ui, -apple-system, sans-serif',
        overflow: 'hidden',
        gap: '8px'
    }}
    onMouseMove={handleDragMove}
    onMouseUp={handleDragEnd}
    >
      
      {/* 1. Header */}
      <header style={{ 
          height: '36px', 
          background: '#111', 
          borderRadius: '6px',
          border: '1px solid #333',
          display: 'flex', 
          alignItems: 'center', 
          padding: '0 16px', 
          gap: '1rem',
          flexShrink: 0 
      }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem', minWidth:'250px' }}>
              <h1 style={{ margin: 0, fontSize: '0.9rem', fontWeight: 'bold', color: '#ffffff' }}>三星電腦螢幕-通路陳列-照片分析</h1>
              <span style={{ fontSize: '0.6rem', background: '#1a1a2e', padding: '2px 6px', borderRadius: '4px', color: '#00f5ff', border: '1px solid #333' }}>{UI_VERSION}</span>
          </div>

          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '8px', maxWidth: '300px' }}>
               <div style={{ flex: 1, height: '4px', background: '#222', borderRadius: '2px', overflow: 'hidden' }}>
                   <div style={{ height: '100%', width: `${progressPercent}%`, background: error ? '#ef5350' : '#2196f3', transition: 'width 0.3s' }}></div>
               </div>
               <span style={{ fontSize: '0.65rem', color: '#666', fontFamily: 'JetBrains Mono' }}>{stats.processed}/{stats.total}</span>
          </div>

          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '0.8rem', fontSize: '0.7rem', color: '#666' }}>
               <span style={{display:'flex', alignItems:'center', gap:'4px', color: error ? '#ef5350' : stats.is_running ? '#66bb6a' : '#666'}}>
                   <div style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'currentColor' }}></div>
                   {error ? '已中斷 (嘗試重連中...)' : stats.is_running ? '運行' : '待機'}
               </span>
          </div>
      </header>
      
      {/* 1.1 Connection Error Banner */}
      {error && (
          <div style={{ background: '#b71c1c', color: '#fff', fontSize: '0.7rem', textAlign: 'center', padding: '2px' }}>
              ⚠️ 與後端伺服器失去連線，正在嘗試重新建立連線... ({error})
          </div>
      )}

      {/* 2. Main Content */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', gap: '8px' }}>
           {/* --- CONTROL PANEL --- */}
           <div style={{ padding: '8px 16px', background: '#111', borderRadius: '6px', border: '1px solid #333', display: 'flex', gap: '8px', alignItems: 'center', flexShrink: 0 }}>
               <span style={{fontSize:'0.75rem', color:'#aaa'}}>📁 Source:</span>
               <input 
                   type="text" 
                   value={targetDir} 
                   onChange={(e) => setTargetDir(e.target.value)}
                   style={{ 
                       background: '#111', 
                       border: '1px solid #444', 
                       color: '#fff', 
                       padding: '3px 6px', 
                       fontSize: '0.75rem', 
                       borderRadius: '3px',
                       width: '180px'
                   }}
               />
               
                <button onClick={() => handleStart(false)} disabled={data?.stats?.is_running} 
                    style={{
                        background: data?.stats?.is_running ? '#333' : '#22c55e', 
                        color: '#fff', border:'1px solid #333', padding:'4px 10px', borderRadius:'4px', cursor: data?.stats?.is_running?'not-allowed':'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px'
                    }}>
                    <Play size={12} /> 繼續執行
                </button>

                <button onClick={() => handleStart(true)} disabled={data?.stats?.is_running} 
                    style={{
                        background: data?.stats?.is_running ? '#333' : '#f59e0b', 
                        color: '#fff', border:'1px solid #333', padding:'4px 10px', borderRadius:'4px', cursor: data?.stats?.is_running?'not-allowed':'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px'
                    }}>
                    <Zap size={12} /> 重新啟動
                </button>

               <button onClick={handleStop} disabled={!data?.is_running} 
                   style={{
                       background: !data?.is_running ? '#333' : '#ef4444', 
                       color: '#fff', border:'1px solid #333', padding:'4px 10px', borderRadius:'4px', cursor: !data?.is_running?'not-allowed':'pointer', fontSize:'0.75rem', fontWeight:'bold', display:'flex', alignItems:'center', gap:'4px'
                   }}>
                   <Square size={12} /> 停止
               </button>
               
               {controlMsg && <span style={{fontSize:'0.7rem', marginLeft:'5px'}}>{controlMsg}</span>}
           </div>

           <div style={{ flex: 1, display: 'flex', gap: '8px', overflow: 'hidden' }}>

          
          {/* Left Column (Main) */}
          <div style={{ 
              flex: 1, 
              display: 'flex', 
              flexDirection: 'column', 
              minWidth: 0, 
              background: '#111',
              borderRadius: '6px',
              border: '1px solid #333',
              overflow: 'hidden'
          }}>
              
              {/* Preview (Height 50%) */}
              <div style={{ 
                  flex: '0 0 50%', 
                  position: 'relative', 
                  borderBottom: '1px solid #333', 
                  display: 'flex', 
                  flexDirection: 'column'
              }}>
                  <div style={{ padding: '4px 8px', background: '#111', display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', fontWeight: 'bold', borderBottom: '1px solid #333' }}>
                      <span style={{display:'flex', alignItems:'center', gap:'4px', color: '#888'}}><ImageIcon size={12}/> 即時預覽</span>
                      <span style={{color: '#00f5ff', fontFamily: 'JetBrains Mono'}}>{data?.current_file || '-'}</span>
                  </div>
                  <div style={{ flex: 1, position: 'relative', overflow: 'hidden', display: 'flex', justifyContent: 'center', alignItems: 'center', background: '#000', borderBottom: '1px solid #333' }}>
                      {currentImage || currentThumb ? (
                          <>
                              {currentThumb && (
                                  <img 
                                      src={`data:image/jpeg;base64,${currentThumb}`}
                                      style={{ 
                                          position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain',
                                          filter: 'blur(4px)', transition: 'opacity 0.4s ease-out',
                                          opacity: imageLoaded ? 0 : 1, zIndex: 10
                                      }}
                                      alt="Thumb"
                                  />
                              )}
                              {currentImage && (
                                  <img 
                                      src={currentImage} 
                                      style={{ 
                                          position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain',
                                          zIndex: 20
                                      }} 
                                      alt="Preview" 
                                      onError={(e) => console.warn("Image load failed", e)}
                                  />
                              )}
                          </>
                      ) : (
                          <div style={{ color: '#333', display:'flex', flexDirection:'column', alignItems:'center' }}><Box size={24} /><span style={{fontSize:'0.7rem'}}>No Signal</span></div>
                      )}
                      
                      {/* Knight Rider Scanner Effect */}
                      {stats.is_running && (
                          <div style={{
                              position: 'absolute',
                              bottom: 0,
                              left: 0,
                              height: '4px',
                              width: '100%',
                              zIndex: 30,
                              background: '#111',
                              overflow: 'hidden'
                          }}>
                              <div style={{
                                  width: '30%',
                                  height: '100%',
                                  background: 'linear-gradient(90deg, transparent, #ff0000, transparent)',
                                  animation: 'scan 1.5s ease-in-out infinite alternate',
                                  boxShadow: '0 0 10px #ff0000',
                                  borderRadius: '50%'
                              }}></div>
                              <style>{`
                                  @keyframes scan {
                                      0% { transform: translateX(-100%); }
                                      100% { transform: translateX(400%); }
                                  }
                              `}</style>
                          </div>
                      )}
                  </div>
              </div>

              {/* Logs (Remaining Height) */}
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                  
                  {/* Stream Buffer Overlay (Thinking) */}
                  {/* Stream Buffer Overlay REMOVED by user request */}

                  <div style={{ padding: '4px 8px', background: '#111', borderBottom: '1px solid #333', fontSize: '0.8rem', fontWeight: 'bold', display:'flex', alignItems:'center', gap:'4px', color: '#888' }}>
                      <Activity size={12} /> LLM 推論日誌
                  </div>
                  <div 
                      ref={logsContainerRef}
                      onScroll={handleScroll}
                      style={{ 
                          flex: 1, 
                          overflowY: 'auto', 
                          padding: '8px', 
                          fontFamily: 'JetBrains Mono, monospace', 
                          fontSize: '0.9rem', 
                          lineHeight: '1.4', 
                          color: '#33ff33',
                          background: '#000',
                          scrollBehavior: 'auto'
                      }}
                  >
                      {/* Real-time Stream Buffer at TOP (User Preference) */}
                      {data?.stream_buffer && (
                          <div ref={streamBufferRef} style={{ 
                              padding: '10px', 
                              color: '#ffffff', 
                              fontSize: '0.9rem', 
                              whiteSpace: 'pre-wrap',
                              fontFamily: 'JetBrains Mono, monospace',
                              lineHeight: '1.4',
                              fontWeight: 'bold',
                              background: 'rgba(88, 86, 214, 0.15)',
                              borderRadius: '6px',
                              borderLeft: '4px solid #5856d6',
                              marginBottom: '12px'
                          }}>
                              {data.stream_buffer}
                          </div>
                      )}

                      {/* Stream buffer moved to bottom for immediate visibility */}
                      {data?.lm_logs?.length > 0 ? (
                          [...data.lm_logs].reverse().map((line, i) => (
                              <div key={i} style={{ 
                                  wordBreak: 'break-all',
                                  whiteSpace: 'pre-wrap',
                                  // Color Logic: System=Green, Warning=Yellow, Error=Red-ish(handled by warning usually), Default=White
                                  color: 
                                    (line.includes('判斷是單機') || line.includes('判斷是遠景')) ? '#ffd700' :
                                    (line.includes('▶️') || line.includes('✅') || line.includes('🔄')) ? '#33ff33' : 
                                    (line.includes('⚠️') || line.includes('💔')) ? '#ffd700' : 
                                    '#ffffff',
                                  fontWeight: (line.includes('[思考詳細]') || line.includes('判斷是單機') || line.includes('判斷是遠景')) ? 'bold' : 'normal',
                                  background: line.includes('[思考詳細]') ? 'rgba(88, 86, 214, 0.1)' : 'none',
                                  padding: line.includes('[思考詳細]') ? '10px' : '0',
                                  borderRadius: '6px',
                                  borderLeft: line.includes('[思考詳細]') ? '4px solid #5856d6' : 'none',
                                  marginBottom: '8px',
                              }}>{line.includes('[思考詳細]') ? (
                                  <>
                                      {/* User Requested to remove prefix and any [Thinking] tag */}
                                      {/* The regex usually returns clean text, but we ensure safety here */}
                                      <span>
                                        {(line.split('] [思考詳細] ')[1] || line)
                                            .replace(/^\[思考\]\s*/, '') // Remove [思考] at start if present
                                            .replace(/^\[思考詳細\]\s*/, '') // Remove [思考詳細] at start if present
                                        }
                                      </span>
                                  </>
                              ) : line}</div>
                          ))
                      ) : (
                          <div style={{textAlign:'center', marginTop:'20%', color:'#222', fontSize:'0.7rem'}}>WAITING FOR LINK...</div>
                      )}

                      <div ref={logsEndRef} />
                  </div>
                  
                  {/* Feedback Input */}
                  <div style={{ padding: '6px', borderTop: '1px solid #222', display: 'flex', gap: '4px' }}>
                      <input 
                          type="text" 
                          placeholder="輸入指令..."
                          value={feedback}
                          onChange={e => setFeedback(e.target.value)}
                          onKeyDown={e => e.key === 'Enter' && sendFeedback()}
                          style={{ flex: 1, background: '#1a1a1a', border: 'none', color: '#fff', padding: '4px 8px', borderRadius: '2px', fontSize: '0.75rem' }}
                      />
                      <button onClick={sendFeedback} style={{ background: '#333', color: '#fff', border: 'none', padding: '0 8px', borderRadius: '2px', cursor: 'pointer', fontSize:'0.75rem' }}>發送</button>
                  </div>
                  {feedbackStatus && <div style={{ fontSize: '0.65rem', padding: '0 6px 4px', color: feedbackStatus.includes('✅') ? '#66bb6a' : '#ef5350' }}>{feedbackStatus}</div>}
              </div>
          </div>

          {/* Right Column (Fixed Narrow Width) */}
          <div style={{ width: '280px', display: 'flex', flexDirection: 'column', background: '#111', borderRadius: '6px', border: '1px solid #333', overflow: 'hidden' }}>
              
              {/* Tech Stats - Uniform 3x2 Grid */}
              <div style={{ padding: '8px', borderBottom: '1px solid #333' }}>
                  <div style={{ 
                      display: 'grid', 
                      gridTemplateColumns: '1fr 1fr', 
                      gap: '4px',
                      fontSize: '0.7rem'
                  }}>
                      {/* Row 1: SUCCESS, FAILED */}
                      <div style={{ background: '#0a0a0f', border: '1px solid #333', padding: '8px', borderRadius: '4px', textAlign: 'center' }}>
                          <div style={{ color: '#888', fontSize: '0.6rem', letterSpacing: '1px' }}>SUCCESS</div>
                          <div style={{ color: '#22c55e', fontSize: '1.2rem', fontWeight: 'bold' }}>{stats.success}</div>
                      </div>
                      <div style={{ background: '#0a0a0f', border: '1px solid #333', padding: '8px', borderRadius: '4px', textAlign: 'center' }}>
                          <div style={{ color: '#888', fontSize: '0.6rem', letterSpacing: '1px' }}>FAILED</div>
                          <div style={{ color: '#ef4444', fontSize: '1.2rem', fontWeight: 'bold' }}>{stats.failed}</div>
                      </div>
                      
                      {/* Row 2: CPU, MEM */}
                      <div style={{ background: '#0a0a0f', border: '1px solid #333', padding: '8px', borderRadius: '4px', textAlign: 'center' }}>
                          <div style={{ color: '#888', fontSize: '0.6rem', letterSpacing: '1px' }}>CPU</div>
                          <div style={{ color: '#00f5ff', fontSize: '1.2rem', fontWeight: 'bold' }}>{data?.resources?.cpu ?? 0}%</div>
                      </div>
                      <div style={{ background: '#0a0a0f', border: '1px solid #333', padding: '8px', borderRadius: '4px', textAlign: 'center' }}>
                          <div style={{ color: '#888', fontSize: '0.6rem', letterSpacing: '1px' }}>MEM</div>
                          <div style={{ color: '#a855f7', fontSize: '1.2rem', fontWeight: 'bold' }}>{data?.resources?.ram ?? 0}%</div>
                      </div>
                      
                      {/* Row 3: LAST, AVG */}
                      <div style={{ background: '#0a0a0f', border: '1px solid #333', padding: '8px', borderRadius: '4px', textAlign: 'center' }}>
                          <div style={{ color: '#888', fontSize: '0.6rem', letterSpacing: '1px' }}>LAST (s)</div>
                          <div style={{ color: '#00f5ff', fontSize: '1.2rem', fontWeight: 'bold' }}>{data?.metrics?.last_duration || '-'}</div>
                      </div>
                      <div style={{ background: '#0a0a0f', border: '1px solid #333', padding: '8px', borderRadius: '4px', textAlign: 'center' }}>
                          <div style={{ color: '#888', fontSize: '0.6rem', letterSpacing: '1px' }}>AVG (s)</div>
                          <div style={{ color: '#a855f7', fontSize: '1.2rem', fontWeight: 'bold' }}>{data?.metrics?.avg_duration || '-'}</div>
                      </div>
                  </div>
              </div>

              {/* Learned Corrections Log - Unified Style */}
              <div style={{ flex: '0 0 150px', display: 'flex', flexDirection: 'column', borderBottom: '1px solid #333' }}>
                  <div style={{ padding: '8px', borderBottom: '1px solid #333', fontSize: '0.8rem', fontWeight: 'bold', display:'flex', alignItems:'center', gap:'4px', color: '#888' }}>
                      <Brain size={12} color="#22c55e"/> 已學習訂正 ({data?.dynamic_examples_list?.length || 0})
                  </div>
                  <div style={{ flex: 1, overflowY: 'auto', padding: '8px', fontSize:'0.7rem' }}>
                      {data?.dynamic_examples_list?.length > 0 ? (
                          <div style={{ display:'flex', flexDirection:'column', gap:'4px' }}>
                              {data.dynamic_examples_list.map((ex, i) => (
                                  <div key={i} style={{ background:'#0a0a0f', padding:'4px', borderRadius:'4px', border:'1px solid #333' }}>
                                      <div style={{ color:'#fff', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', marginBottom:'2px' }}>{ex.file}</div>
                                      <div style={{ color:'#888', fontFamily:'JetBrains Mono' }}>
                                        {ex.category} {ex.model && `/ ${ex.model}`}
                                      </div>
                                  </div>
                              ))}
                          </div>
                      ) : (
                          <div style={{ textAlign: 'center', color: '#444', marginTop: '1rem' }}>尚無學習資料</div>
                      )}
                  </div>
              </div>

              {/* Recent History - Unified Style */}
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                  <div style={{ padding: '8px', borderBottom: '1px solid #333', fontSize: '0.8rem', fontWeight: 'bold', display:'flex', alignItems:'center', gap:'4px', color: '#888' }}>
                      <Zap size={12} color="#f59e0b"/> 辨識紀錄
                  </div>
                  <div style={{ flex: 1, overflowY: 'auto', padding: '8px' }}>
                      {data?.recent_results?.length > 0 ? (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                          {data.recent_results.map((res, i) => (
                            <div key={i} style={{ background: '#161616', border: '1px solid #222', borderRadius: '4px', padding: '6px' }}>
                                <div style={{ display: 'flex', gap: '6px' }}>
                                    {res.thumb_b64 && (
                                        <img 
                                            src={`data:image/jpeg;base64,${res.thumb_b64}`} 
                                            onClick={() => { setSelectedImage(res); setPan({x:0, y:0}); }}
                                            style={{ width: '32px', height: '32px', objectFit: 'cover', borderRadius: '3px', cursor: 'zoom-in', border: '1px solid #333' }}
                                            alt="t"
                                        />
                                    )}
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                            <span style={{ fontSize: '0.7rem', fontWeight: 'bold', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{res.file_name}</span>
                                        </div>
                                        <div style={{ fontSize: '0.65rem', color: '#bbb', marginTop: '2px' }}>
                                            {res.category === '遠景' ? (
                                                <span style={{color: '#42a5f5'}}>● 遠景 (略過規格辨識)</span>
                                            ) : (res.category && (res.category.startsWith('不合格') || res.category === '失敗' || res.category === '無法分辨')) ? (
                                                <span style={{color: '#ef5350'}}>● {res.category || '辨識失敗'}</span>
                                            ) : (
                                                <span style={{color: '#eee'}}>
                                                    <span style={{color:'#66bb6a'}}>[單機]</span> {res.model || '(無型號)'} / {formatDisplayPrice(res.price)}
                                                </span>
                                            )}
                                    </div>
                                    </div>
                                </div> 
                                {/* Correction UI */}
                                <div style={{ borderTop: '1px solid #222', marginTop: '4px', paddingTop: '4px', display:'flex', justifyContent:'flex-end', gap:'8px' }}>
                                   {editingFile === res.file_name ? (
                                       <div style={{ display:'flex', gap:'4px', alignItems:'center', width:'100%' }}>
                                            <input value={correctionData.model} onChange={e=>setCorrectionData({...correctionData, model:e.target.value})} style={{flex:1, background:'#222', border:'none', color:'#fff', fontSize:'0.7rem', padding:'2px'}} placeholder="型號"/>
                                            <input value={correctionData.price} onChange={e=>setCorrectionData({...correctionData, price:e.target.value})} style={{width:'50px', background:'#222', border:'none', color:'#fff', fontSize:'0.7rem', padding:'2px'}} placeholder="價格"/>
                                            <button onClick={()=>handleFeedback(res.file_name, false, correctionData)} style={{fontSize:'0.7rem', background:'#2e7d32', color:'#fff', border:'none', padding:'2px 6px', borderRadius:'2px'}}>V</button>
                                            <button onClick={()=>setEditingFile(null)} style={{fontSize:'0.7rem', background:'#c62828', color:'#fff', border:'none', padding:'2px 6px', borderRadius:'2px'}}>X</button>
                                       </div>
                                   ) : (
                                       <>
                                         <button onClick={() => { setEditingFile(res.file_name); setCorrectionData({ category: res.category, model: res.model, price: res.price }); }} style={{ background: 'none', border: 'none', color: '#666', cursor: 'pointer', fontSize: '0.65rem' }}>✎</button>
                                         <button onClick={() => handleFeedback(res.file_name, true, null)} style={{ background: 'none', border: 'none', color: '#4caf50', cursor: 'pointer', fontSize: '0.65rem' }}>✓</button>
                                       </>
                                   )}
                                </div>
                            </div>
                          ))}
                        </div>
                      ) : (
                          <div style={{ textAlign: 'center', color: '#444', marginTop: '1rem' }}>尚無學習資料</div>
                      )}
                  </div>
              </div>
              {/* Failed Records Section (v11.0) */}
              <div style={{ display: 'flex', flexDirection: 'column', background: '#0a0a0f', borderRadius: '8px', border: '1px solid #333', overflow: 'hidden', maxHeight: '300px' }}>
                  <div style={{ padding: '8px', borderBottom: showFailedFiles ? '1px solid #333' : 'none', fontSize: '0.8rem', fontWeight: 'bold', display:'flex', alignItems:'center', justifyContent: 'space-between', color: '#888', cursor: 'pointer', userSelect: 'none' }} onClick={() => setShowFailedFiles(!showFailedFiles)}>
                      <div style={{ display:'flex', alignItems:'center', gap:'6px' }}>
                          <AlertCircle size={12} color="#ef4444"/>
                          失敗記錄 ({data?.failed_files?.length || 0})
                          <span style={{ fontSize: '0.7rem' }}>{showFailedFiles ? '▼' : '▶'}</span>
                      </div>
                      {data?.failed_files?.length > 0 && (
                          <button onClick={(e) => { e.stopPropagation(); const text = data.failed_files.map(f => `${f.filename} - ${f.reason}`).join('\n'); navigator.clipboard.writeText(text); alert('✅ 已複製失敗記錄清單'); }} style={{ background: '#1e293b', color: '#94a3b8', border: '1px solid #334155', padding: '4px 8px', borderRadius: '4px', fontSize: '0.7rem', cursor: 'pointer', fontWeight: 'normal' }}>
                              📋 複製清單
                          </button>
                      )}
                  </div>
                  {showFailedFiles && (
                      <div style={{ flex: 1, overflowY: 'auto', padding: '8px', fontSize:'0.7rem' }}>
                          {data?.failed_files?.length > 0 ? (
                              <div style={{ display:'flex', flexDirection:'column', gap:'4px' }}>
                                  {data.failed_files.map((f, i) => (
                                      <div key={i} style={{ background:'#0a0a0f', padding:'6px', borderRadius:'4px', border:'1px solid #333', borderLeft: '3px solid #ef4444' }}>
                                          <div style={{ color:'#ef4444', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', marginBottom:'3px', fontFamily:'JetBrains Mono', fontSize: '0.7rem' }}>
                                              ❌ {f.filename}
                                          </div>
                                          <div style={{ color:'#888', fontSize:'0.65rem' }}>{f.reason}</div>
                                          <div style={{ color:'#555', fontSize:'0.6rem', marginTop:'2px' }}>
                                              {new Date(f.timestamp).toLocaleString('zh-TW', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                                          </div>
                                      </div>
                                  ))}
                              </div>
                          ) : (
                              <div style={{ textAlign: 'center', color: '#444', padding: '1rem' }}>尚無失敗記錄</div>
                          )}
                      </div>
                  )}
              </div>
 
              {/* END LEFT COL AND LISTS */}
              </div> 
              {/* END LEFT COL AND LISTS */}
           </div>
      </div>

      {/* 3. Lightbox Overlay (1:1 Full View) */}
      {selectedImage && (
          <div style={{
              position: 'fixed', inset: 0, zIndex: 9999,
              background: 'rgba(0,0,0,0.9)',
              display: 'flex', justifyContent: 'center', alignItems: 'center',
              overflow: 'hidden',
              userSelect: 'none'
          }}>
              {/* Close Button */}
              <button 
                  onClick={() => setSelectedImage(null)}
                  style={{
                      position: 'absolute', top: '20px', right: '20px', zIndex: 10000,
                      background: 'rgba(255, 0, 0, 0.6)', border: '2px solid #fff', color: '#fff',
                      borderRadius: '50%', width: '48px', height: '48px', cursor: 'pointer',
                      fontSize: '24px', display: 'flex', justifyContent: 'center', alignItems: 'center',
                      fontWeight: 'bold', boxShadow: '0 0 10px #000'
                  }}
              >
                  ×
              </button>

              <div 
                  onMouseDown={handleDragStart}
                  style={{
                      transform: `translate(${pan.x}px, ${pan.y}px)`,
                      cursor: isDragging ? 'grabbing' : 'grab',
                      transition: isDragging ? 'none' : 'transform 0.1s',
                      border: '2px solid #333',
                      boxShadow: '0 0 100px rgba(0,0,0,1)'
                  }}
              >
                  <img 
                      src={`/api/image/${encodeURIComponent(selectedImage.file_name)}`}
                      style={{ 
                          // 1:1 Mode: No Max Width/Height constraints
                          // But we start centered.
                          display: 'block',
                          pointerEvents: 'none', // Allow drag on container
                          minWidth: '500px', 
                          maxWidth: 'none',
                          maxHeight: 'none' 
                      }}
                      draggable={false}
                      alt="Full View"
                  />
                  <div style={{
                      position: 'absolute', bottom: '-60px', left: '50%', transform: 'translateX(-50%)',
                      textAlign: 'center', color: '#fff', background: 'rgba(0,0,0,0.8)', padding: '8px 16px',
                      fontSize: '1rem', borderRadius: '20px', whiteSpace: 'nowrap', border: '1px solid #555'
                  }}>
                      {selectedImage.file_name} <span style={{color:'#ffd700'}}>({selectedImage.model || selectedImage.category})</span>
                  </div>
              </div>
          </div>
      )}

    </div>
  );
};

export default App;

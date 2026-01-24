import re

# 讀取 App.jsx
with open('dashboard/src/App.jsx', 'r', encoding='utf-8') as f:
    content = f.read()

# 失敗記錄 UI 程式碼
failed_ui = '''
              {/* Failed Records Section (v11.0) */}
              <div style={{ display: 'flex', flexDirection: 'column', background: '#0a0a0f', borderRadius: '8px', border: '1px solid #333', overflow: 'hidden', maxHeight: '300px' }}>
                  <div style={{ padding: '8px', borderBottom: showFailedFiles ? '1px solid #333' : 'none', fontSize: '0.8rem', fontWeight: 'bold', display:'flex', alignItems:'center', justifyContent: 'space-between', color: '#888', cursor: 'pointer', userSelect: 'none' }} onClick={() => setShowFailedFiles(!showFailedFiles)}>
                      <div style={{ display:'flex', alignItems:'center', gap:'6px' }}>
                          <AlertCircle size={12} color="#ef4444"/>
                          失敗記錄 ({data?.failed_files?.length || 0})
                          <span style={{ fontSize: '0.7rem' }}>{showFailedFiles ? '▼' : '▶'}</span>
                      </div>
                      {data?.failed_files?.length > 0 && (
                          <button onClick={(e) => { e.stopPropagation(); const text = data.failed_files.map(f => `${f.filename} - ${f.reason}`).join('\\n'); navigator.clipboard.writeText(text); alert('✅ 已複製失敗記錄清單'); }} style={{ background: '#1e293b', color: '#94a3b8', border: '1px solid #334155', padding: '4px 8px', borderRadius: '4px', fontSize: '0.7rem', cursor: 'pointer', fontWeight: 'normal' }}>
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
              </div>'''

# 找到插入位置（在 {/* END LEFT COL AND LISTS */} 之前）
if '失敗記錄' in content:
    print('失敗記錄 UI 已存在')
else:
    # 在第一個 {/* END LEFT COL AND LISTS */} 之前插入
    pattern = r'(\s*{/\* END LEFT COL AND LISTS \*/})'
    match = re.search(pattern, content)
    if match:
        insert_pos = match.start()
        new_content = content[:insert_pos] + failed_ui + '\n' + content[insert_pos:]
        
        with open('dashboard/src/App.jsx', 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f'✅ 失敗記錄 UI 已插入到第 {content[:insert_pos].count(chr(10))+1} 行')
    else:
        print('❌ 找不到插入位置')

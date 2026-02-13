import React, { useState, useEffect, useRef } from 'react';
import { Mic, MicOff, Send, Activity, Eye, Calendar, Terminal, StopCircle, Volume2 } from 'lucide-react';
import './App.css';

// Basic App.css addition for layout (since index.css handles global)
// We will write App.css next.

function App() {
  const [socket, setSocket] = useState(null);
  const [messages, setMessages] = useState([]);
  const [inputVal, setInputVal] = useState("");
  const [logs, setLogs] = useState([]);
  const [status, setStatus] = useState({
    vision: {},
    schedule: "Chargement...",
    is_speaking: false,
    stt_enabled: false
  });
  const scrollRef = useRef(null);

  useEffect(() => {
    let ws = null;
    let reconnectTimeout = null;

    const connectWebSocket = () => {
      ws = new WebSocket("ws://localhost:8000/ws/status");

      ws.onopen = () => {
        console.log("Connected to System");
        addLog("System: Web Interface Connected");
      };

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === "status_update") {
          setStatus(prev => ({ ...prev, ...data.payload }));
        } else if (data.type === "log") {
          addLog(data.payload);
        } else if (data.type === "chat_message") {
          addMessage(data.payload);
        } else if (data.type === "history_sync") {
          // Sync full history from server
          setMessages(data.payload);
        } else if (data.type === "stream_token") {
          setMessages(prev => {
            const lastMsg = prev[prev.length - 1];
            if (lastMsg && lastMsg.role === 'assistant' && !lastMsg.isFinal) {
              // Append to existing
              const newContent = lastMsg.content + data.payload;
              return [...prev.slice(0, -1), { ...lastMsg, content: newContent }];
            } else {
              // Start new bubble
              return [...prev, { role: 'assistant', content: data.payload, isFinal: false }];
            }
          });
        }
      };

      ws.onclose = () => {
        console.log("Disconnected. Retrying in 3s...");
        addLog("System: Disconnected. Reconnecting...");
        reconnectTimeout = setTimeout(connectWebSocket, 3000);
      };

      ws.onerror = (err) => {
        ws.close();
      };

      setSocket(ws);
    };

    connectWebSocket();

    return () => {
      if (ws) ws.close();
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
    };
  }, []);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, logs]);

  const addLog = (msg) => {
    setLogs(prev => [...prev.slice(-49), msg]);
  };

  const addMessage = (msg) => {
    setMessages(prev => {
      // Prevent dupes: If the last message was a streaming version (not final) from the same role,
      // replace it with this final version.
      const last = prev[prev.length - 1];
      if (last && last.role === msg.role && !last.isFinal) {
        return [...prev.slice(0, -1), { ...msg, isFinal: true }];
      }
      return [...prev, { ...msg, isFinal: true }];
    });
  };

  const sendMessage = async () => {
    if (!inputVal.trim()) return;

    // Optimistic UI
    // addMessage({ role: 'user', content: inputVal }); // Wait for server echo instead?
    // Actually server will echo it back.

    try {
      await fetch("http://localhost:8000/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: inputVal })
      });
      setInputVal("");
    } catch (e) {
      addLog(`Error sending: ${e}`);
    }
  };

  const toggleSTT = async () => {
    try {
      await fetch(`http://localhost:8000/api/stt?enable=${!status.stt_enabled}`, { method: "POST" });
    } catch (e) {
      console.error(e);
    }
  };

  const stopAudio = async () => {
    await fetch("http://localhost:8000/api/stop", { method: "POST" });
  };

  return (
    <div className="layout">
      {/* LEFT SIDEBAR: Logs & Context */}
      <div className="sidebar left-sidebar glass-panel">
        <div className="panel-header">
          <Terminal size={18} /> <span>SYSTEM LOGS</span>
        </div>
        <div className="logs-container" ref={scrollRef}>
          {logs.map((l, i) => <div key={i} className="log-line">{l}</div>)}
        </div>

        <div className="context-box">
          <div className="panel-header"><Eye size={18} /> <span>VISION</span></div>
          <div className="context-content">
            Faces: {status.vision?.faces_count || 0}<br />
            {status.vision?.faces_names?.length > 0 &&
              <span className="names">{status.vision.faces_names.join(", ")}</span>
            }
          </div>
        </div>

        <div className="context-box">
          <div className="panel-header"><Calendar size={18} /> <span>AGENDA</span></div>
          <div className="context-content small-text">
            {status.schedule_context?.slice(0, 150)}...
          </div>
        </div>
      </div>

      {/* CENTER: CHAT */}
      <div className="main-chat">
        <div className="chat-header glass-panel">
          <div className="status-indicator">
            <div className={`dot ${status.is_speaking ? 'speaking' : 'idle'}`}></div>
            <span>BASTET AI</span>
          </div>
          {status.is_speaking && <div className="speaking-badge"><Volume2 size={16} className="pulse" /> Speaking...</div>}
        </div>

        <div className="messages-area" ref={scrollRef}>
          {messages.map((m, i) => (
            <div key={i} className={`message-row ${m.role}`}>
              <div className={`message-bubble ${m.role}`}>
                {m.content}
              </div>
            </div>
          ))}
        </div>

        <div className="input-area glass-panel">
          <button className={`icon-btn ${status.stt_enabled ? 'active' : ''}`} onClick={toggleSTT}>
            {status.stt_enabled ? <Mic /> : <MicOff className="dim" />}
          </button>
          <input
            type="text"
            value={inputVal}
            onChange={e => setInputVal(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && sendMessage()}
            placeholder="Message Bastet..."
          />
          <button className="icon-btn send" onClick={sendMessage}><Send /></button>
          <button className="icon-btn stop" onClick={stopAudio} title="Stop Audio (INTERRUPT)">
            <StopCircle color="#ff4444" fill="#ff444433" />
          </button>
        </div>
      </div>
    </div>
  );
}

export default App;

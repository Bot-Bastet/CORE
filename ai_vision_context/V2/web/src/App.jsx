import React, { useState, useEffect, useRef } from 'react';
import { Mic, MicOff, Send, Eye, EyeOff, Calendar, Terminal, Volume2, VolumeX, StopCircle, Settings } from 'lucide-react';
import './App.css';

function App() {
  const [socket, setSocket] = useState(null);
  const [messages, setMessages] = useState([]);
  const [inputVal, setInputVal] = useState("");
  const [logs, setLogs] = useState([]);
  const [status, setStatus] = useState({
    vision: {},
    schedule_context: "",
    is_speaking: false,
    stt_enabled: false,
    tts_enabled: false,
    vision_enabled: true,
    calendar_loaded: false
  });
  const [showSettings, setShowSettings] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const scrollRef = useRef(null);
  const logRef = useRef(null);

  useEffect(() => {
    let ws = null;
    let reconnectTimeout = null;

    const connectWebSocket = () => {
      ws = new WebSocket("ws://localhost:8000/ws/status");

      ws.onopen = () => {
        console.log("Connected to Bastet AI");
        addLog("✓ Connecté au serveur");
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
          setMessages(data.payload);
        } else if (data.type === "stream_token") {
          setMessages(prev => {
            const lastMsg = prev[prev.length - 1];
            if (lastMsg && lastMsg.role === 'assistant' && !lastMsg.isFinal) {
              const newContent = lastMsg.content + data.payload;
              return [...prev.slice(0, -1), { ...lastMsg, content: newContent }];
            } else {
              return [...prev, { role: 'assistant', content: data.payload, isFinal: false }];
            }
          });
        }
      };

      ws.onclose = () => {
        addLog("⚠ Déconnecté. Reconnexion...");
        reconnectTimeout = setTimeout(connectWebSocket, 3000);
      };

      ws.onerror = () => ws.close();
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
  }, [messages]);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

  const addLog = (msg) => {
    const time = new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    setLogs(prev => [...prev.slice(-99), `[${time}] ${msg}`]);
  };

  const addMessage = (msg) => {
    setMessages(prev => {
      // Check for duplicate: same role and same content
      const isDuplicate = prev.some(m =>
        m.role === msg.role && m.content === msg.content
      );
      if (isDuplicate) {
        return prev; // Don't add duplicate
      }

      const last = prev[prev.length - 1];
      if (last && last.role === msg.role && !last.isFinal) {
        return [...prev.slice(0, -1), { ...msg, isFinal: true }];
      }
      return [...prev, { ...msg, isFinal: true }];
    });
  };

  const sendMessage = async () => {
    if (!inputVal.trim()) return;

    try {
      await fetch("http://localhost:8000/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: inputVal })
      });
      setInputVal("");
    } catch (e) {
      addLog(`Erreur: ${e}`);
    }
  };

  const toggleSTT = async () => {
    try {
      await fetch(`http://localhost:8000/api/stt?enable=${!status.stt_enabled}`, { method: "POST" });
    } catch (e) {
      console.error(e);
    }
  };

  const toggleTTS = async () => {
    try {
      await fetch(`http://localhost:8000/api/tts?enable=${!status.tts_enabled}`, { method: "POST" });
    } catch (e) {
      console.error(e);
    }
  };

  const toggleVision = async () => {
    try {
      await fetch(`http://localhost:8000/api/vision?enable=${!status.vision_enabled}`, { method: "POST" });
    } catch (e) {
      console.error(e);
    }
  };

  const stopAudio = async () => {
    await fetch("http://localhost:8000/api/stop", { method: "POST" });
  };

  const startRecording = async () => {
    setIsRecording(true);
    addLog("🎤 Enregistrement en cours...");

    try {
      const response = await fetch("http://localhost:8000/api/record?duration=5", { method: "POST" });
      const data = await response.json();

      if (data.text) {
        setInputVal(prev => prev + (prev ? ' ' : '') + data.text);
        addLog(`✓ Dicté: ${data.text.substring(0, 50)}...`);
      } else if (data.error) {
        addLog(`✗ Erreur: ${data.error}`);
      } else {
        addLog("⚠ Aucun texte détecté");
      }
    } catch (e) {
      console.error(e);
      addLog(`✗ Erreur enregistrement`);
    }

    setIsRecording(false);
  };

  return (
    <div className="layout">
      {/* LEFT SIDEBAR */}
      <div className="sidebar">
        <div className="panel">
          <div className="panel-header">
            <Terminal size={16} /> <span>LOGS SYSTÈME</span>
          </div>
          <div className="logs-container" ref={logRef}>
            {logs.map((l, i) => <div key={i} className="log-line">{l}</div>)}
          </div>
        </div>

        <div className="panel">
          <div className="panel-header"><Eye size={16} /> <span>VISION</span></div>
          <div className="panel-content">
            <div className="status-row">
              <span>Visages:</span>
              <span className="value">{status.vision?.faces_count || 0}</span>
            </div>
            {status.vision?.faces_names?.length > 0 && (
              <div className="faces-list">
                {status.vision.faces_names.map((name, i) => (
                  <span key={i} className="face-tag">{name}</span>
                ))}
              </div>
            )}

            <div className="status-row" style={{ marginTop: '12px' }}>
              <span>Objets détectés:</span>
              <span className="value">{status.vision?.objects?.length || 0}</span>
            </div>
            {status.vision?.objects?.length > 0 && (
              <div className="objects-list">
                {status.vision.objects.map((obj, i) => (
                  <span key={i} className="object-tag">{obj}</span>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <Calendar size={16} />
            <span>AGENDA</span>
            <span className={`badge ${status.calendar_loaded ? 'active' : ''}`}>
              {status.calendar_loaded ? 'Chargé' : 'Non chargé'}
            </span>
          </div>
          <div className="panel-content small">
            {status.schedule_context || "Aucun événement"}
          </div>
        </div>

        {/* SETTINGS PANEL */}
        <div className="panel settings-panel">
          <div className="panel-header">
            <Settings size={16} />
            <span>PARAMÈTRES</span>
          </div>
          <div className="panel-content">
            <div className="setting-row">
              <span>Vision (YOLO)</span>
              <button
                className={`toggle-btn ${status.vision_enabled ? 'active' : ''}`}
                onClick={toggleVision}
              >
                {status.vision_enabled ? <Eye size={14} /> : <EyeOff size={14} />}
                {status.vision_enabled ? 'ON' : 'OFF'}
              </button>
            </div>
            <div className="setting-row">
              <span>Text-to-Speech</span>
              <button
                className={`toggle-btn ${status.tts_enabled ? 'active' : ''}`}
                onClick={toggleTTS}
              >
                {status.tts_enabled ? <Volume2 size={14} /> : <VolumeX size={14} />}
                {status.tts_enabled ? 'ON' : 'OFF'}
              </button>
            </div>
            <div className="setting-row">
              <span>Écoute continue</span>
              <button
                className={`toggle-btn ${status.stt_enabled ? 'active' : ''}`}
                onClick={toggleSTT}
              >
                {status.stt_enabled ? <Mic size={14} /> : <MicOff size={14} />}
                {status.stt_enabled ? 'ON' : 'OFF'}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* MAIN CHAT */}
      <div className="main-chat">
        <div className="chat-header">
          <div className="title">
            <div className={`status-dot ${status.is_speaking ? 'speaking' : ''}`}></div>
            <span>BASTET AI</span>
            {status.is_speaking && <span className="speaking-badge">🔊 Parle...</span>}
          </div>
          <div className="header-controls">
            <button
              className={`control-btn ${status.tts_enabled ? 'active' : ''}`}
              onClick={toggleTTS}
              title="Text-to-Speech"
            >
              {status.tts_enabled ? <Volume2 size={18} /> : <VolumeX size={18} />}
              <span>TTS</span>
            </button>
            <button
              className={`control-btn ${status.stt_enabled ? 'active' : ''}`}
              onClick={toggleSTT}
              title="Speech-to-Text"
            >
              {status.stt_enabled ? <Mic size={18} /> : <MicOff size={18} />}
              <span>STT</span>
            </button>
          </div>
        </div>

        <div className="messages-area" ref={scrollRef}>
          {messages.length === 0 && (
            <div className="empty-state">
              <p>Envoyez un message pour commencer la conversation avec Bastet</p>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`message-row ${m.role}`}>
              <div className={`message-bubble ${m.role}`}>
                {m.content}
              </div>
            </div>
          ))}
        </div>

        <div className="input-area">
          <input
            type="text"
            value={inputVal}
            onChange={e => setInputVal(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && sendMessage()}
            placeholder="Message Bastet..."
          />
          <button
            className={`icon-btn mic ${isRecording ? 'recording' : ''}`}
            onClick={startRecording}
            disabled={isRecording}
            title="Enregistrer (5s)"
          >
            <Mic size={20} />
          </button>
          <button className="icon-btn send" onClick={sendMessage}>
            <Send size={20} />
          </button>
          {status.is_speaking && (
            <button className="icon-btn stop" onClick={stopAudio} title="Interrompre">
              <StopCircle size={20} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;

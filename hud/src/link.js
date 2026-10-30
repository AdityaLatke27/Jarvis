// WebSocket link to the JARVIS Python backend (same origin, token-protected).

export class JarvisLink {
  constructor(handlers) {
    this.handlers = handlers; // { message(msg), open(), close() }
    this.token = document.querySelector('meta[name="jarvis-token"]')?.content || '';
    this.socket = null;
    this.retry = 1000;
    this.connected = false;
  }

  connect() {
    if (!this.token) {
      this.handlers.close?.('NO TOKEN — OPEN VIA python main.py --hud');
      return;
    }
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const socket = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(this.token)}`);
    socket.binaryType = 'arraybuffer';
    this.socket = socket;
    socket.onopen = () => {
      this.connected = true;
      this.retry = 1000;
      this.handlers.open?.();
    };
    socket.onmessage = (event) => {
      try {
        this.handlers.message?.(JSON.parse(event.data));
      } catch (err) {
        console.warn('bad message', err);
      }
    };
    socket.onclose = () => {
      const was = this.connected;
      this.connected = false;
      this.handlers.close?.(was ? 'LINK LOST — RECONNECTING' : 'BACKEND OFFLINE — RETRYING');
      setTimeout(() => this.connect(), this.retry);
      this.retry = Math.min(this.retry * 2, 10000);
    };
  }

  send(message) {
    if (!this.connected) return false;
    this.socket.send(JSON.stringify(message));
    return true;
  }

  async sendAudio(blob, mime) {
    if (!this.connected) return false;
    this.socket.send(JSON.stringify({ type: 'audio', mime }));
    this.socket.send(await blob.arrayBuffer());
    return true;
  }
}

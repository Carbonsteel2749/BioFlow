"""User-space TCP bridge: 0.0.0.0:11435 -> 127.0.0.1:11434 (Ollama).

Keeps the Docker backend able to reach the host Ollama instance that only
binds loopback. Run with: nohup setsid python3 ollama_proxy_11435.py &
"""
import select
import socket
import threading


def bridge(client):
    upstream = None
    try:
        upstream = socket.create_connection(("127.0.0.1", 11434), 5)
        sockets = [client, upstream]
        while True:
            readable, _, _ = select.select(sockets, [], [], 300)
            if not readable:
                break
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                target = upstream if source is client else client
                target.sendall(data)
    except Exception:
        pass
    finally:
        for sock in (client, upstream):
            try:
                sock.close()
            except Exception:
                pass


def main():
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", 11435))
    server.listen(64)
    while True:
        client, _ = server.accept()
        threading.Thread(target=bridge, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
